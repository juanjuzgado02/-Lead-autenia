"""Turning a script into a 9:16 video.

The narration is synthesised **one segment at a time** rather than as a single
take. That costs nothing extra and buys two things: exact per-scene durations,
so the visuals cut where the sentence ends instead of where an estimate guessed;
and reuse, so feedback on one scene re-synthesises that scene alone.

Where a scene has no matching footage the background is a typographic card.
That is a deliberate fallback, not a placeholder: showing an unrelated clip, or
a mock-up of a product screen that does not exist, would both be worse than
honest text.

How it is cut — how often the picture changes, how the words arrive, how far the
camera drifts — is not decided here. That lives in :mod:`autenia.formats`, so
the same script, the same voice and the same footage can be assembled three ways
and compared without paying for any of it twice.

Output is 1080x1920, H.264, AAC, 30 fps — the master that Instagram, TikTok and
YouTube all take.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import textwrap
from dataclasses import dataclass, field

from . import assets as asset_lib
from . import ffmpeg as ff
from . import clips, formats, images, voice
from .formats import Format

WIDTH, HEIGHT, FPS = 1080, 1920, 30

#: Longest a single shot stays on screen when no format says otherwise. Anything
#: above about four seconds on one still reads as a slideshow; the feed is full
#: of edits that cut every two.
MAX_SHOT_S = 3.5

#: Vertical safe area. Platform chrome (captions, buttons, the profile row) eats
#: the top and bottom of the frame; text outside this band gets covered.
SAFE_TOP = 320
SAFE_BOTTOM = 480

#: Autenia's palette. Dark, so light-on-dark text stays legible over footage.
BG = "#0E1116"
INK = "#F5F7FA"
ACCENT = "#4F8DF7"

FONT_DISPLAY = "fonts/Anton-Regular.ttf"
FONT_BODY = "fonts/NotoSerif-Bold.ttf"


class RenderError(RuntimeError):
    """Composition failed. Message is safe to log."""


@dataclass
class Shot:
    """Un plano: qué se ve, cuánto dura y desde dónde se mira.

    ``offset`` es el punto del metraje donde entra este plano, así que dos
    planos seguidos sobre el mismo clip avanzan en el tiempo en vez de repetir
    los mismos fotogramas. ``punch`` es lo que hace que el corte se note: el
    encuadre salta un poco más cerca, como si hubiera una segunda cámara.
    """

    background: str | None
    seconds: float
    offset: float = 0.0
    punch: float = 0.0
    index: int = 0


@dataclass
class Segment:
    """One spoken beat: hook, a scene, or the CTA."""

    kind: str            # "hook" | "escena" | "cta"
    text: str            # what is said, and what the subtitle shows
    visual_request: str  # what the script asked to see
    audio_path: str = ""
    duration_s: float = 0.0
    asset_path: str | None = None    # Autenia's own footage, always preferred
    clip_path: str | None = None     # generated footage, reused across videos
    clip_offset: float = 0.0         # where in that clip this scene starts
    image_paths: list[str] = field(default_factory=list)  # generated stand-ins

    @property
    def background(self) -> str | None:
        """What this scene opens on, best available first.

        Own material, then generated footage, then a photograph. Real material
        wins because it is the format's whole point; footage beats a still
        because a still is what Juan kept calling "fotos pasándose".
        """
        return (self.asset_path or self.clip_path
                or (self.image_paths[0] if self.image_paths else None))

    @property
    def image_path(self) -> str | None:
        """Kept for callers that only care whether anything was generated."""
        return self.image_paths[0] if self.image_paths else None

    def shots(self, fmt: Format | None = None) -> list[Shot]:
        """This segment cut into shots, the way this format cuts.

        A scene lasting six seconds on one photograph is a slideshow however
        far the camera creeps. Splitting it across the framings generated for
        it — a wide, then a detail — is what makes it read as edited footage,
        and it costs one extra image rather than a second of Veo.

        Footage is cut too where the format asks for it, and that is not the
        same operation: the clip keeps *running* across the cut, only the
        framing jumps. Nothing is repeated and no second is paid for twice; what
        changes is that the viewer gets an edit instead of one long take.
        """
        fmt = fmt or formats.DEFAULT
        duration = max(0.8, self.duration_s)
        source = self.asset_path or self.clip_path

        # The hook written out before anything is shown. It eats the first
        # seconds of its own scene rather than adding any, so the voice stays
        # exactly where it was — and if the sentence cannot hold a full screen
        # of type on its own, that shows up here rather than in the analytics.
        if fmt.hook_card > 0 and self.kind == "hook" and duration > 1.6:
            held = min(fmt.hook_card, duration / 2)
            # The remainder is asked for its shots as an ordinary scene, which
            # is also what stops this from recursing into itself for ever.
            rest = Segment(kind="escena", text=self.text,
                           visual_request=self.visual_request,
                           duration_s=duration - held,
                           asset_path=self.asset_path, clip_path=self.clip_path,
                           clip_offset=self.clip_offset,
                           image_paths=self.image_paths)
            return [Shot(None, held), *rest.shots(fmt)]

        if source:
            if not fmt.cut_footage:
                return [Shot(source, duration, self.clip_offset)]
            moving = os.path.splitext(source)[1].lower() in asset_lib.VIDEO_SUFFIXES
            count = _shot_count(duration, fmt.max_shot_s)
            each = duration / count
            return [
                Shot(source, each,
                     self.clip_offset + (index * each if moving else 0.0),
                     fmt.punch * (index % 2), index)
                for index in range(count)
            ]

        if not self.image_paths:
            return [Shot(None, duration)]

        count = _shot_count(duration, fmt.max_shot_s)
        each = duration / count
        # More cuts than framings means coming back to one, which is fine as
        # long as it comes back at a different distance — that reads as a second
        # angle rather than as the same photograph shown twice.
        return [Shot(self.image_paths[index % len(self.image_paths)], each,
                     0.0, fmt.punch * (index % 2), index)
                for index in range(count)]


@dataclass
class Rendered:
    path: str
    duration_s: float
    coverage: float
    segments: list[Segment]


def segments_of(script: dict) -> list[Segment]:
    """Split a script into the beats that get spoken and shown separately."""
    beats: list[Segment] = []
    hook = (script.get("hook") or "").strip()
    if hook:
        beats.append(Segment(kind="hook", text=hook,
                             visual_request=script.get("hook_visual", hook)))
    for scene in script.get("escenas", []):
        text = (scene.get("narracion") or "").strip()
        if not text:
            continue
        beats.append(Segment(kind="escena", text=text,
                             visual_request=(scene.get("visual") or "").strip()))
    cta = (script.get("cta") or "").strip()
    if cta:
        beats.append(Segment(kind="cta", text=cta, visual_request=cta))
    return beats


def _stretch_clips(segments: list[Segment]) -> None:
    """Let a clip keep running into the scenes that follow it.

    Footage is billed by the second, and a scene lasting three seconds was
    being given a whole eight-second clip: measured on a real 22-second short,
    40 of the 44 paid-for seconds were shown and **44% was thrown away**.

    So a clip now plays on past its own scene, at the right offset, until it
    runs out. Same seconds bought, nearly twice as much video shown — and it
    reads better too, because a continuous shot under two sentences looks like
    an edit rather than a slideshow of stock.

    Durations are not known until narration, so this runs on the estimate and
    keeps a margin: overrunning the clip would loop it, which is the artefact
    the eight-second length was chosen to avoid.
    """
    budget: dict[str, float] = {}
    for segment in segments:
        if segment.asset_path:
            continue
        if segment.clip_path:
            # A scene with its own clip starts it from the top.
            segment.clip_offset = 0.0
            budget[segment.clip_path] = _estimated_seconds(segment)
            continue

        # No clip of its own: continue whichever one still has footage left.
        for path, used in budget.items():
            spare = clips.CLIP_SECONDS - used
            if spare >= _estimated_seconds(segment) + 0.2:
                segment.clip_path = path
                segment.clip_offset = used
                budget[path] = used + _estimated_seconds(segment)
                break


def _estimated_seconds(segment: Segment) -> float:
    """Length from the word count, since the voice has not spoken yet."""
    if segment.duration_s:
        return segment.duration_s
    return max(0.8, len(re.findall(r"\S+", segment.text)) / 2.6)


def _shot_count(duration: float, max_shot_s: float) -> int:
    """How many cuts fit in this many seconds.

    The tolerance stops a 2,1-second scene from being cut in two under a
    2-second rule: the second half would be a flash, and a flash is a mistake,
    not a rhythm.
    """
    return max(1, math.ceil(duration / max(0.5, max_shot_s) - 0.2))


def _shots_wanted(text: str, fmt: Format | None = None) -> int:
    """How many framings a line of narration is worth.

    From words rather than seconds, because this runs before the voice does.
    Capped at four: past that the cutting starts to fight the sentence, and the
    fast formats reuse framings rather than buying more.
    """
    fmt = fmt or formats.DEFAULT
    seconds = len(re.findall(r"\S+", text)) / 2.6
    return max(1, min(4, _shot_count(seconds, fmt.max_shot_s)))


def _run(args: list[str]) -> None:
    """Run ffmpeg, raising with its own diagnosis rather than a bare exit code."""
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-6:])
        raise RenderError(f"ffmpeg failed: {tail}")


def _ffmpeg() -> str:
    binary = shutil.which("ffmpeg")
    if not binary:
        raise RenderError(
            "ffmpeg is not on PATH. The render runs inside the container image, "
            "which ships it; a bare host checkout does not."
        )
    return binary


# --------------------------------------------------------------------------
# Typographic card
# --------------------------------------------------------------------------

def _wrap(text: str, width: int) -> str:
    return "\n".join(textwrap.wrap(text, width=width)) or text


def card(text: str, out_path: str, *, kind: str = "escena") -> str:
    """A background of type on brand colour, for scenes with no footage.

    Rendered with Pillow rather than ffmpeg's drawtext: wrapping long Spanish
    sentences legibly needs measurement, and drawtext cannot measure.
    """
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415 (optional dep)

    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)

    size = 96 if kind == "hook" else 76
    font_path = FONT_DISPLAY if kind == "hook" else FONT_BODY
    try:
        font = ImageFont.truetype(font_path, size)
    except OSError:
        font = ImageFont.load_default()

    # Narrower wrap for the bigger hook type, or it overruns the frame. Both
    # values leave a side margin: type touching the edge gets cropped by some
    # players and reads as amateur on the ones that keep it.
    wrapped = _wrap(text, 16 if kind == "hook" else 21)
    colour = ACCENT if kind == "cta" else INK

    box = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=18)
    x = (WIDTH - (box[2] - box[0])) / 2
    usable_top, usable_bottom = SAFE_TOP, HEIGHT - SAFE_BOTTOM
    y = usable_top + ((usable_bottom - usable_top) - (box[3] - box[1])) / 2

    draw.multiline_text((x, y), wrapped, font=font, fill=colour,
                        spacing=18, align="center")
    image.save(out_path)
    return out_path


# --------------------------------------------------------------------------
# Per-segment clip
# --------------------------------------------------------------------------

def _escape(text: str) -> str:
    """Escape for ffmpeg's drawtext, which treats several characters specially."""
    return (text.replace("\\", "\\\\").replace(":", "\\:")
                .replace("'", "’").replace("%", "\\%"))


