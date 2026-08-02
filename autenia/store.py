"""Persistence and the operations that enforce the lifecycle rules.

Every rule that costs money if broken lives here rather than in a caller:
budgets are checked on the way into ``renderizando``, concurrent renders are
refused, approved versions are frozen, and regeneration inherits the assets the
feedback did not touch.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core_config import settings as autenia

from .models import Base, Content, CostEntry, Version, utcnow
from .states import (
    ConcurrentRenderError, State, assert_mutable, assert_transition,
)

_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


class BudgetExceededError(RuntimeError):
    """A spend would cross a configured hard stop. Nothing was charged."""


def _database_url(path: str | None = None) -> str:
    return f"sqlite+aiosqlite:///{path or autenia.db_path}"


async def init_db(path: str | None = None) -> None:
    """Create the engine and the schema. Safe to call more than once."""
    global _engine, _sessionmaker
    if _engine is not None:
        return

    db_path = path or autenia.db_path
    if db_path != ":memory:":
        parent = os.path.dirname(os.path.abspath(db_path))
        os.makedirs(parent, exist_ok=True)

    _engine = create_async_engine(_database_url(db_path))
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_db() -> None:
    """Drop the engine. Used between tests and on shutdown."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine, _sessionmaker = None, None


@asynccontextmanager
async def session():
    """A transactional session. Commits on success, rolls back on error."""
    if _sessionmaker is None:
        raise RuntimeError("content store not initialised — call init_db() first")
    async with _sessionmaker() as sess:
        try:
            yield sess
            await sess.commit()
        except Exception:
            await sess.rollback()
            raise


# --------------------------------------------------------------------------
# Creating work
# --------------------------------------------------------------------------

async def create_content(
    sess: AsyncSession, *, title: str, topic_hash: str, angle: str | None = None,
) -> Content:
    """Register an idea with its first version, before anything is spent.

    Raises IntegrityError if ``topic_hash`` was already produced — that is the
    duplicate check, enforced by the database rather than by a prior read that
    another run could race.
    """
    content = Content(title=title, topic_hash=topic_hash, angle=angle)
    sess.add(content)
    await sess.flush()

    version = Version(content_id=content.id, number=1, state=State.CANDIDATO)
    sess.add(version)
    await sess.flush()

    content.current_version_id = version.id
    return content


async def next_version(
    sess: AsyncSession, previous: Version, *, note: str | None = None,
    script: str | None = None, keep_assets: bool = True,
) -> Version:
    """Supersede a version with a fresh one, inheriting what feedback did not touch.

    The previous version ends at ``descartado`` and is kept: it is the record of
    what was rejected and what it cost. The new one starts at ``guion`` — an
    edit always re-earns its way through review, including one the operator
    typed themselves.

    ``script`` carries an operator's rewrite straight from Telegram; without it
    the new version inherits the previous words for the model to revise.
    """
    assert_transition(State(previous.state), State.DESCARTADO)
    previous.state = State.DESCARTADO
    if note:
        previous.note = note

    version = Version(
        content_id=previous.content_id,
        number=previous.number + 1,
        state=State.GUION,
        script=script if script is not None else previous.script,
        sources=previous.sources,
        caption=previous.caption,
        asset_hashes=previous.asset_hashes if keep_assets else None,
        supersedes_id=previous.id,
    )
    sess.add(version)
    await sess.flush()

    content = await sess.get(Content, previous.content_id)
    content.current_version_id = version.id
    return version


def inherited_assets(version: Version, invalidate: set[str] | None = None) -> dict:
    """Assets the next version can reuse, minus the ones feedback invalidated.

    Reuse is the difference between a regeneration costing nothing and costing
    a full render, so this is deliberately explicit: name what changed, keep
    everything else.
    """
    assets = json.loads(version.asset_hashes) if version.asset_hashes else {}
    for name in invalidate or set():
        assets.pop(name, None)
    return assets


# --------------------------------------------------------------------------
# Moving through the lifecycle
# --------------------------------------------------------------------------

async def transition(
    sess: AsyncSession, version: Version, target: State, *, note: str | None = None,
) -> Version:
    """Move a version, refusing anything the lifecycle forbids.

    Entering ``renderizando`` additionally checks the monthly budget and the
    one-render-per-content rule, because that is the doorway to spending.
    """
    current = State(version.state)
    assert_transition(current, target)

    if target is State.RENDERIZANDO:
        await _assert_can_spend(sess, version)

    version.state = target
    version.updated_at = utcnow()
    if note:
        version.note = note

    try:
        await sess.flush()
    except IntegrityError as exc:
        # The partial unique index caught a concurrent render that slipped past
        # the read above. Refusing here is what stops paying twice.
        await sess.rollback()
        raise ConcurrentRenderError(
            f"another version of content {version.content_id} is already rendering"
        ) from exc

    return version


async def _assert_can_spend(sess: AsyncSession, version: Version) -> None:
    already = await sess.scalar(
        select(func.count())
        .select_from(Version)
        .where(
            Version.content_id == version.content_id,
            Version.state == State.RENDERIZANDO,
            Version.id != version.id,
        )
    )
    if already:
        raise ConcurrentRenderError(
            f"another version of content {version.content_id} is already rendering"
        )

    limit_cents = int(round(autenia.limits.max_cost_per_month * 100))
    spent = await month_spend_cents(sess)
    if spent >= limit_cents:
        raise BudgetExceededError(
            f"monthly budget reached ({spent / 100:.2f} of "
            f"{limit_cents / 100:.2f} EUR); nothing was charged"
        )


