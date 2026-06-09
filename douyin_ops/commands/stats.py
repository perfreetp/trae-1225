"""
stats 命令 - 数据汇总
===================
功能：
- summary: 汇总播放、点赞、完播、涨粉
- rank: 视频/账号排行榜
- trend: 按日期趋势汇总
"""
import os
import click
import pandas as pd
from datetime import datetime, timedelta
from douyin_ops.utils.io import read_data, write_data, write_multi_sheet
from douyin_ops.utils.common import parse_date, today
from douyin_ops.utils.console import info, success, warn, error, header, print_table

METRIC_ALIASES = {
    '播放量': ['播放量', '播放数', 'views', 'play_count', '曝光量'],
    '点赞数': ['点赞数', '点赞', '点赞量', 'likes', 'digg_count'],
    '评论数': ['评论数', '评论', 'comments', 'comment_count'],
    '转发数': ['转发数', '转发', '分享数', 'shares', 'share_count'],
    '收藏数': ['收藏数', '收藏', 'collects', 'collect_count'],
    '完播率': ['完播率', '完播', 'finish_rate', 'watch_rate'],
    '涨粉数': ['涨粉数', '涨粉', '新增粉丝', 'new_fans', 'fans_gain'],
    '粉丝数': ['粉丝数', '总粉丝', 'fans', 'follower_count'],
    '发布日期': ['发布日期', '日期', 'date', 'publish_date', '发布时间'],
    '视频标题': ['视频标题', '标题', 'title', '视频名'],
    '账号ID': ['账号ID', '账号', '作者ID', 'author_id', 'account'],
    '视频ID': ['视频ID', 'aweme_id', 'video_id'],
}


def _resolve_col(df_columns: list, target: str) -> str:
    """根据别名解析列名。"""
    if target in df_columns:
        return target
    for alias in METRIC_ALIASES.get(target, []):
        if alias in df_columns:
            return alias
    return ''


def _to_num(series, is_percent=False):
    """将列转为数值。is_percent=True 时:
       - 有 % 去掉后 /100
       - 值 > 1 视作带百分号的整数（38 → 0.38）
       - 值 ≤ 1 视作真实比例（0.38 → 0.38，保持不变）
       这样 "38%" / "38" / "0.38" 三种写法归一后一致。
    """
    s = series.astype(str).str.replace(',', '', regex=False)
    if is_percent:
        s = s.str.replace('%', '', regex=False).str.strip()
        s = pd.to_numeric(s, errors='coerce')
        mask_large = s > 1
        s[mask_large] = s[mask_large] / 100
    else:
        s = pd.to_numeric(s, errors='coerce')
    return s.fillna(0)


@click.group()
def stats():
    """数据汇总：播放、点赞、完播、涨粉。"""
    pass


@stats.command('summary')
@click.option('-i', '--input', 'input_file', required=True, type=click.Path(exists=True),
              help='数据文件（CSV/Excel）')
@click.option('-o', '--output', 'output_file', default='data/stats_summary.xlsx',
              show_default=True, help='输出文件')
@click.option('--group-by', default=None, help='分组字段（如 账号ID/分组）')
@click.option('--start', default=None, help='起始日期过滤')
@click.option('--end', default=None, help='结束日期过滤')
@click.option('--compare/--no-compare', default=True, show_default=True,
              help='输出本周vs上周、本月vs上月周期对比')
@click.option('--ref-date', default=None,
              help='周期对比的参照日期（YYYY-MM-DD，默认今天）')
