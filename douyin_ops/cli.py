"""
抖音运营命令行工具 - 主入口
==========================
Usage:
    douyin COMMAND [OPTIONS] [ARGS]...

Commands:
    account   账号管理：导入、分组、检查缺失资料
    video     视频管理：扫描、清单生成、时长封面校验
    plan      发布排期：按日期生成、避开冲突
    copy      文案替换：批量替换口播词、话题、商品名
    comment   评论管理：导出、筛选、回复草稿
    stats     数据汇总：播放、点赞、完播、涨粉
    report    报表生成：日报、周报
    clean     清理工具：重复素材、过期草稿
"""
import os
import sys
import click

from douyin_ops import __version__
from douyin_ops.commands.account import account
from douyin_ops.commands.video import video
from douyin_ops.commands.plan import plan
from douyin_ops.commands.copy import copy
from douyin_ops.commands.comment import comment
from douyin_ops.commands.stats import stats
from douyin_ops.commands.report import report
from douyin_ops.commands.clean import clean
from douyin_ops.utils.console import info, success, header


@click.group(
    context_settings={'help_option_names': ['-h', '--help'], 'max_content_width': 120},
    invoke_without_command=True,
)
@click.version_option(__version__, '-V', '--version', prog_name='douyin')
@click.option('--data-dir', default='data', envvar='DOUYIN_DATA_DIR',
              show_default=True, help='数据目录')
@click.pass_context
def cli(ctx, data_dir):
    """抖音运营命令行工具 - 批量整理账号数据和发布清单。"""
    ctx.ensure_object(dict)
    ctx.obj['data_dir'] = data_dir

    if not os.path.exists(data_dir):
        os.makedirs(data_dir, exist_ok=True)

    if ctx.invoked_subcommand is None:
        click.echo(click.style(
            r"""
  ____                    _        ___                
 |  _ \  ___   _   _  _ __ (_)      / _ \  _ __   ___  
 | | | |/ _ \ | | | || '__|| |     | | | || '_ \ / __| 
 | |_| | (_) || |_| || |   | |     | |_| || |_) |\__ \ 
 |____/ \___/  \__,_||_|   |_|      \___/ | .__/ |___/ 
                                          |_|          
""", fg='cyan', bold=True))
        click.echo(click.style(f'  抖音运营工具 v{__version__}', fg='white', bold=True))
        click.echo('')
        info(f'数据目录: {os.path.abspath(data_dir)}')
        click.echo('')
        click.echo(ctx.get_help())


cli.add_command(account)
cli.add_command(video)
cli.add_command(plan)
cli.add_command(copy)
cli.add_command(comment)
cli.add_command(stats)
cli.add_command(report)
cli.add_command(clean)


def main():
    """入口函数，用于脚本调用。"""
    try:
        cli(standalone_mode=False)
    except click.ClickException as e:
        e.show()
        sys.exit(e.exit_code)
    except click.Abort:
        click.echo(click.style('\n已取消操作', fg='yellow'))
        sys.exit(130)
    except KeyboardInterrupt:
        click.echo(click.style('\n操作中断', fg='yellow'))
        sys.exit(130)
    except Exception as e:
        click.echo(click.style(f'\n[ERROR] 未处理异常: {e}', fg='red', bold=True), err=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
