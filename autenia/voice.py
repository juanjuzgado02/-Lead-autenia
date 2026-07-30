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
#: A neutral Spanish-capable default. Override with AUTENIA_VOICE_NAME.
#:
#: This is a fallback, not a choice: the voice is picked by listening, with
#: ``python autenia_bot.py voces``, and the winner goes in AUTENIA_VOICE_NAME.
ELEVENLABS_DEFAULT_VOICE = "EXAVITQu4vr4xnSDxMaL"

#: ElevenLabs' public premade voices, male, with fixed ids every account can
#: use. The fallback for a key created without the ``voices_read`` permission —
#: which is the default now, and which otherwise kills the audition even though
#: synthesis itself works fine.
#:
#: These are English-native voices reading Spanish through the multilingual
#: model. They are good, but a Spanish-native voice from the account's own
#: library is better; widening the key's permissions is what unlocks that.
ELEVENLABS_PUBLIC_MALE = (
    ("JBFqnCBsd6RMkjVDRZzb", "George — británica, cálida"),
    ("nPczCjzI2devNBz1zQrb", "Brian — grave, narrador"),
    ("pqHfZKP75CvOlQylNhV4", "Bill — serena, de confianza"),
    ("onwK4e9ZLuTAKqWW03F9", "Daniel — británica, con autoridad"),
    ("CwhRBWXzGAHq8TQ4Fs17", "Roger — segura, directa"),
    ("cjVigY5qzO86Huf0OWal", "Eric — cercana, conversacional"),
    ("bIHbv24MWmeRgasZH58o", "Will — joven, natural"),
    ("TX3LPaxmHKxFdv7VOQHJ", "Liam — enérgica"),
)

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
    voice_id = name or autenia.voice_name or ELEVENLABS_DEFAULT_VOICE
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


async def catalogue(provider: str | None = None,
                    *, male_only: bool = True) -> list[tuple[str, str]]:
    """Voices worth auditioning, as ``(id, description)``.

    Gemini's prebuilt list is fixed and known. ElevenLabs depends on the
    account, so it is asked — a hardcoded voice id is a guess about somebody
    else's library, and the whole point is that the choice is made by ear.
    """
    chosen = provider or autenia.voice_provider
    if chosen == "gemini":
        return [(name, f"{name} — {character}")
                for name, character in GEMINI_MALE_VOICES]

    autenia.require("voice")
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(
            "https://api.elevenlabs.io/v1/voices",
            headers={"xi-api-key": autenia.elevenlabs_api_key})

    if response.status_code in (401, 403):
        # ElevenLabs scopes keys, and a new one has no `voices_read` by
        # default. Synthesis still works, so falling back to the public voices
        # lets the audition happen anyway rather than failing on a permission
        # that has nothing to do with speaking.
        return list(ELEVENLABS_PUBLIC_MALE)
    if response.status_code != 200:
        raise VoiceError(
            f"ElevenLabs returned {response.status_code}: {response.text[:200]}")

    found = []
    for entry in response.json().get("voices", []):
        labels = entry.get("labels") or {}
        if male_only and labels.get("gender", "").lower() not in ("", "male"):
            continue
        detail = ", ".join(
            str(labels[key]) for key in ("accent", "age", "description")
            if labels.get(key))
        name = entry.get("name", "sin nombre")
        found.append((
            _spanishness(entry, labels),
            entry["voice_id"],
            f"{name} — {detail}" if detail else name,
        ))

    # Native Spanish first, and Spain's accent before Latin America's. The
    # audition is truncated to a handful of voices: on 2026-07-30 an account
    # with 23 voices had its only two Spanish ones sitting last in the list,
    # so a run that sent the first eight sent eight English ones. Autenia
    # publishes in Spanish only — the language is not a tie-break, it is the
    # first thing that matters.
    found.sort(key=lambda row: (-row[0], row[2]))
    return [(voice_id, description) for _rank, voice_id, description in found]


def _spanishness(entry: dict, labels: dict) -> int:
    """2 for a Spain-accented Spanish voice, 1 for any Spanish, 0 otherwise."""
    language = (labels.get("language")
                or (entry.get("fine_tuning") or {}).get("language") or "")
    accent = str(labels.get("accent", "")).lower()
    if not str(language).lower().startswith("es"):
        return 0
    return 2 if accent in ("peninsular", "spanish", "castilian", "spain") else 1


def _duration_s(path: str) -> float:
    with wave.open(path, "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())
