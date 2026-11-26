"""Charter API:首次登录问卷向导的后端。

- GET  /api/charter           查询当前 charter(DB 有 → has_charter=true;无 → false 回退文件)
- POST /api/charter/generate  问卷答案 → LLM 生成 charter 文本(不落库,前端预览后再 PUT)
- PUT  /api/charter           保存 charter 文本(可选带 nickname)到 DB(热生效)

复用 settings.py 的 SettingsRepo 与 qa.py 的 LLMClientDep 模式。

问卷 2026-11-26 事实化(v40,主人拍板):旧问卷全是偏好题(「想收什么」「什么算好笔记」),
用户没法回答;新问卷只问事实(昵称/年龄段/性别/学历/当前状态/为什么用),
偏好由 LLM 从事实推导。昵称是唯一要结构化消费的(问答打招呼),随 PUT 落库。
"""

from __future__ import annotations

from typing import Annotated

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
    has_charter: bool  # DB 里有问卷生成的 charter → true;没有 → false(回退内置默认章程)
    charter: str | None  # 有则返回全文,无则 null
    nickname: str | None = None  # 用户昵称(问答打招呼用;null = 未填)


class CharterAnswers(BaseModel):
    """问卷答案(2026-11-26 事实化):只问事实,不问偏好——偏好由 LLM 从事实推导。

    全部可空(没有「必答」):每题不答 = LLM 按通用情况推导。
    """

    nickname: str = Field(default="", max_length=30)  # 怎么称呼你(问答时 AI 用它打招呼)
    age_band: str = Field(default="", max_length=20)  # 年龄段:<18/18-24/25-30/31-40/40+
    gender: str = Field(default="", max_length=10)  # 性别:男/女/不说
    education: str = Field(default="", max_length=20)  # 学历:高中及以下/本科在读/本科/硕士及以上
    status: str = Field(default="", max_length=20)  # 当前状态:在读学生/在职/自由职业/待业/退休
    why: str = Field(default="", max_length=400)  # 为什么使用 baleen(一句话)

    @field_validator("nickname", "age_band", "gender", "education", "status", "why")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class CharterGenerateResponse(BaseModel):
    charter: str  # LLM 生成的 charter Markdown(未落库)


class CharterUpdate(BaseModel):
    """charter 全文;空串 = 清空(删除该账号的章程,回退内置默认章程)。

    nickname 可选(None = 不动;空串 = 清空;非空 = 更新)——问卷保存时一并带上。
    """

    charter: str = Field(max_length=20000)
    nickname: str | None = Field(default=None, max_length=30)

    @field_validator("nickname")
    @classmethod
    def _strip_nickname(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value


# ── 端点 ──


@router.get("", response_model=CharterStatus)
async def get_charter(session: SessionDep, owner_id: CurrentOwnerDep) -> CharterStatus:
    """查 charter:DB 有 → 返回全文 + has_charter=true;DB 无 → null + false(回退内置默认章程)。"""
    row = await SettingsRepo(session, owner_id).get()
    text = row.charter if row is not None else None
    nickname = (row.nickname if row is not None else None) or None
    return CharterStatus(has_charter=bool(text), charter=text, nickname=nickname)


@router.post("/generate", response_model=CharterGenerateResponse)
async def generate(payload: CharterAnswers, llm: LLMClientDep) -> CharterGenerateResponse:
    """问卷答案 → LLM 生成 charter 文本(不落库,前端预览编辑后再 PUT)。"""
    charter_text = await generate_charter(payload.model_dump(), client=llm)
    return CharterGenerateResponse(charter=charter_text)


@router.put("", response_model=CharterStatus)
async def save_charter(
    payload: CharterUpdate, session: SessionDep, owner_id: CurrentOwnerDep
) -> CharterStatus:
    """保存 charter(可选连 nickname)到该用户的 DB 行(热生效:导入整理与问答打招呼即时用新值)。"""
    await SettingsRepo(session, owner_id).save(charter=payload.charter, nickname=payload.nickname)
    await session.commit()
    text = payload.charter.strip()
    if payload.nickname is not None:
        nickname = payload.nickname.strip() or None
    else:
        row = await SettingsRepo(session, owner_id).get()
        nickname = (row.nickname if row is not None else None) or None
    return CharterStatus(has_charter=bool(text), charter=text or None, nickname=nickname)
