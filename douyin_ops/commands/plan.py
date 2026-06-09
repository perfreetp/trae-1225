"""
plan 命令 - 发布排期
===================
功能：
- generate: 按日期生成发布排期
- check: 检查时间冲突
"""
import os
import random
import click
import pandas as pd
from datetime import datetime, timedelta
from douyin_ops.utils.io import read_data, write_data, write_multi_sheet
from douyin_ops.utils.common import parse_date, date_range, is_weekend, today
from douyin_ops.utils.console import info, success, warn, error, header, print_table

DEFAULT_TIME_SLOTS = {
    '早高峰': '07:30',
    '午间': '12:00',
    '下午': '15:00',
    '晚高峰': '18:30',
    '夜间': '21:00',
}


@click.group()
def plan():
    """发布排期：按日期生成、避开冲突。"""
    pass


@plan.command('generate')
@click.option('-s', '--start', 'start_date', required=True, help='起始日期 YYYY-MM-DD')
@click.option('-e', '--end', 'end_date', required=True, help='结束日期 YYYY-MM-DD')
@click.option('-a', '--accounts', 'accounts_file', type=click.Path(exists=True),
              help='账号文件（可选，用于分配账号）')
@click.option('-v', '--videos', 'videos_file', type=click.Path(exists=True),
              help='视频清单（可选，用于分配视频）')
@click.option('-o', '--output', 'output_file', default='data/publish_plan.xlsx',
              show_default=True, help='输出排期文件')
@click.option('--freq', type=click.Choice(['daily', 'weekday', 'weekend', 'custom']),
              default='daily', show_default=True, help='发布频率')
@click.option('--slots', multiple=True,
              help='发布时间段标签，对应 DEFAULT_TIME_SLOTS；或 HH:MM 格式')
@click.option('--per-day', type=int, default=1, show_default=True,
              help='每天发布条数')
@click.option('--shuffle/--no-shuffle', default=False, show_default=True,
              help='随机分配视频和时间段')
