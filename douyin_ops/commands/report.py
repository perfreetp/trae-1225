"""
report 命令 - 报表生成
=====================
功能：
- daily: 生成日报
- weekly: 生成周报
"""
import os
import click
import pandas as pd
from datetime import datetime, timedelta
from douyin_ops.utils.io import read_data, write_data, write_multi_sheet
from douyin_ops.utils.common import parse_date, date_range, today, is_weekend
from douyin_ops.utils.console import info, success, warn, error, header, print_table


def _fmt(val, decimals=0, pct=False):
    if pd.isna(val):
        return '-'
    if pct:
        return f'{val * 100:.1f}%'
    return f'{val:,.{decimals}f}'


@click.group()
def report():
    """报表生成：日报、周报。"""
    pass


@report.command('daily')
@click.option('-i', '--input', 'input_file', required=True, type=click.Path(exists=True),
              help='数据文件')
@click.option('-d', '--date', 'report_date', default=None,
              help='报告日期 YYYY-MM-DD（默认今天）')
@click.option('-o', '--output', 'output_file', default=None,
              help='输出文件（默认 data/reports/daily_YYYYMMDD.xlsx）')
@click.option('--accounts', type=click.Path(exists=True), help='账号文件（用于分组）')
@click.option('--txt/--no-txt', default=False, show_default=True,
              help='同时生成文本摘要')
