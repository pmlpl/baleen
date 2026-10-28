"""G0 解析闸门(纯函数层):文件 → TipTap 兼容 block JSON + parse_status。

选型与规则见 docs/adr/0002-parsing-stack.md;不碰数据库、不碰存储目录,
落库由后续导入状态机任务接入。
"""

from .router import parse_file
from .types import ParseResult, ParseStatus

__all__ = ["ParseResult", "ParseStatus", "parse_file"]
