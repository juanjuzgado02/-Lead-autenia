"""The content store against a real database.

Every test here is about money or about publishing the wrong thing: duplicate
spend, budgets that fail to stop, approved versions changing under the reviewer.
"""

import json

import pytest
import pytest_asyncio

from autenia import store
from autenia.models import Content, Version
from autenia.states import (
    ConcurrentRenderError, ImmutableVersionError, State, StateError,
)
from autenia.store import BudgetExceededError


@pytest_asyncio.fixture
async def db(monkeypatch, tmp_path):
    """A fresh on-disk database per test, with the brief's default limits."""
    monkeypatch.setenv("AUTENIA_DB_PATH", str(tmp_path / "autenia.db"))
    monkeypatch.delenv("AUTENIA_MAX_COST_PER_VIDEO", raising=False)
    monkeypatch.delenv("AUTENIA_MAX_COST_PER_MONTH", raising=False)
    await store.dispose_db()
    await store.init_db()
    yield
    await store.dispose_db()


async def _new_content(sess, *, title="Cómo automatizar tu inventario", topic="t1"):
    return await store.create_content(sess, title=title, topic_hash=topic)


async def _version(sess, content) -> Version:
    return await sess.get(Version, content.current_version_id)


async def _approve(sess, version):
    """Walk a version to `aprobado`, the way the Telegram review does."""
    for target in (State.GUION, State.EN_REVISION, State.APROBADO):
        if State(version.state) is not target:
            await store.transition(sess, version, target)
    return version


# -- creation --------------------------------------------------------------

@pytest.mark.asyncio
async def test_content_and_version_exist_before_anything_is_spent(db):
    async with store.session() as sess:
        content = await _new_content(sess)
        version = await _version(sess, content)

    assert version.number == 1
    assert State(version.state) is State.CANDIDATO
    assert content.current_version_id == version.id


@pytest.mark.asyncio
async def test_the_same_topic_cannot_be_produced_twice(db):
    async with store.session() as sess:
        await _new_content(sess, topic="misma-idea")

    async with store.session() as sess:
        assert await store.topic_already_used(sess, "misma-idea")

    with pytest.raises(Exception):  # IntegrityError from the unique constraint
        async with store.session() as sess:
            await _new_content(sess, title="Otro titular", topic="misma-idea")


# -- spending --------------------------------------------------------------

@pytest.mark.asyncio
async def test_two_versions_of_one_content_cannot_render_at_once(db):
    """The double-click guard: this is what stops paying twice for one video."""
    async with store.session() as sess:
        content = await _new_content(sess)
        first = await _version(sess, content)
        await _approve(sess, first)
        await store.transition(sess, first, State.RENDERIZANDO)

        second = Version(content_id=content.id, number=2, state=State.APROBADO)
        sess.add(second)
        await sess.flush()

        with pytest.raises(ConcurrentRenderError):
            await store.transition(sess, second, State.RENDERIZANDO)


@pytest.mark.asyncio
async def test_monthly_budget_blocks_entry_to_rendering(db, monkeypatch):
    monkeypatch.setenv("AUTENIA_MAX_COST_PER_MONTH", "1.00")

    async with store.session() as sess:
        spent = await _new_content(sess, topic="ya-gastado")
        burned = await _version(sess, spent)
        await store.record_cost(
            sess, burned, provider="elevenlabs", operation="voiceover",
            estimated_cents=100, succeeded=True,
        )

    async with store.session() as sess:
        content = await _new_content(sess, topic="nuevo")
        version = await _version(sess, content)
        await _approve(sess, version)

        with pytest.raises(BudgetExceededError, match="nothing was charged"):
            await store.transition(sess, version, State.RENDERIZANDO)

        # The refusal must not have moved the version.
        assert State(version.state) is State.APROBADO


@pytest.mark.asyncio
async def test_per_video_budget_refuses_before_the_call(db, monkeypatch):
    monkeypatch.setenv("AUTENIA_MAX_COST_PER_VIDEO", "0.50")

    async with store.session() as sess:
        content = await _new_content(sess)
        version = await _version(sess, content)
        await store.record_cost(
            sess, version, provider="elevenlabs", operation="voiceover",
            estimated_cents=40, succeeded=True,
        )

        await store.assert_within_video_budget(sess, version, 10)  # 0.50 exactly, fine

        with pytest.raises(BudgetExceededError):
            await store.assert_within_video_budget(sess, version, 11)


@pytest.mark.asyncio
async def test_a_failed_attempt_still_counts_as_spent(db):
    """Provider credit is gone whether or not the render finished."""
    async with store.session() as sess:
        content = await _new_content(sess)
        version = await _version(sess, content)
        entry = await store.record_cost(
            sess, version, provider="fal", operation="avatar", estimated_cents=65,
        )
        await store.settle_cost(sess, entry, actual_cents=65, succeeded=False)

        await store.transition(sess, version, State.GUION)
        await store.transition(sess, version, State.EN_REVISION)
        await store.transition(sess, version, State.APROBADO)
        await store.transition(sess, version, State.RENDERIZANDO)
        await store.transition(sess, version, State.FALLIDO)

        assert await store.version_spend_cents(sess, version.id) == 65
        assert await store.content_spend_cents(sess, content.id) == 65
        assert await store.month_spend_cents(sess) == 65


