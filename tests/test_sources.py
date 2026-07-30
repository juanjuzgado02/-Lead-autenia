"""Candidate collection: the parts that must hold without calling anyone.

The collector's job is provenance. Every test here is about a way a candidate
could end up citing something that does not exist or is not what it claims.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from autenia import sources
from autenia.gemini import Usage

NOW = datetime.now(timezone.utc)


def chunk(uri, title):
    return {"web": {"uri": uri, "title": title}}


async def collect_with(*, search_text="resumen", chunks=None, extracted=None,
                       resolved=None, **kwargs):
    """Run collect() with the network stubbed at its three boundaries."""
    with patch.object(sources, "_search",
                      AsyncMock(return_value=(search_text, chunks or [], Usage()))), \
         patch.object(sources, "resolve_sources",
                      AsyncMock(return_value=resolved or [])), \
         patch.object(sources, "json_call",
                      AsyncMock(return_value=({"candidatos": extracted or []}, Usage()))):
        return await sources.collect(**kwargs)


SOURCES = [
    {"url": "https://medio.es/articulo-uno", "publisher": "Medio"},
    {"url": "https://otro.es/articulo-dos", "publisher": "Otro"},
]


def entry(**overrides):
    base = {
        "title": "Las empresas pierden horas metiendo pedidos a mano",
        "url": "https://medio.es/articulo-uno",
        "publisher": "Medio",
        "published_at": NOW.date().isoformat(),
        "summary": "Un informe sobre carga administrativa",
        "facts": ["cada pedido consume 30 minutos"],
    }
    base.update(overrides)
    return base


# -- provenance ------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_candidate_keeps_its_canonical_source():
    candidates, _ = await collect_with(resolved=SOURCES, extracted=[entry()])
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.url == "https://medio.es/articulo-uno"
    assert candidate.publisher == "Medio"
    assert candidate.facts == ["cada pedido consume 30 minutos"]
    assert candidate.fetched_at is not None


@pytest.mark.asyncio
async def test_a_url_the_model_invented_is_dropped():
    """The model may only cite URLs we resolved ourselves."""
    invented = entry(url="https://inventado.example/noticia")
    candidates, _ = await collect_with(resolved=SOURCES, extracted=[invented])
    assert candidates == []


@pytest.mark.asyncio
async def test_a_candidate_without_facts_is_dropped():
    candidates, _ = await collect_with(resolved=SOURCES, extracted=[entry(facts=[])])
    assert candidates == []


@pytest.mark.asyncio
async def test_blank_facts_do_not_count_as_evidence():
    candidates, _ = await collect_with(
        resolved=SOURCES, extracted=[entry(facts=["   ", ""])])
    assert candidates == []


@pytest.mark.asyncio
async def test_a_candidate_without_a_title_is_dropped():
    candidates, _ = await collect_with(resolved=SOURCES, extracted=[entry(title="  ")])
    assert candidates == []


@pytest.mark.asyncio
async def test_nothing_resolvable_means_no_candidates():
    """A blank day is a valid outcome; nothing may invent filler."""
    candidates, _ = await collect_with(resolved=[], extracted=[entry()])
    assert candidates == []


# -- dates -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_story_older_than_the_window_is_dropped():
    old = (NOW - timedelta(days=30)).date().isoformat()
    candidates, _ = await collect_with(
        resolved=SOURCES, extracted=[entry(published_at=old)], days=14)
    assert candidates == []


@pytest.mark.asyncio
async def test_a_future_date_does_not_win_the_freshness_score():
    """A guessed date must not outrank a real one."""
    future = (NOW + timedelta(days=5)).date().isoformat()
    candidates, _ = await collect_with(
        resolved=SOURCES, extracted=[entry(published_at=future)])
    assert len(candidates) == 1
    assert candidates[0].published_at <= datetime.now(timezone.utc)


def test_dates_parse_in_the_formats_the_model_uses():
    assert sources._parse_date("2026-07-29").date().isoformat() == "2026-07-29"
    assert sources._parse_date("29/07/2026").date().isoformat() == "2026-07-29"
    assert sources._parse_date("29-07-2026").date().isoformat() == "2026-07-29"


def test_an_unparseable_date_falls_back_to_today_not_to_1970():
    """Epoch would fail the age window and silently drop every candidate."""
    parsed = sources._parse_date("el pasado martes")
    assert (datetime.now(timezone.utc) - parsed).total_seconds() < 60


# -- deduplication ---------------------------------------------------------

@pytest.mark.asyncio
async def test_a_topic_already_produced_is_dropped():
    first, _ = await collect_with(resolved=SOURCES, extracted=[entry()])
    known = {first[0].topic_hash}

    again, _ = await collect_with(
        resolved=SOURCES, extracted=[entry()], exclude_hashes=known)
    assert again == []


@pytest.mark.asyncio
async def test_the_same_story_from_two_outlets_is_only_taken_once():
    duplicate = entry(url="https://otro.es/articulo-dos", publisher="Otro")
    candidates, _ = await collect_with(
        resolved=SOURCES, extracted=[entry(), duplicate])
    assert len(candidates) == 1


# -- source resolution -----------------------------------------------------

@pytest.mark.asyncio
async def test_publishers_stay_aligned_when_a_chunk_has_no_uri():
    """A chunk without a uri must not shift every later title onto the wrong URL."""
    chunks = [
        chunk(None, "sin-uri.es"),
        chunk("https://redirect/1", "primero.es"),
        chunk("https://redirect/2", "segundo.es"),
    ]
    with patch.object(sources, "_resolve", AsyncMock(
            side_effect=["https://primero.es/a", "https://segundo.es/b"])):
        resolved = await sources.resolve_sources(chunks)

    assert resolved == [
        {"url": "https://primero.es/a", "publisher": "primero.es"},
        {"url": "https://segundo.es/b", "publisher": "segundo.es"},
    ]


@pytest.mark.asyncio
async def test_an_unreachable_source_is_dropped_not_guessed():
    chunks = [chunk("https://redirect/1", "roto.es"),
              chunk("https://redirect/2", "vivo.es")]
    with patch.object(sources, "_resolve",
                      AsyncMock(side_effect=[None, "https://vivo.es/a"])):
        resolved = await sources.resolve_sources(chunks)

    assert [s["url"] for s in resolved] == ["https://vivo.es/a"]


@pytest.mark.asyncio
async def test_the_publisher_falls_back_to_the_domain():
    with patch.object(sources, "_resolve",
                      AsyncMock(return_value="https://www.medio.es/articulo")):
        resolved = await sources.resolve_sources([chunk("https://redirect/1", "")])

    assert resolved == [{"url": "https://www.medio.es/articulo", "publisher": "medio.es"}]