def cmd_summary(input_file, output_file, group_by, start, end, compare, ref_date):
    """汇总播放、点赞、完播、涨粉等核心指标，含周期对比。"""
    header('汇总数据指标（含周期对比）')

    df = read_data(input_file)
    info(f'原始数据: {len(df)} 行')

    date_col = _resolve_col(df.columns, '发布日期')
    if date_col:
        df['_date'] = pd.to_datetime(df[date_col], errors='coerce')
        df = df.dropna(subset=['_date'])
        if start:
            df = df[df['_date'] >= parse_date(start)]
        if end:
            df = df[df['_date'] <= parse_date(end)]
        info(f'日期过滤后: {len(df)} 行，范围: {df["_date"].min().date()} ~ {df["_date"].max().date()}')
    else:
        warn('未识别到日期列，将跳过周期对比')
        compare = False

    metrics = ['播放量', '点赞数', '评论数', '转发数', '收藏数', '涨粉数', '完播率']
    col_map = {}
    for m in metrics:
        c = _resolve_col(df.columns, m)
        if c:
            col_map[m] = c

    info(f'识别到字段: {list(col_map.keys())}')

    num_cols = {}
    for m, c in col_map.items():
        is_pct = (m == '完播率')
        num_cols[m] = _to_num(df[c], is_percent=is_pct)

    sheets = {}

    total_rows = []
    for m in col_map:
        s = num_cols[m]
        is_pct = (m == '完播率')
        if is_pct:
            total_rows.append([
                m,
                f'{s.mean():.2%}',
                f'{s.mean():.2%}',
                f'{s.max():.2%}',
                f'{s.median():.2%}',
            ])
        else:
            total_rows.append([
                m,
                int(s.sum()),
                round(s.mean(), 1),
                int(s.max()),
                int(s.median()),
            ])
    if total_rows:
        total_df = pd.DataFrame(total_rows, columns=['指标', '总计', '均值', '最大值', '中位数'])
        sheets['总览'] = total_df
        info('总览:')
        print_table(total_df.columns.tolist(), total_df.values.tolist())

    if compare and date_col:
        ref = parse_date(ref_date) if ref_date else datetime.now()
        this_week_start = ref - timedelta(days=ref.weekday())
        this_week_end = this_week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
        last_week_start = this_week_start - timedelta(days=7)
        last_week_end = this_week_start - timedelta(seconds=1)
        this_month_start = ref.replace(day=1)
        next_month = (this_month_start + timedelta(days=32)).replace(day=1)
        last_month_start = (this_month_start - timedelta(days=1)).replace(day=1)
        last_month_end = this_month_start - timedelta(seconds=1)

        def _slice(s_dt, e_dt):
            mask = (df['_date'] >= s_dt) & (df['_date'] <= e_dt)
            res = {}
            for m in col_map:
                vals = num_cols[m][mask]
                is_pct = (m == '完播率')
                v = vals.mean() if is_pct else vals.sum()
                if pd.isna(v):
                    v = 0
                res[m] = v
            return res, int(mask.sum())

        tw, tw_cnt = _slice(this_week_start, this_week_end)
        lw, lw_cnt = _slice(last_week_start, last_week_end)
        tm, tm_cnt = _slice(this_month_start, ref + timedelta(days=1) - timedelta(seconds=1))
        lm, lm_cnt = _slice(last_month_start, last_month_end)

        week_rows = []
        for m in col_map:
            is_pct = (m == '完播率')
            a, b = tw.get(m, 0), lw.get(m, 0)
            if is_pct:
                week_rows.append([
                    m, tw_cnt, f'{a:.2%}', lw_cnt, f'{b:.2%}',
                    f'{(a - b):.2%}', (f'{((a - b) / b * 100):+.1f}%' if b else 'N/A'),
                ])
            else:
                diff = a - b
                week_rows.append([
                    m, tw_cnt, int(a), lw_cnt, int(b),
                    f'{int(diff):+d}', (f'{(diff / abs(b) * 100):+.1f}%' if b else 'N/A'),
                ])
        week_df = pd.DataFrame(week_rows, columns=[
            '指标', '本周条数', '本周值', '上周条数', '上周值', '差值', '环比%'
        ])
        sheets['周度对比'] = week_df
        info(f'\n周度对比（参照日 {ref.strftime("%Y-%m-%d")}）:')
        print_table(week_df.columns.tolist(), week_df.values.tolist())

        month_rows = []
        for m in col_map:
            is_pct = (m == '完播率')
            a, b = tm.get(m, 0), lm.get(m, 0)
            if is_pct:
                month_rows.append([
                    m, tm_cnt, f'{a:.2%}', lm_cnt, f'{b:.2%}',
                    f'{(a - b):.2%}', (f'{((a - b) / b * 100):+.1f}%' if b else 'N/A'),
                ])
            else:
                diff = a - b
                month_rows.append([
                    m, tm_cnt, int(a), lm_cnt, int(b),
                    f'{int(diff):+d}', (f'{(diff / abs(b) * 100):+.1f}%' if b else 'N/A'),
                ])
        month_df = pd.DataFrame(month_rows, columns=[
            '指标', '本月条数', '本月值', '上月条数', '上月值', '差值', '同比%'
        ])
        sheets['月度对比'] = month_df
        info('月度对比:')
        print_table(month_df.columns.tolist(), month_df.values.tolist())

    if group_by:
        gb = _resolve_col(df.columns, group_by) or group_by
        if gb in df.columns:
            work = df[[gb, '_date']].copy() if date_col else df[[gb]].copy()
            for m in col_map:
                work[m] = num_cols[m].values
            work['_cnt'] = 1
            agg_spec = {'条数': ('_cnt', 'count')}
            for m in col_map:
                if m == '完播率':
                    agg_spec[m] = (m, 'mean')
                else:
                    agg_spec[m + '_总计'] = (m, 'sum')
                    agg_spec[m + '_均值'] = (m, 'mean')
            grouped = work.groupby(gb).agg(**agg_spec).reset_index()

            for col in grouped.columns:
                if '完播率' in col and col != gb:
                    grouped[col] = grouped[col].apply(lambda x: f'{x:.2%}' if pd.notna(x) else x)

            rank_rows = []

            if compare and date_col:
                def _attach_compare(period_label, s_dt, e_dt, s_dt_prev, e_dt_prev, diff_label):
                    """给 grouped 追加一组对比列，并把所有指标的环比行写入 rank_rows。"""
                    nonlocal grouped
                    for m in col_map:
                        is_pct = (m == '完播率')
                        agg_f = 'mean' if is_pct else 'sum'
                        mask_cur = (work['_date'] >= s_dt) & (work['_date'] <= e_dt)
                        mask_prev = (work['_date'] >= s_dt_prev) & (work['_date'] <= e_dt_prev)
                        cur_vals = work[mask_cur].groupby(gb)[m].agg(agg_f)
                        prev_vals = work[mask_prev].groupby(gb)[m].agg(agg_f)
                        cur_name = f'{period_label}_{m}'
                        prev_name = f'{diff_label}_{m}'
                        delta_name = f'变化_{m}_{period_label}vs{diff_label}'
                        merged = pd.DataFrame({cur_name: cur_vals, prev_name: prev_vals}).fillna(0)
                        if is_pct:
                            delta_raw = merged[cur_name] - merged[prev_name]
                            merged[delta_name] = delta_raw.apply(lambda x: f'{x:+.2%}')
                            merged[cur_name] = merged[cur_name].apply(lambda x: f'{x:.2%}')
                            merged[prev_name] = merged[prev_name].apply(lambda x: f'{x:.2%}')
                        else:
                            delta_raw = merged[cur_name] - merged[prev_name]
                            merged[delta_name] = delta_raw.astype(int).apply(lambda x: f'{x:+d}')
                            merged[cur_name] = merged[cur_name].astype(int)
                            merged[prev_name] = merged[prev_name].astype(int)

                        for key, a in merged.iterrows():
                            b_val = (merged[prev_name].loc[key])
                            rate_label = 'N/A'
                            rate_num = None
                            if is_pct:
                                rate_num = None
                            else:
                                try:
                                    pn = int(str(b_val).replace(',', '')) if not isinstance(b_val, (int, float)) else float(b_val)
                                except (ValueError, TypeError):
                                    pn = 0
                                if pn:
                                    rate_num = (float(a[cur_name]) - pn) / abs(pn)
                                    rate_label = f'{rate_num * 100:+.1f}%'
                            rank_rows.append({
                                '分组维度': group_by,
                                '分组值': key,
                                '对比周期': f'{period_label}vs{diff_label}',
                                '指标': m,
                                period_label: a[cur_name],
                                diff_label: b_val,
                                '变化差值': a[delta_name],
                                '变化率%': rate_label,
                                '_变化率_num': rate_num if rate_num is not None else 0,
                            })

                        grouped = grouped.merge(merged, left_on=gb, right_index=True, how='left')

                _attach_compare('本周', this_week_start, this_week_end,
                                last_week_start, last_week_end, '上周')
                _attach_compare('本月', this_month_start, ref + timedelta(days=1) - timedelta(seconds=1),
                                last_month_start, last_month_end, '上月')

            ordered = [gb, '条数']
            base_metrics = [m for m in col_map if m != '完播率']
            for m in base_metrics:
                for suffix in ['_总计', '_均值']:
                    c = m + suffix
                    if c in grouped.columns:
                        ordered.append(c)
            if '完播率' in grouped.columns:
                ordered.append('完播率')
            if compare and date_col:
                compare_tags = []
                for m in col_map:
                    for pair in [('本周', '上周'), ('本月', '上月')]:
                        for tag in [f'{pair[0]}_{m}', f'{pair[1]}_{m}', f'变化_{m}_{pair[0]}vs{pair[1]}']:
                            if tag in grouped.columns:
                                ordered.append(tag)
            grouped = grouped[[c for c in ordered if c in grouped.columns]]
            sheets[f'按{group_by}'] = grouped
            info(f'\n按{group_by}分组: {len(grouped)} 组')
            preview_cols = list(grouped.columns[:min(8, len(grouped.columns))])
            print_table(preview_cols, grouped.head(10)[preview_cols].values.tolist())

            if compare and date_col and rank_rows:
                rdf = pd.DataFrame(rank_rows)
                if not rdf.empty:
                    def _sort_key(row):
                        if pd.isna(row['_变化率_num']) or row['_变化率_num'] == 0:
                            if isinstance(row['变化差值'], str) and row['变化差值'].endswith('%'):
                                try:
                                    return abs(float(row['变化差值'].replace('%', '').replace('+', '')))
                                except ValueError:
                                    return 0
                            return 0
                        return abs(float(row['_变化率_num']))
                    rdf['_abs'] = rdf.apply(_sort_key, axis=1)
                    rdf_sorted = rdf.sort_values(['对比周期', '指标', '_abs'], ascending=[True, True, False])
                    rank_top = []
                    for (period, metric), g in rdf_sorted.groupby(['对比周期', '指标']):
                        cur_name, prev_name = period.split('vs')
                        for i, (_, row) in enumerate(g.head(5).iterrows()):
                            delta_val = row['变化差值']
                            direction = '→持平'
                            if isinstance(delta_val, str):
                                if delta_val.startswith('+'):
                                    direction = '↑上涨'
                                elif delta_val.startswith('-'):
                                    direction = '↓下跌'
                            elif isinstance(delta_val, (int, float)):
                                if delta_val > 0:
                                    direction = '↑上涨'
                                elif delta_val < 0:
                                    direction = '↓下跌'
                            rank_top.append({
                                '排名': i + 1,
                                '变化方向': direction,
                                '对比周期': period,
                                '指标': metric,
                                '分组值': row['分组值'],
                                '本期': row.get(cur_name, ''),
                                '上期': row.get(prev_name, ''),
                                '变化差值': delta_val,
                                '变化率%': row['变化率%'],
                            })
                    top_df = pd.DataFrame(rank_top) if rank_top else pd.DataFrame()
                    display_cols = ['对比周期', '指标', '分组值']
                    for c in ['本周', '上周', '本月', '上月']:
                        if c in rdf_sorted.columns:
                            display_cols.append(c)
                    display_cols += ['变化差值', '变化率%']
                    sheets['变化排行_明细'] = rdf_sorted[display_cols].reset_index(drop=True)
                    if rank_top:
                        sheets['变化排行_Top5'] = top_df
                        info(f'\n变化排行已生成（{len(top_df)} 条 Top 记录），包含"本周vs上周"和"本月vs上月"的所有指标涨跌榜')
                        top_preview = top_df.head(10)
                        print_table(top_preview.columns.tolist(), top_preview.values.tolist())
        else:
            warn(f'分组字段不存在: {gb}')

    write_multi_sheet(sheets, output_file)
    success(f'汇总结果已保存: {output_file}')


