from setuptools import setup, find_packages

setup(
    name='douyin-ops',
    version='1.0.0',
    description='抖音运营命令行工具 - 批量整理账号数据和发布清单',
    author='Douyin Ops Team',
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        'click>=8.1.0',
        'pandas>=2.0.0',
        'openpyxl>=3.1.0',
        'python-dateutil>=2.8.0',
        'Pillow>=10.0.0',
    ],
    entry_points={
        'console_scripts': [
            'douyin=douyin_ops.cli:cli',
        ],
    },
    python_requires='>=3.9',
)
