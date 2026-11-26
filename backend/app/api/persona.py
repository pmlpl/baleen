"""画像 API(2026-11-26)。

页面语义从「知识库章程」扩成「我的画像」:左栏是**我是谁**(问卷事实 + 据此推导的章程,
可原地编辑),右栏是**我在做什么**(系统从真实数据数出来的统计)。

端点:
- ``GET  /api/persona``          左栏:五项事实 + 昵称 + 章程全文
- ``PUT  /api/persona``          左栏原地编辑:改事实/昵称/章程,None=不动、空串=清空
- ``POST /api/persona/generate`` 问卷答案 → LLM 生成章程(不落库,前端预览后再 PUT)
- ``GET  /api/persona/stats``    右栏:全部统计(纯只读,每次现算,没有缓存陈旧问题)
- ``POST /api/persona/topics``   主题打标:LLM 读未打标笔记标题 → 写回 notes.tags

**为什么统计不落库**:主人拍板先不把画像注入问答 prompt。不注入就没有「摘要多久
重算一次」的问题 —— 页面打开即现算。当前规模(62 篇/2580 块/289 轮回答)毫秒级。
等真要做注入时再加落库摘要与触发策略,那时才需要考虑缓存与陈旧。

``charter`` 这个词仍在内部沿用(导入流水线 G2 判据、worker 都叫 charter):
本批只改对外命名与页面,不动内部契约,流水线零风险。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from app.api.deps import CurrentOwnerDep, SessionDep
from app.repo.notes import NotesRepo
from app.repo.persona import PersonaRepo
from app.repo.settings import SettingsRepo
from app.service.charter.generator import generate_charter
from app.service.llm import LLMClient
from app.service.llm.resolve import resolve_llm_config
from app.service.persona.topics import TopicAnalysisError, analyze_topics

router = APIRouter(prefix="/api/persona", tags=["persona"])


# ── LLM client 依赖(设置页 DB 配置优先;api_key 不继承 .env) ──


async def get_llm_client(session: SessionDep, owner_id: CurrentOwnerDep) -> LLMClient:
    base_url, api_key, model = await resolve_llm_config(session, owner_id)
    return LLMClient(base_url=base_url, api_key=api_key, model=model)


LLMClientDep = Annotated[LLMClient, Depends(get_llm_client)]


# ── 左栏:事实 + 章程 ──


class PersonaFacts(BaseModel):
    """五项问卷事实。全部可空:NULL = 这题没答过(老用户这批数据本来就不存在)。

    只有 nickname 被问答消费(称呼);这五项是「我是谁」的展示事实,不进 prompt。
    """

    nickname: str | None = None
    age_band: str | None = None
    gender: str | None = None
    education: str | None = None
    status: str | None = None
    why: str | None = None


class PersonaOut(PersonaFacts):
    has_charter: bool
    charter: str | None


class PersonaUpdate(BaseModel):
    """左栏原地编辑:None = 不动,空串 = 清空。

    charter 必传(与旧 PUT /api/charter 同口径,空串 = 清空回退内置默认章程);
    其余字段可选 —— 用户只改昵称时不必回传整份章程。
    """

    charter: str = Field(max_length=20000)
    nickname: str | None = Field(default=None, max_length=30)
    age_band: str | None = Field(default=None, max_length=20)
    gender: str | None = Field(default=None, max_length=10)
    education: str | None = Field(default=None, max_length=20)
    status: str | None = Field(default=None, max_length=20)
    why: str | None = Field(default=None, max_length=400)

    @field_validator("nickname", "age_band", "gender", "education", "status", "why")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value


class CharterAnswers(BaseModel):
    """问卷答案:只问事实,偏好由 LLM 从事实推导。全部可空(不答 = LLM 按通用情况推理)。"""

    nickname: str = Field(default="", max_length=30)
    age_band: str = Field(default="", max_length=20)
    gender: str = Field(default="", max_length=10)
    education: str = Field(default="", max_length=20)
    status: str = Field(default="", max_length=20)
    why: str = Field(default="", max_length=400)

    @field_validator("nickname", "age_band", "gender", "education", "status", "why")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class CharterGenerateResponse(BaseModel):
    charter: str


def _to_out(row: object | None) -> PersonaOut:
    """app_settings 行 → 响应。行不存在 = 全新用户,全部 None / has_charter=false。"""
    if row is None:
        return PersonaOut(has_charter=False, charter=None)
    charter_text = getattr(row, "charter", None) or None
    return PersonaOut(
        has_charter=bool(charter_text),
        charter=charter_text,
        nickname=getattr(row, "nickname", None) or None,
        age_band=getattr(row, "age_band", None) or None,
        gender=getattr(row, "gender", None) or None,
        education=getattr(row, "education", None) or None,
        status=getattr(row, "status", None) or None,
        why=getattr(row, "why", None) or None,
    )


@router.get("", response_model=PersonaOut)
async def get_persona(session: SessionDep, owner_id: CurrentOwnerDep) -> PersonaOut:
    """左栏:五项事实 + 昵称 + 章程全文(热读,编辑后立即生效)。"""
    return _to_out(await SettingsRepo(session, owner_id).get())


@router.put("", response_model=PersonaOut)
async def save_persona(
    payload: PersonaUpdate, session: SessionDep, owner_id: CurrentOwnerDep
) -> PersonaOut:
    """左栏原地编辑保存。回读整行 → 未传字段保持原值。"""
    repo = SettingsRepo(session, owner_id)
    await repo.save(
        charter=payload.charter,
        nickname=payload.nickname,
        age_band=payload.age_band,
        gender=payload.gender,
        education=payload.education,
        status=payload.status,
        why=payload.why,
    )
    await session.commit()
    return _to_out(await repo.get())


@router.post("/generate", response_model=CharterGenerateResponse)
async def generate(payload: CharterAnswers, llm: LLMClientDep) -> CharterGenerateResponse:
    """问卷答案 → LLM 生成章程文本(不落库,前端预览编辑后再 PUT)。"""
    charter_text = await generate_charter(payload.model_dump(), client=llm)
    return CharterGenerateResponse(charter=charter_text)


# ── 右栏:统计 ──


class SizeOut(BaseModel):
    notes: int
    chunks: int
    sessions: int
    asks: int


class AnswerMixOut(BaseModel):
    """回答方式**互斥**三分,三项相加 = total。

    kb = 有笔记引用(不论是否也联网);web = 无笔记引用但有网页引用;
    self = 两者都没有。早期版本 kb/web 各自计数导致百分比相加超过 100%,
    现在 web 已排除「也有笔记引用」的那部分。
    """

    kb: int
    web: int
    self_: int = Field(alias="self")
    total: int

    model_config = {"populate_by_name": True}


class CitedNoteOut(BaseModel):
    id: int
    title: str
    hits: int


class DeadNoteOut(BaseModel):
    id: int
    title: str
    quality: str


class DeadNotesOut(BaseModel):
    total: int
    items: list[DeadNoteOut]


class SelfAskedOut(BaseModel):
    """纯自答的问题清单。形状与 dead_notes 一致(total 是**真实总数**,
    不是 len(items) —— items 有 30 条上限,拿 len 当总数页面会永远显示 30)。
    """

    total: int
    items: list[str]


class WeekOut(BaseModel):
    week: str
    notes: int


class TopicOut(BaseModel):
    topic: str
    notes: int


class PersonaStatsOut(BaseModel):
    size: SizeOut
    sources: dict[str, int]  # {"video": 37, "doc": 25}
    quality: dict[str, int]  # {"core": 49, "normal": 11, ...}
    answers: AnswerMixOut
    top_cited: list[CitedNoteOut]
    dead_notes: DeadNotesOut
    self_answered: SelfAskedOut
    activity: list[WeekOut]
    topics: list[TopicOut]  # 空 = 还没打过标,前端显示「分析 N 篇」按钮
    untagged: int  # 还没打标的笔记数


@router.get("/stats", response_model=PersonaStatsOut)
async def get_stats(session: SessionDep, owner_id: CurrentOwnerDep) -> PersonaStatsOut:
    """右栏全部统计。每次现算 —— 不落库就没有陈旧问题(主人拍板暂不注入问答)。"""
    repo = PersonaRepo(session, owner_id)
    answers = await repo.answer_mix()
    dead = await repo.dead_notes()
    self_asked = await repo.self_answered()
    return PersonaStatsOut(
        size=SizeOut(**await repo.scale()),
        sources=await repo.source_mix(),
        quality=await repo.quality_mix(),
        answers=AnswerMixOut(
            kb=answers["kb"],
            web=answers["web"],
            **{"self": answers["self"]},
            total=answers["total"],
        ),
        top_cited=[CitedNoteOut(**r) for r in await repo.top_cited()],
        dead_notes=DeadNotesOut(
            total=dead["total"], items=[DeadNoteOut(**r) for r in dead["items"]]
        ),
        self_answered=SelfAskedOut(total=self_asked["total"], items=self_asked["items"]),
        activity=[WeekOut(**r) for r in await repo.activity_by_week()],
        topics=[TopicOut(**r) for r in await repo.topic_mix()],
        untagged=len(await repo.untagged_titles()),
    )


# ── 主题打标 ──


class AnalyzeTopicsOut(BaseModel):
    tagged: int  # 本次成功打标的笔记数
    remaining: int  # 还剩多少篇没打(0 = 全打完)
    topics: list[TopicOut]  # 打完后的主题分布,前端直接替换,不用再拉 stats


@router.post("/topics", response_model=AnalyzeTopicsOut)
async def analyze_note_topics(
    session: SessionDep,
    owner_id: CurrentOwnerDep,
    llm: LLMClientDep,
    retag: Annotated[bool, Query(description="true = 清空已有标签全部重打")] = False,
) -> AnalyzeTopicsOut:
    """给笔记批量打主题标签,写回 ``notes.tags``。

    增量(默认):只处理 tags 为空的笔记,打过的跳过 —— 标题不变就不重打。
    重打(``?retag=true``):清空全部标签重来。用途是标签集合漂移后收敛
    (早期单阶段打标出现过「AI」「AI与大模型」「AI与智能体」三个近义词并存)。

    幂等:全打完时直接返回剩余 0,不重复烧 token。
    """
    notes_repo = NotesRepo(session, owner_id)
    persona_repo = PersonaRepo(session, owner_id)

    if retag:
        await notes_repo.clear_tags()
        await session.commit()

    pending = await persona_repo.untagged_titles()
    if not pending:
        return AnalyzeTopicsOut(
            tagged=0, remaining=0, topics=[TopicOut(**r) for r in await persona_repo.topic_mix()]
        )

    # 已存在的标签一并交给模型:增量打标时让新笔记落到已有主题上,避免标签越滚越碎
    known = [t["topic"] for t in await persona_repo.topic_mix()]
    try:
        mapping = await analyze_topics(pending, client=llm, known_tags=known)
    except TopicAnalysisError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    for note_id, tags in mapping.items():
        await notes_repo.set_tags(note_id, tags)
    await session.commit()

    return AnalyzeTopicsOut(
        tagged=len(mapping),
        remaining=len(await persona_repo.untagged_titles()),
        topics=[TopicOut(**r) for r in await persona_repo.topic_mix()],
    )
