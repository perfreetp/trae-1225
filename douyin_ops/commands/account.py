"""
account 命令 - 账号管理
===================
功能：
- import: 从文件导入账号列表
- group: 按字段分组账号
- check: 检查账号缺失资料
"""
import os
import click
import pandas as pd
from douyin_ops.utils.io import read_data, write_data, ensure_dir
from douyin_ops.utils.console import info, success, warn, error, header, print_table

REQUIRED_FIELDS = ['账号ID', '昵称', '手机号', '认证状态']
OPTIONAL_FIELDS = ['分组', '简介', '头像', 'MCN', '粉丝数', '创建时间', '备注']


@click.group()
def account():
    """账号管理：导入、分组、检查缺失资料。"""
    pass


@account.command('import')
@click.option('-i', '--input', 'input_file', required=True, type=click.Path(exists=True),
              help='账号源文件（CSV/Excel）')
@click.option('-o', '--output', 'output_file', default='data/accounts.xlsx',
              show_default=True, help='输出账号文件路径')
@click.option('--sheet', default=None, help='Excel 工作表名')
@click.option('--append/--no-append', default=False, show_default=True,
              help='追加到现有账号文件')
def cmd_import(input_file, output_file, sheet, append):
    """从文件导入账号列表。"""
    header('导入账号')
    info(f'读取源文件: {input_file}')

    try:
        df = read_data(input_file, sheet_name=sheet)
    except Exception as e:
        error(f'读取失败: {e}')
        return

    info(f'读取到 {len(df)} 条记录，字段: {list(df.columns)}')

    if append and os.path.exists(output_file):
        info(f'追加模式：合并到已有文件 {output_file}')
        existing = read_data(output_file)
        if '账号ID' in df.columns and '账号ID' in existing.columns:
            before = len(existing)
            merged = pd.concat([existing, df], ignore_index=True)
            merged = merged.drop_duplicates(subset=['账号ID'], keep='last')
            added = len(merged) - before
            df = merged
            success(f'新增 {added} 个账号（去重后）')
        else:
            warn('缺少"账号ID"列，无法去重，直接拼接')
            df = pd.concat([existing, df], ignore_index=True)

    for field in REQUIRED_FIELDS:
        if field not in df.columns:
            df[field] = ''
            warn(f'缺少必填列"{field}"，已自动填充空值')

    for field in OPTIONAL_FIELDS:
        if field not in df.columns:
            df[field] = ''

    ordered_cols = REQUIRED_FIELDS + [f for f in OPTIONAL_FIELDS if f not in REQUIRED_FIELDS]
    other_cols = [c for c in df.columns if c not in ordered_cols]
    df = df[ordered_cols + other_cols]

    write_data(df, output_file)
    success(f'账号文件已保存: {output_file}（共 {len(df)} 条）')


@account.command('group')
@click.option('-i', '--input', 'input_file', required=True, type=click.Path(exists=True),
              help='账号文件路径')
@click.option('-o', '--output', 'output_file', default=None,
              help='输出文件（默认覆盖源文件）')
@click.option('--by', 'group_by', default='分组', show_default=True,
              help='按哪个字段分组（如 分组/MCN/认证状态）')
@click.option('--set-group', 'set_group', multiple=True, type=(str, str),
              help='手动设置账号分组，格式：账号ID=分组名，可多次指定')
@click.option('--auto/--no-auto', default=False, show_default=True,
              help='按粉丝数自动分层分组')
def cmd_group(input_file, output_file, group_by, set_group, auto):
    """按字段分组账号或手动设置分组。"""
    header('账号分组')
    output_file = output_file or input_file

    df = read_data(input_file)
    info(f'读取账号数: {len(df)}')

    for account_id, group_name in set_group:
        mask = df['账号ID'].astype(str) == str(account_id)
        if mask.any():
            df.loc[mask, group_by] = group_name
            success(f'已设置 {account_id} → {group_name}')
        else:
            warn(f'未找到账号ID: {account_id}')

    if auto and '粉丝数' in df.columns:
        info('自动按粉丝数分层分组...')
        fans = pd.to_numeric(df['粉丝数'], errors='coerce')

        def tier(f):
            if pd.isna(f) or f < 10000:
                return '新手号'
            elif f < 100000:
                return '腰部号'
            elif f < 1000000:
                return '达人号'
            else:
                return '头部号'

        df[group_by] = fans.apply(tier)
        success('自动分层完成')

    write_data(df, output_file)

    if group_by in df.columns:
        stats = df[group_by].value_counts(dropna=False).reset_index()
        stats.columns = [group_by, '账号数']
        info('分组统计:')
        print_table(stats.columns.tolist(), stats.values.tolist())
    success(f'分组结果已保存: {output_file}')


@account.command('check')
@click.option('-i', '--input', 'input_file', required=True, type=click.Path(exists=True),
              help='账号文件路径')
@click.option('-o', '--output', 'output_file', default=None,
              help='检查报告输出路径（默认显示到控制台）')
@click.option('--strict/--loose', default=False, show_default=True,
              help='严格模式：将空字符串也视为缺失')
def cmd_check(input_file, output_file, strict):
    """检查账号缺失资料。"""
    header('检查账号资料完整性')

    df = read_data(input_file)
    info(f'账号总数: {len(df)}')

    all_fields = REQUIRED_FIELDS + [f for f in OPTIONAL_FIELDS if f in df.columns]
    all_fields += [c for c in df.columns if c not in all_fields]

    missing_summary = []
    missing_records = []

    for field in all_fields:
        if strict:
            mask = df[field].isna() | (df[field].astype(str).str.strip() == '')
        else:
            mask = df[field].isna()
        count = mask.sum()
        missing_summary.append([field, count, f'{count / len(df) * 100:.1f}%'])

        if field in REQUIRED_FIELDS and count > 0:
            for _, row in df[mask].iterrows():
                aid = row.get('账号ID', row.get('昵称', '未知'))
                missing_records.append([aid, field, '缺失'])

    info('缺失统计:')
    print_table(['字段', '缺失数', '缺失率'], missing_summary)

    if missing_records:
        warn(f'必填字段缺失共 {len(missing_records)} 项:')
        print_table(['账号', '缺失字段', '问题'], missing_records[:20])
        if len(missing_records) > 20:
            warn(f'... 还有 {len(missing_records) - 20} 项')

    def _non_empty(series):
        return series.notna() & (series.astype(str).str.strip() != '')

    complete = df[REQUIRED_FIELDS].apply(_non_empty).all(axis=1) if strict else df[REQUIRED_FIELDS].notna().all(axis=1)
    complete_count = int(complete.sum())
    info(f'资料完整账号: {complete_count}/{len(df)} ({complete_count / len(df) * 100:.1f}%)')

    if output_file:
        report_df = pd.DataFrame(missing_records, columns=['账号', '缺失字段', '问题'])
        write_data(report_df, output_file)
        success(f'检查报告已保存: {output_file}')