async def mark_immutable_guard(version: Version) -> None:
    """Raise if a caller is about to edit a version a human approved."""
    assert_mutable(State(version.state))


# --------------------------------------------------------------------------
# Money
# --------------------------------------------------------------------------

async def record_cost(
    sess: AsyncSession, version: Version, *, provider: str, operation: str,
    estimated_cents: int, actual_cents: int | None = None, succeeded: bool = False,
) -> CostEntry:
    """Attribute a provider call to a version.

    Call this *before* the request, with the estimate, and update it after. A
    call that fails keeps its entry: the credit was still consumed.
    """
    entry = CostEntry(
        version_id=version.id, provider=provider, operation=operation,
        estimated_cents=estimated_cents, actual_cents=actual_cents,
        succeeded=succeeded,
    )
    sess.add(entry)
    await sess.flush()
    return entry


async def settle_cost(
    sess: AsyncSession, entry: CostEntry, *, actual_cents: int, succeeded: bool,
) -> CostEntry:
    """Record what a call really cost once the provider has answered."""
    entry.actual_cents = actual_cents
    entry.succeeded = succeeded
    await sess.flush()
    return entry


def _charged(column_actual, column_estimated):
    """Actual cost where known, estimate otherwise."""
    return func.coalesce(column_actual, column_estimated)


async def version_spend_cents(sess: AsyncSession, version_id: str) -> int:
    total = await sess.scalar(
        select(func.coalesce(func.sum(_charged(CostEntry.actual_cents,
                                               CostEntry.estimated_cents)), 0))
        .where(CostEntry.version_id == version_id)
    )
    return int(total or 0)


async def content_spend_cents(sess: AsyncSession, content_id: str) -> int:
    """Everything a content burned, failed attempts included."""
    total = await sess.scalar(
        select(func.coalesce(func.sum(_charged(CostEntry.actual_cents,
                                               CostEntry.estimated_cents)), 0))
        .join(Version, Version.id == CostEntry.version_id)
        .where(Version.content_id == content_id)
    )
    return int(total or 0)


async def month_spend_cents(sess: AsyncSession, when: datetime | None = None) -> int:
    """Spend in the calendar month of ``when`` (UTC), for the monthly hard stop."""
    moment = when or datetime.now(timezone.utc)
    start = moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = await sess.scalar(
        select(func.coalesce(func.sum(_charged(CostEntry.actual_cents,
                                               CostEntry.estimated_cents)), 0))
        .where(CostEntry.created_at >= start)
    )
    return int(total or 0)


async def assert_within_video_budget(sess: AsyncSession, version: Version,
                                     about_to_spend_cents: int) -> None:
    """Refuse a call that would push this video past its per-video limit."""
    limit_cents = int(round(autenia.limits.max_cost_per_video * 100))
    spent = await version_spend_cents(sess, version.id)
    if spent + about_to_spend_cents > limit_cents:
        raise BudgetExceededError(
            f"this video would reach {(spent + about_to_spend_cents) / 100:.2f} EUR, "
            f"over the {limit_cents / 100:.2f} limit; nothing was charged"
        )


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

async def get_version(sess: AsyncSession, version_id: str) -> Version | None:
    return await sess.get(Version, version_id)


async def waiting_for_operator(sess: AsyncSession) -> list[Version]:
    """Todo lo que espera una decisión: guiones y vídeos, lo más viejo primero.

    Los dos cuentan porque a los dos se les puede contestar por escrito, y qué
    significa ese texto depende de dónde esté la versión: sobre un guion es una
    corrección, sobre un vídeo es un defecto de lo que salió.
    """
    result = await sess.scalars(
        select(Version)
        .where(Version.state.in_([State.EN_REVISION, State.REVISION_VIDEO]))
        .order_by(Version.updated_at)
    )
    return list(result)


async def awaiting_review(sess: AsyncSession) -> list[Version]:
    """Versions sitting in Telegram waiting for a human.

    The Telegram bot treats a plain text message as feedback only when this is
    non-empty — otherwise an unrelated message would be read as instructions.
    """
    result = await sess.scalars(
        select(Version).where(Version.state == State.EN_REVISION)
        .order_by(Version.updated_at)
    )
    return list(result)


async def has_active_cycle(sess: AsyncSession) -> bool:
    """True while something is mid-flight.

    The daily scheduler checks this and skips rather than stacking a second
    unapproved video on top of the first.
    """
    count = await sess.scalar(
        select(func.count()).select_from(Version)
        .where(Version.state.in_([State.RENDERIZANDO, State.EN_REVISION,
                                  State.APROBADO, State.REVISION_VIDEO]))
    )
    return bool(count)


async def topic_already_used(sess: AsyncSession, topic_hash: str) -> bool:
    count = await sess.scalar(
        select(func.count()).select_from(Content).where(Content.topic_hash == topic_hash)
    )
    return bool(count)
