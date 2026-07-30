"""Preflight: the last refusal before spending or publishing.

Each test here is a thing that would otherwise reach Autenia's audience.
"""

import pytest

from autenia import preflight
from autenia.preflight import MAX_SECONDS, MIN_SECONDS, check, estimate_seconds


@pytest.fixture(autouse=True)
def default_limits(monkeypatch):
    for name in ("AUTENIA_MAX_COST_PER_VIDEO", "AUTENIA_MAX_COST_PER_MONTH",
                 "AUTENIA_MAX_DURATION_S"):
        monkeypatch.delenv(name, raising=False)


def narration_of(seconds):
    """Narration that estimates to roughly ``seconds``."""
    words = int(seconds * preflight.WORDS_PER_SECOND)
    return " ".join(["palabra"] * words)


def good_script(**overrides):
    script = {
        "hook": "Cuatro horas cada lunes haciendo el mismo informe.",
        "escenas": [
            {
                "narracion": "Según Ejemplo, el 70% de las consultas de una "
                             "tienda son repetidas.",
                "visual": "captura de un agente contestando en WhatsApp",
                "tipo": "hecho",
                "fuente": "Ejemplo",
            },
            {
                "narracion": "Eso es tiempo que no vuelves a tocar.",
                "visual": "cuadro de mando actualizándose solo",
                "tipo": "opinion",
            },
        ],
        "cta": "En la web hay un cuestionario de un minuto que te lo dice.",
        "titulo": "El informe que se hace solo",
        "caption": "Automatiza lo repetitivo",
        "duracion_estimada_s": 30,
    }
    script.update(overrides)
    return script


def rules(result):
    return {problem.rule for problem in result.problems}


# -- the happy path --------------------------------------------------------

def test_a_sound_script_passes():
    result = check(good_script(), narration=narration_of(30))
    assert result.ok, str(result)
    assert bool(result) is True
    assert MIN_SECONDS <= result.estimated_seconds <= MAX_SECONDS


# -- claims ----------------------------------------------------------------

def test_a_fact_without_a_source_is_refused():
    script = good_script()
    script["escenas"][0]["fuente"] = ""
    assert "afirmacion" in rules(check(script, narration=narration_of(30)))


def test_a_scene_that_declares_neither_fact_nor_opinion_is_refused():
    script = good_script()
    script["escenas"][0]["tipo"] = "otra cosa"
    assert "afirmacion" in rules(check(script, narration=narration_of(30)))


def test_a_figure_hidden_inside_an_opinion_is_caught():
    """Numbers read as fact to the viewer regardless of how they are labelled."""
    script = good_script()
    script["escenas"][1]["narracion"] = "Te ahorras unas 4 horas al mes, más o menos."
    assert "afirmacion" in rules(check(script, narration=narration_of(30)))


def test_a_scene_without_a_visual_is_refused():
    script = good_script()
    script["escenas"][1]["visual"] = "  "
    assert "visual" in rules(check(script, narration=narration_of(30)))


# -- forbidden phrasing ----------------------------------------------------

@pytest.mark.parametrize("phrase", [
    "link in bio", "el enlace en la bio", "resultados garantizado", "ingresos pasivos",
])
def test_banned_phrasing_is_refused(phrase):
    narration = narration_of(28) + " " + phrase
    assert "prohibido" in rules(check(good_script(), narration=narration))


def test_a_script_without_a_hook_is_refused():
    assert "hook" in rules(check(good_script(hook=""), narration=narration_of(30)))


def test_a_script_without_a_cta_is_refused():
    assert "cta" in rules(check(good_script(cta="   "), narration=narration_of(30)))


# -- duration --------------------------------------------------------------

def test_too_long_is_refused():
    result = check(good_script(), narration=narration_of(50))
    assert "duracion" in rules(result)


def test_too_short_is_refused():
    result = check(good_script(), narration=narration_of(12))
    assert "duracion" in rules(result)


def test_the_hard_maximum_is_reported_separately(monkeypatch):
    monkeypatch.setenv("AUTENIA_MAX_DURATION_S", "55")
    result = check(good_script(), narration=narration_of(60))
    assert any("máximo duro" in problem.detail for problem in result.problems)


