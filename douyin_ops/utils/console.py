"""
控制台输出工具 - 彩色输出和进度提示。
"""
import click
from typing import Any


def info(msg: str) -> None:
    """普通信息输出（蓝色）。"""
    click.echo(click.style(f'[INFO] {msg}', fg='blue'))


def success(msg: str) -> None:
    """成功信息输出（绿色）。"""
    click.echo(click.style(f'[OK] {msg}', fg='green'))


def warn(msg: str) -> None:
    """警告信息输出（黄色）。"""
    click.echo(click.style(f'[WARN] {msg}', fg='yellow'))


def error(msg: str) -> None:
    """错误信息输出（红色）。"""
    click.echo(click.style(f'[ERROR] {msg}', fg='red'), err=True)


def header(msg: str) -> None:
    """标题输出（粗体青色）。"""
    click.echo(click.style(f'\n===== {msg} =====', fg='cyan', bold=True))


def print_table(headers: list, rows: list) -> None:
    """简单表格输出。"""
    if not rows:
        info('(空)')
        return
    col_widths = [len(str(h)) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(cell)))
    fmt = ' | '.join(f'{{:<{w}}}' for w in col_widths)
    sep = '-+-'.join('-' * w for w in col_widths)
    click.echo(fmt.format(*headers))
    click.echo(sep)
    for row in rows:
        click.echo(fmt.format(*[str(c) for c in row]))


def confirm_action(msg: str, default: bool = False) -> bool:
    """确认提示。"""
    return click.confirm(click.style(msg, fg='magenta'), default=default)


def prompt_input(msg: str, default: Any = None, show_default: bool = True) -> str:
    """输入提示。"""
    return click.prompt(click.style(msg, fg='magenta'), default=default, show_default=show_default)
