"""data model: 6 张表 + pgvector/pg_trgm 扩展 + 索引(ADR 0003)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-28

挂 在 0001_baseline 之后。枚举一律 TEXT + CHECK(不用 PG 原生 ENUM);
chunks 用 CHECK 保证 (parent_id IS NULL) = is_parent。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import HALFVEC

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- 扩展:vector(halfvec 类型依赖它)、pg_trgm(中文词法 GIN) ----
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ---- sources ----
    op.create_table(
        "sources",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("owner_id", sa.BigInteger, nullable=False, server_default=sa.text("1")),
        sa.Column("sha256", sa.Text, nullable=False),
        sa.Column("filename", sa.Text, nullable=False),
        sa.Column("ext", sa.Text, nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("storage_path", sa.Text, nullable=False),
        sa.Column("mime", sa.Text),
        sa.Column("parse_status", sa.Text, nullable=False, server_default=sa.text("'ok'")),
        sa.Column(
            "parse_meta",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("doc", sa.dialects.postgresql.JSONB),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("owner_id", "sha256", name="sources_sha_uq"),
        sa.CheckConstraint(
            "parse_status IN ('ok','no_text_expected','unsupported_format','error','pending_ocr')",
            name="sources_parse_status_chk",
        ),
    )
    op.create_index(
        "sources_owner_created_idx", "sources", ["owner_id", sa.text("created_at DESC")]
    )
    op.create_index(
        "sources_parse_status_nonok_idx",
        "sources",
        ["parse_status"],
        postgresql_where=sa.text("parse_status <> 'ok'"),
    )

    # ---- review_runs ----
    op.create_table(
        "review_runs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("owner_id", sa.BigInteger, nullable=False, server_default=sa.text("1")),
        sa.Column(
            "source_id",
            sa.BigInteger,
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("gate", sa.Text, nullable=False),
        sa.Column("verdict", sa.Text, nullable=False),
        sa.Column("quality", sa.Text),
        sa.Column("note_type", sa.Text),
        sa.Column("score", sa.Numeric(4, 3)),
        sa.Column("action", sa.Text),
        sa.Column(
            "reasons",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("model", sa.Text),
        sa.Column("cost_tokens", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("latency_ms", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("gate IN ('G0','G1','G2')", name="review_runs_gate_chk"),
        sa.CheckConstraint(
            "verdict IN ('accept','partial','reject','skip')", name="review_runs_verdict_chk"
        ),
        sa.CheckConstraint(
            "quality IS NULL OR quality IN ('core','normal','reference','junk')",
            name="review_runs_quality_chk",
        ),
        sa.CheckConstraint(
            "action IS NULL OR action IN ('index','extract_spans','topic_dialog','drop')",
            name="review_runs_action_chk",
        ),
    )
    op.create_index("review_runs_source_gate_idx", "review_runs", ["source_id", "gate"])
    op.create_index("review_runs_created_idx", "review_runs", [sa.text("created_at DESC")])

    # ---- notes ----
    op.create_table(
        "notes",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("owner_id", sa.BigInteger, nullable=False, server_default=sa.text("1")),
        sa.Column("source_id", sa.BigInteger, sa.ForeignKey("sources.id", ondelete="SET NULL")),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("doc", sa.dialects.postgresql.JSONB, nullable=False),
        sa.Column("markdown", sa.Text),
        sa.Column("lang", sa.Text),
        sa.Column(
            "tags",
            sa.dialects.postgresql.ARRAY(sa.Text),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("quality", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'active'")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("lang IS NULL OR lang IN ('zh','en','mixed')", name="notes_lang_chk"),
        sa.CheckConstraint(
            "quality IS NULL OR quality IN ('core','normal','reference','junk')",
            name="notes_quality_chk",
        ),
        sa.CheckConstraint("status IN ('active','archived')", name="notes_status_chk"),
    )
    op.create_index("notes_owner_status_idx", "notes", ["owner_id", "status"])
    op.create_index("notes_source_idx", "notes", ["source_id"])

    # ---- chunks(父子同表,自引用) ----
    op.create_table(
        "chunks",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "note_id", sa.BigInteger, sa.ForeignKey("notes.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("parent_id", sa.BigInteger, sa.ForeignKey("chunks.id", ondelete="CASCADE")),
        sa.Column("ord", sa.Integer, nullable=False),
        sa.Column("heading_path", sa.Text),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("token_count", sa.Integer, nullable=False),
        sa.Column("is_parent", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column(
            "meta",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("(parent_id IS NULL) = is_parent", name="chunks_parent_is_parent_chk"),
    )
    op.create_index("chunks_note_ord_idx", "chunks", ["note_id", "ord"])
    op.create_index(
        "chunks_parent_idx",
        "chunks",
        ["parent_id"],
        postgresql_where=sa.text("parent_id IS NOT NULL"),
    )
    op.create_index("chunks_is_parent_idx", "chunks", ["is_parent"])
    op.create_index(
        "chunks_text_trgm_idx",
        "chunks",
        ["text"],
        postgresql_using="gin",
        postgresql_ops={"text": "gin_trgm_ops"},
    )

    # ---- embeddings(vec halfvec(1024),HNSW 余弦) ----
    op.create_table(
        "embeddings",
        sa.Column(
            "chunk_id",
            sa.BigInteger,
            sa.ForeignKey("chunks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("model", sa.Text, primary_key=True),
        sa.Column("dim", sa.SmallInteger, nullable=False),
        sa.Column("vec", HALFVEC(1024), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "embeddings_vec_hnsw",
        "embeddings",
        ["vec"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"vec": "halfvec_cosine_ops"},
    )

    # ---- jobs ----
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("owner_id", sa.BigInteger, nullable=False, server_default=sa.text("1")),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column(
            "payload",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("dedupe_key", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'queued'")),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.Text),
        sa.Column("locked_by", sa.Text),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("dedupe_key", name="jobs_dedupe_uq"),
        sa.CheckConstraint("kind IN ('import','reindex')", name="jobs_kind_chk"),
        sa.CheckConstraint(
            "status IN ('queued','running','done','failed','dead')", name="jobs_status_chk"
        ),
    )
    op.create_index("jobs_status_locked_idx", "jobs", ["status", "locked_at"])


def downgrade() -> None:
    # 扩展不删(可能被其他东西依赖);只倒表,顺序逆依赖
    op.drop_table("embeddings")
    op.drop_table("chunks")
    op.drop_table("notes")
    op.drop_table("review_runs")
    op.drop_table("jobs")
    op.drop_table("sources")