#: Caption geometry. The plate is one rounded rectangle behind the whole block.
#:
#: Toned down on 2026-07-30: at 56px with a heavy plate the caption was the
#: loudest thing on screen, and the picture — the part that took the work — sat
#: behind it. Smaller type, a lighter plate and a lower seat put the words back
#: where they belong: readable, and second.
CAPTION_SIZE = 44
CAPTION_PAD = 20
CAPTION_LEADING = 10

#: How far the caption sits above the bottom edge. Lower than the safe area's
#: full inset: platform chrome eats the very bottom, but not 200px of it, and
#: captions parked high in frame read as a slideshow subtitle rather than a
#: short's burned-in text.
CAPTION_BOTTOM = 210

#: Plate opacity. Enough to hold white type over a bright photograph, little
#: enough to see the photograph through it.
CAPTION_PLATE_ALPHA = 115


def _caption_chunks(text: str, duration: float,
                    fmt: Format) -> list[tuple[str, float, float]]:
    """The caption split into what appears at once, as ``(text, start, end)``.

    A whole sentence sitting still for five seconds is read once and then
    ignored. Three or four words at a time land with the voice, which is why
    every short on the feed does it — and it is free, because the timing comes
    from word counts inside a segment whose real length is already known.
    """
    if fmt.caption == "placa":
        return [(text, 0.0, duration)]

    words = re.findall(r"\S+", text)
    if not words:
        return []

    # Break where the sentence breaks. A group that ends on a comma lands with
    # the pause in the voice; one that ends mid-clause —"el 90% de los"— reads
    # like a badly torn page, and the eye stops to repair it instead of moving on.
    size = max(1, fmt.caption_words)
    groups: list[list[str]] = [[]]
    for word in words:
        groups[-1].append(word)
        closes = word.endswith((",", ".", ";", ":", "?", "!", "…"))
        if len(groups[-1]) >= size or (closes and len(groups[-1]) >= size - 1):
            groups.append([])
    if not groups[-1]:
        groups.pop()

    # A last group of one word reads as a typo. Fold it into the one before.
    if len(groups) > 1 and len(groups[-1]) == 1:
        groups[-2].extend(groups.pop())

    total = len(words)
    chunks: list[tuple[str, float, float]] = []
    start = 0.0
    for index, group in enumerate(groups):
        end = (duration if index == len(groups) - 1
               else start + duration * len(group) / total)
        chunks.append((" ".join(group), start, end))
        start = end
    return chunks


