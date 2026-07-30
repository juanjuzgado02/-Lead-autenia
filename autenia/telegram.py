"""The review conversation: one chat, one operator, four buttons.

Long polling rather than a webhook. A webhook needs a public domain with a
valid certificate, and making a domain a prerequisite for reviewing a script is
a self-inflicted blocker — polling works from a laptop behind NAT and from a
small VPS with no inbound ports open.

Two rules protect this surface:

* **One chat is authorised.** Anyone can find a bot's username and message it.
  Every update from any other chat is dropped without a reply, because replying
  confirms the bot exists and is worth probing.
* **Callbacks are idempotent and bound to a version.** Telegram redelivers, and
  a double tap on `Aprobar` must approve once. A callback naming a version that
  has already moved on is acknowledged and ignored.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx

from core_config import settings as autenia

API = "https://api.telegram.org"

#: How long a poll waits for something to happen. Telegram holds the connection
#: open, so this costs nothing and reacts instantly.
POLL_TIMEOUT_S = 25

#: Telegram rejects text over 4096 characters outright.
MAX_MESSAGE = 4096


class TelegramError(RuntimeError):
    """A Telegram call failed. Message is safe to log."""


@dataclass
class Action:
    """What the operator did, once it has been checked and attributed."""

    kind: str          # "aprobar" | "cambios" | "rechazar" | "regenerar" | "texto"
    version_id: str | None
    text: str = ""
    callback_id: str | None = None


def _base() -> str:
    autenia.require("review")
    return f"{API}/bot{autenia.telegram_bot_token}"


async def _call(method: str, payload: dict | None = None, *,
                timeout: float = 60.0) -> dict:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(f"{_base()}/{method}", json=payload or {})
    try:
        body = response.json()
    except ValueError as exc:
        raise TelegramError(f"{method} returned a non-JSON body") from exc
    if not body.get("ok"):
        # The token is in the URL, never in the description, so this is safe.
        raise TelegramError(f"{method} failed: {body.get('description')}")
    return body.get("result")


def _authorised(chat_id) -> bool:
    expected = (autenia.telegram_chat_id or "").strip()
    return bool(expected) and str(chat_id) == expected


# --------------------------------------------------------------------------
# Sending a script for review
# --------------------------------------------------------------------------

def _escape(text: str) -> str:
    """Escape for Telegram's HTML parse mode."""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def review_text(script: dict, *, candidate=None, version_number: int = 1,
                estimated_seconds: float = 0.0, estimated_cents: int = 0,
                coverage: float | None = None) -> str:
    """The script as the operator reads it before deciding.

    Shows the sources and the fact/opinion split, because those are what the
    operator is actually being asked to vouch for.
    """
    lines = [
        f"<b>{_escape(script.get('titulo', 'Sin título'))}</b>",
        f"<i>versión {version_number} · ~{estimated_seconds:.0f}s · "
        f"{estimated_cents / 100:.2f} €</i>",
        "",
        f"🎣 <b>{_escape(script.get('hook', ''))}</b>",
        "",
    ]

    for index, scene in enumerate(script.get("escenas", []), 1):
        mark = "📊" if scene.get("tipo") == "hecho" else "💬"
        lines.append(f"{mark} <b>{index}.</b> {_escape(scene.get('narracion', ''))}")
        lines.append(f"    <i>🎬 {_escape(scene.get('visual', ''))}</i>")
        if scene.get("fuente"):
            lines.append(f"    <i>📎 {_escape(scene['fuente'])}</i>")
        lines.append("")

    lines.append(f"👉 {_escape(script.get('cta', ''))}")

    if candidate is not None:
        lines += ["", f"<b>Fuente:</b> {_escape(candidate.publisher)}",
                  f"{_escape(candidate.url)}"]

    if coverage is not None:
        pct = f"{coverage:.0%}"
        note = "" if coverage >= 0.6 else "  ← más capturas mejorarían esto"
        lines += ["", f"<b>Material propio:</b> {pct} de las escenas{note}"]

    lines += ["", "<i>Responde con un texto para pedir cambios concretos.</i>"]

    text = "\n".join(lines)
    if len(text) > MAX_MESSAGE:
        text = text[:MAX_MESSAGE - 20].rsplit("\n", 1)[0] + "\n…"
    return text


def _keyboard(version_id: str) -> dict:
    """The four buttons, each carrying the version it refers to.

    The version travels in the callback data so a tap on an old message cannot
    approve the current script by accident.
    """
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Aprobar", "callback_data": f"aprobar:{version_id}"},
                {"text": "✏️ Cambios", "callback_data": f"cambios:{version_id}"},
            ],
            [
                {"text": "🔄 Regenerar", "callback_data": f"regenerar:{version_id}"},
                {"text": "🗑 Rechazar", "callback_data": f"rechazar:{version_id}"},
            ],
        ]
    }


