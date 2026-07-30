"""Editorial filters and scoring.

The brief's §6 exclusions each get a test, because each one is a way of
publishing something Autenia would have to take down.
"""

from datetime import datetime, timedelta, timezone

import pytest

from autenia.editorial import (
    MIN_SCORE, Candidate, hard_rejections, pick, rank, score, survives,
)

NOW = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)


def make(title, *, summary="", facts=("un dato con fuente",), age_days=0, **kwargs):
    return Candidate(
        title=title,
        url=kwargs.pop("url", f"https://example.com/{abs(hash(title))}"),
        publisher=kwargs.pop("publisher", "Ejemplo"),
        published_at=NOW - timedelta(days=age_days),
        fetched_at=NOW,
        facts=list(facts),
        summary=summary,
        **kwargs,
    )


def rules(candidate):
    return {rejection.rule for rejection in hard_rejections(candidate)}


# -- one test per hard exclusion -------------------------------------------

def test_politics_is_excluded():
    c = make("El Gobierno aprueba una ley de automatización para pymes")
    assert "politica" in rules(c)
    assert not survives(c)


def test_manufactured_outrage_is_excluded():
    c = make("Polémica con los agentes de IA en atención al cliente")
    assert "polemica" in rules(c)


def test_rumours_are_excluded():
    c = make("Se rumorea que la nueva IA automatizará todo el CRM")
    assert "rumor" in rules(c)


def test_misleading_promises_are_excluded():
    c = make("Automatiza tu pyme y consigue ingresos pasivos garantizados")
    assert "promesa_enganosa" in rules(c)


def test_attacking_a_competitor_is_excluded():
    c = make("Por qué ese CRM con IA es una estafa para autónomos")
    assert "ataque" in rules(c)


def test_client_data_is_excluded():
    c = make("Cómo automatizamos el inventario", mentions_client_data=True)
    assert "datos_de_cliente" in rules(c)


def test_material_without_usage_rights_is_excluded():
    c = make("Demo de un agente contestando en WhatsApp", usage_rights_ok=False)
    assert "derechos_de_uso" in rules(c)


def test_a_piece_unrelated_to_autenia_is_excluded():
    """The filter that stops the feed drifting into generic news."""
    c = make("El precio del alquiler sube en las grandes ciudades")
    assert "sin_relacion" in rules(c)


def test_a_candidate_without_sourced_facts_is_excluded():
    c = make("Los agentes de IA cambian la atención al cliente", facts=())
    assert "sin_evidencia" in rules(c)


def test_a_clean_candidate_survives():
    c = make(
        "Un agente de IA contesta el 70% de las consultas de una pyme",
        summary="Reduce el tiempo de atención al cliente en tiendas online",
        facts=["el 70% de las consultas son repetidas", "ahorro de 4 horas semanales"],
    )
    assert survives(c), hard_rejections(c)


# -- accents and word boundaries -------------------------------------------

def test_exclusions_match_without_accents():
    assert "politica" in rules(make("Polemica del Gobierno sobre la IA en pymes"))
    assert "politica" in rules(make("Polemica del gobierno sobre la IA en pymes"))


def test_relevance_terms_do_not_match_inside_other_words():
    """'ia' must not fire inside 'financiera', or everything looks relevant."""
    c = make("La consultora financiera abre nueva sede")
    assert "sin_relacion" in rules(c)


# -- scoring ---------------------------------------------------------------

def test_fresher_beats_stale_all_else_equal():
    fresh = make("Automatizar informes con IA en una pyme", age_days=0)
    stale = make("Automatizar informes con IA en un taller", age_days=12)
    assert score(fresh, now=NOW).total > score(stale, now=NOW).total


def test_more_sourced_facts_score_higher():
    thin = make("Agentes de IA para pymes", facts=["un dato"])
    solid = make("Agentes de IA para autónomos",
                 facts=["dato uno", "dato dos", "dato tres"])
    assert score(solid, now=NOW).parts["evidencia"] > score(thin, now=NOW).parts["evidencia"]


def test_absent_judgments_are_neutral_not_zero():
    """A missing model judgment must not condemn a candidate."""
    c = make("Automatizar la facturación de una pyme con IA")
    parts = score(c, now=NOW).parts
    assert parts["dolor"] == 0.5
    assert parts["hook"] == 0.5


def test_rank_drops_rejected_candidates_entirely():
    good = make("Automatizar informes en una pyme con un agente de IA")
    bad = make("El Gobierno debate la nueva ley")
    ranked = rank([bad, good], now=NOW)
    assert [c.title for c, _ in ranked] == [good.title]


# -- the blank day ---------------------------------------------------------

def test_pick_returns_none_when_nothing_clears_the_bar():
    """A blank day is a valid outcome. Nothing here may invent a fallback."""
    weak = make("Automatizar algo con IA", age_days=13, facts=["un dato"])
    judgments = {weak.url: {"dolor": 0.0, "hook": 0.0, "visual": 0.0, "conversion": 0.0}}
    assert pick([weak], judgments=judgments, now=NOW) is None


def test_pick_returns_none_when_every_candidate_was_rejected():
    assert pick([make("El Gobierno aprueba la ley")], now=NOW) is None


def test_pick_returns_the_best_survivor():
    strong = make(
        "Un agente de IA contesta las consultas repetidas de una pyme",
        summary="automatizacion de atencion al cliente con integracion al CRM",
        facts=["70% de consultas repetidas", "4 horas semanales", "sin ampliar plantilla"],
    )
    weaker = make("La IA llega a las pymes", facts=["un dato"], age_days=9)
    judgments = {strong.url: {"dolor": 0.9, "hook": 0.9, "visual": 0.8, "conversion": 0.8}}

    chosen = pick([weaker, strong], judgments=judgments, now=NOW)

    assert chosen is not None
    candidate, result = chosen
    assert candidate.title == strong.title
    assert result.total >= MIN_SCORE


# -- deduplication ---------------------------------------------------------

def test_the_same_story_republished_hashes_the_same():
    original = make("Agentes de IA para pymes", url="https://a.example/1")
    reprint = make("Agentes de IA para pymes", url="https://b.example/2")
    assert original.topic_hash == reprint.topic_hash


def test_word_order_does_not_change_the_topic_hash():
    a = make("Automatizar informes con IA")
    b = make("Con IA automatizar informes")
    assert a.topic_hash == b.topic_hash


def test_different_stories_hash_differently():
    a = make("Agentes de IA para pymes")
    b = make("Cuadros de mando en tiempo real para pymes")
    assert a.topic_hash != b.topic_hash
