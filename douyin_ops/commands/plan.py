"""
plan 命令 - 发布排期
===================
功能：
- generate: 按日期生成发布排期（生成时自动避冲突）
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

MIN_ACCOUNT_GAP_MINUTES = 60
COLLISION_STEP_MINUTES = 5
COLLISION_MAX_STEPS = 12


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
@click.option('--min-gap', type=int, default=MIN_ACCOUNT_GAP_MINUTES, show_default=True,
              help='同账号最小发布间隔（分钟）')
@click.option('--allow-reuse/--no-allow-reuse', default=False, show_default=True,
              help='允许视频重复使用（默认一个视频只排一次）')
@click.option('--step-minutes', type=int, default=COLLISION_STEP_MINUTES, show_default=True,
              help='冲突时自动错开的步长（分钟）')
def cmd_generate(start_date, end_date, accounts_file, videos_file, output_file,
                 freq, slots, per_day, shuffle, min_gap, allow_reuse, step_minutes):
    """按日期生成发布排期，生成时自动避开冲突。

    \b
    自动避冲突策略：
    1. 同一发布时间精确重复 → 自动顺延 step-minutes 分钟
    2. 同账号两次发布间隔 < min-gap 分钟 → 自动顺延
    3. 同一视频重复使用（默认禁用） → 启用 allow-reuse 才允许
    4. 每天条数多于时间段 → 用顺延分钟方式补齐
    """
    header('生成发布排期（自动避冲突）')

    if freq == 'weekday':
        dates = date_range(start_date, end_date, include_weekends=False)
    elif freq == 'weekend':
        all_dates = date_range(start_date, end_date)
        dates = [d for d in all_dates if is_weekend(d)]
    else:
        dates = date_range(start_date, end_date)

    info(f'排期范围: {start_date} ~ {end_date}，有效天数: {len(dates)}')
    info(f'避冲突配置: 同账号间隔≥{min_gap}分钟，冲突步长={step_minutes}分钟，视频复用={allow_reuse}')

    base_slots = []
    if slots:
        for s in slots:
            if s in DEFAULT_TIME_SLOTS:
                base_slots.append((s, DEFAULT_TIME_SLOTS[s]))
            else:
                base_slots.append((f'自定义{s}', s))
    else:
        base_slots = list(DEFAULT_TIME_SLOTS.items())

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
                '_used': False,
            })
        info(f'加载视频素材: {len(videos)} 条')

    total_needed = per_day * len(dates)
    if videos and not allow_reuse and total_needed > len(videos):
        error(f'需要 {total_needed} 条视频，但仅有 {len(videos)} 条，且 --no-allow-reuse。')
        error(f'请增加视频素材数量，或启用 --allow-reuse 允许重复使用。')
        return

    if shuffle:
        random.shuffle(videos)

    scheduled_times = []
    account_last_time = {}
    used_video_paths = set()
    rows = []
    warnings = []

    vid_idx = 0

    def _pick_video():
        """挑选下一个可用视频，返回 (vid_dict, is_reused)。"""
        nonlocal vid_idx
        if not videos:
            return {}, False
        if allow_reuse:
            v = videos[vid_idx % len(videos)]
            vid_idx += 1
            return v, False
        for i in range(len(videos)):
            cand = videos[(vid_idx + i) % len(videos)]
            key = cand.get('完整路径') or cand.get('视频文件') or ''
            if key and key not in used_video_paths:
                vid_idx = (vid_idx + i + 1) % len(videos)
                if key:
                    used_video_paths.add(key)
                return cand, False
        return None, False

    def _resolve_time(d_str: str, candidate: str, account_id: str, step: int = 0):
        """
        返回最终 datetime，若尝试失败返回 None。
        检查：1) 时间精确重复；2) 同账号间隔过近。
        """
        base_dt = datetime.strptime(f'{d_str} {candidate}', '%Y-%m-%d %H:%M')
        for attempt in range(COLLISION_MAX_STEPS):
            dt = base_dt + timedelta(minutes=step + attempt * step_minutes)
            for sched in scheduled_times:
                if abs((dt - sched['dt']).total_seconds()) < 60:
                    if attempt == COLLISION_MAX_STEPS - 1:
                        warnings.append(f'{d_str} {candidate} → 同时间冲突，已最多顺延 {attempt * step_minutes} 分钟仍冲突')
                    continue
            if account_id and account_id in account_last_time:
                last_dt = account_last_time[account_id]
                gap = abs((dt - last_dt).total_seconds()) / 60
                if gap < min_gap:
                    if attempt == COLLISION_MAX_STEPS - 1:
                        warnings.append(f'账号 {account_id} 间隔仅 {gap:.0f} 分钟，尝试多次仍不足 {min_gap} 分钟')
                    continue
            return dt
        return base_dt + timedelta(minutes=step + (COLLISION_MAX_STEPS - 1) * step_minutes)

    for d in dates:
        daily_slots_pool = []
        mult = (per_day + len(base_slots) - 1) // len(base_slots)
        for m in range(mult):
            for slot_name, slot_time in base_slots:
                offset_minutes = m * step_minutes * 2
                daily_slots_pool.append((slot_name if m == 0 else f'{slot_name}+{offset_minutes}m', slot_time, offset_minutes))
        if shuffle:
            random.shuffle(daily_slots_pool)
        daily_slots_pool = daily_slots_pool[:per_day]

        day_offset = 0
        for si, (slot_name, slot_time, extra_offset) in enumerate(daily_slots_pool):
            vid, reused = _pick_video()
            if vid is None and videos and not allow_reuse:
                error(f'视频素材已耗尽：当前日期 {d} 仅排了 {si} / {per_day} 条')
                break

            account = accounts[(len(rows)) % len(accounts)] if accounts else ''
            final_dt = _resolve_time(d, slot_time, account, step=extra_offset + day_offset)
            if final_dt is None:
                day_offset += step_minutes
                final_dt = _resolve_time(d, slot_time, account, step=extra_offset + day_offset)

            final_time_str = final_dt.strftime('%H:%M')
            full_dt_str = final_dt.strftime('%Y-%m-%d %H:%M')
            scheduled_times.append({'dt': final_dt})
            if account:
                account_last_time[account] = final_dt

            vid_path = vid.get('完整路径') or vid.get('视频文件') or ''
            if vid_path and not allow_reuse:
                used_video_paths.add(vid_path)

            rows.append({
                '日期': d,
                '星期': parse_date(d).strftime('%A'),
                '时间段': slot_name,
                '发布时间': full_dt_str,
                '账号ID': account,
                '视频文件': vid.get('视频文件', '') if isinstance(vid, dict) else '',
                '完整路径': vid.get('完整路径', '') if isinstance(vid, dict) else '',
                '标题': vid.get('标题', '') if isinstance(vid, dict) else '',
                '口播词': vid.get('口播词', '') if isinstance(vid, dict) else '',
                '话题': vid.get('话题', '') if isinstance(vid, dict) else '',
                '状态': '待发布',
                '备注': f'原时间 {slot_time}，已顺延' if final_time_str != slot_time else '',
            })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(['发布时间']).reset_index(drop=True)
        df.insert(0, '序号', range(1, len(df) + 1))

    info(f'生成排期 {len(df)} 条')
    if warnings:
        warn(f'自动避让时的 {len(warnings)} 条提示:')
        for w in warnings[:10]:
            warn(f'  - {w}')
        if len(warnings) > 10:
            warn(f'  ... 还有 {len(warnings) - 10} 条')

    summary = df.groupby('日期').size().reset_index(name='条数')
    info('按日分布:')
    print_table(summary.columns.tolist(), summary.values.tolist())

    actual_times = pd.to_datetime(df['发布时间'])
    dup_count = int(actual_times.duplicated().sum())
    if dup_count:
        warn(f'最终仍有 {dup_count} 条发布时间完全重复，请手动调整（建议增加时间段或减少 per-day）')
    else:
        success('发布时间唯一性校验：通过')

    if '账号ID' in df.columns and df['账号ID'].astype(str).ne('').any():
        gap_ok = True
        for acc, g in df.groupby('账号ID'):
            if not acc:
                continue
            ats = pd.to_datetime(g['发布时间']).sort_values()
            for i in range(1, len(ats)):
                gap = (ats.iloc[i] - ats.iloc[i - 1]).total_seconds() / 60
                if gap < min_gap:
                    gap_ok = False
                    warn(f'账号 {acc} 仍存在 {gap:.0f} 分钟间隔（不足 {min_gap}）')
        if gap_ok:
            success(f'同账号间隔校验（≥{min_gap} 分钟）：通过')

    sheets = {
        '排期明细': df,
        '按日汇总': summary,
    }
    if accounts_file and not df.empty:
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
