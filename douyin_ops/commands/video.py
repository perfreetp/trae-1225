"""
video 命令 - 视频管理
===================
功能：
- scan: 扫描本地视频文件
- list: 生成视频清单
- validate: 校验时长和封面
"""
import os
import glob
import struct
import click
import pandas as pd
from pathlib import Path
from douyin_ops.utils.io import read_data, write_data
from douyin_ops.utils.common import format_duration, md5_file
from douyin_ops.utils.console import info, success, warn, error, header, print_table

VIDEO_EXTS = ['.mp4', '.mov', '.avi', '.mkv', '.flv', '.m4v', '.wmv', '.webm']
IMAGE_EXTS = ['.jpg', '.jpeg', '.png', '.webp', '.bmp']


def _get_mp4_duration(filepath: str) -> float:
    """尝试读取 MP4 时长（秒）。基于 moov/mvhd 解析。"""
    try:
        with open(filepath, 'rb') as f:
            data = f.read(1024 * 1024)
        idx = data.find(b'mvhd')
        if idx < 0:
            return 0.0
        idx += 4
        version = data[idx]
        idx += 4
        if version == 1:
            idx += 8 + 8
            timescale = struct.unpack('>I', data[idx:idx + 4])[0]
            duration = struct.unpack('>Q', data[idx + 4:idx + 12])[0]
        else:
            idx += 4 + 4
            timescale = struct.unpack('>I', data[idx:idx + 4])[0]
            duration = struct.unpack('>I', data[idx + 4:idx + 8])[0]
        return duration / timescale if timescale > 0 else 0.0
    except Exception:
        return 0.0


def _guess_cover(video_path: str) -> str:
    """查找同名封面图片。"""
    base = os.path.splitext(video_path)[0]
    for ext in IMAGE_EXTS:
        for case_ext in [ext, ext.upper(), ext.title()]:
            p = base + case_ext
            if os.path.exists(p):
                return p
    dir_ = os.path.dirname(video_path)
    name = os.path.basename(base)
    for ext in IMAGE_EXTS:
        for case_ext in [ext, ext.upper(), ext.title()]:
            p = os.path.join(dir_, 'covers', name + case_ext)
            if os.path.exists(p):
                return p
    return ''


@click.group()
def video():
    """视频管理：扫描、清单生成、时长封面校验。"""
    pass


@video.command('scan')
@click.option('-d', '--dir', 'scan_dir', required=True, type=click.Path(exists=True),
              help='扫描目录')
@click.option('-r/-R', '--recursive/--no-recursive', default=True, show_default=True,
              help='递归扫描子目录')
@click.option('-o', '--output', 'output_file', default='data/video_list.xlsx',
              show_default=True, help='输出清单文件')
def cmd_scan(scan_dir, recursive, output_file):
    """扫描本地视频文件生成清单（支持 .MP4/.MOV 等大写扩展名）。"""
    header('扫描视频文件')
    info(f'扫描目录: {scan_dir}（递归={recursive}）')

    pattern = '**/*' if recursive else '*'
    files = []
    for ext in VIDEO_EXTS:
        for case_ext in [ext, ext.upper(), ext.title()]:
            matched = glob.glob(os.path.join(scan_dir, pattern + case_ext), recursive=recursive)
            files.extend(matched)
    files = sorted(set(files))

    if not files:
        warn('未找到任何视频文件')
        info(f'尝试匹配的扩展名: {sorted(set(e + "/" + e.upper() for e in VIDEO_EXTS))}')
        return

    info(f'找到 {len(files)} 个视频，处理中...')
    rows = []
    with click.progressbar(files, label='扫描进度') as bar:
        for fp in bar:
            try:
                size = os.path.getsize(fp)
                dur = _get_mp4_duration(fp)
                cover = _guess_cover(fp)
                md5 = md5_file(fp)
                stat = os.stat(fp)
                rows.append({
                    '文件名': os.path.basename(fp),
                    '完整路径': fp,
                    '文件大小(MB)': round(size / 1024 / 1024, 2),
                    '时长(秒)': round(dur, 1),
                    '时长': format_duration(dur) if dur > 0 else '',
                    '封面': cover,
                    'MD5': md5,
                    '修改时间': pd.Timestamp(stat.st_mtime, unit='s').strftime('%Y-%m-%d %H:%M'),
                    '扩展名': os.path.splitext(fp)[1].lower(),
                })
            except Exception as e:
                warn(f'处理失败 {fp}: {e}')

    df = pd.DataFrame(rows)
    write_data(df, output_file)
    success(f'扫描完成，清单已保存: {output_file}（{len(df)} 条）')


