#!/usr/bin/env python
"""Run Autenia's review bot, and optionally produce today's script.

    python autenia_bot.py listen     # answer the buttons until stopped
    python autenia_bot.py cycle      # find a topic and send its script
    python autenia_bot.py both       # run one cycle, then keep listening
    python autenia_bot.py voces      # send voice auditions to Telegram

Long polling, so this needs no domain, no certificate and no open ports.
"""

import asyncio
import os
import subprocess
import shutil
import sys
import tempfile

from core_config import settings as autenia
from core_config import validate_startup
from autenia import cycle, store, telegram, voice


async def run_cycle_once() -> None:
    await store.init_db()
    result = await cycle.run_cycle()
    print(f"[cycle] {result.outcome}: {result.detail}")

    if result.outcome == "en_revision":
        return
    # A skipped day is worth saying out loud: silence is indistinguishable from
    # a crashed scheduler.
    reasons = {
        "sin_candidatos": "🔍 Hoy no he encontrado nada que merezca un vídeo.",
        "bajo_umbral": "📉 Había candidatos, pero ninguno supera el listón.",
        "preflight": "⚠️ El guion no pasó el preflight, no se ha renderizado.",
        "ciclo_activo": "⏳ Ya hay un guion esperando tu revisión.",
    }
    message = reasons.get(result.outcome, f"Ciclo terminado: {result.outcome}")
    await telegram.send_message(f"{message}\n<i>{result.detail}</i>")


async def audition_voices(limit: int = 8) -> None:
    """Read one line in every candidate voice and send them all to Telegram.

    The voice is Autenia's on every video it publishes, so it is chosen by ear,
    not from a catalogue. This makes that repeatable: run it whenever the
    provider changes or a new voice appears, listen, and put the winner in
    ``AUTENIA_VOICE_NAME``.
    """
    provider = autenia.voice_provider
    voices = (await voice.catalogue(provider))[:limit]
    if not voices:
        await telegram.send_message(
            f"No hay voces que probar en <b>{provider}</b>.")
        return

    await telegram.send_message(
        f"🎙 <b>{len(voices)} voces de {provider}</b>. Escúchalas y dime el número.\n"
        f"<i>Todas leen el mismo texto, o compararías guiones y no voces.</i>")

    encoder = shutil.which("ffmpeg")
    workdir = tempfile.mkdtemp(prefix="autenia-voces-")
    for index, (identifier, description) in enumerate(voices, 1):
        wav = os.path.join(workdir, f"{index:02d}.wav")
        try:
            await voice.synthesize(voice.AUDITION_TEXT, out_path=wav,
                                   provider=provider, name=identifier)
        except voice.VoiceError as exc:
            print(f"[voces] {identifier}: {exc}")
            continue

        sendable = wav
        if encoder:
            # Telegram plays mp3 inline; a WAV arrives as a file to download.
            sendable = os.path.join(workdir, f"{index:02d}.mp3")
            subprocess.run([encoder, "-y", "-v", "error", "-i", wav,
                            "-b:a", "96k", sendable], check=False)
            if not os.path.isfile(sendable):
                sendable = wav

        await telegram.send_audio(
            sendable, caption=f"<b>{index}.</b> {description}",
            title=f"{index}. {description[:40]}")
        print(f"[voces] {index}. {description}")

    print(f"[voces] {len(voices)} enviadas. Los archivos quedan en {workdir}")


async def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "listen"

    for warning in validate_startup(["editorial", "review"]):
        print(f"⚠️  {warning}")
    if not autenia.has("review"):
        print("Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en .env")
        return 1

    if command == "voces":
        await audition_voices()
        return 0
    if command in ("cycle", "both"):
        await run_cycle_once()
    if command in ("listen", "both"):
        print("[bot] escuchando… (Ctrl+C para parar)")
        await cycle.listen()
    if command not in ("cycle", "listen", "both"):
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n[bot] parado")
