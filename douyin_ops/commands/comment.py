"""
comment 命令 - 评论管理
=====================
功能：
- export: 导出评论
- filter: 按关键词筛选
- reply: 生成回复草稿
"""
import os
import re
import json
import random
import click
import pandas as pd
from douyin_ops.utils.io import read_data, write_data, write_multi_sheet
from douyin_ops.utils.console import info, success, warn, error, header, print_table

DEFAULT_REPLY_TEMPLATES = {
    '感谢': ['感谢支持~', '谢谢喜欢！', '感谢关注❤️'],
    '疑问': ['具体可以看我们主页介绍哦', '私信你啦，查收一下~', '可以留言我们帮你解答'],
    '负面': ['抱歉给你带来不好的体验，我们会改进的', '感谢反馈，我们已记录'],
    '通用': ['感谢评论~', '收到！', '❤️❤️❤️', '记得关注不迷路哦'],
}


@click.group()
def comment():
    """评论管理：导出、筛选、回复草稿。"""
    pass


@comment.command('export')
@click.option('-i', '--input', 'input_file', required=True, type=click.Path(exists=True),
              help='评论源文件（CSV/Excel）')
@click.option('-o', '--output', 'output_file', default='data/comments_export.xlsx',
              show_default=True, help='输出文件')
@click.option('--sheet', default=None, help='源工作表名')
@click.option('--video-col', default='视频ID', show_default=True, help='视频标识列名')
@click.option('--content-col', default='评论内容', show_default=True, help='评论内容列名')
@click.option('--user-col', default='用户昵称', show_default=True, help='用户列名')
@click.option('--time-col', default='评论时间', show_default=True, help='时间列名')
def cmd_export(input_file, output_file, sheet, video_col, content_col, user_col, time_col):
    """标准化导出评论数据。"""
    header('导出评论')

    df = read_data(input_file, sheet_name=sheet)
    info(f'原始 {len(df)} 条评论')

    rename_map = {}
    for src, dst in [(video_col, '视频ID'), (content_col, '评论内容'),
                     (user_col, '用户昵称'), (time_col, '评论时间')]:
        if src in df.columns and src != dst:
            rename_map[src] = dst
    if rename_map:
        df = df.rename(columns=rename_map)

    for col in ['视频ID', '评论内容', '用户昵称', '评论时间']:
        if col not in df.columns:
            df[col] = ''

    df['评论ID'] = range(1, len(df) + 1)
    df['回复状态'] = '待回复'
    df['回复内容'] = ''
    df['情感倾向'] = ''
    df['标签'] = ''

    cols_order = ['评论ID', '视频ID', '用户昵称', '评论内容', '评论时间',
                  '情感倾向', '标签', '回复状态', '回复内容']
    other = [c for c in df.columns if c not in cols_order]
    df = df[cols_order + other]

    stats = []
    if '视频ID' in df.columns:
        vc = df['视频ID'].value_counts().head(10).reset_index()
        vc.columns = ['视频ID', '评论数']
        info('评论数 Top10 视频:')
        print_table(vc.columns.tolist(), vc.values.tolist())

    write_data(df, output_file)
    success(f'评论已导出: {output_file}（{len(df)} 条）')


@comment.command('filter')
@click.option('-i', '--input', 'input_file', required=True, type=click.Path(exists=True),
              help='评论文件')
@click.option('-o', '--output', 'output_file', default=None,
              help='输出文件（默认按关键词分表保存）')
@click.option('-k', '--keywords', multiple=True, help='关键词，可多次指定')
@click.option('--keyword-file', type=click.Path(exists=True),
              help='关键词文件（每行一个）')
@click.option('--exclude', multiple=True, help='排除关键词')
@click.option('--field', default='评论内容', show_default=True,
              help='搜索字段')
