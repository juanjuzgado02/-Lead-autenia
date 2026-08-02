"""One way to ask for speech, whichever service produces it.

The voice is Autenia's on every video it publishes, so the choice belongs to
whoever listens to it — not to whatever was wired in first. Both providers write
the same thing (a mono WAV at ``out_path``) and are selected by
``AUTENIA_VOICE_PROVIDER``, so switching costs one variable and no code.

* ``gemini`` (default) — no second credential, no subscription.
* ``elevenlabs`` — better Spanish prosody, needs a paid plan.
"""

from __future__ import annotations

import wave
from dataclasses import dataclass

import httpx

from core_config import settings as autenia

from . import gemini

#: ElevenLabs' multilingual model handles Spanish; v2 is the stable one.
ELEVENLABS_MODEL = "eleven_multilingual_v2"

#: The only ElevenLabs voices this channel uses, chosen by Juan on 2026-08-02.
#:
#: All four are **native Spanish from Spain**, which is the whole reason the
#: previous list is gone: it held ElevenLabs' English premade voices reading
#: Spanish through the multilingual model, and the account's own 28 voices are
#: 22 English ones plus a handful of Spanish that an audition truncated to
#: "the first eight" never reached.
#:
#: They are library voices, not account voices, and they were verified on
#: 2026-08-02 to synthesise directly from their public id with this key — so
#: nothing has to be added to the account for them to work.
#:
#: **Do not widen this list without Juan hearing the alternative.** It is the
#: voice on every video the company publishes.
ELEVENLABS_VOICES = (
    ("dq5fzy66iCKSIQWm5YMU", "Cadalso — redonda, agradable y creíble"),
    ("ZCh4e9eZSUf41K4cmCEL", "Emilio — cálida, sólida y convincente"),
    ("TOFW0dONbX4o9MmkxwBB", "Ernesto — cercana, dinámica y natural"),
    ("FmAk3rEwAp8LKP5FV4ao", "Marco Cruz — sólida, persuasiva e inteligente"),
)

#: Which one speaks when nobody says otherwise. First of the four, and a
#: placeholder in the honest sense: the choice between them is made by
#: listening, with ``python autenia_bot.py voces``, and the winner goes in
#: AUTENIA_VOICE_NAME.
ELEVENLABS_DEFAULT_VOICE = ELEVENLABS_VOICES[0][0]

#: So the ``.env`` can say ``AUTENIA_VOICE_NAME=Cadalso`` instead of carrying a
#: twenty-character id that nobody can check at a glance.
ELEVENLABS_ALIASES = {
    "cadalso": ELEVENLABS_VOICES[0][0],
    "emilio": ELEVENLABS_VOICES[1][0],
    "ernesto": ELEVENLABS_VOICES[2][0],
    "marco": ELEVENLABS_VOICES[3][0],
    "marco cruz": ELEVENLABS_VOICES[3][0],
}


#: La voz de las historias. Marco Cruz lee distinto un relato que un dato, y un
#: guion con protagonista pide a alguien contándolo, no a alguien informando.
VOZ_HISTORIA = ELEVENLABS_VOICES[3][0]

#: Las tres que se reparten lo demás. Rotan por día del año, no al azar: un
#: canal que suena igual todos los días cansa, y uno que cambia de voz sin
#: patrón suena a que lo llevan tres personas distintas.
VOCES_NOTICIA = (ELEVENLABS_VOICES[0][0], ELEVENLABS_VOICES[1][0],
                 ELEVENLABS_VOICES[2][0])


def for_script(script: dict, *, provider: str | None = None,
               when=None) -> str | None:
    """Qué voz lee este guion, o ``None`` para dejar la configurada.

    Dos reglas, decididas por Juan el 2 de agosto de 2026: las historias las
    lee Marco Cruz, y el resto se reparte entre las otras tres rotando por día.

    Una voz fijada a mano en ``AUTENIA_VOICE_NAME`` gana siempre: si alguien la
    escribió es porque quiere esa y no un turno.

    La rotación es por día, así que dos vídeos del mismo día comparten voz. Es
    el caso raro —el canal publica uno al día— y la alternativa, rotar por
    vídeo, obligaría a esta función a consultar la base de datos para decidir
    algo que se oye en dos segundos.
    """
    chosen = provider or autenia.voice_provider
    if chosen != "elevenlabs" or autenia.voice_name:
        return None

    if (script or {}).get("genero") == "historia":
        return VOZ_HISTORIA

    from datetime import date  # noqa: PLC0415
    dia = (when or date.today()).toordinal()
    return VOCES_NOTICIA[dia % len(VOCES_NOTICIA)]


def elevenlabs_voice(name: str | None = None) -> str:
    """The voice id to speak with: an alias, a raw id, or the default.

    A raw id that is not one of the four is passed through rather than refused —
    it is how an experiment happens — but it says so, because the difference
    between "I am testing a voice" and "the brand voice changed and nobody
    noticed" is whether anybody was told.
    """
    chosen = (name or autenia.voice_name or "").strip()
    if not chosen:
        return ELEVENLABS_DEFAULT_VOICE
    known = ELEVENLABS_ALIASES.get(chosen.lower())
    if known:
        return known
    if chosen not in {vid for vid, _ in ELEVENLABS_VOICES}:
        print(f"[voice] {chosen} no es una de las cuatro voces de Autenia; "
              f"se usa igualmente, pero no es la voz de la marca")
    return chosen

#: Gemini's male prebuilt voices, with the character each one advertises.
#: Sampled 2026-07-30; Juan chose Iapetus.
GEMINI_MALE_VOICES = (
    ("Charon", "informativa"), ("Orus", "firme"), ("Iapetus", "clara"),
    ("Achird", "cercana"), ("Alnilam", "rotunda"), ("Algenib", "grave"),
    ("Rasalgethi", "didáctica"), ("Sadaltager", "experta"),
)

