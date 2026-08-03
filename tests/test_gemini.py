"""What the script prompts promise, which is what preflight later enforces.

These do not call the model. They check the instructions it is given, because
every rule here has a matching refusal in :mod:`autenia.preflight`, and the two
drifting apart is how a good script gets thrown away for obeying.
"""

import pytest

from autenia import gemini

ESCRITO = {
    "hook": "Las facturas en PDF dejan de valer.",
    "escenas": [{"narracion": "Te toca cambiarlo este año.",
                 "visual": "un escritorio con papeles", "tipo": "opinion"}],
    "cta": "En la web hay un cuestionario de un minuto.",
    "titulo": "Factura electrónica", "caption": "Cambia ya.",
    "duracion_estimada_s": 28,
}


@pytest.fixture
def captured(monkeypatch):
    """Intercept the call, keep what the model would have been told."""
    seen = {}

    async def fake_json_call(prompt, schema, *, system=None):
        seen["prompt"] = prompt
        seen["system"] = system
        return ESCRITO, gemini.Usage()

    monkeypatch.setattr(gemini, "json_call", fake_json_call)
    return seen


class _Candidate:
    title = "El 94% de las pymes sigue enviando facturas en PDF"
    url = "https://ejemplo.es/factura"
    publisher = "Ejemplo"
    summary = "Resumen"
    facts = ["el 94% usa PDF", "95 días de media para cobrar",
             "24 meses de plazo"]

    class published_at:
        @staticmethod
        def date():
            import datetime
            return datetime.date(2026, 7, 30)


@pytest.mark.asyncio
async def test_a_news_script_is_told_to_pick_one_figure(captured):
    """Three figures in thirty seconds is a teleprinter, not a short."""
    await gemini.write_script(_Candidate())
    assert "Elige UNO de esos datos" in captured["prompt"]


@pytest.mark.asyncio
async def test_a_brief_without_a_source_forbids_figures(captured):
    """Nothing was collected, so there is nothing to attribute a number to."""
    await gemini.write_brief_script("lo que cuesta contestar los mismos WhatsApps")
    prompt = captured["prompt"]
    assert "lo que cuesta contestar los mismos WhatsApps" in prompt
    assert "no lleva ni una cifra" in prompt
    assert "'opinion'" in prompt


@pytest.mark.asyncio
async def test_a_brief_keeps_the_word_budget(captured):
    """The duration gate does not know where the script came from."""
    await gemini.write_brief_script("los albaranes", seconds=30)
    assert "PRESUPUESTO DE PALABRAS" in captured["prompt"]


@pytest.mark.asyncio
async def test_both_paths_share_the_brand_voice(captured):
    await gemini.write_brief_script("los albaranes")
    from_brief = captured["system"]
    await gemini.write_script(_Candidate())
    assert captured["system"] == from_brief == gemini._SCRIPT_SYSTEM


def test_the_hook_may_not_open_with_the_attribution():
    """The rule that made the first real script sound like the nine o'clock news."""
    assert 'Prohibido abrir con "Según"' in gemini._SCRIPT_SYSTEM


def test_the_prompt_still_demands_the_source_out_loud():
    """Punchier writing must not cost the honesty rule."""
    assert "dice de dónde sale" in gemini._SCRIPT_SYSTEM
    assert "no sustituye" in gemini._SCRIPT_SYSTEM or "ve el revisor" in gemini._SCRIPT_SYSTEM