@video.command('list')
@click.option('-i', '--input', 'input_file', required=True, type=click.Path(exists=True),
              help='视频清单文件')
@click.option('-o', '--output', 'output_file', default=None,
              help='输出整理后清单')
@click.option('--add-columns', multiple=True, help='追加列（用于后续填充），可多次指定')
@click.option('--sort-by', default='修改时间', show_default=True,
              help='排序字段')
@click.option('--desc/--asc', default=True, show_default=True, help='降序/升序')
def cmd_list(input_file, output_file, add_columns, sort_by, desc):
    """整理视频清单，添加元数据列。"""
    header('生成视频清单')
    output_file = output_file or input_file

    df = read_data(input_file)
    info(f'现有 {len(df)} 条视频')

    default_cols = ['标题', '口播词', '话题', '商品名', '关联账号', '状态', '备注']
    for col in list(add_columns) + default_cols:
        if col not in df.columns:
            df[col] = ''

    if sort_by in df.columns:
        df = df.sort_values(sort_by, ascending=not desc)

    write_data(df, output_file)
    success(f'清单已保存: {output_file}')

    preview = df[['文件名', '时长', '文件大小(MB)']].head(10)
    info('预览前 10 条:')
    print_table(preview.columns.tolist(), preview.values.tolist())


@video.command('validate')
@click.option('-i', '--input', 'input_file', required=True, type=click.Path(exists=True),
              help='视频清单文件')
@click.option('-o', '--output', 'output_file', default=None,
              help='校验报告输出路径')
@click.option('--min-dur', default=15, type=float, show_default=True, help='最小时长（秒）')
@click.option('--max-dur', default=300, type=float, show_default=True, help='最大时长（秒）')
@click.option('--require-cover/--no-require-cover', default=True, show_default=True,
              help='是否要求封面')
def cmd_validate(input_file, output_file, min_dur, max_dur, require_cover):
    """校验视频时长和封面。"""
    header(f'校验视频（时长 {min_dur}-{max_dur}s）')

    df = read_data(input_file)
    info(f'待校验: {len(df)} 条')

    issues = []
    dur_col = '时长(秒)' if '时长(秒)' in df.columns else '时长'

    for idx, row in df.iterrows():
        name = row.get('文件名', f'第{idx + 1}行')
        dur_val = row.get(dur_col, 0) if dur_col in df.columns else 0
        try:
            dur = float(dur_val) if dur_val else 0
        except (ValueError, TypeError):
            dur = 0

        if dur <= 0:
            issues.append([name, '时长', '无法识别时长', '需重新扫描或手动填写'])
        elif dur < min_dur:
            issues.append([name, '时长', f'过短 ({dur}s)', f'需 ≥{min_dur}s'])
        elif dur > max_dur:
            issues.append([name, '时长', f'过长 ({dur}s)', f'需 ≤{max_dur}s'])

        if require_cover:
            cover = str(row.get('封面', '')).strip()
            if not cover or not os.path.exists(cover):
                issues.append([name, '封面', '缺失或不存在', '需准备封面图片'])

    if issues:
        warn(f'发现 {len(issues)} 个问题:')
        print_table(['视频', '类型', '问题', '建议'], issues[:30])
        if len(issues) > 30:
            warn(f'... 还有 {len(issues) - 30} 项')
    else:
        success('全部校验通过！')

    info(f'统计: 时长通过 {sum(1 for i in issues if i[1] != "时长")} 条略')
    dur_ok = 0
    for _, row in df.iterrows():
        try:
            d = float(row.get(dur_col, 0) or 0)
            if min_dur <= d <= max_dur:
                dur_ok += 1
        except (ValueError, TypeError):
            pass
    info(f'时长合规: {dur_ok}/{len(df)}')

    if output_file and issues:
        rep = pd.DataFrame(issues, columns=['视频', '类型', '问题', '建议'])
        write_data(rep, output_file)
        success(f'校验报告已保存: {output_file}')