def test_duration_estimate_tracks_word_count():
    assert estimate_seconds("") == 0
    assert estimate_seconds(narration_of(30)) == pytest.approx(30, abs=1)


# -- money -----------------------------------------------------------------

def test_a_video_over_its_own_budget_is_refused(monkeypatch):
    monkeypatch.setenv("AUTENIA_MAX_COST_PER_VIDEO", "0.50")
    result = check(good_script(), narration=narration_of(30),
                   estimated_cents=20, spent_this_video_cents=40)
    assert "coste" in rules(result)


def test_a_video_that_would_cross_the_monthly_budget_is_refused(monkeypatch):
    monkeypatch.setenv("AUTENIA_MAX_COST_PER_MONTH", "25.00")
    result = check(good_script(), narration=narration_of(30),
                   estimated_cents=100, spent_this_month_cents=2450)
    assert "coste" in rules(result)


def test_a_free_render_passes_the_budget_check(monkeypatch):
    """Gemini voice costs nothing, so budget must never be what blocks it."""
    monkeypatch.setenv("AUTENIA_MAX_COST_PER_VIDEO", "0.50")
    result = check(good_script(), narration=narration_of(30), estimated_cents=0,
                   spent_this_video_cents=0)
    assert result.ok, str(result)


# -- reporting -------------------------------------------------------------

def test_every_problem_is_reported_at_once():
    """One round trip should surface everything, not the first failure."""
    script = good_script(hook="", cta="")
    script["escenas"][0]["fuente"] = ""
    result = check(script, narration=narration_of(70))
    assert {"hook", "cta", "afirmacion", "duracion"} <= rules(result)


# -- spoken attribution (2026-07-30) --------------------------------------

def test_a_fact_that_does_not_name_its_source_out_loud_is_refused():
    """The `fuente` field is for the reviewer; the viewer never sees it.

    A figure with nobody's name behind it sounds invented — which is exactly
    the difference between a fact and a salesman's promise, on a channel that
    sells "experiencia real, no hype".
    """
    script = good_script(escenas=[{
        "narracion": "El absentismo cerró el año en el 7,7%.",
        "visual": "un cuadro de mando",
        "tipo": "hecho",
        "fuente": "Europa Press",
    }])
    result = check(script, narration=narration_of(28))
    assert not result.ok
    assert any("no nombra su fuente" in p.detail for p in result.problems)


def test_naming_the_outlet_in_the_line_is_enough():
    script = good_script(escenas=[{
        "narracion": "Según Europa Press, el absentismo cerró el año en el 7,7%.",
        "visual": "un cuadro de mando",
        "tipo": "hecho",
        "fuente": "Europa Press",
    }])
    assert check(script, narration=narration_of(28)).ok


@pytest.mark.parametrize("linea", [
    "El mismo informe cifra el coste en 8.000 millones.",
    "Esos datos apuntan a 12 horas semanales.",
    "Un estudio reciente lo sitúa en el 40%.",
])
def test_a_second_mention_may_be_generic(linea):
    """Saying "Según Europa Press" three times running reads like a teleprinter."""
    script = good_script(escenas=[{
        "narracion": linea, "visual": "un cuadro de mando",
        "tipo": "hecho", "fuente": "Europa Press",
    }])
    assert check(script, narration=narration_of(28)).ok


def test_a_common_word_in_the_source_name_does_not_count_as_attribution():
    """"Blog Empresas Yoigo" must not be satisfied by the word "empresas"."""
    script = good_script(escenas=[{
        "narracion": "Las empresas pierden 12 horas al mes en papeleo.",
        "visual": "un escritorio",
        "tipo": "hecho",
        "fuente": "Blog Empresas Yoigo",
    }])
    result = check(script, narration=narration_of(28))
    assert not result.ok


def test_an_opinion_needs_no_attribution():
    script = good_script(escenas=[{
        "narracion": "Es tiempo que no vuelves a recuperar.",
        "visual": "un escritorio", "tipo": "opinion",
    }])
    assert check(script, narration=narration_of(28)).ok
