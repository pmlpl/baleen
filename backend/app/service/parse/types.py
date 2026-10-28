"""G0 解析结果类型与公共常量。

G0 是导入状态机的第一道闸(确定性):文件 → TipTap 兼容 block JSON。
本模块无 IO 副作用之外的任何依赖(纯数据定义),不触碰数据库。
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class ParseStatus(StrEnum):
    """解析结论,语义见 ADR 0002 §2.1。

    - ok: 正常解析出内容(可能有 warnings,但结论可信)
    - no_text_expected: 文件本来就没有文本层(扫描件 PDF / 纯图片),应走 OCR,不是格式错误
    - unsupported_format: 格式认识但不支持(.doc/.xlsx),或不认识的扩展名
    - error: 格式认识、解析时出错(文件损坏/加密等)
    """

    OK = "ok"
    NO_TEXT_EXPECTED = "no_text_expected"
    UNSUPPORTED_FORMAT = "unsupported_format"
    ERROR = "error"


# 扫描件判定阈值(ADR §2.1):单页【原始文字层】非空白字符达到该数才算"有文本页"
PAGE_HAS_TEXT_CHARS = 20
# 有文本页占比低于该值,同样判为扫描件(混合型 PDF:少数页有字、多数页是图)
TEXT_PAGE_RATIO = 0.1


class ParseResult(BaseModel):
    """G0 单个文件的解析产物。"""

    parse_status: ParseStatus
    # TipTap 兼容 block JSON(AGENTS.md §5 决策3),空文档为空 content 数组
    blocks: dict = Field(default_factory=lambda: {"type": "doc", "content": []})
    # 非致命提示(中文),如"已过滤 6 行页眉/页脚"
    warnings: list[str] = Field(default_factory=list)
    # unsupported / no_text_expected / error 时给用户看的中文说明;ok 时为 None
    message: str | None = None
    # 引擎与诊断信息:engine / chars / pages|slides / tables / images / per_page_chars 等
    meta: dict = Field(default_factory=dict)


def dist_version(dist_name: str) -> str:
    """读取已装发行版版本号,用于 meta.engine;失败不致命。"""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(dist_name)
    except PackageNotFoundError:
        return "unknown"