def caption_png(text: str, out_path: str,
                fmt: Format | None = None) -> tuple[str, int]:
    """The caption as a transparent PNG, and its height.

    Drawn with Pillow rather than ffmpeg's ``drawtext``, after two rounds of
    fighting that filter:

    * escaping a Spanish sentence into a filtergraph means fighting three
      parsers — the caption rendered "horas en\\npapeleo" as "horas ennpapeleo"
      because the graph parser ate the backslash, and colons, apostrophes and
      percent signs each break the line their own way;
    * ``box=1`` draws one rectangle **per line**, so a three-line caption comes
      out as a staircase of separate bands instead of a plate.

    Pillow measures text, so it can centre lines against each other and put a
    single rounded plate behind all of them.
    """
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415 (optional dep)

    fmt = fmt or formats.DEFAULT
    # Big type with no plate needs a heavier face and an outline instead: over a
    # bright photograph, white serif at 86px still disappears without one.
    display = fmt.caption == "kinetico"
    size = fmt.caption_size
    alpha = fmt.plate_alpha
    stroke = max(0, round(size / 12)) if display else 0

    try:
        font = ImageFont.truetype(FONT_DISPLAY if display else FONT_BODY, size)
    except OSError:
        font = ImageFont.load_default()

    lines = _wrap(text, fmt.caption_width).split("\n")
    ruler = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    widths, heights = [], []
    for line in lines:
        box = ruler.textbbox((0, 0), line, font=font, stroke_width=stroke)
        widths.append(box[2] - box[0])
        heights.append(box[3] - box[1])

    line_h = max(heights) if heights else size
    text_w = max(widths) if widths else 0
    block_h = line_h * len(lines) + CAPTION_LEADING * (len(lines) - 1)

    pad = CAPTION_PAD + stroke
    plate_w = min(WIDTH - 60, text_w + pad * 2)
    plate_h = block_h + pad * 2

    image = Image.new("RGBA", (plate_w, plate_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if alpha > 0:
        draw.rounded_rectangle((0, 0, plate_w - 1, plate_h - 1), radius=14,
                               fill=(0, 0, 0, alpha))

    space = ruler.textlength(" ", font=font)
    y = pad
    for line, width in zip(lines, widths):
        # Word by word rather than line by line, so a figure can be picked out
        # in the brand colour. Everything else about the layout is unchanged:
        # the line was already measured and centred as a whole.
        x = (plate_w - width) / 2
        for word in line.split(" "):
            colour = ACCENT if (fmt.emphasis and _is_figure(word)) else INK
            draw.text((x, y), word, font=font, fill=colour, anchor="la",
                      stroke_width=stroke, stroke_fill=(0, 0, 0, 220))
            x += ruler.textlength(word, font=font) + space
        y += line_h + CAPTION_LEADING

    image.save(out_path)
    return out_path, plate_h


#: What counts as a figure worth colouring: a number, a percentage, an amount.
#: Deliberately narrow — colour everything and nothing is emphasised.
_FIGURE = re.compile(r"\d|%|€")


def _is_figure(word: str) -> bool:
    return bool(_FIGURE.search(word))


def brand_png(text: str, out_path: str) -> str:
    """The domain, small, for the corner of every frame.

    Nobody remembers a good video from an account they cannot name, and the site
    is the only thing this channel sells.
    """
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415 (optional dep)

    try:
        font = ImageFont.truetype(FONT_BODY, 34)
    except OSError:
        font = ImageFont.load_default()

    ruler = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    box = ruler.textbbox((0, 0), text, font=font, stroke_width=3)
    image = Image.new("RGBA", (box[2] - box[0] + 24, box[3] - box[1] + 20),
                      (0, 0, 0, 0))
    ImageDraw.Draw(image).text(
        (12, 10), text, font=font, fill=INK, anchor="la",
        stroke_width=3, stroke_fill=(0, 0, 0, 200))
    image.save(out_path)
    return out_path


#: How much a still drifts across its scene. Enough to read as alive, little
#: enough not to look like a screensaver: 8% over four seconds is roughly a
#: slow push-in on a tripod.
KEN_BURNS_ZOOM = 0.08


def _ken_burns(duration: float, index: int, *,
               zoom: float = KEN_BURNS_ZOOM, punch: float = 0.0) -> str:
    """Slow push on a still, alternating direction from scene to scene.

    A photograph held perfectly still for four seconds reads as a slideshow, and
    a slideshow reads as something nobody bothered to edit. The image is scaled
    to double size first because zoompan samples the *input* frame: zooming a
    1080-wide source produces visible stepping as it crosses pixel boundaries.

    Direction alternates so that four scenes in a row do not all creep the same
    way, which is its own kind of monotony.
    """
    frames = max(2, int(round(duration * FPS)))
    # `punch` lifts the whole move closer: the drift is the same, but the shot
    # starts tighter, so cutting back to the same photograph reads as an angle.
    base = 1 + punch
    if index % 2 == 0:
        zoom = f"{base}+{zoom}*on/{frames}"                # push in
    else:
        zoom = f"{base + zoom}-{zoom}*on/{frames}"         # pull out
    return (
        f"scale={WIDTH * 2}:{HEIGHT * 2}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH * 2}:{HEIGHT * 2},"
        f"zoompan=z='{zoom}':d={frames}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s={WIDTH}x{HEIGHT}:fps={FPS}"
    )


def _furniture(fmt: Format, workdir: str, elapsed: float,
               total: float) -> tuple[list[str], str]:
    """The bits that sit on every frame: the progress bar and the domain.

    Returned as ffmpeg arguments and a filter fragment because both have to be
    threaded through a graph that is built per shot, while what they show
    depends on where that shot sits in the whole video.
    """
    args: list[str] = []
    chain = ""

    if fmt.progress and total > 0:
        # `t` restarts at zero in every shot, so the bar is told where its shot
        # begins. Without that it would refill from empty at every cut.
        chain += (
            f",drawbox=x=0:y=ih-8:h=8:"
            f"w='iw*min(1,({elapsed:.3f}+t)/{total:.3f})':"
            f"color={ACCENT.lstrip('#')}@0.95:t=fill")

    if fmt.brand:
        args += ["-i", brand_png(fmt.brand, os.path.join(workdir, "marca.png"))]

    return args, chain


def _shot_clip(segment: Segment, shot: Shot, out_path: str, workdir: str,
               number: int, *, fmt: Format,
               captions: list[tuple[str, float, float]],
               elapsed: float = 0.0, total: float = 0.0) -> str:
    """One shot as a silent video of exactly its share of the narration."""
    ffmpeg = _ffmpeg()
    duration = max(0.4, shot.seconds)
    background = shot.background

    if background and os.path.isfile(background):
        moving = os.path.splitext(background)[1].lower() in asset_lib.VIDEO_SUFFIXES
        if moving:
            # Cover the frame and crop, never letterbox: black bars on a
            # vertical feed read as a reposted landscape video. `punch` crops
            # tighter, which is what turns a cut inside one take into an edit.
            reach = 1 + shot.punch
            motion = (f"scale={int(WIDTH * reach)}:{int(HEIGHT * reach)}:"
                      f"force_original_aspect_ratio=increase,"
                      f"crop={WIDTH}:{HEIGHT},setpts=PTS-STARTPTS")
            # -ss before -i so a shot continuing a clip picks up where the
            # previous one left off instead of restarting it, which would read
            # as a jump cut back to a shot the viewer just saw.
            args = [ffmpeg, "-y", "-stream_loop", "-1"]
            if shot.offset > 0.01:
                args += ["-ss", f"{shot.offset:.3f}"]
            args += ["-t", f"{duration:.3f}", "-i", background]
        else:
            motion = _ken_burns(duration, shot.index, zoom=fmt.zoom,
                                punch=shot.punch)
            args = [ffmpeg, "-y", "-loop", "1", "-t", f"{duration:.3f}",
                    "-i", background]

        # A photograph needs the words on top of it; a type card already is them.
        graph = [f"[0:v]{motion},fps={FPS},format=yuv420p[bg0]"]
        stage = "bg0"
        slot = 0
        for text, start, end in captions:
            plate, plate_h = caption_png(
                text, os.path.join(workdir, f"cap{number:02d}_{slot}.png"),
                fmt=fmt)
            args += ["-i", plate]
            slot += 1
            graph.append(
                f"[{stage}][{slot}:v]overlay=(W-w)/2:"
                f"{HEIGHT - fmt.caption_bottom - plate_h}:format=auto:"
                f"enable='between(t,{start:.3f},{end:.3f})'[v{slot}]")
            stage = f"v{slot}"

        extra, bar = _furniture(fmt, workdir, elapsed, total)
        if extra:
            args += extra
            slot += 1
            graph.append(f"[{stage}][{slot}:v]overlay=48:{SAFE_TOP - 120}:"
                         f"format=auto[v{slot}]")
            stage = f"v{slot}"

        graph.append(f"[{stage}]format=yuv420p{bar}[v]")
        args += ["-filter_complex", ";".join(graph), "-map", "[v]"]
    else:
        source = card(segment.text, os.path.join(workdir, f"card{number:02d}.png"),
                      kind=segment.kind)
        args = [ffmpeg, "-y", "-loop", "1", "-t", f"{duration:.3f}", "-i", source]
        _, bar = _furniture(fmt, workdir, elapsed, total)
        args += ["-vf", f"scale={WIDTH}:{HEIGHT},fps={FPS},format=yuv420p{bar}"]

    args += ["-an", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
             "-t", f"{duration:.3f}", out_path]
    _run(args)
    return out_path


# --------------------------------------------------------------------------
# Composition
# --------------------------------------------------------------------------

def _silences(path: str, *, threshold_db: int = -34,
              min_len_s: float = 0.22) -> list[tuple[float, float]]:
    """Where the speaker pauses, as (start, end) pairs.

    Used to find the real sentence boundaries in a continuous take. ffmpeg's
    silencedetect writes them to stderr as log lines; there is no structured
    output to ask for.
    """
    result = subprocess.run(
        [_ffmpeg(), "-i", path, "-af",
         f"silencedetect=noise={threshold_db}dB:d={min_len_s}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    starts, spans = [], []
    for line in result.stderr.splitlines():
        if "silence_start:" in line:
            starts.append(float(line.split("silence_start:")[1].strip()))
        elif "silence_end:" in line and starts:
            end = float(line.split("silence_end:")[1].split("|")[0].strip())
            spans.append((starts.pop(), end))
    return spans


def _split_points(total_s: float, segments: list[Segment],
                  silences: list[tuple[float, float]]) -> list[float]:
    """Where to cut the take so each segment gets its own words.

    Starts from where each segment *should* end if speech were perfectly even,
    then snaps to the nearest real pause. Snapping is what keeps a visual cut
    from landing in the middle of a word; the proportional estimate alone is
    close but not clean.
    """
    lengths = [max(1, len(re.findall(r"\S+", s.text))) for s in segments]
    total_words = sum(lengths)

    points: list[float] = []
    running = 0
    previous = 0.0
    for length in lengths[:-1]:
        running += length
        target = total_s * running / total_words
        # Only consider pauses after the previous cut, or two segments could
        # snap to the same silence and one would end up with no audio at all.
        usable = [mid for start, end in silences
                  if (mid := (start + end) / 2) > previous + 0.35
                  and mid < total_s - 0.35]
        if usable:
            nearest = min(usable, key=lambda point: abs(point - target))
            # A pause far from the estimate belongs to a different sentence.
            if abs(nearest - target) < 1.2:
                target = nearest
        previous = target
        points.append(round(target, 3))
    return points


async def narrate(segments: list[Segment], workdir: str) -> None:
    """Speak the whole script in one take, then cut it into segments.

    One continuous performance rather than one call per line: independent calls
    drift in tone and energy, and the result sounds like several narrators
    taking turns. The cost is identical and the voice stays one voice.
    """
    full_text = " ".join(segment.text.strip() for segment in segments)
    take = os.path.join(workdir, "voz.wav")
    spoken = await voice.synthesize(full_text, out_path=take)

    if len(segments) == 1:
        segments[0].audio_path = spoken.path
        segments[0].duration_s = spoken.duration_s
        return

    points = _split_points(spoken.duration_s, segments, _silences(take))
    bounds = [0.0, *points, spoken.duration_s]

    ffmpeg = _ffmpeg()
    for index, segment in enumerate(segments):
        start, end = bounds[index], bounds[index + 1]
        piece = os.path.join(workdir, f"voz{index:02d}.wav")
        _run([ffmpeg, "-y", "-i", take, "-ss", f"{start:.3f}",
              "-to", f"{end:.3f}", "-c", "copy", piece])
        segment.audio_path = piece
        segment.duration_s = round(end - start, 3)


def compose(segments: list[Segment], out_path: str, workdir: str, *,
            fmt: Format | None = None) -> Rendered:
    """Join every segment into the 9:16 master, cut the way this format cuts."""
    if not segments:
        raise RenderError("nothing to render: the script produced no segments")

    fmt = fmt or formats.DEFAULT
    os.makedirs(workdir, exist_ok=True)
    ffmpeg = _ffmpeg()
    clips, audios = [], []
    shot_number = 0
    total = sum(max(0.8, s.duration_s) for s in segments)
    played = 0.0
    for segment in segments:
        # The audio is one file per segment; the picture may change two or three
        # times inside it. Splitting the video without touching the audio keeps
        # them in sync by construction, because the shots add up to exactly the
        # segment's own length.
        spoken = max(0.8, segment.duration_s)
        chunks = _caption_chunks(segment.text, spoken, fmt)
        elapsed = 0.0
        for shot in segment.shots(fmt):
            # A caption group can straddle a cut. Each shot carries the part of
            # it that belongs to its own window, in its own timebase, so the
            # words stay with the voice no matter where the picture changes.
            here = []
            for text, start, end in chunks:
                visible_from = max(start, elapsed)
                visible_to = min(end, elapsed + shot.seconds)
                if visible_to - visible_from > 0.08:
                    here.append((text, visible_from - elapsed,
                                 visible_to - elapsed))
            clips.append(_shot_clip(
                segment, shot, os.path.join(workdir, f"clip{shot_number:02d}.mp4"),
                workdir, shot_number, fmt=fmt, captions=here,
                elapsed=played + elapsed, total=total))
            elapsed += shot.seconds
            shot_number += 1
        played += elapsed
        audios.append(segment.audio_path)

    video_list = os.path.join(workdir, "clips.txt")
    audio_list = os.path.join(workdir, "audio.txt")
    for listing, paths in ((video_list, clips), (audio_list, audios)):
        with open(listing, "w", encoding="utf-8") as handle:
            for path in paths:
                handle.write(f"file '{os.path.abspath(path)}'\n")

    joined_video = os.path.join(workdir, "video.mp4")
    joined_audio = os.path.join(workdir, "audio.wav")
    _run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", video_list,
          "-c", "copy", joined_video])
    _run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", audio_list,
          "-c", "copy", joined_audio])

    # Loudness matters more here than it looks. TikTok, Reels and Shorts all
    # normalise playback to about -14 LUFS: a quiet narration is not left quiet,
    # it is turned up along with its noise floor, and it sounds thin next to
    # everything else in the feed. Synthesised speech has no consistent level,
    # so normalise at the one encode where the audio is touched anyway.
    _run([ffmpeg, "-y", "-i", joined_video, "-i", joined_audio,
          "-c:v", "copy", *ff.audio_encode_args(), "-b:a", "192k", "-ar", "48000",
          "-shortest", "-movflags", "+faststart", out_path])

    total = sum(segment.duration_s for segment in segments)
    used = [segment.asset_path for segment in segments]
    return Rendered(
        path=out_path,
        duration_s=round(total, 2),
        coverage=asset_lib.coverage(used),
        segments=segments,
    )


