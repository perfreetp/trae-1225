"""
clean 命令 - 清理工具
===================
功能：
- dedup: 清理重复素材
- purge: 清理过期草稿
- archive: 归档旧文件
"""
import os
import shutil
import click
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from douyin_ops.utils.io import read_data, write_data
from douyin_ops.utils.common import md5_file, parse_date, today
from douyin_ops.utils.console import info, success, warn, error, header, print_table, confirm_action

VIDEO_EXTS = ['.mp4', '.mov', '.avi', '.mkv', '.flv', '.m4v', '.wmv', '.webm']
IMAGE_EXTS = ['.jpg', '.jpeg', '.png', '.webp', '.bmp']


@click.group()
def clean():
    """清理工具：重复素材、过期草稿。"""
    pass


@clean.command('dedup')
@click.option('-d', '--dir', 'scan_dir', required=True, type=click.Path(exists=True),
              help='扫描目录')
@click.option('-t', '--type', 'file_type',
              type=click.Choice(['video', 'image', 'all']), default='all',
              show_default=True, help='文件类型')
@click.option('-m', '--method', default='md5',
              type=click.Choice(['md5', 'name+size', 'size']), show_default=True,
              help='去重方法')
@click.option('--by-name/--by-content', default=False, show_default=True,
              help='按文件名匹配（默认按内容 MD5）')
@click.option('--delete/--dry-run', default=False, show_default=True,
              help='实际删除（默认仅预览）')
@click.option('-o', '--output', 'output_file', default='data/clean_duplicates.xlsx',
              show_default=True, help='重复清单输出')
@click.option('--keep', type=click.Choice(['newest', 'oldest', 'largest', 'shortest-name']),
              default='newest', show_default=True, help='保留策略')
def cmd_dedup(scan_dir, file_type, method, by_name, delete, output_file, keep):
    """扫描并清理重复素材。"""
    header('清理重复素材')
    info(f'目录: {scan_dir}，类型: {file_type}，方法: {"文件名" if by_name else method}，策略: 保留{keep}')

    exts = []
    if file_type in ('video', 'all'):
        exts.extend(VIDEO_EXTS)
    if file_type in ('image', 'all'):
        exts.extend(IMAGE_EXTS)

    files = []
    for root, _, fnames in os.walk(scan_dir):
        for fn in fnames:
            ext = os.path.splitext(fn)[1].lower()
            if ext in exts:
                fp = os.path.join(root, fn)
                try:
                    stat = os.stat(fp)
                    files.append({
                        '路径': fp,
                        '文件名': fn,
                        '大小': stat.st_size,
                        '修改时间': stat.st_mtime,
                    })
                except OSError as e:
                    warn(f'无法访问 {fp}: {e}')

    info(f'扫描到 {len(files)} 个文件')

    if not files:
        return

    df = pd.DataFrame(files)

    if by_name:
        df['_key'] = df['文件名']
    elif method == 'name+size':
        df['_key'] = df['文件名'] + '_' + df['大小'].astype(str)
    elif method == 'size':
        df['_key'] = df['大小'].astype(str)
    else:
        info('计算 MD5...')
        md5s = []
        with click.progressbar(df['路径'].tolist(), label='MD5 进度') as bar:
            for fp in bar:
                try:
                    md5s.append(md5_file(fp))
                except Exception:
                    md5s.append('')
        df['_key'] = md5s
        df = df[df['_key'] != '']

    dup_groups = []
    to_delete = []
    to_keep = []

    for key, g in df.groupby('_key'):
        if len(g) < 2:
            continue
        g = g.copy()
        if keep == 'newest':
            g = g.sort_values('修改时间', ascending=False)
        elif keep == 'oldest':
            g = g.sort_values('修改时间', ascending=True)
        elif keep == 'largest':
            g = g.sort_values('大小', ascending=False)
        else:
            g['name_len'] = g['文件名'].str.len()
            g = g.sort_values('name_len', ascending=True)

        keeper = g.iloc[0]
        to_keep.append(keeper['路径'])
        for _, row in g.iloc[1:].iterrows():
            dup_groups.append({
                '分组键': key[:16] if len(key) > 16 else key,
                '文件数': len(g),
                '保留文件': keeper['文件名'],
                '重复文件': row['文件名'],
                '路径': row['路径'],
                '大小(KB)': round(row['大小'] / 1024, 1),
            })
            to_delete.append(row['路径'])

    info(f'发现 {len(dup_groups)} 个重复文件，涉及 {df["_key"].duplicated().sum()} 组')

    if dup_groups:
        preview = pd.DataFrame(dup_groups[:20])
        print_table(preview.columns.tolist(), preview.values.tolist())
        total_size = sum(os.path.getsize(p) for p in to_delete if os.path.exists(p))
        info(f'可释放空间: {total_size / 1024 / 1024:.1f} MB')

    report_df = pd.DataFrame(dup_groups)
    write_data(report_df, output_file)
    info(f'重复清单: {output_file}')

    if not to_delete:
        success('无重复文件，无需清理')
        return

    if delete:
        if not confirm_action(f'确认删除 {len(to_delete)} 个文件？此操作不可恢复！'):
            warn('已取消删除')
            return
        deleted = 0
        for fp in to_delete:
            try:
                os.remove(fp)
                deleted += 1
            except OSError as e:
                warn(f'删除失败 {fp}: {e}')
        success(f'已删除 {deleted}/{len(to_delete)} 个文件')
    else:
        info('预览模式，未执行删除。使用 --delete 实际清理')


