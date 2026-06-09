"""
copy 命令 - 文案批量替换
=====================
功能：
- replace: 批量替换口播词、话题、商品名
- template: 应用模板生成文案
- merge: 合并话题标签
"""
import os
import re
import json
import click
import pandas as pd
from douyin_ops.utils.io import read_data, write_data
from douyin_ops.utils.console import info, success, warn, error, header, print_table


def _load_replacements(rules_file: str) -> dict:
    """加载替换规则文件（支持 JSON 或 CSV/Excel）。"""
    ext = os.path.splitext(rules_file)[1].lower()
    if ext == '.json':
        with open(rules_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    else:
        df = read_data(rules_file)
        rules = {}
        for _, row in df.iterrows():
            col_field = row.get('字段', row.get('field', ''))
            col_old = row.get('原内容', row.get('old', row.iloc[0] if len(row) > 0 else ''))
            col_new = row.get('新内容', row.get('new', row.iloc[1] if len(row) > 1 else ''))
            if col_field:
                rules.setdefault(col_field, {})[str(col_old)] = str(col_new)
            else:
                for col in ['口播词', '标题', '话题', '商品名', '简介']:
                    rules.setdefault(col, {})[str(col_old)] = str(col_new)
        return rules


@click.group()
def copy():
    """文案替换：批量替换口播词、话题、商品名。"""
    pass


@copy.command('replace')
@click.option('-i', '--input', 'input_file', required=True, type=click.Path(exists=True),
              help='目标清单文件')
@click.option('-r', '--rules', 'rules_file', required=True, type=click.Path(exists=True),
              help='替换规则文件（JSON 或 Excel/CSV）')
@click.option('-o', '--output', 'output_file', default=None,
              help='输出文件（默认覆盖）')
@click.option('--regex/--no-regex', default=False, show_default=True,
              help='使用正则匹配')
@click.option('--case-sensitive/--no-case-sensitive', default=False, show_default=True,
              help='区分大小写')
def cmd_replace(input_file, rules_file, output_file, regex, case_sensitive):
    """按规则批量替换文案字段。"""
    header('批量替换文案')
    output_file = output_file or input_file

    df = read_data(input_file)
    rules = _load_replacements(rules_file)
    info(f'加载替换规则: {sum(len(v) for v in rules.values())} 条，涉及字段: {list(rules.keys())}')

    total_replaced = 0
    flags = 0 if case_sensitive else re.IGNORECASE

    for field, mappings in rules.items():
        if field not in df.columns:
            warn(f'跳过不存在的字段: {field}')
            continue
        count = 0
        for old_val, new_val in mappings.items():
            if regex:
                mask = df[field].astype(str).str.contains(old_val, na=False,
                                                           case=case_sensitive, regex=True)
                count += int(mask.sum())
                df.loc[mask, field] = df.loc[mask, field].astype(str).str.replace(
                    old_val, new_val, case=case_sensitive, regex=True)
            else:
                mask = df[field].astype(str).str.contains(re.escape(old_val), na=False,
                                                           case=case_sensitive, regex=False)
                count += int(mask.sum())
                df.loc[mask, field] = df.loc[mask, field].astype(str).str.replace(
                    old_val, new_val, case=case_sensitive, regex=False)
        info(f'字段"{field}": 替换 {count} 处')
        total_replaced += count

    write_data(df, output_file)
    success(f'替换完成，共 {total_replaced} 处，保存至: {output_file}')


@copy.command('template')
@click.option('-i', '--input', 'input_file', required=True, type=click.Path(exists=True),
              help='视频/排期清单文件')
@click.option('-t', '--template', 'template_str', required=False,
              help='模板字符串，使用 {字段名} 占位符')
@click.option('-f', '--template-file', 'template_file', type=click.Path(exists=True),
              help='从文件读取模板（txt 格式）')
@click.option('--target', 'target_field', default='口播词', show_default=True,
              help='写入目标字段')
@click.option('-o', '--output', 'output_file', default=None,
              help='输出文件（默认覆盖）')
def cmd_template(input_file, template_str, template_file, target_field, output_file):
    """应用模板生成文案（支持 {字段名} 占位符）。"""
    header('应用文案模板')
    output_file = output_file or input_file

    if not template_str and not template_file:
        error('请提供 --template 或 --template-file')
        return
    if template_file:
        with open(template_file, 'r', encoding='utf-8') as f:
            template_str = f.read()

    info(f'模板: {template_str[:80]}...' if len(template_str) > 80 else f'模板: {template_str}')

    df = read_data(input_file)
    cols = df.columns.tolist()
    used_fields = re.findall(r'\{(\w+)\}', template_str)
    missing = [f for f in used_fields if f not in cols]
    if missing:
        warn(f'模板引用的字段不存在: {missing}，将替换为空')

    def render(row):
        result = template_str
        for f in used_fields:
            val = str(row.get(f, '')) if f in cols else ''
            result = result.replace('{' + f + '}', val)
        return result

    df[target_field] = df.apply(render, axis=1)
    filled = df[target_field].astype(str).str.strip().ne('').sum()
    info(f'生成 {filled}/{len(df)} 条有效文案')

    write_data(df, output_file)
    success(f'模板应用完成，保存至: {output_file}')


@copy.command('merge')
@click.option('-i', '--input', 'input_file', required=True, type=click.Path(exists=True),
              help='目标清单文件')
@click.option('--extra-tags', multiple=True, help='追加话题标签，可多次指定')
@click.option('--tag-file', type=click.Path(exists=True),
              help='从文件读取话题（每行一个，或 Excel 单列）')
@click.option('--limit', type=int, default=5, show_default=True,
              help='单条最多保留话题数')
@click.option('--target', 'target_field', default='话题', show_default=True,
              help='目标字段')
@click.option('-o', '--output', 'output_file', default=None,
              help='输出文件')
def cmd_merge(input_file, extra_tags, tag_file, limit, target_field, output_file):
    """合并/去重话题标签，控制数量。"""
    header('合并话题标签')
    output_file = output_file or input_file

    df = read_data(input_file)

    file_tags = []
    if tag_file:
        ext = os.path.splitext(tag_file)[1].lower()
        if ext in ('.xlsx', '.xls', '.csv'):
            tdf = read_data(tag_file)
            file_tags = tdf.iloc[:, 0].dropna().astype(str).tolist()
        else:
            with open(tag_file, 'r', encoding='utf-8') as f:
                file_tags = [l.strip() for l in f if l.strip()]

    all_extra = list(extra_tags) + file_tags
    if all_extra:
        info(f'追加话题 {len(all_extra)} 个: {", ".join(all_extra[:5])}{"..." if len(all_extra) > 5 else ""}')

    def dedup_tags(tags_str):
        tags = re.findall(r'#(\S+)', str(tags_str)) if tags_str else []
        tags.extend([t.lstrip('#') for t in all_extra])
        seen = []
        for t in tags:
            t = t.strip()
            if t and t not in seen:
                seen.append(t)
        seen = seen[:limit]
        return ' '.join('#' + t for t in seen)

    if target_field not in df.columns:
        df[target_field] = ''

    df[target_field] = df[target_field].apply(dedup_tags)
    has_tags = df[target_field].astype(str).str.strip().ne('').sum()
    info(f'有话题的记录: {has_tags}/{len(df)}')

    write_data(df, output_file)
    success(f'话题合并完成，保存至: {output_file}')
