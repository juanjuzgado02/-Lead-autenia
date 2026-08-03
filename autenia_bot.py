#!/usr/bin/env python
"""Run Autenia's review bot, and optionally produce today's script.

    python autenia_bot.py listen     # answer the buttons until stopped
    python autenia_bot.py cycle      # find a topic and send its script
    python autenia_bot.py both       # run one cycle, then keep listening
    python autenia_bot.py voces      # send voice auditions to Telegram
    python autenia_bot.py voces elevenlabs   # ...from the other provider

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

# Windows consoles still default to cp1252, which cannot encode a single one of
# the emoji this bot prints — and the first thing it prints once publishing is
# armed is "⚠️ AUTENIA_PUBLISH_DRY_RUN is off". Without this the process dies on
# that line, so turning real publishing on is exactly what stops the bot from
# starting. `errors="replace"` rather than a hard UTF-8 switch: a terminal that
# cannot draw a character should show a box, never take the bot down.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


#: Puerto de loopback que se queda cogido mientras el bot escucha. No sirve
#: para hablar con él —nadie se conecta ahí, ni podría desde fuera de esta
#: máquina—: sirve para que el segundo arranque encuentre la puerta cogida.
#:
#: Dos bots con el mismo token se roban las actualizaciones entre sí. Telegram
#: entrega cada una a **un solo** ``getUpdates``, así que con dos procesos la
#: mitad de las pulsaciones se las lleva el que no toca y no pasa nada visible.
#: Pasó el 2 de agosto de 2026 y costó una hora entenderlo, porque por fuera se
#: ve como un bot que "a veces necesita que le des tres veces".
#:
#: Un socket y no un fichero de bloqueo porque el sistema lo suelta solo cuando
#: el proceso muere: un fichero sobrevive a un corte de luz y deja al bot sin
#: arrancar por un candado que ya no guarda nada.
CERROJO_PUERTO = 47821

_cerrojo = None


def tomar_cerrojo() -> bool:
    """Coger el sitio del bot, o decir que ya está cogido."""
    global _cerrojo  # noqa: PLW0603 - vive lo que vive el proceso
    import socket  # noqa: PLC0415

    intento = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        intento.bind(("127.0.0.1", CERROJO_PUERTO))
    except OSError:
        intento.close()
        return False
    _cerrojo = intento          # se suelta cuando termina el proceso
    return True


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


async def audition_voices(limit: int = 8, provider: str | None = None) -> None:
    """Read one line in every candidate voice and send them all to Telegram.

    The voice is Autenia's on every video it publishes, so it is chosen by ear,
    not from a catalogue. This makes that repeatable: run it whenever the
    provider changes or a new voice appears, listen, and put the winner in
    ``AUTENIA_VOICE_NAME``.
    """
    # Comparing the two providers is the point of an audition, and having to
    # edit .env between halves of a comparison is how the comparison stops
    # happening.
    provider = provider or autenia.voice_provider
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
    skipped: list[str] = []
    for index, (identifier, description) in enumerate(voices, 1):
        wav = os.path.join(workdir, f"{index:02d}.wav")
        try:
            await voice.synthesize(voice.AUDITION_TEXT, out_path=wav,
                                   provider=provider, name=identifier)
        except voice.VoiceError as exc:
            # Say which ones did not come, or the numbering has silent holes
            # and the one that matters most is the one that went missing:
            # ElevenLabs refuses library voices on a free plan, and those are
            # exactly the native Spanish ones.
            reason = ("hace falta plan de pago" if "payment_required" in str(exc)
                      else str(exc)[:80])
            skipped.append(f"<b>{index}.</b> {description} — {reason}")
            print(f"[voces] {index}. {description}: {exc}")
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

    if skipped:
        await telegram.send_message(
            "⚠️ Estas no se han podido probar:\n" + "\n".join(skipped))
    print(f"[voces] {len(voices) - len(skipped)} enviadas, {len(skipped)} saltadas. "
          f"Los archivos quedan en {workdir}")


async def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "listen"

    for warning in validate_startup(["editorial", "review"]):
        print(f"⚠️  {warning}")
    if not autenia.has("review"):
        print("Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en .env")
        return 1

    if command == "voces":
        await audition_voices(provider=sys.argv[2] if len(sys.argv) > 2 else None)
        return 0
    # Antes de gastar un céntimo: si ya hay un bot escuchando, este sobra y
    # además estorba, porque los dos se repartirían las pulsaciones al azar.
    if command in ("listen", "both") and not tomar_cerrojo():
        print("Ya hay un bot de Autenia escuchando en esta máquina.\n"
              "Dos a la vez se roban las pulsaciones entre sí, así que este no "
              "arranca.\n"
              "Cierra la otra ventana si quieres arrancar de cero.")
        return 1

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