async def prepare(script: dict, *, workdir: str,
                  library_dir: str | None = None,
                  generate_images: bool = True,
                  fmt: Format | None = None) -> list[Segment]:
    """Everything a render needs before a single frame is drawn.

    Split out from :func:`render` because this is the half that costs money —
    the footage, the photographs, the voice — and the half that does not is the
    one worth running three times to compare montages. Prepare once, compose as
    many ways as there are formats to judge.

    Three tiers of background, in strict order of preference: Autenia's own
    footage where the library has something that fits; a generated photograph
    where it does not; typography where even that fails. The order matters —
    real material is the whole point of the format, and generated imagery is
    the stand-in that keeps the short watchable until there is enough of it.
    """
    fmt = fmt or formats.DEFAULT
    os.makedirs(workdir, exist_ok=True)
    segments = segments_of(script)
    if not segments:
        raise RenderError("the script has neither hook, scenes nor CTA")

    library = asset_lib.load_library(library_dir) if library_dir else []
    plan = asset_lib.plan_visuals([s.visual_request for s in segments], library)
    for segment, chosen in zip(segments, plan):
        segment.asset_path = chosen.path if chosen else None

    # Generated footage, above photographs and below Autenia's own material.
    # Cache hits are free, so a library that has seen this subject before costs
    # nothing; only genuinely new shots are rationed.
    uncovered = [s for s in segments if not s.asset_path]
    if generate_images and uncovered and clips.enabled():
        footage = await clips.for_scenes(
            [s.visual_request for s in uncovered], [s.text for s in uncovered],
            budget=min(fmt.new_clips, clips.MAX_NEW_PER_VIDEO))
        for segment, clip in zip(uncovered, footage):
            segment.clip_path = clip
        _stretch_clips(segments)

    uncovered = [s for s in segments if not s.asset_path and not s.clip_path]
    if generate_images and uncovered:
        # How many framings each scene needs is known only after narration,
        # which is when its real length is known — but generating images then
        # would serialise two slow steps. Estimating from the word count costs
        # nothing and is close enough: a scene one shot short simply holds its
        # last framing a little longer.
        wanted = [_shots_wanted(s.text, fmt) for s in uncovered]
        pictures = await images.for_scenes(
            [s.visual_request for s in uncovered],
            [s.text for s in uncovered], wanted)
        for segment, taken in zip(uncovered, pictures):
            segment.image_paths = list(taken)

    await narrate(segments, workdir)
    return segments


async def render(script: dict, *, out_path: str, workdir: str,
                 library_dir: str | None = None,
                 generate_images: bool = True,
                 fmt: Format | None = None) -> Rendered:
    """Script in, 9:16 master out."""
    fmt = fmt or formats.DEFAULT
    segments = await prepare(script, workdir=workdir, library_dir=library_dir,
                             generate_images=generate_images, fmt=fmt)
    return compose(segments, out_path, workdir, fmt=fmt)


def probe(path: str) -> dict:
    """What ffprobe says the file actually is. Used to verify, not to trust."""
    binary = shutil.which("ffprobe")
    if not binary:
        raise RenderError("ffprobe is not on PATH")
    result = subprocess.run(
        [binary, "-v", "error", "-print_format", "json", "-show_format",
         "-show_streams", path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RenderError(f"ffprobe failed on {os.path.basename(path)}")
    return json.loads(result.stdout)