@pytest.mark.asyncio
async def test_estimate_counts_until_the_real_cost_is_known(db):
    async with store.session() as sess:
        content = await _new_content(sess)
        version = await _version(sess, content)
        entry = await store.record_cost(
            sess, version, provider="elevenlabs", operation="voiceover",
            estimated_cents=30,
        )
        assert await store.version_spend_cents(sess, version.id) == 30

        await store.settle_cost(sess, entry, actual_cents=42, succeeded=True)
        assert await store.version_spend_cents(sess, version.id) == 42


# -- approval integrity ----------------------------------------------------

@pytest.mark.asyncio
async def test_an_approved_version_cannot_be_modified(db):
    async with store.session() as sess:
        content = await _new_content(sess)
        version = await _version(sess, content)
        await store.transition(sess, version, State.GUION)
        await store.transition(sess, version, State.EN_REVISION)
        await store.transition(sess, version, State.APROBADO)

        with pytest.raises(ImmutableVersionError):
            await store.mark_immutable_guard(version)


@pytest.mark.asyncio
async def test_rendering_cannot_start_before_a_human_approved(db):
    """Review moved ahead of the render: a rejected idea must cost nothing."""
    async with store.session() as sess:
        content = await _new_content(sess)
        version = await _version(sess, content)
        await store.transition(sess, version, State.GUION)

        with pytest.raises(StateError):
            await store.transition(sess, version, State.RENDERIZANDO)


@pytest.mark.asyncio
async def test_publication_requires_passing_through_review(db):
    async with store.session() as sess:
        content = await _new_content(sess)
        version = await _version(sess, content)
        await store.transition(sess, version, State.GUION)

        with pytest.raises(StateError):
            await store.transition(sess, version, State.PUBLICADO)


# -- regeneration ----------------------------------------------------------

@pytest.mark.asyncio
async def test_feedback_creates_a_version_and_keeps_the_old_one(db):
    async with store.session() as sess:
        content = await _new_content(sess)
        first = await _version(sess, content)
        first.asset_hashes = json.dumps({"script": "a1", "voice": "b2", "images": "c3"})
        await store.transition(sess, first, State.GUION)
        await store.transition(sess, first, State.EN_REVISION)

        second = await store.next_version(sess, first, note="el hook es flojo")

        assert second.number == 2
        assert second.supersedes_id == first.id
        assert State(second.state) is State.GUION
        # The rejected version survives as the record of what it cost.
        assert State(first.state) is State.DESCARTADO
        assert first.note == "el hook es flojo"

        refreshed = await sess.get(Content, content.id)
        assert refreshed.current_version_id == second.id


@pytest.mark.asyncio
async def test_regeneration_inherits_the_assets_feedback_did_not_touch(db):
    """Reuse is the difference between a free regeneration and a full re-render."""
    async with store.session() as sess:
        content = await _new_content(sess)
        first = await _version(sess, content)
        first.asset_hashes = json.dumps({"script": "a1", "voice": "b2", "images": "c3"})

        kept = store.inherited_assets(first, invalidate={"script", "voice"})

        assert kept == {"images": "c3"}


# -- scheduler support -----------------------------------------------------

@pytest.mark.asyncio
async def test_awaiting_review_is_empty_until_something_waits(db):
    """The Telegram bot reads plain text as feedback only when this is non-empty."""
    async with store.session() as sess:
        content = await _new_content(sess)
        version = await _version(sess, content)
        assert await store.awaiting_review(sess) == []

        await store.transition(sess, version, State.GUION)
        await store.transition(sess, version, State.EN_REVISION)

        waiting = await store.awaiting_review(sess)
        assert [v.id for v in waiting] == [version.id]


@pytest.mark.asyncio
async def test_scheduler_sees_an_active_cycle_until_it_is_resolved(db):
    async with store.session() as sess:
        content = await _new_content(sess)
        version = await _version(sess, content)
        assert not await store.has_active_cycle(sess)

        await store.transition(sess, version, State.GUION)
        await store.transition(sess, version, State.EN_REVISION)
        assert await store.has_active_cycle(sess)

        await store.transition(sess, version, State.DESCARTADO)
        assert not await store.has_active_cycle(sess)


@pytest.mark.asyncio
async def test_the_queue_holds_scripts_and_videos(db):
    """A un vídeo también se le contesta por escrito, y ese texto es un defecto."""
    async with store.session() as sess:
        guion = await _new_content(sess, topic="ha")
        v1 = await _version(sess, guion)
        await store.transition(sess, v1, State.GUION)
        await store.transition(sess, v1, State.EN_REVISION)

        video = await _new_content(sess, topic="hb")
        v2 = await _version(sess, video)
        for destino in (State.GUION, State.EN_REVISION, State.APROBADO,
                        State.RENDERIZANDO, State.REVISION_VIDEO):
            await store.transition(sess, v2, destino)

        esperando = await store.waiting_for_operator(sess)
        assert {v.id for v in esperando} == {v1.id, v2.id}
        # Sólo los guiones cuentan como "pendientes de revisar el texto".
        assert [v.id for v in await store.awaiting_review(sess)] == [v1.id]
