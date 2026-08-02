"""Which voice speaks, and how it can be changed.

The voice is Autenia's on every published video, so the rules here are about
nobody changing it by accident — including a future refactor that "tidies up" a
default it does not realise was chosen by listening.
"""

import pytest

from autenia import gemini, voice


def test_the_default_voice_is_the_one_juan_chose():
    """Picked by ear on 2026-07-30 from five male voices reading one script.

    A test rather than a comment because the previous default (Kore, female)
    was never a decision — it was the first thing wired in, and it stayed for
    weeks until somebody listened to a finished video.
    """
    assert gemini.DEFAULT_VOICE == "Iapetus"


def test_an_experiment_can_override_it_without_touching_code(monkeypatch):
    monkeypatch.setenv("AUTENIA_VOICE_NAME", "Charon")
    from core_config import settings
    assert settings.voice_name == "Charon"


def test_gemini_is_the_default_provider(monkeypatch):
    """No second credential and no subscription to produce a video."""
    monkeypatch.delenv("AUTENIA_VOICE_PROVIDER", raising=False)
    from core_config import settings
    assert settings.voice_provider == "gemini"


def test_an_unknown_provider_is_refused_rather_than_guessed(monkeypatch):
    monkeypatch.setenv("AUTENIA_VOICE_PROVIDER", "openai")
    from core_config import ConfigError, settings
    with pytest.raises(ConfigError):
        settings.voice_provider


def test_the_gemini_voice_costs_nothing_to_estimate():
    """Budgets read this before spending, so free must estimate as free."""
    assert voice.estimate_cents("una frase cualquiera", provider="gemini") == 0


def test_elevenlabs_is_estimated_generously_not_optimistically():
    """An estimate that is too low lets a video through the budget it breaks."""
    short = voice.estimate_cents("hola", provider="elevenlabs")
    long = voice.estimate_cents("hola " * 500, provider="elevenlabs")
    assert long > short >= 0


# -- las cuatro voces de ElevenLabs ----------------------------------------

def test_only_the_four_approved_voices_are_offered():
    """Elegidas por Juan el 2/08/2026; todas nativas de España."""
    nombres = [d for _id, d in voice.ELEVENLABS_VOICES]
    assert len(voice.ELEVENLABS_VOICES) == 4
    assert any("Cadalso" in n for n in nombres)
    assert any("Emilio" in n for n in nombres)
    assert any("Ernesto" in n for n in nombres)
    assert any("Marco Cruz" in n for n in nombres)


def test_the_default_is_one_of_the_four():
    assert voice.ELEVENLABS_DEFAULT_VOICE in {i for i, _ in voice.ELEVENLABS_VOICES}


@pytest.mark.parametrize("escrito,esperado_nombre", [
    ("Cadalso", "Cadalso"), ("cadalso", "Cadalso"),
    ("Emilio", "Emilio"), ("ERNESTO", "Ernesto"),
    ("Marco Cruz", "Marco Cruz"), ("marco", "Marco Cruz"),
])
def test_the_env_can_name_a_voice_instead_of_pasting_an_id(monkeypatch, escrito,
                                                          esperado_nombre):
    """Un id de veinte caracteres en el .env no se puede revisar de un vistazo."""
    monkeypatch.setenv("AUTENIA_VOICE_NAME", escrito)
    resuelto = voice.elevenlabs_voice()
    descripcion = dict(voice.ELEVENLABS_VOICES)[resuelto]
    assert descripcion.startswith(esperado_nombre)


def test_without_a_name_it_falls_back_to_the_default(monkeypatch):
    monkeypatch.delenv("AUTENIA_VOICE_NAME", raising=False)
    assert voice.elevenlabs_voice() == voice.ELEVENLABS_DEFAULT_VOICE


def test_a_foreign_id_still_works_but_says_so(monkeypatch, capsys):
    """Un experimento se permite; lo que no se permite es que sea silencioso."""
    monkeypatch.setenv("AUTENIA_VOICE_NAME", "XXotroidcualquiera")
    assert voice.elevenlabs_voice() == "XXotroidcualquiera"
    assert "no es una de las cuatro" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_the_audition_offers_exactly_those_four(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    assert await voice.catalogue("elevenlabs") == list(voice.ELEVENLABS_VOICES)


# -- qué voz lee qué -------------------------------------------------------

from datetime import date  # noqa: E402


@pytest.fixture
def eleven(monkeypatch):
    monkeypatch.setenv("AUTENIA_VOICE_PROVIDER", "elevenlabs")
    monkeypatch.delenv("AUTENIA_VOICE_NAME", raising=False)


def test_a_story_is_read_by_marco_cruz(eleven):
    """Un relato con protagonista se cuenta, no se informa."""
    assert voice.for_script({"genero": "historia"}) == voice.VOZ_HISTORIA


@pytest.mark.parametrize("guion", [
    {"genero": "noticia"}, {}, {"genero": "cualquier-cosa"}, None,
])
def test_everything_else_rotates_among_the_other_three(eleven, guion):
    """En la duda, noticia: una historia a medias suena peor."""
    elegida = voice.for_script(guion)
    assert elegida in voice.VOCES_NOTICIA
    assert elegida != voice.VOZ_HISTORIA


def test_the_rotation_actually_rotates(eleven):
    """Tres días seguidos son tres voces distintas."""
    dias = [date(2026, 8, 2), date(2026, 8, 3), date(2026, 8, 4)]
    elegidas = [voice.for_script({}, when=d) for d in dias]
    assert len(set(elegidas)) == 3


def test_a_voice_set_by_hand_beats_the_rotation(eleven, monkeypatch):
    """Si alguien la escribió es porque quiere esa, no un turno."""
    monkeypatch.setenv("AUTENIA_VOICE_NAME", "Emilio")
    assert voice.for_script({"genero": "historia"}) is None


def test_gemini_is_left_alone(monkeypatch):
    """La regla es sobre las cuatro voces de ElevenLabs, no sobre el proveedor."""
    monkeypatch.setenv("AUTENIA_VOICE_PROVIDER", "gemini")
    assert voice.for_script({"genero": "historia"}) is None
