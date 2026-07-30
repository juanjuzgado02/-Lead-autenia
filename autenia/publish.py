"""Posting an approved video to TikTok, Instagram and YouTube.

Upload-Post fronts all three networks, so this module is thin on purpose: the
value it adds over a raw HTTP call is the three rules that keep a bad post from
being worse than no post.

* **One call per platform, never one call for three.** Upload-Post accepts a
  list of platforms in a single request and answers with a single status. That
  hides partial failure: two networks accepted and one refused reads exactly
  like everything worked. Posting one at a time costs a few extra seconds and
  buys an honest per-network verdict.
* **Dry run is the default and it never touches the network.** The switch lives
  in :mod:`core_config` and stays on for any unset or malformed value, so the
  cost of a typo is a simulated post, not a real one on the company account.
* **Nothing published is ever unapproved.** This module refuses to be handed a
  video without a caption and a title that a human has seen; the caller is
  responsible for only reaching here from the approved state.

Per-platform limits are enforced here rather than trusted to the vendor,
because a rejected upload after a paid render is the most expensive kind of
avoidable failure.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass, field

import httpx

from core_config import sanitize
from core_config import settings as autenia

API = "https://api.upload-post.com/api/upload"

#: The three destinations. Order is the order they are attempted in.
PLATFORMS = ("tiktok", "instagram", "youtube")

#: What each network truncates silently or rejects outright. YouTube's 100-char
#: title is the tight one; the rest are generous but not infinite.
TITLE_LIMIT = {"youtube": 100, "tiktok": 150, "instagram": 150}
BODY_LIMIT = {"youtube": 5000, "tiktok": 2200, "instagram": 2200}

#: Uploading a 1080x1920 master over a domestic connection is not fast, and a
#: timeout here means a video that may or may not have been accepted.
UPLOAD_TIMEOUT_S = 300.0


class PublishError(RuntimeError):
    """Publishing could not be attempted. The message is safe to log."""


@dataclass
class Result:
    """What one network did with one video."""

    platform: str
    ok: bool
    key: str                      # idempotency key for this version+platform
    external_id: str | None = None
    detail: str = ""
    dry_run: bool = False
    payload: dict = field(default_factory=dict)   # sanitised, safe to persist

    @property
    def summary(self) -> str:
        mark = "🧪" if self.dry_run else ("✅" if self.ok else "❌")
        name = {"tiktok": "TikTok", "instagram": "Instagram",
                "youtube": "YouTube"}.get(self.platform, self.platform)
        return f"{mark} {name}" + (f" — {self.detail}" if self.detail else "")


def key_for(version_id: str, platform: str) -> str:
    """Stable identifier for "this version, this network".

    Upload-Post has no idempotency header, so this is the value the caller
    persists to answer "did we already post this?" before calling again. A
    retry after a crash must be able to tell a lost response from a real
    second post.
    """
    digest = hashlib.sha256(f"{version_id}:{platform}".encode()).hexdigest()
    return f"{platform}-{digest[:16]}"


def _clip(text: str, limit: int) -> str:
    """Cut to the limit at a word boundary, so nothing ends mid-word.

    The ellipsis is part of the budget, not added on top of it — YouTube counts
    it, and a title one character over is rejected outright.
    """
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    room = limit - 1
    cut = text[:room].rsplit(" ", 1)[0]
    return (cut or text[:room]).rstrip(" ,.;:-") + "…"


def _payload(platform: str, *, user: str, title: str, body: str) -> dict:
    """The form fields for one network.

    Each network reads a different pair of keys, which is why this is a
    per-platform function rather than one dict with everything in it.
    """
    data = {
        "user": user,
        "title": _clip(title, TITLE_LIMIT.get(platform, 100)),
        "platform[]": platform,
        "async_upload": "true",
    }
    caption = _clip(body, BODY_LIMIT.get(platform, 2200))
    if platform == "tiktok":
        data["tiktok_title"] = caption
    elif platform == "instagram":
        data["instagram_title"] = caption
        data["media_type"] = "REELS"
    elif platform == "youtube":
        data["youtube_title"] = data["title"]
        data["youtube_description"] = caption
        data["privacyStatus"] = "public"
    return data


def _external_id(body: dict) -> str | None:
    """Dig the vendor's own identifier out of a response shape that varies."""
    for key in ("id", "job_id", "post_id", "upload_id"):
        value = body.get(key)
        if isinstance(value, (str, int)):
            return str(value)
    results = body.get("results")
    if isinstance(results, dict):
        for entry in results.values():
            if isinstance(entry, dict):
                found = _external_id(entry)
                if found:
                    return found
    return None