@click.option('--case-sensitive/--no-case-sensitive', default=False, show_default=True)
def cmd_filter(input_file, output_file, keywords, keyword_file, exclude, field, case_sensitive):
    """按关键词筛选评论。"""
    header('筛选评论')

    all_kw = list(keywords)
    if keyword_file:
        with open(keyword_file, 'r', encoding='utf-8') as f:
            all_kw.extend([l.strip() for l in f if l.strip()])

    if not all_kw:
        error('请通过 --keywords 或 --keyword-file 提供关键词')
        return

    info(f'筛选关键词: {", ".join(all_kw)}（排除: {", ".join(exclude) if exclude else "无"}）')

    df = read_data(input_file)
    info(f'共 {len(df)} 条评论')

    sheets = {}
    flags = 0 if case_sensitive else re.IGNORECASE
    content = df[field].astype(str)

    for kw in all_kw:
        mask = content.str.contains(kw, na=False, regex=False, case=case_sensitive)
        for ex in exclude:
            mask &= ~content.str.contains(ex, na=False, regex=False, case=case_sensitive)
        subset = df[mask].copy()
        if len(subset) > 0:
            sheet_name = re.sub(r'[\\/:*?"<>|]', '_', kw)[:28]
            sheets[sheet_name] = subset
            info(f'  "{kw}": {len(subset)} 条')
        else:
            warn(f'  "{kw}": 无匹配')

    total = sum(len(v) for v in sheets.values())
    info(f'合计匹配: {total} 条')

    if not sheets:
        warn('无任何匹配结果')
        return

    if output_file:
        write_multi_sheet(sheets, output_file)
        success(f'筛选结果已保存: {output_file}（{len(sheets)} 个工作表）')
    else:
        for name, sdf in sheets.items():
            fp = f'data/comments_{name}.xlsx'
            write_data(sdf, fp)
            success(f'  保存: {fp}')


@comment.command('reply')
@click.option('-i', '--input', 'input_file', required=True, type=click.Path(exists=True),
              help='评论文件')
@click.option('-o', '--output', 'output_file', default=None,
              help='输出文件')
@click.option('--templates', type=click.Path(exists=True),
              help='回复模板 JSON 文件')
@click.option('--sentiment/--no-sentiment', default=True, show_default=True,
              help='简单情感分类')
@click.option('--tag-field', default='标签', show_default=True,
              help='按该字段选择模板')
def cmd_reply(input_file, output_file, templates, sentiment, tag_field):
    """生成回复草稿。"""
    header('生成回复草稿')
    output_file = output_file or input_file

    tpls = DEFAULT_REPLY_TEMPLATES.copy()
    if templates:
        with open(templates, 'r', encoding='utf-8') as f:
            loaded = json.load(f)
            tpls.update(loaded)

    df = read_data(input_file)
    info(f'共 {len(df)} 条评论')

    neg_words = ['差', '垃圾', '烂', '垃圾', '不好', '失望', '假', '骗', '坑', '退款', '投诉']
    q_words = ['?', '？', '怎么', '如何', '多少', '多少钱', '哪里', '什么', '吗', '呢']
    pos_words = ['好', '赞', '喜欢', '棒', '爱', '支持', '感谢', '顶', '666', '牛']

    def classify(text):
        text = str(text)
        if any(w in text for w in neg_words):
            return '负面'
        if any(w in text for w in q_words):
            return '疑问'
        if any(w in text for w in pos_words):
            return '感谢'
        return '通用'

    def pick_reply(row):
        content = str(row.get('评论内容', ''))
        tag = str(row.get(tag_field, '')) if tag_field in row else ''
        cat = classify(content) if sentiment else '通用'
        if tag and tag in tpls:
            pool = tpls[tag]
        elif cat in tpls:
            pool = tpls[cat]
        else:
            pool = tpls.get('通用', ['感谢评论~'])
        return random.choice(pool) if pool else ''

    if sentiment and '情感倾向' in df.columns:
        df['情感倾向'] = df['评论内容'].apply(classify)
        sent_stats = df['情感倾向'].value_counts().reset_index()
        sent_stats.columns = ['情感', '数量']
        info('情感分布:')
        print_table(sent_stats.columns.tolist(), sent_stats.values.tolist())

    df['回复内容'] = df.apply(pick_reply, axis=1)
    df['回复状态'] = df['回复状态'].replace('', '草稿')

    write_data(df, output_file)
    success(f'回复草稿已生成: {output_file}')