#: The line every candidate voice reads. Same words for all of them, or the
#: comparison is between scripts rather than between voices.
AUDITION_TEXT = (
    "La factura electrónica obligatoria ya está aquí. No es solo un PDF: es un "
    "formato estructurado que exige la ley. Si esto te suena, en la web hay un "
    "cuestionario de un minuto."
)

#: Roughly what ElevenLabs charges per character on the entry plan, in cents.
#: Used only to estimate before spending; the real figure settles afterwards.
ELEVENLABS_CENTS_PER_1K_CHARS = 3

#: A qué ritmo lee cada proveedor, en palabras por segundo. **Medido, no
#: supuesto**: el 2 de agosto de 2026, sobre los seis vídeos ya renderizados con
#: Gemini (2,21 a 2,81; media 2,49) y sobre el primer guion locutado con
#: Cadalso (2,06).
#:
#: Antes había un único 2,6 escrito en tres sitios, y erraba hacia el lado malo:
#: el preflight estima segundos dividiendo por este número, así que un ritmo
#: demasiado alto hace que un guion largo parezca corto y pase un límite que
#: existe para pararlo. Se elige por debajo de la media de cada proveedor por el
#: mismo motivo que ``estimate_cents`` redondea hacia arriba: una estimación
#: optimista es un freno que no frena.
PALABRAS_POR_SEGUNDO = {"gemini": 2.4, "elevenlabs": 2.05}

#: El de por defecto cuando no se sabe qué proveedor habla.
PALABRAS_POR_SEGUNDO_DEFECTO = 2.4


def words_per_second(provider: str | None = None) -> float:
    """Palabras por segundo del proveedor que vaya a hablar."""
    try:
        chosen = provider or autenia.voice_provider
    except Exception:  # noqa: BLE001 - un .env roto no debe romper una estimación
        chosen = "gemini"
    return PALABRAS_POR_SEGUNDO.get(chosen, PALABRAS_POR_SEGUNDO_DEFECTO)


class VoiceError(RuntimeError):
    """Speech synthesis failed. Message is safe to log."""


@dataclass
class Spoken:
    """The result of a synthesis, and what it is expected to have cost."""

    path: str
    duration_s: float
    provider: str
    estimated_cents: int


def estimate_cents(text: str, provider: str | None = None) -> int:
    """What this narration should cost, before calling anyone.

    Budgets act on this, so it must never under-report: an estimate that is too
    low is a hard stop that fails to stop.
    """
    chosen = provider or autenia.voice_provider
    if chosen == "gemini":
        return 0  # free tier
    return -(-len(text) * ELEVENLABS_CENTS_PER_1K_CHARS // 1000)  # ceil


async def synthesize(text: str, *, out_path: str,
                     provider: str | None = None,
                     name: str | None = None) -> Spoken:
    """Speak ``text`` into a mono WAV. Raises VoiceError on any failure.

    ``name`` overrides the configured voice for one call. It exists so an
    audition can read the same line in eight voices without eight restarts.
    """
    if not text or not text.strip():
        raise VoiceError("nothing to speak: the narration is empty")

    chosen = provider or autenia.voice_provider
    if chosen == "gemini":
        path = await _gemini(text, out_path, name)
    elif chosen == "elevenlabs":
        path = await _elevenlabs(text, out_path, name)
    else:  # pragma: no cover - core_config validates this
        raise VoiceError(f"unknown voice provider {chosen!r}")

    return Spoken(
        path=path,
        duration_s=_duration_s(path),
        provider=chosen,
        estimated_cents=estimate_cents(text, chosen),
    )


async def _gemini(text: str, out_path: str, name: str | None = None) -> str:
    try:
        path, _usage = await gemini.synthesize(
            text, voice=name or autenia.voice_name or gemini.DEFAULT_VOICE,
            out_path=out_path,
        )
    except gemini.GeminiError as exc:
        raise VoiceError(str(exc)) from exc
    return path


async def _elevenlabs(text: str, out_path: str, name: str | None = None) -> str:
    autenia.require("voice")
    voice_id = elevenlabs_voice(name)
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.post(
            url,
            headers={"xi-api-key": autenia.elevenlabs_api_key},
            params={"output_format": "pcm_24000"},
            json={"text": text, "model_id": ELEVENLABS_MODEL},
        )
    if response.status_code != 200:
        raise VoiceError(
            f"ElevenLabs returned {response.status_code}: {response.text[:300]}"
        )

    # pcm_24000 is raw signed 16-bit little-endian, same as Gemini's, so both
    # providers hand the rest of the pipeline an identical WAV.
    with wave.open(out_path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(response.content)
    return out_path


async def catalogue(provider: str | None = None) -> list[tuple[str, str]]:
    """Voices worth auditioning, as ``(id, description)``.

    Both lists are fixed and short on purpose. It used to ask ElevenLabs for
    the account's voices and sort the Spanish ones to the front, which was a
    workaround for a list that was mostly English and mostly irrelevant: an
    audition of "the first eight" sent eight English voices. Choosing between
    four voices somebody already vetted is a better use of a listen than
    ranking twenty-eight that nobody did.
    """
    chosen = provider or autenia.voice_provider
    if chosen == "gemini":
        return [(name, f"{name} — {character}")
                for name, character in GEMINI_MALE_VOICES]
    autenia.require("voice")
    return list(ELEVENLABS_VOICES)


def _duration_s(path: str) -> float:
    with wave.open(path, "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())
