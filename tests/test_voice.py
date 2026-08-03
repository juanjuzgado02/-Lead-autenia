"""Which voice speaks, and how it can be changed.

The voice is Autenia's on every published video, so the rules here are about
nobody changing it by accident — including a future refactor that "tidies up" a
default it does not realise was chosen by listening.
"""

import os

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


# -- lo que se dice, no lo que se escribe ----------------------------------

@pytest.mark.parametrize("escrito,dicho", [
    ("reduce un 30% los costes", "reduce un 30 por ciento los costes"),
    ("el 7,7 % de absentismo", "el 7,7 por ciento de absentismo"),
    ("cuesta 5.000 €", "cuesta 5.000 euros"),
    ("ahorras 4 h a la semana", "ahorras 4 horas a la semana"),
])
def test_symbols_are_spelled_out_for_the_voice(escrito, dicho):
    """El 1/08/2026 la locución dijo "reduce un treinta costes": el % se cayó."""
    assert voice.speakable(escrito) == dicho


def test_the_subtitle_keeps_the_symbol():
    """Solo cambia lo que se manda al sintetizador; el guion no se toca."""
    guion = "reduce un 30% los costes"
    assert voice.speakable(guion) != guion
    assert "30%" in guion


# -- que lo dicho sea lo escrito -------------------------------------------
#
# El 2/08/2026 salió a revisión un vídeo cuyo guion decía "No se trata de
# despedir a nadie" y cuya locución dijo "no se trata de despedir a nadie, er a
# nadie". El texto mandado era correcto y el corte en pausas era exacto: el
# tropiezo estaba dentro del WAV, donde hasta entonces no miraba nadie.

DICHO = ("No se trata de despedir a nadie. Se trata de eliminar tareas "
         "repetitivas.")


def test_a_stutter_is_caught():
    oido = "No se trata de despedir a nadie, er a nadie. Se trata de eliminar tareas repetitivas."
    fallos = voice.tropiezos(DICHO, oido)
    assert fallos and "nadie" in fallos[0]


def test_a_clean_take_is_left_alone():
    assert voice.tropiezos(DICHO, DICHO) == []


def test_punctuation_case_and_accents_are_not_defects():
    """El transcriptor puntúa como quiere; eso no es un tropiezo de la voz."""
    oido = "no se trata de despedir a nadie se trata de eliminar tareas repetitivas"
    assert voice.tropiezos(DICHO, oido) == []


def test_a_figure_read_aloud_is_not_a_missing_word():
    """Se manda "30 por ciento" y se transcribe "30%": sin esto, tres de más."""
    escrito = "Automatizar reduce un 30% los costes y ahorra 5.000 €."
    assert voice.tropiezos(escrito, escrito) == []
    assert voice.tropiezos(escrito, "Automatizar reduce un 30 por ciento los "
                                    "costes y ahorra 5.000 euros.") == []


def test_one_missing_short_word_is_not_worth_a_second_take():
    """Un transcriptor que se come un "de" es más común que una voz que lo hace."""
    oido = "No se trata despedir a nadie. Se trata de eliminar tareas repetitivas."
    assert voice.tropiezos(DICHO, oido) == []


def test_a_swallowed_phrase_is_caught():
    oido = "No se trata de despedir a nadie. Se trata de tareas repetitivas."
    assert voice.tropiezos(DICHO, oido) == []  # una sola palabra, no
    oido = "No se trata de despedir a nadie. Se trata repetitivas."
    assert voice.tropiezos(DICHO, oido) != []  # tres seguidas, sí


def test_a_take_that_stops_halfway_is_caught():
    """Una locución truncada es más grave que un tartamudeo, no menos."""
    assert voice.tropiezos(DICHO, "No se trata de despedir a nadie.") != []


def test_a_transcription_that_understood_nothing_is_disbelieved():
    """Media locución distinta no es una voz que tartamudea; es un oído roto."""
    assert voice.tropiezos(DICHO, "cualquier otra cosa completamente distinta "
                                  "que no se parece en nada al guion") == []


