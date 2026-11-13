"""sources 仓储:原始文件表的读写(ADR 0003 §2.1)。"""

from __future__ import annotations

from sqlalchemy import delete, func, select, update

from app.models import Note, Source

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

    async def update_content(
        self,
        source_id: int,
        *,
        storage_path: str,
        size_bytes: int,
        filename: str,
        ext: str,
        mime: str | None = None,
    ) -> None:
        """更新 source 的内容字段（视频异步路径：提取完成后填入真实内容信息）。

        sha256 保持占位值不变——dedupe 基于 BVID，不需要真实 sha。
        """
        values: dict = {
            "storage_path": storage_path,
            "size_bytes": size_bytes,
            "filename": filename,
            "ext": ext,
        }
        if mime is not None:
            values["mime"] = mime
        await self.session.execute(
            update(Source)
            .where(Source.id == source_id, Source.owner_id == self.owner_id)
            .values(**values)
        )

    async def update_parse_meta(self, source_id: int, meta: dict) -> None:
        """只更新 parse_meta 字段（视频进度更新用，不影响 parse_status/doc）。"""
        await self.session.execute(
            update(Source)
            .where(Source.id == source_id, Source.owner_id == self.owner_id)
            .values(parse_meta=meta)
        )

    async def count_notes_for(self, source_id: int) -> int:
        """还挂着这个 source 的笔记行数(含 archived——软删的行也还占着引用)。"""
        return int(
            await self.session.scalar(
                select(func.count()).select_from(Note).where(Note.source_id == source_id)
            )
            or 0
        )

    async def delete(self, source_id: int) -> str | None:
        """删 source 行(Core delete;review_runs 的级联由 DB FK 完成)。

        返回 blob 的 storage_path 供调用方在事务提交后删文件;source 不存在返回 None。
        """
        source = await self.get(source_id)
        if source is None:
            return None
        await self.session.execute(
            delete(Source).where(Source.id == source_id, Source.owner_id == self.owner_id)
        )
        return source.storage_path
