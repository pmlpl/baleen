"""review_runs 仓储:每闸门一行的判定记录(ADR 0003 §2.2)。"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.models import ReviewRun

from .base import Repo


class ReviewRunsRepo(Repo):
    async def get(self, source_id: int, gate: str) -> ReviewRun | None:
        return await self.session.scalar(
            select(ReviewRun).where(
                ReviewRun.owner_id == self.owner_id,
                ReviewRun.source_id == source_id,
                ReviewRun.gate == gate,
            )
        )

    async def add(
        self,
        *,
        source_id: int,
        gate: str,
        verdict: str,
        quality: str | None = None,
        note_type: str | None = None,
        score: float | None = None,
        action: str | None = None,
        reasons: list[str] | None = None,
        model: str | None = None,
        cost_tokens: int = 0,
        latency_ms: int = 0,
    ) -> ReviewRun:
        run = ReviewRun(
            owner_id=self.owner_id,
            source_id=source_id,
            gate=gate,
            verdict=verdict,
            quality=quality,
            note_type=note_type,
            score=Decimal(str(score)) if score is not None else None,
            action=action,
            reasons=list(reasons or []),
            model=model,
            cost_tokens=cost_tokens,
            latency_ms=latency_ms,
        )
        self.session.add(run)
        await self.session.flush()
        return run