async def send_review(script: dict, version_id: str, **kwargs) -> dict:
    """Put a script in front of the operator with its four buttons."""
    return await _call("sendMessage", {
        "chat_id": autenia.telegram_chat_id,
        "text": review_text(script, **kwargs),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": _keyboard(version_id),
    })


async def send_message(text: str) -> dict:
    """A plain notification: a blank day, a failure, a publication."""
    return await _call("sendMessage", {
        "chat_id": autenia.telegram_chat_id,
        "text": text[:MAX_MESSAGE],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })


async def send_video(path: str, caption: str = "") -> dict:
    """Send the finished video. Used after publishing, as a record."""
    autenia.require("review")
    with open(path, "rb") as handle:
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                f"{_base()}/sendVideo",
                data={"chat_id": autenia.telegram_chat_id,
                      "caption": caption[:1024], "parse_mode": "HTML"},
                files={"video": (path.rsplit("/", 1)[-1], handle, "video/mp4")},
            )
    body = response.json()
    if not body.get("ok"):
        raise TelegramError(f"sendVideo failed: {body.get('description')}")
    return body["result"]


async def answer_callback(callback_id: str, text: str = "") -> None:
    """Clear the button's spinner. Telegram retries the update until we do."""
    try:
        await _call("answerCallbackQuery",
                    {"callback_query_id": callback_id, "text": text[:200]})
    except TelegramError:
        # An expired callback id is not worth failing the whole action over —
        # the decision it carried has already been recorded.
        pass


async def clear_keyboard(chat_id, message_id) -> None:
    """Remove the buttons from a message that has been acted on.

    Without this the operator can tap `Aprobar` on yesterday's script and get a
    silent no-op, which reads as the bot being broken.
    """
    try:
        await _call("editMessageReplyMarkup",
                    {"chat_id": chat_id, "message_id": message_id,
                     "reply_markup": {"inline_keyboard": []}})
    except TelegramError:
        pass


# --------------------------------------------------------------------------
# Receiving decisions
# --------------------------------------------------------------------------

def parse_update(update: dict, *, awaiting: set[str] | None = None) -> Action | None:
    """Turn one raw update into an attributed Action, or None to ignore it.

    ``awaiting`` holds the version ids currently in review. Free text is only
    read as feedback when something is actually waiting — otherwise an idle
    "gracias" would be filed as instructions for the next render.
    """
    callback = update.get("callback_query")
    if callback:
        chat_id = callback.get("message", {}).get("chat", {}).get("id")
        if not _authorised(chat_id):
            return None
        data = callback.get("data", "")
        if ":" not in data:
            return None
        kind, version_id = data.split(":", 1)
        if kind not in ("aprobar", "cambios", "rechazar", "regenerar"):
            return None
        return Action(kind=kind, version_id=version_id,
                      callback_id=callback.get("id"))

    message = update.get("message") or update.get("edited_message")
    if not message:
        return None
    if not _authorised(message.get("chat", {}).get("id")):
        return None

    text = (message.get("text") or "").strip()
    if not text:
        return None

    waiting = awaiting or set()
    if not waiting:
        return None
    # With several scripts in review, the newest is the one being discussed.
    return Action(kind="texto", version_id=sorted(waiting)[-1], text=text)


async def poll(handler: Callable[[Action], Awaitable[None]], *,
               awaiting: Callable[[], set[str]],
               stop: asyncio.Event | None = None) -> None:
    """Long-poll until stopped, handing each authorised action to ``handler``.

    Offsets are advanced only after an update has been handled, so a crash
    mid-decision redelivers it rather than losing it.
    """
    autenia.require("review")
    offset = None
    halt = stop or asyncio.Event()

    while not halt.is_set():
        try:
            updates = await _call(
                "getUpdates",
                {"timeout": POLL_TIMEOUT_S,
                 **({"offset": offset} if offset is not None else {})},
                timeout=POLL_TIMEOUT_S + 15,
            )
        except (TelegramError, httpx.HTTPError):
            # Telegram hiccups and home connections drop. Back off and keep the
            # loop alive; the review queue is not lost, it is in the database.
            await asyncio.sleep(5)
            continue

        for update in updates or []:
            offset = update["update_id"] + 1
            action = parse_update(update, awaiting=awaiting())
            if action is None:
                continue
            try:
                await handler(action)
            except Exception as exc:  # noqa: BLE001 - one bad action must not
                # take the bot down; the operator would have no way to tell.
                await send_message(f"⚠️ Error procesando la acción: {exc}")


async def resolve_chat_id() -> list[dict]:
    """Chats that have messaged the bot. Used once, to fill TELEGRAM_CHAT_ID."""
    updates = await _call("getUpdates", {"timeout": 0})
    seen = {}
    for update in updates or []:
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat")
        if chat:
            seen[chat["id"]] = chat
    return list(seen.values())
