"""
通用工具函数
"""
import os
import hashlib
from datetime import datetime, timedelta
from typing import List, Optional, Tuple


def fmt_date(value, fmt: str = '%Y-%m-%d'):
    """格式化日期。"""
    if isinstance(value, str):
        return datetime.strptime(value, fmt)
    return value.strftime(fmt) if hasattr(value, 'strftime') else value


def today(fmt: str = '%Y-%m-%d') -> str:
    """获取今天日期字符串。"""
    return datetime.now().strftime(fmt)


def parse_date(date_str: str) -> datetime:
    """解析日期字符串，支持多种格式。"""
    formats = [
        '%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d',
        '%Y%m%d',
        '%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S',
    ]
    for fmt in formats:
        try:
            return datetime.strptime(str(date_str), fmt)
        except (ValueError, TypeError):
            continue
    raise ValueError(f"无法解析日期: {date_str}")


def date_range(start_date: str, end_date: str, include_weekends: bool = True) -> List[str]:
    """生成日期范围列表。"""
    start = parse_date(start_date)
    end = parse_date(end_date)
    dates = []
    current = start
    while current <= end:
        if include_weekends or current.weekday() < 5:
            dates.append(current.strftime('%Y-%m-%d'))
        current += timedelta(days=1)
    return dates


def is_weekend(date_str: str) -> bool:
    """判断是否为周末。"""
    d = parse_date(date_str)
    return d.weekday() >= 5


def md5_file(file_path: str) -> str:
    """计算文件 MD5 哈希值。"""
    hash_md5 = hashlib.md5()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b''):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def format_duration(seconds: float) -> str:
    """将秒数格式化为 mm:ss。"""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f'{h:02d}:{m:02d}:{s:02d}'
    return f'{m:02d}:{s:02d}'


def parse_duration(duration_str: str) -> int:
    """解析时长字符串为秒数，支持 mm:ss 或 hh:mm:ss。"""
    parts = str(duration_str).split(':')
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + int(s)
    elif len(parts) == 2:
        m, s = parts
        return int(m) * 60 + int(s)
    else:
        return int(float(duration_str))