@stats.command('rank')
@click.option('-i', '--input', 'input_file', required=True, type=click.Path(exists=True),
              help='数据文件')
@click.option('-o', '--output', 'output_file', default='data/stats_rank.xlsx',
              show_default=True)
@click.option('--by', 'rank_by', default='播放量', show_default=True,
              help='排序指标')
@click.option('--top', type=int, default=20, show_default=True, help='Top N')
@click.option('--group-by', default=None, help='分组内排名')
@click.option('--ascending/--descending', default=False, show_default=True)
def cmd_rank(input_file, output_file, rank_by, top, group_by, ascending):
    """生成视频/账号排行榜。"""
    header(f'生成排行榜（按 {rank_by} Top{top}）')

    df = read_data(input_file)
    info(f'数据量: {len(df)} 行')

    metric_col = _resolve_col(df.columns, rank_by)
    if not metric_col:
        error(f'找不到字段: {rank_by}')
        return

    df['_metric'] = _to_num(df[metric_col])
    df = df.sort_values('_metric', ascending=ascending)

    title_col = _resolve_col(df.columns, '视频标题') or _resolve_col(df.columns, '账号ID') or df.columns[0]

    if group_by:
        gb = _resolve_col(df.columns, group_by) or group_by
        if gb in df.columns:
            result = df.groupby(gb).head(top).reset_index(drop=True)
            info(f'按{gb}分组，每组 Top{top}，共 {len(result)} 条')
        else:
            warn(f'分组字段不存在: {gb}')
            result = df.head(top).reset_index(drop=True)
    else:
        result = df.head(top).reset_index(drop=True)

    result.insert(0, '排名', range(1, len(result) + 1))

    display_cols = ['排名', title_col, metric_col]
    avail = [c for c in display_cols if c in result.columns]
    info(f'排行榜预览 Top{min(10, len(result))}:')
    print_table(avail, result[avail].head(10).values.tolist())

    write_data(result, output_file)
    success(f'排行榜已保存: {output_file}')


