"""仓储聚合:一次构造,流水线只通过它访问数据库(AGENTS.md §9)。

所有查询都在各表 Repo 里,且一律带 owner_id 作用域;调用方不裸用 session 拼 SQL。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from .chunks import ChunksRepo
from .embeddings import EmbeddingsRepo
from .jobs import JobsRepo
from .notes import NotesRepo
from .qa import QaRepo
from .review_runs import ReviewRunsRepo
from .search import SearchRepo
from .sources import SourcesRepo


@dataclass
class Repos:
    session: AsyncSession
    owner_id: int = 1
    sources: SourcesRepo = field(init=False)
    review_runs: ReviewRunsRepo = field(init=False)
    notes: NotesRepo = field(init=False)
    chunks: ChunksRepo = field(init=False)
    embeddings: EmbeddingsRepo = field(init=False)
    jobs: JobsRepo = field(init=False)
    search: SearchRepo = field(init=False)
    qa: QaRepo = field(init=False)

    def __post_init__(self) -> None:
        self.sources = SourcesRepo(self.session, self.owner_id)
        self.review_runs = ReviewRunsRepo(self.session, self.owner_id)
        self.notes = NotesRepo(self.session, self.owner_id)
        self.chunks = ChunksRepo(self.session, self.owner_id)
        self.embeddings = EmbeddingsRepo(self.session, self.owner_id)
        self.jobs = JobsRepo(self.session, self.owner_id)
        self.search = SearchRepo(self.session, self.owner_id)
        self.qa = QaRepo(self.session, self.owner_id)
