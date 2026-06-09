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
              help='每天发布条数（所有账号合计）')
@click.option('--shuffle/--no-shuffle', default=False, show_default=True,
              help='随机分配视频和时间段')
@click.option('--min-gap', type=int, default=MIN_ACCOUNT_GAP_MINUTES, show_default=True,
              help='同账号最小发布间隔（分钟）')
@click.option('--allow-reuse/--no-allow-reuse', default=False, show_default=True,
              help='允许视频重复使用（默认一个视频只排一次）')
@click.option('--step-minutes', type=int, default=COLLISION_STEP_MINUTES, show_default=True,
              help='冲突时自动错开的步长（分钟）')
@click.option('--existing', 'existing_files', multiple=True, type=click.Path(exists=True),
              help='已有排期文件，纳入冲突判断（同账号冷却、同时间、同视频）可多次指定')
@click.option('--rules', 'rules_file', type=click.Path(exists=True),
              help='运营规则文件（JSON/Excel）：按账号设置 max_per_day / allowed_weekdays / blacklist_dates')
@click.option('--global-max-per-day', type=int, default=None,
              help='全账号每天最多发布条数上限（可被规则文件覆盖）')
def cmd_generate(start_date, end_date, accounts_file, videos_file, output_file,
                 freq, slots, per_day, shuffle, min_gap, allow_reuse, step_minutes,
                 existing_files, rules_file, global_max_per_day):
    """按日期生成发布排期，生成时自动避开冲突。

    \b
    自动避冲突策略：
    1. 同一发布时间精确重复 → 自动顺延 step-minutes 分钟
    2. 同账号两次发布间隔 < min-gap 分钟 → 自动顺延
    3. 同一视频重复使用（默认禁用） → 启用 allow-reuse 才允许
    4. 每天条数多于时间段 → 用顺延分钟方式补齐
    5. 已存在排期（--existing）的账号/时间/视频 → 全部加入冲突判断

    \b
    运营规则文件（JSON 示例）:
    {
      "1001": { "max_per_day": 2, "allowed_weekdays": [1,2,3,4,5], "blacklist_dates": ["2026-06-18"] },
      "1002": { "max_per_day": 1 },
      "*":    { "max_per_day": 1, "allowed_weekdays": [1,2,3,4,5,6,7] }
    }
    或 Excel 表头: 账号ID, max_per_day, allowed_weekdays, blacklist_dates（多值用逗号）
    """
    header('生成发布排期（自动避冲突 + 运营规则）')

    if freq == 'weekday':
        dates = date_range(start_date, end_date, include_weekends=False)
    elif freq == 'weekend':
        all_dates = date_range(start_date, end_date)
        dates = [d for d in all_dates if is_weekend(d)]
    else:
        dates = date_range(start_date, end_date)

    info(f'排期范围: {start_date} ~ {end_date}，有效天数: {len(dates)}')
    info(f'避冲突配置: 同账号间隔≥{min_gap}分钟，步长={step_minutes}分钟，视频复用={allow_reuse}')

    rules = {}
    if rules_file:
        rules = _load_rules(rules_file)
        rule_accs = [k for k in rules.keys() if k != '*']
        info(f'加载运营规则: {len(rule_accs)} 个账号 + 1 个默认')
        if '*' in rules:
            info(f'  默认规则: {rules["*"]}')
    if global_max_per_day:
        rules.setdefault('*', {})['max_per_day'] = global_max_per_day
        info(f'全局每天每条账号上限: {global_max_per_day}')

    base_slots = []
    if slots:
        slot_counts = {}
        for s in slots:
            slot_counts[s] = slot_counts.get(s, 0) + 1
        for s, cnt in slot_counts.items():
            if s in DEFAULT_TIME_SLOTS:
                tm = DEFAULT_TIME_SLOTS[s]
                base_slots.append((s, tm))
                for i in range(1, cnt):
                    base_slots.append((f'{s}#{i+1}', tm))
            else:
                base_slots.append((f'自定义{s}', s))
                for i in range(1, cnt):
                    base_slots.append((f'自定义{s}#{i+1}', s))
        info(f'时间段槽位（含重复展开）: {len(base_slots)} 个')
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
            })
        info(f'加载视频素材: {len(videos)} 条')

    scheduled_times = []
    account_last_time = {}
    account_daily_count = {}
    used_video_paths = set()
    rows = []
    warnings = []
    blocked_reasons = []

    def _rule(acc):
        if acc and acc in rules:
            r = rules[acc].copy()
        else:
            r = rules.get('*', {}).copy()
        r.setdefault('max_per_day', 999)
        r.setdefault('allowed_weekdays', list(range(1, 8)))
        r.setdefault('blacklist_dates', set())
        if isinstance(r['allowed_weekdays'], str):
            r['allowed_weekdays'] = [int(x) for x in r['allowed_weekdays'].split(',') if x.strip()]
        if isinstance(r['blacklist_dates'], (list, set)):
            r['blacklist_dates'] = set(str(x) for x in r['blacklist_dates'])
        return r

    for fp in existing_files or []:
        ext_df = read_data(fp)
        info(f'载入已有排期: {fp}（{len(ext_df)} 条）')
        if '发布时间' in ext_df.columns:
            ext_df['_dt'] = pd.to_datetime(ext_df['发布时间'], errors='coerce')
            for _, r in ext_df.dropna(subset=['_dt']).iterrows():
                scheduled_times.append({'dt': r['_dt'].to_pydatetime()})
                acc = str(r.get('账号ID', '')) if '账号ID' in ext_df.columns else ''
                if acc:
                    account_last_time[acc] = r['_dt'].to_pydatetime()
                    d_str = r['_dt'].strftime('%Y-%m-%d')
                    account_daily_count[(acc, d_str)] = account_daily_count.get((acc, d_str), 0) + 1
                vp = str(r.get('完整路径') or r.get('视频文件') or '').strip()
                if vp:
                    used_video_paths.add(vp)
        else:
            warn(f'  缺少"发布时间"列，无法纳入时间/账号冲突判断')

    total_needed = per_day * len(dates)
    if videos and not allow_reuse:
        avail_vids = len(videos) - sum(1 for v in videos if (v.get('完整路径') or v.get('视频文件') or '') in used_video_paths)
        if total_needed > avail_vids:
            blocked_reasons.append(
                f'视频素材不足：需要 {total_needed} 条，不重复视频仅 {avail_vids} 条（含已占用）')
            error(blocked_reasons[-1])
            error('请增加视频素材数量，或启用 --allow-reuse 允许重复使用。')
            return

    if shuffle:
        random.shuffle(videos)

    vid_idx = 0

    def _pick_video():
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
                return cand, False
        return None, False

    def _account_ok_on_date(acc, d_str, d_obj):
        r = _rule(acc)
        if d_str in r['blacklist_dates']:
            return False, '黑名单日期'
        wd = d_obj.isoweekday()
        if wd not in r['allowed_weekdays']:
            return False, f'星期{wd}不在允许发布日'
        cur_cnt = account_daily_count.get((acc, d_str), 0)
        if cur_cnt >= r['max_per_day']:
            return False, f'当日已达上限 {r["max_per_day"]} 条'
        return True, ''

    def _resolve_time(d_str, d_obj, candidate, account_id, step, try_accounts=None):
        """
        尝试找一个可用的 datetime。
        会同时检查：时间精确重复、同账号冷却、账号当日是否还可发、星期/黑名单。
        返回 (dt, 实际用的账号, 错误信息)。dt=None 表示失败。
        """
        base_dt = datetime.strptime(f'{d_str} {candidate}', '%Y-%m-%d %H:%M')
        candidates_accounts = try_accounts or ([account_id] if account_id else [''])

        for attempt in range(COLLISION_MAX_STEPS):
            dt = base_dt + timedelta(minutes=step + attempt * step_minutes)

            time_ok = True
            for sched in scheduled_times:
                if abs((dt - sched['dt']).total_seconds()) < 60:
                    time_ok = False
                    break
            if not time_ok:
                continue

            for cand_acc in candidates_accounts:
                if cand_acc:
                    ok, reason = _account_ok_on_date(cand_acc, d_str, d_obj)
                    if not ok:
                        continue
                    if cand_acc in account_last_time:
                        last_dt = account_last_time[cand_acc]
                        gap = abs((dt - last_dt).total_seconds()) / 60
                        if gap < min_gap:
                            continue
                return dt, cand_acc, ''
        return None, '', '多次尝试仍无法满足时间或账号规则'

    def _rotate_accounts(current_idx, pool):
        return [pool[(current_idx + i) % len(pool)] for i in range(len(pool))]

    account_cursor = 0

    for d in dates:
        d_obj = parse_date(d)
        daily_slots_pool = []
        mult = (per_day + len(base_slots) - 1) // len(base_slots)
        for m in range(mult):
            for slot_name, slot_time in base_slots:
                offset_minutes = m * step_minutes * 3
                name = slot_name if m == 0 and not slot_name.endswith((')', ']', '#')) else f'{slot_name}+{offset_minutes}m'
                daily_slots_pool.append((name, slot_time, offset_minutes))
        if shuffle:
            random.shuffle(daily_slots_pool)
        daily_slots_pool = daily_slots_pool[:per_day]

        failed_today = []
        day_offset = 0

        for si, (slot_name, slot_time, extra_offset) in enumerate(daily_slots_pool):
            vid, reused = _pick_video()
            if vid is None and videos and not allow_reuse:
                reason = f'视频素材耗尽（第 {si + 1} 条无法分配）'
                failed_today.append((slot_name, reason))
                blocked_reasons.append(f'{d} | 时间段 {slot_name}: {reason}')
                continue

            base_acc = accounts[account_cursor % len(accounts)] if accounts else ''
            account_cursor += 1
            try_pool = _rotate_accounts(account_cursor, accounts) if accounts else ['']

            final_dt, chosen_acc, err = _resolve_time(
                d, d_obj, slot_time, base_acc,
                step=extra_offset + day_offset,
                try_accounts=try_pool if accounts else [''])

            if final_dt is None:
                day_offset += step_minutes
                final_dt, chosen_acc, err = _resolve_time(
                    d, d_obj, slot_time, base_acc,
                    step=extra_offset + day_offset,
                    try_accounts=try_pool if accounts else [''])

            if final_dt is None:
                fail_reason = err or f'无法在时间段 {slot_name} 找到符合规则的时间+账号组合'
                failed_today.append((slot_name, fail_reason))
                blocked_reasons.append(f'{d} | 时间段 {slot_name} (想分配账号 {base_acc}): {fail_reason}')
                account_cursor -= 1
                continue

            full_dt_str = final_dt.strftime('%Y-%m-%d %H:%M')
            final_time_str = final_dt.strftime('%H:%M')
            scheduled_times.append({'dt': final_dt})
            if chosen_acc:
                account_last_time[chosen_acc] = final_dt
                account_daily_count[(chosen_acc, d)] = account_daily_count.get((chosen_acc, d), 0) + 1

            vid_path = vid.get('完整路径') or vid.get('视频文件') or '' if isinstance(vid, dict) else ''
            if vid_path and not allow_reuse:
                used_video_paths.add(vid_path)

            rows.append({
                '日期': d,
                '星期': d_obj.strftime('%A'),
                '时间段': slot_name,
                '发布时间': full_dt_str,
                '账号ID': chosen_acc,
                '视频文件': vid.get('视频文件', '') if isinstance(vid, dict) else '',
                '完整路径': vid.get('完整路径', '') if isinstance(vid, dict) else '',
                '标题': vid.get('标题', '') if isinstance(vid, dict) else '',
                '口播词': vid.get('口播词', '') if isinstance(vid, dict) else '',
                '话题': vid.get('话题', '') if isinstance(vid, dict) else '',
                '状态': '待发布',
                '备注': f'原 {slot_time}→{final_time_str}; 账号调整 {base_acc}→{chosen_acc}'
                    if (final_time_str != slot_time or (base_acc != chosen_acc and chosen_acc))
                    else '',
            })

        if failed_today:
            warn(f'{d} 排期失败 {len(failed_today)} 条:')
            for name, reason in failed_today:
                warn(f'  - {name}: {reason}')

    df = None
    if rows:
        df = pd.DataFrame(rows)
        df = df.sort_values(['发布时间']).reset_index(drop=True)
        df['_dt'] = pd.to_datetime(df['发布时间'])
        duped_mask = df['_dt'].duplicated(keep='first')
        if duped_mask.any():
            dup_n = int(duped_mask.sum())
            info(f'发现 {dup_n} 条残余发布时间重复，自动错开 +{step_minutes} 分钟')
            for idx in df.index[duped_mask]:
                for k in range(1, COLLISION_MAX_STEPS + 1):
                    new_dt = df.at[idx, '_dt'] + timedelta(minutes=step_minutes * k)
                    clash = ((df['_dt'] - new_dt).abs() < pd.Timedelta(minutes=1)).any()
                    if not clash:
                        df.at[idx, '_dt'] = new_dt
                        df.at[idx, '发布时间'] = new_dt.strftime('%Y-%m-%d %H:%M')
                        df.at[idx, '备注'] = (str(df.at[idx, '备注']) + f'; 最终去重偏移+{step_minutes * k}m').strip('; ')
                        break
        df = df.drop(columns=['_dt'])
        df.insert(0, '序号', range(1, len(df) + 1))
    else:
        df = pd.DataFrame(columns=['序号', '日期', '星期', '时间段', '发布时间', '账号ID'])

    info(f'生成排期 {len(df)} 条（期望 {per_day * len(dates)} 条，缺失 {per_day * len(dates) - len(df)} 条）')

    if blocked_reasons:
        error(f'\n排期过程中共 {len(blocked_reasons)} 个阻塞问题（按日期+时间段列出）:')
        for b in blocked_reasons[:30]:
            error(f'  ❌ {b}')
        if len(blocked_reasons) > 30:
            error(f'  ... 还有 {len(blocked_reasons) - 30} 条，建议调整规则后重新生成')
        error('\n常见原因与建议:')
        error('  · 账号数 × 每日上限 < 目标 daily 条数 → 减少 per-day 或增加账号/调大 max_per_day')
        error('  · 同账号冷却时间过长 → 减小 --min-gap 或增加时间段槽位')
        error('  · 只允许工作日且范围过小 → 放宽日期范围或取消工作日限制')
        error('  · 视频不足 → 增加素材或 --allow-reuse')

    summary = df.groupby('日期').size().reset_index(name='条数') if not df.empty else pd.DataFrame(columns=['日期', '条数'])
    info('\n按日分布:')
    print_table(summary.columns.tolist(), summary.values.tolist())

    actual_times = pd.to_datetime(df['发布时间']) if not df.empty else pd.Series(dtype='datetime64[ns]')
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

    if videos and not allow_reuse and not df.empty:
        vp_col = df['完整路径'].where(df['完整路径'].astype(str).ne(''), df['视频文件'])
        dup_v = vp_col[vp_col.astype(str).ne('')].duplicated().sum()
        if dup_v.any() if hasattr(dup_v, 'any') else int(dup_v) > 0:
            warn(f'同视频仍被重复使用 {int(dup_v)} 次')
        else:
            success('视频唯一性校验（no-allow-reuse）：通过')

    sheets = {
        '排期明细': df,
        '按日汇总': summary,
    }
    if accounts_file and not df.empty:
        acc_stats = df.groupby('账号ID').size().reset_index(name='条数') if '账号ID' in df.columns else pd.DataFrame()
        if not acc_stats.empty:
            sheets['按账号汇总'] = acc_stats
    if blocked_reasons:
        sheets['阻塞问题'] = pd.DataFrame([dict(zip(['问题'], [b])) for b in blocked_reasons])

    write_multi_sheet(sheets, output_file)
    success(f'排期已保存: {output_file}（已附"阻塞问题"工作表）' if blocked_reasons else f'排期已保存: {output_file}')


def _load_rules(rules_file):
    """从 JSON 或 Excel 加载运营规则。"""
    ext = os.path.splitext(rules_file)[1].lower()
    if ext == '.json':
        import json
        with open(rules_file, 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    df = read_data(rules_file)
    rules = {}
    for _, r in df.iterrows():
        acc = str(r.get('账号ID') or r.iloc[0]).strip()
        if not acc:
            continue
        entry = {}
        if 'max_per_day' in df.columns:
            try:
                entry['max_per_day'] = int(r['max_per_day'])
            except (ValueError, TypeError):
                pass
        if 'allowed_weekdays' in df.columns:
            wd = str(r['allowed_weekdays']).strip()
            if wd:
                entry['allowed_weekdays'] = [int(x) for x in re.split(r'[,\s]+', wd) if x.isdigit()]
        if 'blacklist_dates' in df.columns:
            bd = str(r['blacklist_dates']).strip()
            if bd:
                entry['blacklist_dates'] = set(x.strip() for x in re.split(r'[,\s]+', bd) if x.strip())
        rules[acc] = entry
    return rules


import re


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