@stats.command('trend')
@click.option('-i', '--input', 'input_file', required=True, type=click.Path(exists=True),
              help='数据文件')
@click.option('-o', '--output', 'output_file', default='data/stats_trend.xlsx',
              show_default=True)
@click.option('--freq', type=click.Choice(['D', 'W', 'M']), default='D',
              show_default=True, help='粒度：日/周/月')
@click.option('--fill/--no-fill', default=True, show_default=True,
              help='填充缺失日期')
def cmd_trend(input_file, output_file, freq, fill):
    """按日期趋势汇总。"""
    header(f'趋势分析（粒度: {{"D":"日","W":"周","M":"月"}}[freq]）')

    df = read_data(input_file)
    date_col = _resolve_col(df.columns, '发布日期')
    if not date_col:
        error('找不到日期字段')
        return

    df['_date'] = pd.to_datetime(df[date_col], errors='coerce')
    df = df.dropna(subset=['_date']).copy()
    info(f'有效数据: {len(df)} 行，日期范围: {df["_date"].min().date()} ~ {df["_date"].max().date()}')

    metrics = ['播放量', '点赞数', '评论数', '转发数', '收藏数', '涨粉数', '完播率']
    col_map = {}
    for m in metrics:
        c = _resolve_col(df.columns, m)
        if c:
            col_map[m] = c
            df[m] = _to_num(df[c])

    if not col_map:
        error('无可用指标字段')
        return

    if freq == 'W':
        df['_period'] = df['_date'].dt.to_period('W').dt.to_timestamp()
    elif freq == 'M':
        df['_period'] = df['_date'].dt.to_period('M').dt.to_timestamp()
    else:
        df['_period'] = df['_date'].dt.normalize()

    agg_dict = {}
    for m in col_map:
        agg_dict[m] = 'mean' if m == '完播率' else 'sum'
    agg_dict['视频ID' if _resolve_col(df.columns, '视频ID') else df.columns[0]] = 'count'

    trend = df.groupby('_period').agg({
        m: ('mean' if m == '完播率' else 'sum') for m in col_map
    })
    first_key = list(col_map.keys())[0]
    trend['发布数'] = df.groupby('_period')[first_key].count()
    trend = trend.reset_index()
    trend = trend.rename(columns={'_period': '周期'})
    trend = trend.sort_values('周期')

    if fill and freq == 'D':
        trend = trend.set_index('周期').asfreq('D', fill_value=0).reset_index()
        trend = trend.rename(columns={'index': '周期'})

    for m in col_map:
        if m != '完播率':
            trend[f'{m}环比'] = trend[m].pct_change().round(4)

    info(f'趋势 {len(trend)} 行，预览前 7 天:')
    preview_cols = ['周期', '发布数'] + list(col_map.keys())[:4]
    avail = [c for c in preview_cols if c in trend.columns]
    print_table(avail, trend.head(7)[avail].values.tolist())

    write_data(trend, output_file)
    success(f'趋势数据已保存: {output_file}')