def cmd_daily(input_file, report_date, output_file, accounts, txt):
    """生成日报。"""
    report_date = report_date or today()
    header(f'生成日报 - {report_date}')

    dt = parse_date(report_date)
    if not output_file:
        output_file = f'data/reports/daily_{dt.strftime("%Y%m%d")}.xlsx'

    df = read_data(input_file)
    info(f'读取数据: {len(df)} 行')

    from douyin_ops.commands.stats import _resolve_col, _to_num

    date_col = _resolve_col(df.columns, '发布日期')
    if date_col:
        df['_date'] = pd.to_datetime(df[date_col], errors='coerce')
        df_daily = df[df['_date'].dt.date == dt.date()].copy()
    else:
        df_daily = df.copy()
    info(f'当日数据: {len(df_daily)} 行')

    metrics_map = {}
    for m in ['播放量', '点赞数', '评论数', '转发数', '收藏数', '涨粉数', '完播率']:
        c = _resolve_col(df.columns, m)
        if c:
            metrics_map[m] = c

    sheets = {}
    summary_rows = []
    for m, col in metrics_map.items():
        today_vals = _to_num(df_daily[col]) if len(df_daily) else pd.Series(dtype=float)
        all_vals = _to_num(df[col])
        today_sum = today_vals.sum() if len(today_vals) else 0
        today_mean = today_vals.mean() if len(today_vals) else 0
        is_pct = (m == '完播率')
        summary_rows.append({
            '指标': m,
            '当日总计': _fmt(today_sum, pct=is_pct) if is_pct else _fmt(today_sum),
            '当日均值': _fmt(today_mean, 1, pct=is_pct),
            '历史总计': _fmt(all_vals.sum(), pct=is_pct) if is_pct else _fmt(all_vals.sum()),
            '当日占比': _fmt(today_sum / all_vals.sum() if all_vals.sum() else 0, pct=True),
        })
    summary_df = pd.DataFrame(summary_rows)
    sheets['核心指标'] = summary_df
    info('核心指标:')
    print_table(summary_df.columns.tolist(), summary_df.values.tolist())

    title_col = _resolve_col(df.columns, '视频标题') or df.columns[0]
    play_col = _resolve_col(df.columns, '播放量')
    if play_col and len(df_daily):
        df_daily['_play'] = _to_num(df_daily[play_col])
        top = df_daily.nlargest(5, '_play')[[title_col, play_col,
                                              _resolve_col(df_daily.columns, '点赞数') or play_col]].copy()
        top.insert(0, '排名', range(1, len(top) + 1))
        sheets['Top5视频'] = top
        info('Top 5 视频:')
        print_table(top.columns.tolist(), top.values.tolist())

    acc_col = _resolve_col(df.columns, '账号ID')
    if acc_col and accounts:
        acc_df = read_data(accounts)
        group_col = '分组' if '分组' in acc_df.columns else None
        if group_col:
            df_with_group = df_daily.merge(acc_df[[acc_col, group_col]], on=acc_col, how='left')
            group_agg = {}
            for m, col in metrics_map.items():
                df_with_group[f'_{m}'] = _to_num(df_with_group[col])
                group_agg[f'_{m}'] = 'mean' if m == '完播率' else 'sum'
            grp = df_with_group.groupby(group_col).agg(group_agg).round(2).reset_index()
            grp.columns = [group_col] + list(metrics_map.keys())
            sheets['分组汇总'] = grp

    write_multi_sheet(sheets, output_file)
    success(f'日报已保存: {output_file}')

    if txt:
        txt_path = os.path.splitext(output_file)[0] + '.txt'
        lines = [
            f'抖音运营日报 - {report_date}',
            '=' * 40,
            '',
            '一、核心指标',
        ]
        for _, r in summary_df.iterrows():
            lines.append(f'  {r["指标"]}: 当日{r["当日总计"]} (均值 {r["当日均值"]}, 占比 {r["当日占比"]})')
        if 'Top5视频' in sheets:
            lines.append('')
            lines.append('二、Top 5 视频')
            for _, r in sheets['Top5视频'].iterrows():
                t = str(r.iloc[1])[:30]
                lines.append(f'  {r["排名"]}. {t} - 播放 {r.iloc[2]}')
        lines.append('')
        lines.append(f'生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        success(f'文本摘要已保存: {txt_path}')


@report.command('weekly')
@click.option('-i', '--input', 'input_file', required=True, type=click.Path(exists=True),
              help='数据文件')
@click.option('-w', '--week', 'week_date', default=None,
              help='周内任意日期 YYYY-MM-DD（默认本周）')
@click.option('-o', '--output', 'output_file', default=None,
              help='输出文件')
@click.option('--accounts', type=click.Path(exists=True), help='账号文件')
@click.option('--compare/--no-compare', default=True, show_default=True,
              help='环比上周')
@click.option('--txt/--no-txt', default=False, show_default=True)
def cmd_weekly(input_file, week_date, output_file, accounts, compare, txt):
    """生成周报。"""
    ref = parse_date(week_date or today())
    week_start = ref - timedelta(days=ref.weekday())
    week_end = week_start + timedelta(days=6)
    prev_start = week_start - timedelta(days=7)
    prev_end = week_end - timedelta(days=7)
    header(f'生成周报 - {week_start.strftime("%Y-%m-%d")} ~ {week_end.strftime("%Y-%m-%d")}')

    if not output_file:
        output_file = f'data/reports/weekly_{week_start.strftime("%Y%m%d")}_{week_end.strftime("%Y%m%d")}.xlsx'

    df = read_data(input_file)
    from douyin_ops.commands.stats import _resolve_col, _to_num

    date_col = _resolve_col(df.columns, '发布日期')
    if not date_col:
        error('找不到日期字段')
        return

    df['_date'] = pd.to_datetime(df[date_col], errors='coerce')
    cur = df[(df['_date'] >= week_start) & (df['_date'] <= week_end)].copy()
    info(f'本周数据: {len(cur)} 行')

    metrics_map = {}
    for m in ['播放量', '点赞数', '评论数', '转发数', '收藏数', '涨粉数', '完播率']:
        c = _resolve_col(df.columns, m)
        if c:
            metrics_map[m] = c

    sheets = {}
    rows = []
    for m, col in metrics_map.items():
        is_pct = (m == '完播率')
        cur_vals = _to_num(cur[col])
        cur_val = cur_vals.mean() if is_pct else cur_vals.sum()
        row = {'指标': m, '本周': _fmt(cur_val, pct=is_pct) if is_pct else _fmt(cur_val)}
        if compare:
            prev = df[(df['_date'] >= prev_start) & (df['_date'] <= prev_end)]
            prev_vals = _to_num(prev[col])
            prev_val = prev_vals.mean() if is_pct else prev_vals.sum()
            row['上周'] = _fmt(prev_val, pct=is_pct) if is_pct else _fmt(prev_val)
            if prev_val != 0:
                change = (cur_val - prev_val) / abs(prev_val)
                row['环比'] = _fmt(change, pct=True)
            else:
                row['环比'] = 'N/A'
        rows.append(row)
    comp_df = pd.DataFrame(rows)
    sheets['周度对比'] = comp_df
    info('周度对比:')
    print_table(comp_df.columns.tolist(), comp_df.values.tolist())

    daily_rows = []
    for d in date_range(week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d')):
        dd = parse_date(d)
        day = cur[cur['_date'].dt.date == dd.date()]
        r = {'日期': d, '星期': dd.strftime('%A'), '发布数': len(day)}
        for m, col in metrics_map.items():
            vals = _to_num(day[col])
            r[m] = vals.mean() if m == '完播率' else vals.sum()
        daily_rows.append(r)
    daily_df = pd.DataFrame(daily_rows)
    sheets['按日明细'] = daily_df
    info('按日明细:')
    cols = list(daily_df.columns)[:6]
    print_table(cols, daily_df[cols].values.tolist())

    title_col = _resolve_col(df.columns, '视频标题') or df.columns[0]
    play_col = _resolve_col(df.columns, '播放量')
    if play_col and len(cur):
        cur['_play'] = _to_num(cur[play_col])
        top = cur.nlargest(10, '_play')[[title_col, play_col,
                                           _resolve_col(cur.columns, '点赞数') or play_col,
                                           _resolve_col(cur.columns, '涨粉数') or play_col]].copy()
        top.insert(0, '排名', range(1, len(top) + 1))
        sheets['Top10视频'] = top

    write_multi_sheet(sheets, output_file)
    success(f'周报已保存: {output_file}')

    if txt:
        txt_path = os.path.splitext(output_file)[0] + '.txt'
        lines = [
            f'抖音运营周报',
            f'周期: {week_start.strftime("%Y-%m-%d")} ~ {week_end.strftime("%Y-%m-%d")}',
            '=' * 50,
            '',
            '一、核心指标对比',
        ]
        for _, r in comp_df.iterrows():
            extra = f' (环比 {r["环比"]})' if compare else ''
            lines.append(f'  {r["指标"]}: 本周{r["本周"]}{extra}')
        lines.append('')
        lines.append('二、每日发布数')
        for _, r in daily_df.iterrows():
            lines.append(f'  {r["日期"]} {r["星期"]}: {r["发布数"]} 条')
        lines.append('')
        lines.append(f'生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        success(f'文本摘要已保存: {txt_path}')
