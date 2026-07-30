#!/usr/bin/env python
"""Run Autenia's review bot, and optionally produce today's script.

    python autenia_bot.py listen     # answer the buttons until stopped
    python autenia_bot.py cycle      # find a topic and send its script
    python autenia_bot.py both       # run one cycle, then keep listening

Long polling, so this needs no domain, no certificate and no open ports.
"""

import asyncio
import sys

from core_config import settings as autenia
from core_config import validate_startup
from autenia import cycle, store, telegram


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


async def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "listen"

    for warning in validate_startup(["editorial", "review"]):
        print(f"⚠️  {warning}")
    if not autenia.has("review"):
        print("Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en .env")
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
