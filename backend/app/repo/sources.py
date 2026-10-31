"""sources 仓储:原始文件表的读写(ADR 0003 §2.1)。"""

from __future__ import annotations

from sqlalchemy import select, update

from app.models import Source

from .base import Repo


class SourcesRepo(Repo):
    async def get(self, source_id: int) -> Source | None:
        return await self.session.scalar(
            select(Source).where(Source.id == source_id, Source.owner_id == self.owner_id)
        )

    async def get_by_sha256(self, sha256: str) -> Source | None:
        return await self.session.scalar(
            select(Source).where(Source.owner_id == self.owner_id, Source.sha256 == sha256)
        )

    async def create_or_get_by_sha256(
        self,
        *,
        sha256: str,
        filename: str,
        ext: str,
        size_bytes: int,
        storage_path: str,
        mime: str | None = None,
        parse_status: str = "pending",
    ) -> tuple[Source, bool]:
        """按 (owner_id, sha256) 幂等创建;返回 (source, 是否新建)。"""
        existing = await self.get_by_sha256(sha256)
        if existing is not None:
            return existing, False
        source = Source(
            owner_id=self.owner_id,
            sha256=sha256,
            filename=filename,
            ext=ext,
            size_bytes=size_bytes,
            storage_path=storage_path,
            mime=mime,
            parse_status=parse_status,
        )
        self.session.add(source)
        await self.session.flush()
        return source, True

    async def update_parse(
        self,
        source_id: int,
        *,
        parse_status: str,
        parse_meta: dict,
        doc: dict,
    ) -> None:
        await self.session.execute(
            update(Source)
            .where(Source.id == source_id, Source.owner_id == self.owner_id)
            .values(parse_status=parse_status, parse_meta=parse_meta, doc=doc)
        )

    async def mark_status(self, source_id: int, parse_status: str) -> None:
        await self.session.execute(
            update(Source)
            .where(Source.id == source_id, Source.owner_id == self.owner_id)
            .values(parse_status=parse_status)
        )
