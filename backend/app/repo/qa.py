"""qa_sessions / qa_messages 的数据访问(阶段2)。

只 ``flush()`` 不 commit —— 事务边界由调用方定(upload.py / pipeline.py 同一模式);
一条提问 = 建/找会话 + 存 user 消息 + 存 assistant 消息,是一个原子单元,中途失败整体回滚。
"""

from __future__ import annotations

from sqlalchemy import delete, func, select

from app.models.qa import QaDigest, QaMessage, QaSession
from app.repo.base import Repo


class QaRepo(Repo):
    async def count_sessions(self) -> int:
        """该用户的会话总数(个人中心统计)。"""
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(QaSession)
                .where(QaSession.owner_id == self.owner_id)
            )
            or 0
        )

    async def count_messages(self) -> int:
        """该用户的问答消息总数(user + assistant,个人中心统计)。"""
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(QaMessage)
                .where(QaMessage.owner_id == self.owner_id)
            )
            or 0
        )

    async def create_session(self, title: str) -> QaSession:
        row = QaSession(owner_id=self.owner_id, title=title)
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_sessions(self, *, limit: int = 50) -> tuple[int, list[QaSession]]:
        base = select(QaSession).where(QaSession.owner_id == self.owner_id)
        total = int(
            await self.session.scalar(select(func.count()).select_from(base.subquery())) or 0
        )
        rows = await self.session.scalars(
            base.order_by(QaSession.created_at.desc(), QaSession.id.desc()).limit(limit)
        )
        return total, list(rows)

    async def get_session(self, session_id: int) -> QaSession | None:
        return await self.session.scalar(
            select(QaSession).where(
                QaSession.owner_id == self.owner_id, QaSession.id == session_id
            )
        )

    async def list_messages(self, session_id: int) -> list[QaMessage]:
        rows = await self.session.scalars(
            select(QaMessage)
            .where(QaMessage.owner_id == self.owner_id, QaMessage.session_id == session_id)
            .order_by(QaMessage.id)
        )
        return list(rows)

    async def add_message(
        self,
        session_id: int,
        *,
        role: str,
        content: str,
        llm_content: str | None = None,
        citations: list | None = None,
        model: str | None = None,
        cost_tokens: int = 0,
        cache_hit_tokens: int = 0,
        cache_miss_tokens: int = 0,
        trace: list | None = None,
    ) -> QaMessage:
        row = QaMessage(
            owner_id=self.owner_id,
            session_id=session_id,
            role=role,
            content=content,
            llm_content=llm_content,
            citations=citations if citations is not None else [],
            model=model,
            cost_tokens=cost_tokens,
            cache_hit_tokens=cache_hit_tokens,
            cache_miss_tokens=cache_miss_tokens,
            trace=trace,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def delete_session(self, session_id: int) -> bool:
        """删会话;消息靠 FK ondelete CASCADE 连带删。返回是否真的删了。"""
        result = await self.session.execute(
            delete(QaSession).where(
                QaSession.owner_id == self.owner_id, QaSession.id == session_id
            )
        )
        return bool(result.rowcount)

    # ---- 冻结分段摘要(Tier-1 长会话上下文管理,见 service/qa/digest.py)----

    async def list_digests(self, session_id: int) -> list[QaDigest]:
        """该会话的 digest 段(按 id 升序 = 冻结时间序)。"""
        rows = await self.session.scalars(
            select(QaDigest)
            .where(QaDigest.owner_id == self.owner_id, QaDigest.session_id == session_id)
            .order_by(QaDigest.id)
        )
        return list(rows)

    async def add_digest(
        self,
        session_id: int,
        *,
        start_msg_id: int,
        end_msg_id: int,
        summary: str,
    ) -> QaDigest:
        row = QaDigest(
            owner_id=self.owner_id,
            session_id=session_id,
            start_msg_id=start_msg_id,
            end_msg_id=end_msg_id,
            summary=summary,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_messages_after(self, session_id: int, after_msg_id: int) -> list[QaMessage]:
        """id > after_msg_id 的消息(按 id 升序)——近窗原文(after_msg_id=0 返回全部)。"""
        rows = await self.session.scalars(
            select(QaMessage)
            .where(
                QaMessage.owner_id == self.owner_id,
                QaMessage.session_id == session_id,
                QaMessage.id > after_msg_id,
            )
            .order_by(QaMessage.id)
        )
        return list(rows)

    async def list_messages_in_range(
        self, session_id: int, start_id: int, end_id: int
    ) -> list[QaMessage]:
        """[start_id, end_id] 闭区间的消息(按 id 升序)——worker 读待冻结消息用。"""
        rows = await self.session.scalars(
            select(QaMessage)
            .where(
                QaMessage.owner_id == self.owner_id,
                QaMessage.session_id == session_id,
                QaMessage.id >= start_id,
                QaMessage.id <= end_id,
            )
            .order_by(QaMessage.id)
        )
        return list(rows)

    async def count_window_messages(self, session_id: int, after_msg_id: int) -> int:
        """近窗消息条数(id > after_msg_id)——触发冻结判断用,不传文本。"""
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(QaMessage)
                .where(
                    QaMessage.owner_id == self.owner_id,
                    QaMessage.session_id == session_id,
                    QaMessage.id > after_msg_id,
                )
            )
            or 0
        )
