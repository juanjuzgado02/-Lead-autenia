"""Real moving footage, generated once and reused for ever.

Photographs with a camera move over them are not video, and after a week of
watching them Juan said so. Veo produces genuine footage, but it is billed by
the second: a 25-second short generated end to end costs several euros, every
day, which is an order of magnitude outside what this tool is allowed to spend.

The way out is that **Autenia's subjects repeat**. Paperwork, invoices, a small
office, hands sorting documents, someone on the phone — the same dozen scenes
carry most of what this channel will ever say. So a clip is generated at most
once, cached on disk by the hash of its prompt, and reused by every later video
that asks for something similar. The cost is front-loaded and then approaches
zero, instead of recurring daily.

Two guards keep that promise from failing quietly:

* **A hard ceiling on new clips per video** (:data:`MAX_NEW_PER_VIDEO`). A day
  whose scenes are all new must not silently spend a month's budget; it fills
  what it is allowed to and falls back to photographs for the rest.
* **Generation is off unless asked for.** The default stays on images, because
  they are twenty times cheaper and the difference only matters once there is a
  library worth drawing from.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time

import httpx

from core_config import settings as autenia

from . import revision
from .gemini import BASE, GeminiError, request

#: The cheapest Veo tier that still looks like footage. Verified 2026-07-30
#: against 3.1-fast: at 720x1280 behind a caption the difference is not worth
#: the price gap.
VIDEO_MODEL = "veo-3.1-lite-generate-preview"

#: Seconds per generated clip. Eight is Veo's ceiling — ten is refused — and it
#: is the right choice despite costing twice a four-second one: a clip shorter
#: than its scene is played on loop, and a visible loop looks worse than a
#: photograph. Measured 2026-07-30: scenes run 2.6 to 6.3 seconds.
CLIP_SECONDS = 8

#: Roughly what one eight-second clip costs, in cents. List price, not measured
#: on the account — treat the ledger figure as an upper bound until a real bill
#: confirms it.
CLIP_COST_CENTS = 80

#: How many *new* clips one video may pay for. Everything else comes from the
#: cache or falls back to a photograph.
MAX_NEW_PER_VIDEO = int(os.environ.get("AUTENIA_MAX_NEW_CLIPS", "3"))

#: Where generated footage lives between runs, like the image cache and for the
#: same reason: two videos asking for the same shot should pay once.
CACHE_DIR = os.environ.get("AUTENIA_CLIP_CACHE", "data/cache/clips")

#: Generation is slow. This is how long one clip may take before giving up and
#: letting the scene fall back to a photograph.
POLL_TIMEOUT_S = 240


class ClipError(RuntimeError):
    """Video generation failed. The caller falls back to a still."""


def enabled() -> bool:
    """Whether generated footage is switched on at all.

    Off by default: photographs cost about a twentieth as much, and real
    footage only earns its price once the cache has something in it.
    """
    return (os.environ.get("AUTENIA_VIDEO_CLIPS", "").strip().lower()
            in ("1", "true", "yes", "on", "si", "sí"))


def build_prompt(visual_request: str, narration: str = "") -> str:
    """A shot description Veo can film, from a script's visual note.

    Deliberately close to :func:`images.build_prompt` — same subject, same
    bans — plus the one thing a still does not need: what moves.
    """
    from . import images  # noqa: PLC0415 (circular at module scope)

    base = images.build_prompt(visual_request, narration)
    return (f"{base} Movimiento sutil y real: la cámara se desplaza muy despacio "
            f"y el sujeto hace un gesto natural y pequeño. Nada de zooms bruscos, "
            f"cortes ni movimientos de cámara imposibles. Plano continuo.")


def cache_path(prompt: str) -> str:
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:20]
    return os.path.join(CACHE_DIR, f"{digest}.mp4")


def cached(prompt: str) -> str | None:
    """The clip for this exact prompt if it is already paid for, else None."""
    path = cache_path(prompt)
    return path if os.path.isfile(path) and os.path.getsize(path) > 10_000 else None


def library() -> list:
    """Every clip generated so far, as matchable footage.

    This is what makes "pay once, reuse for ever" true rather than aspirational.
    Keying the cache on the prompt's hash only ever hits when two scenes ask for
    *identical* wording, which two different news stories essentially never do —
    the promise was empty until this existed.

    Matching by meaning reuses the same code path as Autenia's own material, so
    a generated clip about paperwork answers tomorrow's scene about paperwork.
    """
    from . import assets as asset_lib  # noqa: PLC0415 (circular at module scope)

    return asset_lib.load_library(CACHE_DIR)


def _remember(path: str, description: str) -> None:
    """Record what a clip shows, so it can be found by meaning later."""
    import json  # noqa: PLC0415

    sidecar = os.path.join(CACHE_DIR, "library.json")
    known: dict = {}
    if os.path.isfile(sidecar):
        try:
            with open(sidecar, encoding="utf-8") as handle:
                known = json.load(handle)
        except (json.JSONDecodeError, OSError):
            known = {}

    known[os.path.basename(path)] = {"description": description}
    tmp = f"{sidecar}.part"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(known, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, sidecar)


async def generate(prompt: str, *, seconds: int = CLIP_SECONDS,
                   description: str = "") -> str:
    """Film this shot, or return the copy already on disk."""
    hit = cached(prompt)
    if hit:
        return hit

    autenia.require("editorial")
    started = await request(
        f"models/{VIDEO_MODEL}:predictLongRunning",
        {
            "instances": [{"prompt": prompt}],
            "parameters": {"aspectRatio": "9:16", "durationSeconds": seconds},
        },
        timeout=120.0,
    )
    operation = started.get("name")
    if not operation:
        raise ClipError("Veo did not start an operation")

    uri = await _await_video(operation)
    path = await _download(uri, cache_path(prompt))

    # Revisado antes de entrar en la biblioteca, no antes de usarse: lo que se
    # cachea se reutiliza durante meses, así que un plano con una mano de más
    # que entre aquí sale en un vídeo de dentro de seis semanas.
    #
    # Sin reintento, al revés que las fotos: un clip cuesta veinte veces más, y
    # la escena tiene debajo una fotografía que sí se puede repetir barata.
    veredicto = await revision.revisar(path)
    if not veredicto.apto:
        await revision.descartar(path, str(veredicto))
        raise ClipError(f"el clip no pasa revisión: {veredicto}")

    _remember(path, description or prompt[:300])
    return path


async def _await_video(operation: str) -> str:
    """Poll until the clip is ready, and return where to fetch it."""
    deadline = time.monotonic() + POLL_TIMEOUT_S
    async with httpx.AsyncClient(timeout=120.0) as client:
        while time.monotonic() < deadline:
            response = await client.get(
                f"{BASE}/{operation}",
                params={"key": autenia.gemini_api_key})
            body = response.json()
            if not body.get("done"):
                await asyncio.sleep(8)
                continue
            if "error" in body:
                raise ClipError(f"Veo failed: {str(body['error'])[:200]}")
            try:
                samples = body["response"]["generateVideoResponse"]["generatedSamples"]
                return samples[0]["video"]["uri"]
            except (KeyError, IndexError) as exc:
                raise ClipError("Veo returned no video") from exc
    raise ClipError(f"Veo did not finish within {POLL_TIMEOUT_S}s")


async def _download(uri: str, path: str) -> str:
    """Fetch the finished clip.

    The key goes in a **header**, not the query string: with ``?key=`` the file
    endpoint answers 200 with an empty body, which lands a zero-byte mp4 in the
    cache and poisons it for every later run.
    """
    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.get(
            uri, headers={"x-goog-api-key": autenia.gemini_api_key},
            follow_redirects=True)
    if response.status_code != 200 or len(response.content) < 10_000:
        raise ClipError(
            f"download failed: {response.status_code}, "
            f"{len(response.content)} bytes")

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    staging = f"{path}.part"
    with open(staging, "wb") as handle:
        handle.write(response.content)
    os.replace(staging, path)
    return path


async def for_scenes(requests: list[str], narrations: list[str] | None = None,
                     *, budget: int = MAX_NEW_PER_VIDEO) -> list[str | None]:
    """A clip per scene where one is affordable, else ``None``.

    Three tiers, cheapest first: footage already generated that *means* the same
    thing, then the exact-prompt cache, then new film. Only the third is
    rationed, because it is the only one that costs anything.

    Scenes are filmed in order, so the hook — the shot that decides whether
    anyone watches the rest — gets first claim on the budget.
    """
    if not enabled():
        return [None] * len(requests)

    from . import assets as asset_lib  # noqa: PLC0415 (circular at module scope)

    narrations = narrations or [""] * len(requests)
    prompts = [build_prompt(req, narration)
               for req, narration in zip(requests, narrations)]

    # Reuse what has already been paid for, matched by meaning. plan_visuals
    # refuses a weak match and never repeats a shot inside one video, which is
    # exactly the behaviour wanted here too.
    reused = asset_lib.plan_visuals(list(requests), library())
    found: list[str | None] = [asset.path if asset else None for asset in reused]

    for index, prompt in enumerate(prompts):
        if found[index] is None:
            found[index] = cached(prompt)

    spent = 0
    for index, prompt in enumerate(prompts):
        if found[index] is not None or spent >= budget:
            continue
        try:
            found[index] = await generate(prompt, description=requests[index])
            spent += 1
        except (ClipError, GeminiError) as exc:
            # A missing clip is a scene that shows a photograph instead. That is
            # a worse shot, not a failed video.
            print(f"[clips] escena {index + 1}: {exc}")
    return found


def new_cost_cents(paths: list[str | None], before: set[str]) -> int:
    """What this run actually spent: reused footage is free."""
    return sum(CLIP_COST_CENTS for path in paths if path and path not in before)
