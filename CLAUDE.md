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
python autenia_bot.py voces       # read one line in every voice, to choose by ear
python -m tools.muestras          # the same script cut five ways, plus a comparison reel
pytest tests/ -q                  # the whole suite: 301 tests, ~3.5 seconds
docker compose up --build         # the same bot, containerised
```

`ffmpeg` and `ffprobe` must be on PATH — they are system binaries, not Python
packages. The container installs them; a host run needs `apt install ffmpeg` or
`winget install ffmpeg`.

## Architecture

### The daily cycle

1. **Collect** (`autenia/sources.py`) — Gemini with Google grounding. Eighteen
   angles in two families (what AI now does for a small company, what the viewer
   already suffers); three are rotated in per run, one call each, and a day that
   finds nothing asks the remaining fifteen before giving up. Redirects are
   resolved to canonical URLs and checked against `autenia/urls.py` before being
   fetched.
2. **Filter and score** (`autenia/editorial.py`) — hard exclusions first, then a
   cheap score over eight signals whose weights sum to one: subject (how much
   this is an AI story minus how much it is an advert), customer pain, fit,
   hook, recency, evidence, visual potential, conversion. Below `MIN_SCORE`
   (0.55) the day stays blank.
3. **Write** (`autenia/gemini.py`) — a grounded script that keeps facts and
   opinions apart and carries its sources.
4. **Preflight** (`autenia/preflight.py`) — the last gate before spending:
   duration, claims (a fact must name its source *inside the narration*, not
   just in the `fuente` field), banned phrasing, estimated cost. Returns every
   problem at once; a failed version is kept in `descartado` as the record.
5. **Review** (`autenia/telegram.py`) — script, sources and cost to one chat,
   with `Aprobar`, `Pedir cambios`, `Rechazar`, `Regenerar`. Nothing renders
   before a human approves the words. `/guion <tema>` writes one on a subject
   the operator chooses instead of waiting for the news; with no sources behind
   it, that script may not state a figure at all.
6. **Render** (`autenia/render.py`) — `prepare()` buys everything (footage,
   photographs, one continuous narration cut at the real pauses), `compose()`
   assembles it. Backgrounds in strict order: Autenia's own footage
   (`autenia/assets.py`), generated footage (`autenia/clips.py`), a generated
   photograph (`autenia/images.py`), then typography. Stills get a slow push so
   they do not read as a slideshow. Captions are drawn with Pillow and
   overlaid. 1080×1920 H.264/AAC 30 fps, normalised to −14 LUFS.
7. **Second gate** (`revision_video`) — the finished video goes back to the
   chat with `Publicar` / `No publicar` / `Defectuoso`. Rendering never
   publishes on its own: approving a script is not the same as having seen what
   came out of it, and the platform is the one place with no undo.
8. **Repair** (`autenia/arreglo.py`) — `Defectuoso` asks what is wrong and buys
   only that. A video is three layers assembled at the end, and `plan.json`
   records which is which, so a stutter costs one narration and six fingers cost
   one scene's shot. A defect in the *words* is the one that cannot be repaired
   this way: those were approved, so it goes back to the script gate.
9. **Publish** (`autenia/publish.py`) — one Upload-Post call per network so a
   single refusal cannot hide two successes. `AUTENIA_REDES` picks which
   networks (Autenia posts to YouTube only; the rest are uploaded by hand from
   the video Telegram delivers). Dry run by default.

### Key files

| File | Purpose |
|------|---------|
| `core_config.py` | The one place configuration is resolved. Masks secrets, refuses to boot with `BILLING_ENABLED`. |
| `autenia_bot.py` | Entry point: `listen` (long polling) or `cycle` (one run). |
| `autenia/cycle.py` | The orchestration above, and the state transitions around it. |
| `autenia/states.py` | The state machine. Every move is legal or it raises. |
| `autenia/store.py` | SQLite via async SQLAlchemy. Deliberately separate from the upstream's Postgres. |
| `autenia/models.py` | The schema: contents, versions, cost entries. Money in whole cents. |
| `autenia/editorial.py` | Hard filters and scoring. |
| `autenia/sources.py` | Grounded search, canonical URLs, publisher attribution. |
| `autenia/gemini.py` | Model calls: judgement, script, TTS. |
| `autenia/voice.py` | Provider-agnostic narration — Gemini TTS by default (voice `Iapetus`, chosen by ear), ElevenLabs opt-in. Also listens to the take it bought and asks for another if it stutters. |
| `autenia/render.py` | Script → 9:16 master, ffmpeg only. Writes `plan.json` next to the video: what each scene bought. |
| `autenia/arreglo.py` | Which layer a reported defect belongs to, and buying only that one. |
| `autenia/formats.py` | How it is cut — cadence, captions, camera. Data, not code. |
| `autenia/clips.py` | Generated footage, 8 s a piece, cached and reused by meaning. |
| `autenia/assets.py` | Autenia's own footage library, what the operator sends into it, and coverage measurement. |
| `autenia/images.py` | Generated scene photographs, cached on disk. ~0,03 € each. |
| `autenia/publish.py` | Upload-Post, per network, dry-run by default. |
| `autenia/ffmpeg.py` | Encoder selection and −14 LUFS loudness normalisation. |
| `autenia/urls.py` | SSRF guard for URLs this machine fetches. |
| `tools/muestras.py` | Renders one script in every montage and glues them into one reel. Touches neither the database nor the networks. |

### Things that will bite

- **Backgrounds have three tiers and the order is not negotiable**: own
  footage, then a generated photograph, then type. Generated imagery keeps the
  short watchable while `data/library` fills up; it is not a substitute for real
  material, and a scene asking for "una pantalla de Autenia" is answered with the
  desk around the screen, never with an invented interface.
- **Eight seconds is Veo's ceiling, not a preference.** The API refuses 10 and
  12 outright ("between 4 and 8"), so more footage means *more clips*, never
  longer ones. Cost is linear in seconds, so nothing is saved by buying four
  short clips instead of two long ones — what a long clip buys is that the
  median scene (4,6 s, measured over the first four scripts) fits inside it
  without looping, and that its spare seconds run on into the next scene.
- **A cut inside a clip must advance the clip.** `Shot.offset` keeps moving
  while the framing jumps, so a fast montage reads as two cameras on one action.
  Restarting the clip at each cut would show the same seconds twice and pay for
  the privilege.
- **The montage is a variable, not a rewrite.** `formats.py` holds the presets;
  `AUTENIA_FORMATO` picks one. `render.prepare()` buys the footage, the
  photographs and the voice, and `render.compose()` assembles them — so five
  montages of the same script cost what one costs. Proposing a sixth is adding
  an entry to `PRESETS`, not touching the renderer.
- **A hook card takes its seconds from the hook, never adds them.** The voice is
  cut into segments before the picture is planned, so a shot that appears from
  nowhere slides every later caption out of sync with what is being said.
- **A synthesiser fails by returning a valid file.** Not an error, not a short
  WAV: a perfectly good take in which a word is said twice or half a sentence is
  missing. On 2026-08-02 a script reading "No se trata de despedir a nadie" was
  narrated as "no se trata de despedir a nadie, er a nadie", and nothing caught
  it — the text sent was right and the cut at real pauses was exact to the
  millisecond. So `voice.narracion()` transcribes the take, diffs it against
  what was sent, and buys another when it does not match. Extra words and two or
  more consecutive missing ones are defects; substitutions are not, they are how
  a transcriber fails, and enough of them mean the take is believed rather than
  the transcript. The gate absolves on doubt, like the visual reviewer: no key,
  no network or a nonsense transcript lets the take through.
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
- **`renderizando` is the only state that spends money**, and neither it nor
  `revision_video` is terminal. A version parked in either blocks the next cycle
  for ever, so every path out of them must reach `publicado`, `descartado` or
  `fallido` — including dry runs and including a publish that failed before it
  could be attempted. A repair is the one loop between them
  (`revision_video → renderizando → revision_video`) and it goes through the
  spending door on purpose: that is where the budget is checked and where a
  second tap on `Defectuoso` finds the door shut. A repair that fails goes back
  to `revision_video`, never to `fallido` — the video it was trying to improve
  was publishable, and trying to improve something is not how you lose it.
- **The cost ledger is written but not wired, and reading the code the other way
  round is the easy mistake.** `store.record_cost`, `settle_cost`,
  `month_spend_cents` and `assert_within_video_budget` all exist and are tested,
  but nothing in `cycle.py` or `render.py` calls them: `cost_entries` is empty,
  the monthly stop always sees zero, and preflight only ever gets the voice
  estimate (0 on Gemini) — never the images or the Veo clips. What actually
  caps spending today is `AUTENIA_MAX_NEW_CLIPS` plus the cache.
  `clips.new_cost_cents()` and `images.cost_cents()` exist to feed that ledger;
  connecting them is what turns the limits into limits.

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
- `AUTENIA_INTERNAL_MODE` (default on) — single-user private deployment.
  Declared, not enforced: the CORS and browser-key surfaces it used to guard
  were removed rather than closed. It survives so a malformed value fails at
  boot, and so anything new that would serve HTTP has a flag to consult.
- `AUTENIA_VOICE_PROVIDER`, `AUTENIA_VOICE_NAME` (`Iapetus`), `AUTENIA_TZ`
  (`Europe/Madrid`), `AUTENIA_DB_PATH`, `AUTENIA_WORK_DIR`,
  `AUTENIA_LIBRARY_DIR`, `AUTENIA_IMAGE_CACHE`, `AUTENIA_CLIP_CACHE`
- `AUTENIA_SUBTITULOS` (on) — off takes the captions off any of the five
  montages. A caption that runs ahead of what is being said is worse than none,
  so this is an escape hatch, not a preference
- `AUTENIA_REVISION_VOZ` (on), `AUTENIA_INTENTOS_VOZ` (3) — whether the take is
  listened to before it is cut, and how many are bought before the least bad one
  is kept. Off is for a montage test, where the words do not matter
- `FFMPEG_ENCODER` (`x264` | `nvenc` | `auto`), `AUDIO_NORMALIZE` (on) — the
  encoder and the −14 LUFS normalisation in `autenia/ffmpeg.py`
- `AUTENIA_VIDEO_CLIPS` — generated footage. **Off in the code, on in
  `.env.example`**: the default stays cheap, the checked-in template turns it on
  because the cache has already paid for itself.
- `AUTENIA_MAX_NEW_CLIPS` — the hard ceiling on new footage per video (3 in the
  code, 6 in `.env.example`); the format asks for what it needs under it
- `AUTENIA_FORMATO` — `continuo` | `rapido` | `kinetico` | `titular` |
  `marcado`. An unknown name falls back to the default rather than failing the
  cycle: losing a day's video to a typo in the montage would be the worst trade
  in the system
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
