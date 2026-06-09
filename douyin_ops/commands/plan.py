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
@click.option('--precheck/--no-precheck', default=False, show_default=True,
              help='生成前先跑容量预检（账号每日上限+历史占用+素材），预检失败仍会继续生成')
@click.option('--precheck-only', is_flag=True, default=False, show_default=True,
              help='只跑预检+输出诊断报告，不生成任何排期明细')
@click.option('--explain/--no-explain', default=True, show_default=True,
              help='输出可解释冲突报告（账号/时间/视频的调整明细）')
def cmd_generate(start_date, end_date, accounts_file, videos_file, output_file,
                 freq, slots, per_day, shuffle, min_gap, allow_reuse, step_minutes,
                 existing_files, rules_file, global_max_per_day,
                 precheck, precheck_only, explain):
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
      "*":    { "max_per_day": 1 }
    }
    或 Excel 表头: 账号ID, max_per_day, allowed_weekdays, blacklist_dates（多值用逗号，留空=用默认规则）
    """
    header('生成发布排期（自动避冲突 + 运营规则 + 预检诊断）')

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

    conflict_time_log = []
    conflict_account_log = []
    conflict_video_log = []

    def _rule(acc):
        if acc and acc in rules:
            r = rules[acc].copy()
        else:
            r = rules.get('*', {}).copy()
        r.setdefault('max_per_day', 999)
        r.setdefault('allowed_weekdays', list(range(1, 8)))
        r.setdefault('blacklist_dates', set())

        aw = r['allowed_weekdays']
        if aw is None or (isinstance(aw, (list, set, tuple)) and len(aw) == 0) or (isinstance(aw, str) and aw.strip() == ''):
            r['allowed_weekdays'] = list(range(1, 8))
        elif isinstance(aw, str):
            r['allowed_weekdays'] = [int(x) for x in aw.split(',') if x.strip()]

        bd = r['blacklist_dates']
        if bd is None or (isinstance(bd, (list, set, tuple)) and len(bd) == 0) or (isinstance(bd, str) and bd.strip() == ''):
            r['blacklist_dates'] = set()
        elif isinstance(bd, (list, set)):
            r['blacklist_dates'] = set(str(x) for x in bd)
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
    avail_vids = 0
    if videos and not allow_reuse:
        avail_vids = len(videos) - sum(1 for v in videos if (v.get('完整路径') or v.get('视频文件') or '') in used_video_paths)

    def _run_precheck():
        """预检：计算每天容量/缺口/素材，返回诊断 dict 和 按日/按账号/卡点排行 DataFrame。"""
        pc_daily_rows = []
        pc_account_rows = []
        total_capacity = 0
        days_short = 0
        capacity_problems = []
        all_acc_pool = accounts or ['(未指定账号)']

        chokepoint_counts = {}

        def _add_choke(acc, cat, detail, loss):
            key = (acc, cat, detail)
            if key not in chokepoint_counts:
                chokepoint_counts[key] = {'账号ID': acc, '卡点类型': cat, '具体说明': detail, '命中天数': 0, '损失容量': 0}
            chokepoint_counts[key]['命中天数'] += 1
            chokepoint_counts[key]['损失容量'] += loss

        for d in dates:
            d_obj = parse_date(d)
            day_cap = 0
            per_acc_info = []
            for acc in all_acc_pool:
                r = _rule(acc)
                max_pd = r['max_per_day']
                hist = account_daily_count.get((acc, d), 0)
                slot_can_take = max(0, max_pd - hist)
                wd = d_obj.isoweekday()
                wd_ok = wd in r['allowed_weekdays']
                bl_ok = d not in r['blacklist_dates']

                if not bl_ok:
                    _add_choke(acc, '黑名单日期', f'{d} 被列入黑名单', max_pd)
                    slot_can_take = 0
                elif not wd_ok:
                    _add_choke(acc, '星期限制', f'周{wd}不在允许列表{str(r["allowed_weekdays"])}', max_pd)
                    slot_can_take = 0
                else:
                    if hist > 0:
                        _add_choke(acc, '历史占用', f'{d} 已占 {hist} 条', min(hist, max_pd))
                    if slot_can_take <= 0 and max_pd > 0 and hist >= max_pd:
                        _add_choke(acc, '每日上限', f'max_per_day={max_pd} 被历史用完', max_pd)

                day_cap += slot_can_take
                if accounts:
                    pc_account_rows.append({
                        '日期': d,
                        '星期': d_obj.isoweekday(),
                        '账号ID': acc,
                        'max_per_day': max_pd,
                        '历史已排': hist,
                        '剩余容量': slot_can_take,
                        '星期允许': '是' if wd_ok else f'否(周{wd})',
                        '黑名单': '是' if (d in r['blacklist_dates']) else '否',
                    })
                    per_acc_info.append(f'{acc}:剩{slot_can_take}')
            day_need = per_day
            gap = day_cap - day_need
            total_capacity += day_cap
            if gap < 0:
                days_short += 1
                capacity_problems.append(
                    f'{d}(周{d_obj.isoweekday()}) 容量{day_cap} < 目标{day_need}，缺口 {-gap}。 账号明细: {", ".join(per_acc_info[:8])}')
            pc_daily_rows.append({
                '日期': d,
                '星期': d_obj.isoweekday(),
                '目标条数': day_need,
                '理论容量': day_cap,
                '缺口': -gap if gap < 0 else 0,
                '状态': 'OK' if gap >= 0 else '不足',
            })

        pc_summary = {
            '总天数': len(dates),
            '总目标条数': total_needed,
            '总理论容量': total_capacity,
            '总缺口': max(0, total_needed - total_capacity),
            '不足天数': days_short,
            '视频可用': avail_vids if videos else '(未提供)',
            '视频缺口': max(0, total_needed - avail_vids) if videos else 0,
        }
        choke_df = pd.DataFrame(list(chokepoint_counts.values())) if chokepoint_counts else pd.DataFrame(
            columns=['账号ID', '卡点类型', '具体说明', '命中天数', '损失容量'])
        if not choke_df.empty:
            choke_df = choke_df.sort_values(['损失容量', '命中天数'], ascending=[False, False]).reset_index(drop=True)
            choke_df.insert(0, '卡点排名', range(1, len(choke_df) + 1))
        return pc_summary, pd.DataFrame(pc_daily_rows), pd.DataFrame(pc_account_rows), capacity_problems, choke_df

    run_pc = precheck or precheck_only
    pc_ok = True
    pc_summary_df = None
    pc_daily_df = None
    pc_account_df = None
    pc_problems = []
    pc_choke_df = None
    decision_flow_log = []

    if run_pc:
        info('\n========= 预检诊断开始 =========')
        pc_summary, pc_daily_df, pc_account_df, pc_problems, pc_choke_df = _run_precheck()
        pc_summary_df = pd.DataFrame(
            list(pc_summary.items()), columns=['项目', '值']
        )
        info('预检总览:')
        print_table(pc_summary_df.columns.tolist(), pc_summary_df.values.tolist())
        info('\n按日容量:')
        preview_daily = pc_daily_df.head(20)
        print_table(preview_daily.columns.tolist(), preview_daily.values.tolist())
        if len(pc_daily_df) > 20:
            info(f'  ... 其余 {len(pc_daily_df) - 20} 天见输出文件"预检_按日容量"表')

        if pc_choke_df is not None and not pc_choke_df.empty:
            info('\n账号卡点排行（按损失容量从大到小）:')
            preview_choke = pc_choke_df.head(15)
            print_table(preview_choke.columns.tolist(), preview_choke.values.tolist())
            if len(pc_choke_df) > 15:
                info(f'  ... 还有 {len(pc_choke_df) - 15} 条，详见输出"预检_账号卡点排行"表')
        elif accounts:
            info('\n账号卡点排行: 无明显卡点（所有账号按规则均有可排容量）')

        if pc_problems:
            pc_ok = False
            error(f'\n预检发现 {len(pc_problems)} 个日期容量不足:')
            for p in pc_problems[:20]:
                error(f'  ⚠️  {p}')
            if len(pc_problems) > 20:
                error(f'  ... 还有 {len(pc_problems) - 20} 项')
        if videos and not allow_reuse and total_needed > avail_vids:
            pc_ok = False
            error(f'\n预检发现视频素材不足: 目标 {total_needed} 条，可用 {avail_vids} 条，缺口 {total_needed - avail_vids} 条')

        if pc_problems or (videos and not allow_reuse and total_needed > avail_vids):
            error('\n预检建议:')
            error('  · 容量不足 → 减少 per-day，或增加账号，或调大 max_per_day，或缩短 min-gap')
            error('  · 某些日期被周/黑名单限制 → 扩大 allowed_weekdays，或从 blacklist_dates 移除，或扩大日期范围')
            error('  · 视频不足 → 增加素材条数，或加 --allow-reuse')
        else:
            success('预检结论: 容量、账号规则、视频素材均达标。')
        info('========= 预检诊断结束 =========\n')

    if precheck_only:
        info('--precheck-only 模式: 仅保存诊断报告，不生成排期明细。')
        sheets = {
            '预检_总览': pc_summary_df,
            '预检_按日容量': pc_daily_df,
        }
        if accounts and pc_account_df is not None and not pc_account_df.empty:
            sheets['预检_按账号容量'] = pc_account_df
        if pc_choke_df is not None and not pc_choke_df.empty:
            sheets['预检_账号卡点排行'] = pc_choke_df
        if pc_problems:
            sheets['预检_容量问题'] = pd.DataFrame([{'问题': p} for p in pc_problems])
        if videos and not allow_reuse and total_needed > avail_vids:
            sheets['预检_视频问题'] = pd.DataFrame([{
                '问题': f'视频素材不足，缺口 {total_needed - avail_vids} 条',
                '目标条数': total_needed,
                '可用视频': avail_vids,
            }])
        write_multi_sheet(sheets, output_file)
        success(f'预检诊断报告已保存: {output_file}')
        return

    if videos and not allow_reuse and total_needed > avail_vids:
        blocked_reasons.append(
            f'视频素材不足：需要 {total_needed} 条，不重复视频仅 {avail_vids} 条（含已占用）')
        error(blocked_reasons[-1])
        error('预检已确认视频不足，已停止生成半成品。将仅保存诊断表（预检+阻塞+调整明细）。')

    if shuffle:
        random.shuffle(videos)

    vid_idx = 0

    def _pick_video(slot_info=None):
        nonlocal vid_idx
        if not videos:
            return {}, False, ''
        if allow_reuse:
            v = videos[vid_idx % len(videos)]
            vid_idx += 1
            return v, False, ''
        orig_vid = videos[vid_idx % len(videos)] if videos else None
        orig_key = orig_vid.get('完整路径') or orig_vid.get('视频文件') or '' if orig_vid else ''
        skipped_reasons = []
        for i in range(len(videos)):
            cand = videos[(vid_idx + i) % len(videos)]
            key = cand.get('完整路径') or cand.get('视频文件') or ''
            if key and key not in used_video_paths:
                vid_idx = (vid_idx + i + 1) % len(videos)
                if i > 0 and orig_vid and slot_info:
                    conflict_video_log.append({
                        '日期': slot_info.get('日期', ''),
                        '时间段': slot_info.get('时间段', ''),
                        '原视频': orig_vid.get('视频文件', '') or orig_key,
                        '跳过原因': f'已在历史排期中使用（被占用 {orig_key}）',
                        '最终采用视频': cand.get('视频文件', '') or key,
                    })
                return cand, False, ''
            else:
                skipped_reasons.append(f'{cand.get("视频文件","") or key}已占用')
        return None, False, '; '.join(skipped_reasons[:3]) if skipped_reasons else '全部视频已占用'

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

    def _resolve_time(d_str, d_obj, candidate, account_id, step, try_accounts=None, slot_info=None):
        base_dt = datetime.strptime(f'{d_str} {candidate}', '%Y-%m-%d %H:%M')
        candidates_accounts = try_accounts or ([account_id] if account_id else [''])
        original_time_str = candidate
        original_account = account_id
        time_shifted = False
        shift_reasons = []

        for attempt in range(COLLISION_MAX_STEPS):
            dt = base_dt + timedelta(minutes=step + attempt * step_minutes)
            cur_time_str = dt.strftime('%H:%M')

            time_conflict_reason = ''
            for sched in scheduled_times:
                if abs((dt - sched['dt']).total_seconds()) < 60:
                    time_conflict_reason = f'同时间冲突({sched["dt"].strftime("%H:%M")}已被占用)'
                    break
            if time_conflict_reason:
                if cur_time_str != original_time_str:
                    time_shifted = True
                shift_reasons.append(time_conflict_reason)
                continue

            for cand_acc in candidates_accounts:
                acc_reason = ''
                if cand_acc:
                    ok, acc_reason = _account_ok_on_date(cand_acc, d_str, d_obj)
                    if not ok:
                        shift_reasons.append(f'账号{cand_acc}: {acc_reason}')
                        continue
                    if cand_acc in account_last_time:
                        last_dt = account_last_time[cand_acc]
                        gap = abs((dt - last_dt).total_seconds()) / 60
                        if gap < min_gap:
                            shift_reasons.append(f'账号{cand_acc}: 同账号冷却仅{gap:.0f}分钟，要求≥{min_gap}')
                            continue

                if cur_time_str != original_time_str and slot_info:
                    conflict_time_log.append({
                        '日期': d_str,
                        '星期': d_obj.isoweekday(),
                        '时间段': slot_info.get('时间段', ''),
                        '原时间': original_time_str,
                        '最终时间': cur_time_str,
                        '偏移分钟': (dt - base_dt).seconds // 60,
                        '偏移原因': '; '.join(list(dict.fromkeys(shift_reasons))[:5]),
                    })
                if cand_acc != original_account and cand_acc and slot_info:
                    conflict_account_log.append({
                        '日期': d_str,
                        '星期': d_obj.isoweekday(),
                        '时间段': slot_info.get('时间段', ''),
                        '计划发布时间': cur_time_str,
                        '原账号': original_account,
                        '最终账号': cand_acc,
                        '调整原因': (acc_reason or '原账号无法通过冷却或规则') +
                                     (f'; 偏移原因: {"; ".join(list(dict.fromkeys(shift_reasons))[:3])}' if shift_reasons else ''),
                    })
                return dt, cand_acc, ''
        return None, '', '多次尝试仍无法满足时间或账号规则'

    def _rotate_accounts(current_idx, pool):
        return [pool[(current_idx + i) % len(pool)] for i in range(len(pool))]

    account_cursor = 0
    should_generate = len(blocked_reasons) == 0

    if should_generate:
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
                slot_info = {'日期': d, '时间段': slot_name}
                orig_vid_idx = vid_idx % len(videos) if videos else 0
                orig_vid_name = videos[orig_vid_idx].get('视频文件', '') if videos else ''

                vid, reused, vid_log = _pick_video(slot_info=slot_info)
                if vid is None and videos and not allow_reuse:
                    reason = f'视频素材耗尽（第 {si + 1} 条无法分配）' + (f'；{vid_log}' if vid_log else '')
                    failed_today.append((slot_name, reason))
                    blocked_reasons.append(f'{d} | 时间段 {slot_name}: {reason}')
                    decision_flow_log.append({
                        '日期': d, '星期': d_obj.isoweekday(), '时间段': slot_name,
                        '计划时间': slot_time, '最终时间': '', '最终账号': '', '最终视频': '',
                        '状态': '失败', '失败原因': reason,
                        '原计划账号': '', '避让原因': f'视频耗尽; {vid_log}',
                        '最终发布时间': '',
                    })
                    continue

                base_acc = accounts[account_cursor % len(accounts)] if accounts else ''
                account_cursor += 1
                try_pool = _rotate_accounts(account_cursor, accounts) if accounts else ['']

                t_before = len(conflict_time_log)
                a_before = len(conflict_account_log)

                final_dt, chosen_acc, err = _resolve_time(
                    d, d_obj, slot_time, base_acc,
                    step=extra_offset + day_offset,
                    try_accounts=try_pool if accounts else [''],
                    slot_info=slot_info)

                if final_dt is None:
                    day_offset += step_minutes
                    final_dt, chosen_acc, err = _resolve_time(
                        d, d_obj, slot_time, base_acc,
                        step=extra_offset + day_offset,
                        try_accounts=try_pool if accounts else [''],
                        slot_info=slot_info)

                if final_dt is None:
                    fail_reason = err or f'无法在时间段 {slot_name} 找到符合规则的时间+账号组合'
                    failed_today.append((slot_name, fail_reason))
                    blocked_reasons.append(f'{d} | 时间段 {slot_name} (想分配账号 {base_acc}): {fail_reason}')
                    account_cursor -= 1

                    acc_dodges = conflict_account_log[a_before:]
                    time_dodges = conflict_time_log[t_before:]
                    evaded = []
                    if time_dodges:
                        for item in time_dodges:
                            evaded.append(f'时间冲突: {item.get("偏移原因", "")}')
                    if acc_dodges:
                        for item in acc_dodges:
                            evaded.append(f'账号: {item.get("调整原因", "")}')
                    decision_flow_log.append({
                        '日期': d, '星期': d_obj.isoweekday(), '时间段': slot_name,
                        '计划时间': slot_time, '最终时间': '', '最终账号': '', '最终视频': '',
                        '状态': '失败', '失败原因': fail_reason,
                        '原计划账号': base_acc,
                        '避让原因': ' | '.join(evaded[:5]) if evaded else ('视频:' + vid_log) if vid_log else '',
                        '最终发布时间': '',
                    })
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

                video_final = vid.get('视频文件', '') if isinstance(vid, dict) else ''

                acc_dodges = conflict_account_log[a_before:]
                time_dodges = conflict_time_log[t_before:]
                evaded = []
                if time_dodges:
                    for item in time_dodges:
                        evaded.append(f'时间 {item.get("原时间","")}→{item.get("最终时间","")} ({item.get("偏移原因","")[:30]})')
                if acc_dodges:
                    for item in acc_dodges:
                        evaded.append(f'账号 {item.get("原账号","")}→{item.get("最终账号","")} ({item.get("调整原因","")[:30]})')
                vd_before = len(conflict_video_log) - 1
                if (videos and video_final and video_final != orig_vid_name and vd_before >= 0):
                    item = conflict_video_log[-1]
                    evaded.append(f'视频 {item.get("原视频","")}→{item.get("最终采用视频","")} ({item.get("跳过原因","")[:30]})')

                decision_flow_log.append({
                    '日期': d, '星期': d_obj.isoweekday(), '时间段': slot_name,
                    '计划时间': slot_time, '最终时间': final_time_str,
                    '原计划账号': base_acc, '最终账号': chosen_acc,
                    '原计划视频': orig_vid_name, '最终视频': video_final,
                    '状态': '成功',
                    '避让原因': ' | '.join(evaded[:6]),
                    '最终发布时间': full_dt_str,
                    '失败原因': '',
                })

                rows.append({
                    '日期': d,
                    '星期': d_obj.strftime('%A'),
                    '时间段': slot_name,
                    '发布时间': full_dt_str,
                    '账号ID': chosen_acc,
                    '视频文件': video_final,
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
    has_rows = bool(rows)
    if has_rows:
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
                        orig_time = df.at[idx, '_dt'].strftime('%H:%M')
                        df.at[idx, '_dt'] = new_dt
                        df.at[idx, '发布时间'] = new_dt.strftime('%Y-%m-%d %H:%M')
                        df.at[idx, '备注'] = (str(df.at[idx, '备注']) + f'; 最终去重偏移+{step_minutes * k}m').strip('; ')
                        if explain:
                            conflict_time_log.append({
                                '日期': df.at[idx, '日期'],
                                '星期': parse_date(df.at[idx, '日期']).isoweekday(),
                                '时间段': df.at[idx, '时间段'],
                                '原时间': orig_time,
                                '最终时间': new_dt.strftime('%H:%M'),
                                '偏移分钟': step_minutes * k,
                                '偏移原因': '最终写入前重复去重',
                            })
                            for dfentry in decision_flow_log:
                                if (dfentry['日期'] == df.at[idx, '日期'] and
                                        dfentry['时间段'] == df.at[idx, '时间段'] and
                                        dfentry['最终时间'] == orig_time):
                                    dfentry['最终时间'] = new_dt.strftime('%H:%M')
                                    dfentry['最终发布时间'] = new_dt.strftime('%Y-%m-%d %H:%M')
                                    dfentry['避让原因'] = (str(dfentry.get('避让原因', '')) + f' | 最终去重+{step_minutes * k}m').strip(' | ')
                        break
        df = df.drop(columns=['_dt'])
        df.insert(0, '序号', range(1, len(df) + 1))
    else:
        df = pd.DataFrame(columns=['序号', '日期', '星期', '时间段', '发布时间', '账号ID'])

    expected = per_day * len(dates)
    actual_count = len(rows) if has_rows else 0
    info(f'生成排期 {actual_count} 条（期望 {expected} 条，缺失 {expected - actual_count} 条）')

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

    sheets = {}

    if has_rows and not blocked_reasons:
        summary = df.groupby('日期').size().reset_index(name='条数')
        info('\n按日分布:')
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

        if videos and not allow_reuse:
            vp_col = df['完整路径'].where(df['完整路径'].astype(str).ne(''), df['视频文件'])
            dup_v = vp_col[vp_col.astype(str).ne('')].duplicated().sum()
            if dup_v.any() if hasattr(dup_v, 'any') else int(dup_v) > 0:
                warn(f'同视频仍被重复使用 {int(dup_v)} 次')
            else:
                success('视频唯一性校验（no-allow-reuse）：通过')

        sheets['排期明细'] = df
        sheets['按日汇总'] = summary
        if accounts_file:
            acc_stats = df.groupby('账号ID').size().reset_index(name='条数') if '账号ID' in df.columns else pd.DataFrame()
            if not acc_stats.empty:
                sheets['按账号汇总'] = acc_stats
    else:
        warn('未写入"排期明细/按日汇总"（存在阻塞问题或未生成），仅输出诊断表供运营复盘。')

    if run_pc and pc_summary_df is not None:
        sheets['预检_总览'] = pc_summary_df
        sheets['预检_按日容量'] = pc_daily_df
        if accounts and pc_account_df is not None and not pc_account_df.empty:
            sheets['预检_按账号容量'] = pc_account_df
        if pc_choke_df is not None and not pc_choke_df.empty:
            sheets['预检_账号卡点排行'] = pc_choke_df
        if pc_problems:
            sheets['预检_容量问题'] = pd.DataFrame([{'问题': p} for p in pc_problems])

    if blocked_reasons:
        sheets['阻塞问题'] = pd.DataFrame([dict(zip(['问题'], [b])) for b in blocked_reasons])

    if explain:
        if conflict_time_log:
            ct_df = pd.DataFrame(conflict_time_log)
            info(f'\n时间避让统计: 共 {len(ct_df)} 次调整')
            sheets['调整_时间避让明细'] = ct_df
            preview = ct_df.head(10)
            print_table(preview.columns.tolist(), preview.values.tolist())
        else:
            info('\n时间避让统计: 无需调整（所有发布时间原封不动）')
        if conflict_account_log:
            ca_df = pd.DataFrame(conflict_account_log)
            info(f'账号避让统计: 共 {len(ca_df)} 次调整')
            sheets['调整_账号避让明细'] = ca_df
            preview = ca_df.head(10)
            print_table(preview.columns.tolist(), preview.values.tolist())
        else:
            info('账号避让统计: 无需调整（所有账号原封不动）')
        if conflict_video_log:
            cv_df = pd.DataFrame(conflict_video_log)
            info(f'视频避让统计: 共 {len(cv_df)} 次调整')
            sheets['调整_视频避让明细'] = cv_df
            preview = cv_df.head(10)
            print_table(preview.columns.tolist(), preview.values.tolist())
        else:
            info('视频避让统计: 无需调整（视频分配一步到位）')

        if decision_flow_log:
            flow_df = pd.DataFrame(decision_flow_log)
            if not flow_df.empty:
                flow_df = flow_df.sort_values(['日期', '计划时间']).reset_index(drop=True)
                flow_df.insert(0, '流水号', range(1, len(flow_df) + 1))
                sheets['排期决策流水'] = flow_df
                info(f'排期决策流水: 共 {len(flow_df)} 条（按日期时间排序）')
                flow_preview_cols = ['流水号', '日期', '时间段', '计划时间', '最终时间', '原计划账号', '最终账号', '状态']
                avail_cols = [c for c in flow_preview_cols if c in flow_df.columns]
                print_table(avail_cols, flow_df[avail_cols].head(12).values.tolist())

    write_multi_sheet(sheets, output_file)
    written = list(sheets.keys())
    msg = f'诊断/排期已保存: {output_file}（共 {len(written)} 个表: {", ".join(written)}）'
    if blocked_reasons:
        warn(msg)
        warn('注意: 由于存在阻塞问题，文件中仅包含诊断表，不含排期明细。请调整规则后重新生成。')
    else:
        success(msg)


def _load_rules(rules_file):
    """从 JSON 或 Excel 加载运营规则。空单元格/NaN 视为未配置，走默认值。"""
    def _is_blank(value):
        if value is None:
            return True
        if isinstance(value, float) and pd.isna(value):
            return True
        s = str(value).strip()
        if s == '':
            return True
        if s.lower() in ('nan', 'none', 'null', 'na', '-'):
            return True
        return False

    ext = os.path.splitext(rules_file)[1].lower()
    if ext == '.json':
        import json
        with open(rules_file, 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    df = read_data(rules_file)
    rules = {}
    for _, r in df.iterrows():
        acc_val = r.get('账号ID') if '账号ID' in df.columns else r.iloc[0]
        acc = '' if _is_blank(acc_val) else str(acc_val).strip()
        if not acc:
            continue
        entry = {}
        if 'max_per_day' in df.columns:
            mpd = r['max_per_day']
            if not _is_blank(mpd):
                try:
                    entry['max_per_day'] = int(float(mpd))
                except (ValueError, TypeError):
                    pass
        if 'allowed_weekdays' in df.columns:
            wd_raw = r['allowed_weekdays']
            if not _is_blank(wd_raw):
                wd = str(wd_raw).strip()
                if wd:
                    digits = [int(x) for x in re.split(r'[,\s]+', wd) if x.isdigit()]
                    if digits:
                        entry['allowed_weekdays'] = digits
        if 'blacklist_dates' in df.columns:
            bd_raw = r['blacklist_dates']
            if not _is_blank(bd_raw):
                bd = str(bd_raw).strip()
                if bd:
                    parts = [x.strip() for x in re.split(r'[,\s]+', bd) if x.strip()]
                    if parts:
                        entry['blacklist_dates'] = set(parts)
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
