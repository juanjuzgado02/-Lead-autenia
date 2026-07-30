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
ELEVENLABS_DEFAULT_VOICE = "EXAVITQu4vr4xnSDxMaL"

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
                     provider: str | None = None) -> Spoken:
    """Speak ``text`` into a mono WAV. Raises VoiceError on any failure."""
    if not text or not text.strip():
        raise VoiceError("nothing to speak: the narration is empty")

    chosen = provider or autenia.voice_provider
    if chosen == "gemini":
        path = await _gemini(text, out_path)
    elif chosen == "elevenlabs":
        path = await _elevenlabs(text, out_path)
    else:  # pragma: no cover - core_config validates this
        raise VoiceError(f"unknown voice provider {chosen!r}")

    return Spoken(
        path=path,
        duration_s=_duration_s(path),
        provider=chosen,
        estimated_cents=estimate_cents(text, chosen),
    )


async def _gemini(text: str, out_path: str) -> str:
    try:
        path, _usage = await gemini.synthesize(
            text, voice=autenia.voice_name or gemini.DEFAULT_VOICE,
            out_path=out_path,
        )
    except gemini.GeminiError as exc:
        raise VoiceError(str(exc)) from exc
    return path


async def _elevenlabs(text: str, out_path: str) -> str:
    autenia.require("voice")
    voice_id = autenia.voice_name or ELEVENLABS_DEFAULT_VOICE
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


def _duration_s(path: str) -> float:
    with wave.open(path, "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())