async def _post_one(client: httpx.AsyncClient, platform: str, *, user: str,
                    video: tuple[str, bytes], title: str, body: str,
                    version_id: str) -> Result:
    data = _payload(platform, user=user, title=title, body=body)
    key = key_for(version_id, platform)
    try:
        response = await client.post(
            API,
            headers={"Authorization": f"Apikey {autenia.upload_post_api_key}"},
            data=data,
            files={"video": (video[0], video[1], "video/mp4")},
        )
    except httpx.HTTPError as exc:
        # A timeout is genuinely ambiguous: the upload may have landed. Say so
        # rather than reporting a clean failure the operator would retry blind.
        return Result(platform, False, key, detail=f"sin respuesta ({exc.__class__.__name__})",
                      payload=sanitize(data))

    try:
        parsed = response.json()
    except ValueError:
        parsed = {"raw": response.text[:500]}

    if response.status_code not in (200, 201, 202):
        detail = parsed.get("error") or parsed.get("message") or f"HTTP {response.status_code}"
        return Result(platform, False, key, detail=str(detail)[:300],
                      payload=sanitize({"request": data, "response": parsed}))

    return Result(platform, True, key, external_id=_external_id(parsed),
                  payload=sanitize({"request": data, "response": parsed}))


async def publish(video_path: str, *, version_id: str, title: str, caption: str,
                  platforms: tuple[str, ...] = PLATFORMS) -> list[Result]:
    """Post one approved video to each network, independently.

    Returns one :class:`Result` per platform, in the order given. It never
    raises for a network that refused — that is a result, not an exception —
    and only raises when publishing could not be attempted at all.
    """
    unknown = [p for p in platforms if p not in PLATFORMS]
    if unknown:
        raise PublishError(f"unknown platform(s): {', '.join(unknown)}")
    if not os.path.isfile(video_path):
        raise PublishError(f"there is no video at {video_path}")
    if not title.strip() or not caption.strip():
        raise PublishError("refusing to post without a title and a caption")

    if autenia.publish_dry_run:
        # No client, no key lookup, no network. The payload is still built, so
        # a dry run proves the request would have been well formed.
        return [
            Result(platform, True, key_for(version_id, platform), dry_run=True,
                   detail="simulado, no se ha enviado nada",
                   payload=_payload(platform, user="(dry-run)", title=title,
                                    body=caption))
            for platform in platforms
        ]

    autenia.require("publish")
    user = autenia.upload_post_user
    if not user:
        raise PublishError(
            "UPLOAD_POST_USER is not set — it names the Upload-Post profile "
            "whose TikTok, Instagram and YouTube accounts are connected")

    with open(video_path, "rb") as handle:
        content = handle.read()
    video = (os.path.basename(video_path), content)

    results: list[Result] = []
    async with httpx.AsyncClient(timeout=UPLOAD_TIMEOUT_S) as client:
        for platform in platforms:
            results.append(await _post_one(
                client, platform, user=user, video=video, title=title,
                body=caption, version_id=version_id))
            # Uploading the same file three times back to back is the kind of
            # burst that rate limiters notice.
            if platform != platforms[-1]:
                await asyncio.sleep(2)
    return results