def cmd_generate(start_date, end_date, accounts_file, videos_file, output_file,
                 freq, slots, per_day, shuffle):
    """按日期生成发布排期。"""
    header('生成发布排期')

    if freq == 'weekday':
        dates = date_range(start_date, end_date, include_weekends=False)
    elif freq == 'weekend':
        all_dates = date_range(start_date, end_date)
        dates = [d for d in all_dates if is_weekend(d)]
    else:
        dates = date_range(start_date, end_date)

    info(f'排期范围: {start_date} ~ {end_date}，有效天数: {len(dates)}')

    time_slots = []
    if slots:
        for s in slots:
            if s in DEFAULT_TIME_SLOTS:
                time_slots.append((s, DEFAULT_TIME_SLOTS[s]))
            else:
                time_slots.append((f'自定义{s}', s))
    else:
        time_slots = list(DEFAULT_TIME_SLOTS.items())

    accounts = []
    if accounts_file:
        acc_df = read_data(accounts_file)
        accounts = acc_df['账号ID'].astype(str).tolist() if '账号ID' in acc_df.columns else []
        info(f'加载账号: {len(accounts)} 个')

    videos = []
    if videos_file:
        vid_df = read_data(videos_file)
        for _, r in vid_df.iterrows():
            videos.append({
                '视频文件': r.get('文件名', ''),
                '完整路径': r.get('完整路径', ''),
                '标题': r.get('标题', ''),
                '口播词': r.get('口播词', ''),
                '话题': r.get('话题', ''),
            })
        info(f'加载视频素材: {len(videos)} 条')

    rows = []
    vid_idx = 0
    total_slots = per_day * len(dates)
    if shuffle:
        random.shuffle(videos)

    for d in dates:
        daily_slots = time_slots * ((per_day // len(time_slots)) + 1)
        if shuffle:
            random.shuffle(daily_slots)
        daily_slots = daily_slots[:per_day]

        for si, (slot_name, slot_time) in enumerate(daily_slots):
            vid = videos[vid_idx % len(videos)] if videos else {}
            vid_idx += 1
            account = accounts[(len(rows)) % len(accounts)] if accounts else ''
            rows.append({
                '日期': d,
                '星期': parse_date(d).strftime('%A'),
                '时间段': slot_name,
                '发布时间': f'{d} {slot_time}',
                '账号ID': account,
                '视频文件': vid.get('视频文件', ''),
                '完整路径': vid.get('完整路径', ''),
                '标题': vid.get('标题', ''),
                '口播词': vid.get('口播词', ''),
                '话题': vid.get('话题', ''),
                '状态': '待发布',
                '备注': '',
            })

    df = pd.DataFrame(rows)
    df = df.sort_values(['日期', '发布时间']).reset_index(drop=True)
    df.insert(0, '序号', range(1, len(df) + 1))

    summary = df.groupby('日期').size().reset_index(name='条数')
    info(f'生成排期 {len(df)} 条，按日分布:')
    print_table(summary.columns.tolist(), summary.values.tolist())

    sheets = {
        '排期明细': df,
        '按日汇总': summary,
    }
    if accounts_file:
        acc_stats = df.groupby('账号ID').size().reset_index(name='条数') if '账号ID' in df.columns else pd.DataFrame()
        if not acc_stats.empty:
            sheets['按账号汇总'] = acc_stats

    write_multi_sheet(sheets, output_file)
    success(f'排期已保存: {output_file}')


@plan.command('check')
@click.option('-i', '--input', 'input_file', required=True, type=click.Path(exists=True),
              help='排期文件路径')
@click.option('-o', '--output', 'output_file', default=None,
              help='冲突报告输出路径')
@click.option('--min-gap', type=int, default=30, show_default=True,
              help='同一账号最小发布间隔（分钟）')
@click.option('--time-col', default='发布时间', show_default=True,
              help='发布时间列名')
@click.option('--account-col', default='账号ID', show_default=True,
              help='账号列名')
def cmd_check(input_file, output_file, min_gap, time_col, account_col):
    """检查排期时间冲突。"""
    header('检查排期冲突')

    df = read_data(input_file)
    info(f'读取 {len(df)} 条排期')

    if time_col not in df.columns:
        error(f'缺少时间列: {time_col}')
        return

    conflicts = []
    df['_dt'] = pd.to_datetime(df[time_col], errors='coerce')
    df = df.sort_values('_dt').reset_index()

    dup_time = df[df.duplicated(subset=[time_col], keep=False)]
    for dt, g in dup_time.groupby(time_col):
        if len(g) > 1:
            conflicts.append([
                str(dt),
                f'同时间 {len(g)} 条',
                '、'.join(g['账号ID'].astype(str).tolist()) if account_col in g.columns else '-',
                '、'.join(str(i + 1) for i in g['index'].tolist()),
            ])

    if account_col in df.columns:
        for acc, g in df.groupby(account_col):
            g = g.sort_values('_dt')
            dts = g['_dt'].dropna().tolist()
            idxs = g['index'].tolist()
            for i in range(1, len(dts)):
                gap = (dts[i] - dts[i - 1]).total_seconds() / 60
                if gap < min_gap:
                    conflicts.append([
                        f'{dts[i - 1]} ~ {dts[i]}',
                        f'账号 {acc} 间隔 {gap:.0f} 分钟',
                        f'需 ≥ {min_gap} 分钟',
                        f'行 {idxs[i - 1] + 1}、{idxs[i] + 1}',
                    ])

    video_col = '视频文件' if '视频文件' in df.columns else '完整路径'
    if video_col in df.columns:
        dup_vid = df[df[video_col].notna() & df.duplicated(subset=[video_col], keep=False)]
        for v, g in dup_vid.groupby(video_col):
            if str(v).strip():
                conflicts.append([
                    '、'.join(g[time_col].astype(str).tolist()),
                    f'视频重复: {os.path.basename(str(v))}',
                    f'{len(g)} 次使用',
                    '、'.join(str(i + 1) for i in g['index'].tolist()),
                ])

    if conflicts:
        warn(f'发现 {len(conflicts)} 处冲突:')
        print_table(['时间', '问题', '详情', '行号'], conflicts[:30])
    else:
        success('未发现冲突，排期合规！')

    if output_file and conflicts:
        rep = pd.DataFrame(conflicts, columns=['时间', '问题', '详情', '行号'])
        write_data(rep, output_file)
        success(f'冲突报告已保存: {output_file}')
