"""Pictures for scenes that Autenia's own library cannot cover.

A short made of typographic cards is honest and unwatchable. This module fills
the gap the library leaves, and the way it does so is shaped by three things
that go wrong with generated imagery:

* **Generated text is garbage.** Image models write plausible-looking gibberish
  on signs, screens and documents, and a viewer spots it instantly. Every
  prompt here forbids text, and the real words —the figure, the caption— are
  drawn afterwards by the renderer, sharp and correct.
* **A fake product screenshot is a lie.** The script often asks for "un cuadro
  de mando de Autenia mostrando el 7,7%". Generating an invented interface and
  passing it off as the product is the one thing that would embarrass Autenia
  most, so screen requests become the *context* instead: the desk, the office,
  the person, never the software. Real screenshots come from `data/library`.
* **The same scene must not be paid for twice.** Feedback on one scene
  regenerates that scene alone, so images are cached on disk by the hash of the
  prompt that made them.

Cost, measured 2026-07-30: 1.120 output tokens per image, about 0,03 € each, so
a five-scene short spends roughly 0,15 € — inside the per-video ceiling, but not
free, which is why the cache is not optional.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import re

from core_config import settings as autenia

from .gemini import GeminiError, request

#: Fast image model. The pro tier costs several times more for a background
#: that spends four seconds on screen behind a caption.
IMAGE_MODEL = "gemini-3.1-flash-image"

#: Roughly what one image costs, in cents, for the cost ledger.
IMAGE_COST_CENTS = 3

#: Where generated images live between runs. Deliberately outside the per-video
#: working directory: two videos asking for the same picture should pay once.
CACHE_DIR = os.environ.get("AUTENIA_IMAGE_CACHE", "data/cache/images")

#: Words that mean "show the product". A generated interface would be invented,
#: so these requests are answered with the human context around the screen.
_SCREEN_WORDS = (
    "pantalla", "captura", "cuadro de mando", "dashboard", "panel", "interfaz",
    "app", "aplicación", "aplicacion", "software", "gráfico", "grafico",
    "tabla", "informe", "hoja de cálculo", "hoja de calculo", "excel", "crm",
    "flujo de trabajo", "workflow", "chat", "whatsapp",
)

_STYLE = (
    "Fotografía editorial vertical 9:16, luz natural, profundidad de campo suave, "
    "tonos sobrios y fríos, aspecto documental español contemporáneo, realista, "
    "sin filtros llamativos."
)

#: Repeated in every prompt because it is the single biggest tell of a generated
#: image, and because the renderer draws the real words itself.
_BAN = (
    "SIN texto de ningún tipo, sin letras, sin números, sin carteles, sin "
    "logotipos, sin marcas de agua, sin interfaces de software legibles. "
    "Ningún rostro reconocible en primer plano."
)

_SCREEN_CONTEXT = (
    "Muestra el entorno humano alrededor del trabajo —el escritorio, los "
    "papeles, las manos, la oficina— y NO la pantalla del ordenador en detalle. "
    "Si aparece un monitor, que salga desenfocado o de refilón."
)


class ImageError(RuntimeError):
    """Image generation failed. The caller falls back to a typographic card."""


def _wants_a_screen(request_text: str) -> bool:
    lowered = request_text.lower()
    return any(word in lowered for word in _SCREEN_WORDS)


def build_prompt(visual_request: str, narration: str = "") -> str:
    """Turn a script's visual note into something an image model can shoot.

    The script writes for a human editor ("un cuadro de mando de Autenia con el
    7,7% de absentismo"). An image model given that sentence invents an
    interface and misspells the number on it. This rewrites the request as a
    scene, and hands the number back to the renderer to draw properly.
    """
    subject = " ".join((visual_request or narration or "").split())
    # Numbers belong in the overlay, not in the photograph.
    subject = re.sub(r"\d+[\d.,]*\s*%?", "", subject).strip(" ,.–-")

    parts = [_STYLE]
    if _wants_a_screen(visual_request or ""):
        parts.append(f"Escena: {subject or 'trabajo administrativo en una pequeña empresa'}.")
        parts.append(_SCREEN_CONTEXT)
    else:
        parts.append(f"Escena: {subject or 'una pequeña empresa española trabajando'}.")
    parts.append("Contexto: una pyme española, oficina real y modesta, no un rascacielos.")
    parts.append(_BAN)
    return " ".join(parts)


def cache_path(prompt: str) -> str:
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:20]
    return os.path.join(CACHE_DIR, f"{digest}.jpg")


async def generate(prompt: str, *, timeout: float = 180.0) -> str:
    """Return a path to an image for this prompt, generating it if needed."""
    path = cache_path(prompt)
    if os.path.isfile(path) and os.path.getsize(path) > 1024:
        return path

    autenia.require("editorial")
    payload = await request(
        f"models/{IMAGE_MODEL}:generateContent",
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"imageConfig": {"aspectRatio": "9:16"}},
        },
        timeout=timeout,
    )

    try:
        parts = payload["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError) as exc:
        raise ImageError("the image model returned no content") from exc

    for part in parts:
        blob = part.get("inlineData") or part.get("inline_data")
        if not blob or not blob.get("data"):
            continue
        raw = base64.b64decode(blob["data"])
        if len(raw) < 1024:
            continue
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # Write beside the target and move, so an interrupted run cannot leave a
        # half-written file that the cache would happily serve for ever.
        staging = f"{path}.part"
        with open(staging, "wb") as handle:
            handle.write(raw)
        os.replace(staging, path)
        return path

    raise ImageError("the image model returned no image")


async def for_scenes(requests: list[str], narrations: list[str] | None = None
                     ) -> list[str | None]:
    """One image per scene, concurrently. ``None`` where generation failed.

    A failure is not fatal: the renderer draws a typographic card instead. A
    short with one plain scene beats no short at all, and the failure is visible
    in the review before anything is published.
    """
    narrations = narrations or [""] * len(requests)
    prompts = [build_prompt(req, narration)
               for req, narration in zip(requests, narrations)]
    results = await asyncio.gather(
        *(generate(prompt) for prompt in prompts), return_exceptions=True)
    return [None if isinstance(r, BaseException) else r for r in results]


def cost_cents(paths: list[str | None], *, before: set[str] | None = None) -> int:
    """What this run actually spent: cache hits are free, new files are not."""
    if before is None:
        return sum(IMAGE_COST_CENTS for path in paths if path)
    return sum(IMAGE_COST_CENTS for path in paths if path and path not in before)
