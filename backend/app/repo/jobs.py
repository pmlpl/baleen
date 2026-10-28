"""jobs 仓储:导入任务(ADR 0003 §2.6)。dedupe_key 幂等,claim 做乐观锁。"""

from __future__ import annotations

from sqlalchemy import func, select, update

from app.models import Job

from .base import Repo


class JobsRepo(Repo):
    async def get(self, job_id: int) -> Job | None:
        return await self.session.scalar(
            select(Job).where(Job.id == job_id, Job.owner_id == self.owner_id)
        )

    async def get_by_dedupe_key(self, dedupe_key: str) -> Job | None:
        return await self.session.scalar(
            select(Job).where(Job.owner_id == self.owner_id, Job.dedupe_key == dedupe_key)
        )

    async def create_or_get(
        self, *, dedupe_key: str, payload: dict | None = None, kind: str = "import"
    ) -> tuple[Job, bool]:
        existing = await self.get_by_dedupe_key(dedupe_key)
        if existing is not None:
            return existing, False
        job = Job(
            owner_id=self.owner_id,
            kind=kind,
            payload=payload or {},
            dedupe_key=dedupe_key,
            status="queued",
        )
        self.session.add(job)
        await self.session.flush()
        return job, True

    async def claim(self, job_id: int, worker: str) -> bool:
        """queued/failed → running;成功返回 True(别人已抢到则 False)。"""
        result = await self.session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status.in_(("queued", "failed")))
            .values(
                status="running",
                locked_by=worker,
                locked_at=func.now(),
                attempts=Job.attempts + 1,
                updated_at=func.now(),
            )
        )
        return bool(result.rowcount)

    async def update_status(
        self,
        job_id: int,
        status: str,
        *,
        last_error: str | None = None,
        payload: dict | None = None,
    ) -> None:
        values: dict = {"status": status, "updated_at": func.now()}
        if last_error is not None:
            values["last_error"] = last_error
        elif status == "done":
            values["last_error"] = None  # 成功即清掉上一次失败的错误
        if payload is not None:
            values["payload"] = payload
        if status in ("queued", "done", "failed", "dead"):
            values["locked_by"] = None
            values["locked_at"] = None
        await self.session.execute(update(Job).where(Job.id == job_id).values(**values))
