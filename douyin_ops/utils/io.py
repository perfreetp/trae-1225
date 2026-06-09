"""
数据读写工具
==========
提供统一的数据文件读写接口，支持 CSV 和 Excel 格式。
"""
import os
import pandas as pd
from pathlib import Path
from typing import Optional, List, Dict, Any


def ensure_dir(path: str) -> None:
    """确保目录存在，不存在则创建。"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def read_data(file_path: str, sheet_name: Optional[str] = None) -> pd.DataFrame:
    """
    读取数据文件，支持 .xlsx/.xls 和 .csv 格式。

    Args:
        file_path: 文件路径
        sheet_name: Excel 工作表名（仅对 Excel 有效）

    Returns:
        pandas DataFrame
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext in ('.xlsx', '.xls'):
        return pd.read_excel(file_path, sheet_name=sheet_name or 0 if sheet_name is None else sheet_name)
    elif ext == '.csv':
        return pd.read_csv(file_path)
    else:
        raise ValueError(f"不支持的文件格式: {ext}")


def write_data(df: pd.DataFrame, file_path: str, sheet_name: str = 'Sheet1') -> None:
    """
    写入数据文件，根据扩展名自动选择格式。

    Args:
        df: 要写入的数据
        file_path: 输出文件路径
        sheet_name: Excel 工作表名
    """
    ensure_dir(file_path)
    ext = os.path.splitext(file_path)[1].lower()
    if ext in ('.xlsx', '.xls'):
        df.to_excel(file_path, index=False, sheet_name=sheet_name, engine='openpyxl')
    elif ext == '.csv':
        df.to_csv(file_path, index=False, encoding='utf-8-sig')
    else:
        raise ValueError(f"不支持的文件格式: {ext}")


def read_multi_sheet(file_path: str) -> Dict[str, pd.DataFrame]:
    """读取 Excel 多工作表。"""
    ext = os.path.splitext(file_path)[1].lower()
    if ext in ('.xlsx', '.xls'):
        return pd.read_excel(file_path, sheet_name=None)
    elif ext == '.csv':
        return {'Sheet1': pd.read_csv(file_path)}
    else:
        raise ValueError(f"不支持的文件格式: {ext}")


def write_multi_sheet(sheets: Dict[str, pd.DataFrame], file_path: str) -> None:
    """写入多工作表 Excel。"""
    ensure_dir(file_path)
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in ('.xlsx', '.xls'):
        raise ValueError('多工作表仅支持 Excel 格式')
    with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
        for sheet_name, df in sheets.items():
            df.to_excel(writer, index=False, sheet_name=sheet_name)
