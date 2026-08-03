"""Database schema for the content store.

SQLite through aiosqlite. Separate engine, separate file and separate metadata
from ``cloud/`` — that one is Postgres-only (it runs ``CREATE EXTENSION citext``
at boot) and licensed apart.

Money is stored in **cents as integers**. Floats accumulate error, and a budget
that silently drifts is a budget that does not stop anything.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .states import State


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Content(Base):
    """One editorial idea, from candidate to whatever it became."""

    __tablename__ = "contents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    title: Mapped[str] = mapped_column(String(300))
    angle: Mapped[str | None] = mapped_column(String(80), default=None)

    # Deduplication across days: the editorial engine refuses a candidate whose
    # topic hash it has already produced.
    topic_hash: Mapped[str] = mapped_column(String(64), index=True)

    # The version currently representing this content.
    current_version_id: Mapped[str | None] = mapped_column(String(36), default=None)

    versions: Mapped[list["Version"]] = relationship(
        back_populates="content", cascade="all, delete-orphan",
        foreign_keys="Version.content_id", order_by="Version.number",
    )

    __table_args__ = (
        UniqueConstraint("topic_hash", name="uq_contents_topic_hash"),
    )


class Version(Base):
    """One attempt at a content. Feedback creates the next one; none is edited."""

    __tablename__ = "versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    content_id: Mapped[str] = mapped_column(
        ForeignKey("contents.id", ondelete="CASCADE"), index=True
    )
    number: Mapped[int] = mapped_column(Integer)

    state: Mapped[State] = mapped_column(String(20), default=State.CANDIDATO, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    # Editorial payload. JSON-encoded text: SQLite has no native JSON type and a
    # column of blobs we never query by is not worth a dialect-specific type.
    script: Mapped[str | None] = mapped_column(Text, default=None)
    sources: Mapped[str | None] = mapped_column(Text, default=None)
    caption: Mapped[str | None] = mapped_column(Text, default=None)

    # Reuse across regenerations: a hash per reusable asset (script, voice,
    # images, segments). Feedback that does not touch an asset inherits it
    # instead of paying to make it again.
    asset_hashes: Mapped[str | None] = mapped_column(Text, default=None)

    video_path: Mapped[str | None] = mapped_column(String(500), default=None)
    duration_s: Mapped[float | None] = mapped_column(default=None)

    # Why this version ended where it did — feedback text, failure reason.
    note: Mapped[str | None] = mapped_column(Text, default=None)

    # What the previous version was, when this one supersedes it.
    supersedes_id: Mapped[str | None] = mapped_column(String(36), default=None)

    content: Mapped[Content] = relationship(
        back_populates="versions", foreign_keys=[content_id]
    )
    costs: Mapped[list["CostEntry"]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("content_id", "number", name="uq_versions_content_number"),
        # One version per content may be spending. Partial index: SQLite honours
        # the WHERE clause, so only rows in `renderizando` take part.
        Index(
            "uq_versions_one_rendering",
            "content_id",
            unique=True,
            sqlite_where=(state == State.RENDERIZANDO),
        ),
        CheckConstraint("number > 0", name="ck_versions_number_positive"),
    )


class CostEntry(Base):
    """Money attributed to a version, per provider.

    Written when a call is *attempted*, not when it succeeds: a render that dies
    halfway still burned the provider's credit, and a budget that only counts
    successes under-reports exactly when things are going wrong.
    """

    __tablename__ = "cost_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    version_id: Mapped[str] = mapped_column(
        ForeignKey("versions.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    provider: Mapped[str] = mapped_column(String(40))   # gemini | elevenlabs | fal | ...
    operation: Mapped[str] = mapped_column(String(60))  # script | voiceover | ...

    estimated_cents: Mapped[int] = mapped_column(Integer, default=0)
    # None until the provider tells us; the estimate is what budgets act on.
    actual_cents: Mapped[int | None] = mapped_column(Integer, default=None)

    succeeded: Mapped[bool] = mapped_column(default=False)

    version: Mapped[Version] = relationship(back_populates="costs")

    __table_args__ = (
        CheckConstraint("estimated_cents >= 0", name="ck_cost_estimated_non_negative"),
        CheckConstraint(
            "actual_cents IS NULL OR actual_cents >= 0",
            name="ck_cost_actual_non_negative",
        ),
    )
