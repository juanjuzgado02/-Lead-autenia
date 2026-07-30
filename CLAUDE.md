# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Autenia Viral Shorts is the internal tool that produces Autenia's own social
media: one vertical Spanish short per day about AI and the problems it solves
for Spanish SMEs, reviewed by one person over Telegram, and published to TikTok,
Instagram Reels and YouTube Shorts only after that person approves it. It sells
[auteniaai.com](https://auteniaai.com/); it is not a product and has no customers.

**This repository began as a fork of OpenShorts**, a SaaS that cut long YouTube
videos into clips. Almost none of that survives, and the difference matters when
reading old commits: OpenShorts started from somebody else's finished video and
looked for the good bits. This tool starts from a news story and writes,
narrates and composes an original one. There is no ingest, no transcription, no
speaker tracking and no clipping, because there is no source video.

Anything the fork carried that only served the old product — the React SaaS
dashboard, the billing package, Whisper, YOLO, MediaPipe, PySceneDetect, yt-dlp,
the Remotion renderer, the FastAPI job queue — was **removed on 2026-07-30**, not
disabled. It is in git history if a decision needs revisiting; it is not in the
working tree, and it should not come back without a reason that names this
product.

## Development Commands

```bash
pip install -r requirements.txt   # small on purpose: httpx, sqlalchemy, Pillow
python autenia_bot.py listen      # the review bot (long polling)
python autenia_bot.py cycle       # run one daily cycle by hand
python autenia_bot.py both        # one cycle, then keep listening
pytest tests/ -q                  # the whole suite, ~3 seconds
docker compose up --build         # the same bot, containerised
```

`ffmpeg` and `ffprobe` must be on PATH — they are system binaries, not Python
packages. The container installs them; a host run needs `apt install ffmpeg` or
`winget install ffmpeg`.

## Architecture

### The daily cycle

1. **Collect** (`autenia/sources.py`) — Gemini with Google grounding, searching
   problem-shaped angles rather than technology names. Redirects are resolved to
   canonical URLs and checked against `autenia/urls.py` before being fetched.
2. **Filter and score** (`autenia/editorial.py`) — hard exclusions first, then a
   cheap score: recency, customer pain, fit with Autenia's services, hook
   strength, evidence, visual potential, conversion, cost.
3. **Write** (`autenia/gemini.py`) — a grounded script that keeps facts and
   opinions apart and carries its sources.
4. **Preflight** (`autenia/preflight.py`) — the last gate before spending:
   duration, claims, estimated cost, cached assets.
5. **Review** (`autenia/telegram.py`) — script, sources and cost to one chat,
   with `Aprobar`, `Pedir cambios`, `Rechazar`, `Regenerar`. Nothing renders
   before a human approves the words.
6. **Render** (`autenia/render.py`) — narration per segment via
   `autenia/voice.py`, then a background chosen in strict order: Autenia's own
   footage (`autenia/assets.py`), a generated photograph (`autenia/images.py`),
   or typography. Stills get a slow push so they do not read as a slideshow.
   Captions are drawn with Pillow and overlaid. 1080×1920 H.264/AAC 30 fps.
7. **Publish** (`autenia/publish.py`) — one Upload-Post call per network so a
   single refusal cannot hide two successes. Dry run by default.

### Key files

| File | Purpose |
|------|---------|
| `core_config.py` | The one place configuration is resolved. Masks secrets, refuses to boot with `BILLING_ENABLED`. |
| `autenia_bot.py` | Entry point: `listen` (long polling) or `cycle` (one run). |
| `autenia/cycle.py` | The orchestration above, and the state transitions around it. |
| `autenia/states.py` | The state machine. Every move is legal or it raises. |
| `autenia/store.py` | SQLite via async SQLAlchemy. Deliberately separate from the upstream's Postgres. |
| `autenia/editorial.py` | Hard filters and scoring. |
| `autenia/sources.py` | Grounded search, canonical URLs, publisher attribution. |
| `autenia/gemini.py` | Model calls: judgement, script, TTS. |
| `autenia/voice.py` | Provider-agnostic narration — Gemini TTS by default (voice `Iapetus`, chosen by ear), ElevenLabs opt-in. |
| `autenia/render.py` | Script → 9:16 master, ffmpeg only. |
| `autenia/assets.py` | Autenia's own footage library and coverage measurement. |
| `autenia/images.py` | Generated scene photographs, cached on disk. ~0,03 € each. |
| `autenia/publish.py` | Upload-Post, per network, dry-run by default. |
| `autenia/ffmpeg.py` | Encoder selection and −14 LUFS loudness normalisation. |
| `autenia/urls.py` | SSRF guard for URLs this machine fetches. |

### Things that will bite

- **Backgrounds have three tiers and the order is not negotiable**: own
  footage, then a generated photograph, then type. Generated imagery keeps the
  short watchable while `data/library` fills up; it is not a substitute for real
  material, and a scene asking for "una pantalla de Autenia" is answered with the
  desk around the screen, never with an invented interface.
- **Never let an image model write text.** Every prompt in `images.py` forbids
  letters, numbers and logos: generated lettering is gibberish and a viewer spots
  it instantly. The real words are drawn afterwards by Pillow.
- **Captions go through Pillow, not `drawtext`.** Two failure modes drove that:
  the filtergraph parser eats the escaping on Spanish text, so a wrapped
  caption rendered as "horas ennpapeleo", and `box=1` draws one rectangle per line, so a
  three-line caption comes out as a staircase.
- **Search angles point at the problem, not the technology.** If the system
  strings together empty days, review the angles before touching the threshold.
  Lowering the bar is exactly what the brief forbids: a day without a video is a
  valid outcome, filling the calendar is not.
- **`renderizando` is the only state that spends money**, and it is not terminal.
  A version parked there blocks the next cycle for ever, so every path out of it
  must reach `publicado` or `fallido` — including dry runs.

## Environment Variables

Everything is server-side. There is no browser, so there is no such thing as a
key the browser holds.

**Provider keys:**
- `GEMINI_API_KEY` — required: research, judgement, script and the default voice
- `ELEVENLABS_API_KEY` — only with `AUTENIA_VOICE_PROVIDER=elevenlabs`. Not
  needed: the default voice is a free Gemini one Juan chose by listening
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` — the review conversation
- `UPLOAD_POST_API_KEY`, `UPLOAD_POST_USER` — publishing; the profile names the
  account whose TikTok, Instagram and YouTube are connected
- `FAL_KEY` — avatar experiments only; the faceless default never calls it

**Behaviour:**
- `AUTENIA_PUBLISH_DRY_RUN` — **on unless explicitly set to a value that reads as
  false.** Alone among the booleans it does not raise on a malformed value, it
  stays safe: a crash loop from a typo gets "fixed" by deleting the line, and
  that is how an unapproved video reaches the company's accounts.
- `AUTENIA_INTERNAL_MODE` (default on) — single-user private deployment. Retained
  because it still guards CORS and key resolution, though the public surfaces it
  used to close no longer exist to be closed.
- `AUTENIA_VOICE_PROVIDER`, `AUTENIA_TZ` (`Europe/Madrid`), `AUTENIA_DB_PATH`,
  `AUTENIA_WORK_DIR`, `AUTENIA_LIBRARY_DIR`
- Limits: `AUTENIA_MAX_COST_PER_VIDEO` (0.50 €), `AUTENIA_MAX_COST_PER_MONTH`
  (25 €), `AUTENIA_MAX_DURATION_S` (55)

**Never set `BILLING_ENABLED`.** It belonged to the separately-licensed `cloud/`
package, which is gone; `core_config.validate_startup()` still refuses to boot
with it on, and that guard stays.

> Never log a secret. `core_config.mask()` renders one safely and
> `core_config.sanitize()` masks credentials in a payload before it is persisted
> or printed — use it on every provider request and response.

## Tech Stack

Python 3.11, httpx, SQLAlchemy + aiosqlite, Pillow, ffmpeg. Gemini for research,
script and voice; ElevenLabs optional; Upload-Post for distribution. Docker for
deployment. No frontend, no web server, no inbound ports.