@clean.command('purge')
@click.option('-i', '--input', 'input_file', required=True, type=click.Path(exists=True),
              help='排期/草稿文件（CSV/Excel）')
@click.option('-d', '--dir', 'content_dir', type=click.Path(exists=True),
              help='素材目录（可选，同时删除对应素材）')
@click.option('--days', type=int, default=30, show_default=True,
              help='超过多少天算过期')
@click.option('--status-col', default='状态', show_default=True,
              help='状态列名')
@click.option('--date-col', default='日期', show_default=True,
              help='日期列名')
@click.option('--path-cols', multiple=True,
              default=['完整路径', '封面'],
              help='包含文件路径的列名，可多次指定')
@click.option('--expired-status', multiple=True,
              default=['已发布', '已取消', '草稿'],
              help='视为过期的状态，可多次指定')
@click.option('--delete/--dry-run', default=False, show_default=True)
@click.option('-o', '--output', 'output_file', default='data/clean_purged.xlsx',
              show_default=True)
def cmd_purge(input_file, content_dir, days, status_col, date_col, path_cols,
              expired_status, delete, output_file):
    """清理过期草稿和对应素材。"""
    header(f'清理过期草稿（>{days} 天，状态: {list(expired_status)}）')

    df = read_data(input_file)
    info(f'读取 {len(df)} 条记录')

    cutoff = datetime.now() - timedelta(days=days)
    total = len(df)

    df['_date'] = pd.to_datetime(df[date_col], errors='coerce') if date_col in df.columns else pd.NaT
    any_valid_date = bool(df['_date'].notna().any()) if date_col in df.columns else False

    if date_col in df.columns and any_valid_date:
        old_mask = (df['_date'] < cutoff) | df['_date'].isna()
        info(f'按日期筛选: 早于 {cutoff.strftime("%Y-%m-%d")}（共 {days} 天前） 或日期无效')
    else:
        if date_col in df.columns and not any_valid_date:
            warn(f'日期列 "{date_col}" 存在但全部无法解析为有效日期，将忽略日期条件，仅按状态筛选')
        else:
            warn(f'未找到日期列 "{date_col}"，将忽略日期条件，仅按状态筛选')
        old_mask = pd.Series([True] * total, index=df.index)

    if status_col in df.columns:
        status_mask = df[status_col].astype(str).isin(list(expired_status))
        info(f'按状态筛选: 属于 {list(expired_status)}')
    else:
        warn(f'未找到状态列 "{status_col}"，将忽略状态条件，仅按日期筛选')
        status_mask = pd.Series([True] * total, index=df.index)

    mask = old_mask & status_mask
    expired = df[mask].copy()

    remain = total - len(expired)
    info(f'筛选用时: 日期通过 {int(old_mask.sum())}/{total}，状态通过 {int(status_mask.sum())}/{total}')

    info(f'过期记录: {len(expired)} 条')
    if len(expired) == 0:
        success('无过期数据')
        return

    write_data(expired, output_file)
    info(f'过期清单: {output_file}')
    preview_cols = [c for c in [date_col, status_col] if c in expired.columns]
    if preview_cols:
        print_table(preview_cols, expired[preview_cols].head(15).values.tolist())

    material_files = []
    if content_dir:
        for col in path_cols:
            if col in expired.columns:
                for p in expired[col].dropna().astype(str):
                    if os.path.isabs(p) and os.path.exists(p):
                        material_files.append(p)
                    elif content_dir:
                        full = os.path.join(content_dir, p)
                        if os.path.exists(full):
                            material_files.append(full)
        info(f'关联素材文件: {len(material_files)} 个')

    to_delete = list(set(material_files))
    if not delete:
        info('预览模式，未执行删除。使用 --delete 实际清理')
        if to_delete:
            total_size = sum(os.path.getsize(p) for p in to_delete if os.path.exists(p))
            info(f'关联素材可释放: {total_size / 1024 / 1024:.1f} MB')
        return

    if not confirm_action(f'确认删除 {len(expired)} 条记录 + {len(to_delete)} 个素材文件？'):
        warn('已取消')
        return

    remaining = df[~mask].copy()
    remaining = remaining.drop(columns=['_date'], errors='ignore')
    write_data(remaining, input_file)
    success(f'记录已更新，保留 {len(remaining)} 条')

    deleted = 0
    for fp in to_delete:
        try:
            os.remove(fp)
            deleted += 1
        except OSError as e:
            warn(f'删除失败 {fp}: {e}')
    success(f'素材删除: {deleted}/{len(to_delete)}')


