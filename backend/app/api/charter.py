"""Charter API:首次登录问卷向导的后端。

- GET  /api/charter           查询当前 charter(DB 有 → has_charter=true;无 → false 回退文件)
- POST /api/charter/generate  问卷答案 → LLM 生成 charter 文本(不落库,前端预览后再 PUT)
- PUT  /api/charter           保存 charter 文本到 DB(热生效,G2 下次调用即用新章程)

复用 settings.py 的 SettingsRepo 与 qa.py 的 LLMClientDep 模式。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from app.api.deps import CurrentOwnerDep, SessionDep
from app.repo.settings import SettingsRepo
from app.service.charter.generator import generate_charter
from app.service.llm import LLMClient
from app.service.llm.resolve import resolve_llm_config

router = APIRouter(prefix="/api/charter", tags=["charter"])


# ── LLM client 依赖(设置页 DB 配置优先;api_key 不继承 .env) ──


async def get_llm_client(session: SessionDep, owner_id: CurrentOwnerDep) -> LLMClient:
    base_url, api_key, model = await resolve_llm_config(session, owner_id)
    return LLMClient(base_url=base_url, api_key=api_key, model=model)


LLMClientDep = Annotated[LLMClient, Depends(get_llm_client)]


# ── 请求/响应模型 ──


class CharterStatus(BaseModel):
    has_charter: bool  # DB 里有问卷生成的 charter → true;没有 → false(回退 charter.md 文件)
    charter: str | None  # 有则返回全文,无则 null


def _str_list(max_items: int = 12, max_len: int = 60) -> Any:
    """多选字段的校验:字符串列表,截断条数与单条长度。"""

    def check(value: list[str]) -> list[str]:
        return [v.strip()[:max_len] for v in value if isinstance(v, str) and v.strip()][:max_items]

    return check


class CharterAnswers(BaseModel):
    """问卷答案:字段对应向导各步,全部可空(旧版客户端少传字段也兼容)。"""

    direction: str = Field(default="", max_length=200)  # 你是什么方向
    purpose: str = Field(default="", max_length=200)  # 这个库用来做什么
    collect: list[str] = Field(default_factory=list)  # 想收什么(多选)
    reject: list[str] = Field(default_factory=list)  # 不收什么(多选)
    unsure: str = Field(default="collect_low")  # 拿不准怎么办:collect_low/reject/discuss
    extra: str = Field(default="", max_length=1000)  # 补充说明
    # --- 扩展问卷(每步多选 + 自填) ---
    domains: list[str] = Field(default_factory=list)  # 技术领域
    sources: list[str] = Field(default_factory=list)  # 笔记来源偏好
    formats: list[str] = Field(default_factory=list)  # 喜欢的笔记形态
    quality_marks: list[str] = Field(default_factory=list)  # 好笔记的标准

    @field_validator("domains", "sources", "formats", "quality_marks")
    @classmethod
    def _clean_lists(cls, value: list[str]) -> list[str]:
        return _str_list()(value)


class CharterGenerateResponse(BaseModel):
    charter: str  # LLM 生成的 charter Markdown(未落库)


class CharterUpdate(BaseModel):
    """charter 全文;空串 = 清空(删除该账号的章程,回退 charter.md 文件)。"""

    charter: str = Field(max_length=20000)


# ── 端点 ──


@router.get("", response_model=CharterStatus)
async def get_charter(session: SessionDep, owner_id: CurrentOwnerDep) -> CharterStatus:
    """查 charter:DB 有 → 返回全文 + has_charter=true;DB 无 → null + false(回退 charter.md)。"""
    row = await SettingsRepo(session, owner_id).get()
    text = row.charter if row is not None else None
    return CharterStatus(has_charter=bool(text), charter=text)


@router.post("/generate", response_model=CharterGenerateResponse)
async def generate(payload: CharterAnswers, llm: LLMClientDep) -> CharterGenerateResponse:
    """问卷答案 → LLM 生成 charter 文本(不落库,前端预览编辑后再 PUT)。"""
    charter_text = await generate_charter(payload.model_dump(), client=llm)
    return CharterGenerateResponse(charter=charter_text)


@router.put("", response_model=CharterStatus)
async def save_charter(
    payload: CharterUpdate, session: SessionDep, owner_id: CurrentOwnerDep
) -> CharterStatus:
    """保存 charter 到该用户的 DB 行(热生效:G2 下次调用即从 DB 读到新章程)。"""
    await SettingsRepo(session, owner_id).save(charter=payload.charter)
    await session.commit()
    text = payload.charter.strip()
    return CharterStatus(has_charter=bool(text), charter=text or None)