@pytest.mark.asyncio
async def test_the_review_can_be_switched_off(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTENIA_REVISION_VOZ", "off")
    monkeypatch.setattr(voice, "transcribir", _no_llamar)
    assert await voice.revisar_toma(str(tmp_path / "voz.wav"), DICHO) == []


@pytest.mark.asyncio
async def test_a_take_that_cannot_be_heard_passes(monkeypatch, tmp_path):
    """La duda absuelve: sin red no se deja al canal sin vídeo."""
    async def sorda(_path):
        return None
    monkeypatch.setattr(voice, "transcribir", sorda)
    assert await voice.revisar_toma(str(tmp_path / "voz.wav"), DICHO) == []


async def _no_llamar(_path):  # pragma: no cover - existe para fallar si se llama
    raise AssertionError("no debería transcribirse nada")


# -- la locución, con reintento --------------------------------------------

def _falso_sintetizador(tmp_path, tomas):
    """Un sintetizador que escribe tomas distintas, una por llamada."""
    hechas = []

    async def synthesize(text, *, out_path, provider=None, name=None):
        contenido = tomas[min(len(hechas), len(tomas) - 1)]
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write(contenido)
        hechas.append(contenido)
        return voice.Spoken(path=out_path, duration_s=float(len(hechas)),
                            provider="elevenlabs", estimated_cents=1)

    return synthesize, hechas


@pytest.mark.asyncio
async def test_a_stuttered_take_is_asked_for_again(monkeypatch, tmp_path):
    synthesize, hechas = _falso_sintetizador(tmp_path, ["mala", "buena"])
    monkeypatch.setattr(voice, "synthesize", synthesize)

    async def escuchar(path):
        return open(path, encoding="utf-8").read()

    monkeypatch.setattr(voice, "transcribir", escuchar)
    monkeypatch.setattr(voice, "tropiezos",
                        lambda dicho, oido: [] if oido == "buena" else ["repite"])

    destino = str(tmp_path / "voz.wav")
    hablada = await voice.narracion("lo que sea", out_path=destino, intentos=3)

    assert len(hechas) == 2
    assert open(destino, encoding="utf-8").read() == "buena"
    assert hablada.duration_s == 2.0  # la de la toma que se queda


@pytest.mark.asyncio
async def test_when_none_is_clean_the_least_bad_survives(monkeypatch, tmp_path,
                                                         capsys):
    """Un vídeo con una palabra repetida lo ve una persona; no tenerlo, nadie."""
    synthesize, hechas = _falso_sintetizador(tmp_path, ["dos", "uno", "dos"])
    monkeypatch.setattr(voice, "synthesize", synthesize)

    async def escuchar(path):
        return open(path, encoding="utf-8").read()

    monkeypatch.setattr(voice, "transcribir", escuchar)
    monkeypatch.setattr(voice, "tropiezos",
                        lambda dicho, oido: ["a", "b"] if oido == "dos" else ["a"])

    destino = str(tmp_path / "voz.wav")
    hablada = await voice.narracion("lo que sea", out_path=destino, intentos=3)

    assert len(hechas) == 3
    assert open(destino, encoding="utf-8").read() == "uno"  # la menos mala
    assert hablada.path == destino
    assert hablada.duration_s == 2.0
    assert not os.path.exists(destino + ".mejor")
    assert "menos mala" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_an_audition_is_not_charged_three_times(monkeypatch, tmp_path):
    """``synthesize`` sigue siendo una llamada y una sola: ocho voces, ocho."""
    synthesize, hechas = _falso_sintetizador(tmp_path, ["mala"])
    monkeypatch.setattr(voice, "synthesize", synthesize)
    await voice.synthesize(voice.AUDITION_TEXT, out_path=str(tmp_path / "a.wav"))
    assert len(hechas) == 1


def test_an_unreadable_retry_count_does_not_kill_the_cycle(monkeypatch):
    monkeypatch.setenv("AUTENIA_INTENTOS_VOZ", "tres")
    assert voice.intentos_locucion() == voice.INTENTOS_LOCUCION
    monkeypatch.setenv("AUTENIA_INTENTOS_VOZ", "1")
    assert voice.intentos_locucion() == 1