@clean.command('archive')
@click.option('-d', '--dir', 'source_dir', required=True, type=click.Path(exists=True),
              help='待归档目录')
@click.option('-t', '--target', 'target_dir', default='archive',
              show_default=True, help='归档目标目录')
@click.option('--pattern', default='*', show_default=True, help='文件匹配模式')
@click.option('--days', type=int, default=90, show_default=True,
              help='超过多少天的文件归档')
@click.option('--move/--copy', default=True, show_default=True,
              help='移动（默认）或复制')
@click.option('--by-month/--by-day', default=True, show_default=True,
              help='按月份子目录归档')
def cmd_archive(source_dir, target_dir, pattern, days, move, by_month):
    """按日期归档旧文件。"""
    header(f'归档: {source_dir} → {target_dir}（>{days} 天）')

    cutoff = datetime.now() - timedelta(days=days)
    moved = 0
    skipped = 0

    for root, _, fnames in os.walk(source_dir):
        for fn in fnames:
            if pattern != '*' and not fn.endswith(tuple(pattern.replace('*.', '.').split(','))):
                pass
            fp = os.path.join(root, fn)
            try:
                mtime = datetime.fromtimestamp(os.stat(fp).st_mtime)
            except OSError:
                continue
            if mtime >= cutoff:
                skipped += 1
                continue
            sub = mtime.strftime('%Y-%m') if by_month else mtime.strftime('%Y-%m-%d')
            dest_root = os.path.join(target_dir, sub)
            rel = os.path.relpath(root, source_dir)
            if rel and rel != '.':
                dest_root = os.path.join(dest_root, rel)
            os.makedirs(dest_root, exist_ok=True)
            dest = os.path.join(dest_root, fn)
            if os.path.exists(dest):
                base, ext = os.path.splitext(fn)
                dest = os.path.join(dest_root, f'{base}_{mtime.strftime("%H%M%S")}{ext}')
            try:
                if move:
                    shutil.move(fp, dest)
                else:
                    shutil.copy2(fp, dest)
                moved += 1
            except OSError as e:
                warn(f'归档失败 {fp}: {e}')

    info(f'跳过 {skipped} 个未到期文件')
    action = '移动' if move else '复制'
    success(f'已{action} {moved} 个文件至: {target_dir}')
