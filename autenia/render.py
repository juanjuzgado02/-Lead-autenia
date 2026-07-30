"""Turning a script into a 9:16 video.

The narration is synthesised **one segment at a time** rather than as a single
take. That costs nothing extra and buys two things: exact per-scene durations,
so the visuals cut where the sentence ends instead of where an estimate guessed;
and reuse, so feedback on one scene re-synthesises that scene alone.

Where a scene has no matching footage the background is a typographic card.
That is a deliberate fallback, not a placeholder: showing an unrelated clip, or
a mock-up of a product screen that does not exist, would both be worse than
honest text.

Output is 1080x1920, H.264, AAC, 30 fps — the master that Instagram, TikTok and
YouTube all take.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import textwrap
from dataclasses import dataclass

from . import assets as asset_lib
from . import ffmpeg as ff
from . import images, voice

WIDTH, HEIGHT, FPS = 1080, 1920, 30

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
class Segment:
    """One spoken beat: hook, a scene, or the CTA."""

    kind: str            # "hook" | "escena" | "cta"
    text: str            # what is said, and what the subtitle shows
    visual_request: str  # what the script asked to see
    audio_path: str = ""
    duration_s: float = 0.0
    asset_path: str | None = None    # Autenia's own footage, always preferred
    image_path: str | None = None    # generated stand-in when the library is short

    @property
    def background(self) -> str | None:
        """What this scene actually shows. Own material wins over generated."""
        return self.asset_path or self.image_path


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
CAPTION_SIZE = 56
CAPTION_PAD = 28
CAPTION_LEADING = 14


def caption_png(text: str, out_path: str) -> tuple[str, int]:
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

    try:
        font = ImageFont.truetype(FONT_BODY, CAPTION_SIZE)
    except OSError:
        font = ImageFont.load_default()

    lines = _wrap(text, 26).split("\n")
    ruler = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    widths, heights = [], []
    for line in lines:
        box = ruler.textbbox((0, 0), line, font=font)
        widths.append(box[2] - box[0])
        heights.append(box[3] - box[1])

    line_h = max(heights) if heights else CAPTION_SIZE
    text_w = max(widths) if widths else 0
    block_h = line_h * len(lines) + CAPTION_LEADING * (len(lines) - 1)

    plate_w = min(WIDTH - 60, text_w + CAPTION_PAD * 2)
    plate_h = block_h + CAPTION_PAD * 2

    image = Image.new("RGBA", (plate_w, plate_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, plate_w - 1, plate_h - 1), radius=18,
                           fill=(0, 0, 0, 150))

    y = CAPTION_PAD
    for line, width in zip(lines, widths):
        draw.text(((plate_w - width) / 2, y), line, font=font, fill=INK,
                  anchor="la")
        y += line_h + CAPTION_LEADING

    image.save(out_path)
    return out_path, plate_h


#: How much a still drifts across its scene. Enough to read as alive, little
#: enough not to look like a screensaver: 8% over four seconds is roughly a
#: slow push-in on a tripod.
KEN_BURNS_ZOOM = 0.08


def _ken_burns(duration: float, index: int) -> str:
    """Slow push on a still, alternating direction from scene to scene.

    A photograph held perfectly still for four seconds reads as a slideshow, and
    a slideshow reads as something nobody bothered to edit. The image is scaled
    to double size first because zoompan samples the *input* frame: zooming a
    1080-wide source produces visible stepping as it crosses pixel boundaries.

    Direction alternates so that four scenes in a row do not all creep the same
    way, which is its own kind of monotony.
    """
    frames = max(2, int(round(duration * FPS)))
    if index % 2 == 0:
        zoom = f"1+{KEN_BURNS_ZOOM}*on/{frames}"          # push in
    else:
        zoom = f"{1 + KEN_BURNS_ZOOM}-{KEN_BURNS_ZOOM}*on/{frames}"   # pull out
    return (
        f"scale={WIDTH * 2}:{HEIGHT * 2}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH * 2}:{HEIGHT * 2},"
        f"zoompan=z='{zoom}':d={frames}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s={WIDTH}x{HEIGHT}:fps={FPS}"
    )


def _segment_clip(segment: Segment, out_path: str, workdir: str, index: int) -> str:
    """One segment as a silent video of exactly its narration's length."""
    ffmpeg = _ffmpeg()
    duration = max(0.8, segment.duration_s)
    background = segment.background

    if background and os.path.isfile(background):
        is_video = os.path.splitext(background)[1].lower() in asset_lib.VIDEO_SUFFIXES
        if is_video:
            # Cover the frame and crop, never letterbox: black bars on a
            # vertical feed read as a reposted landscape video.
            motion = (f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
                      f"crop={WIDTH}:{HEIGHT}")
            args = [ffmpeg, "-y", "-stream_loop", "-1", "-t", f"{duration:.3f}",
                    "-i", background]
        else:
            motion = _ken_burns(duration, index)
            args = [ffmpeg, "-y", "-loop", "1", "-t", f"{duration:.3f}",
                    "-i", background]

        # A photograph needs the words on top of it; a type card already is them.
        plate, plate_h = caption_png(
            segment.text, os.path.join(workdir, f"cap{index:02d}.png"))
        args += ["-i", plate]
        graph = (
            f"[0:v]{motion},fps={FPS},format=yuv420p[bg];"
            f"[bg][1:v]overlay=(W-w)/2:{HEIGHT - SAFE_BOTTOM - plate_h}:"
            f"format=auto,format=yuv420p[v]"
        )
        args += ["-filter_complex", graph, "-map", "[v]"]
    else:
        source = card(segment.text, os.path.join(workdir, f"card{index:02d}.png"),
                      kind=segment.kind)
        args = [ffmpeg, "-y", "-loop", "1", "-t", f"{duration:.3f}", "-i", source]
        args += ["-vf", f"scale={WIDTH}:{HEIGHT},fps={FPS},format=yuv420p"]

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


def compose(segments: list[Segment], out_path: str, workdir: str) -> Rendered:
    """Join every segment into the 9:16 master."""
    if not segments:
        raise RenderError("nothing to render: the script produced no segments")

    ffmpeg = _ffmpeg()
    clips, audios = [], []
    for index, segment in enumerate(segments):
        clips.append(_segment_clip(
            segment, os.path.join(workdir, f"clip{index:02d}.mp4"), workdir, index))
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


async def render(script: dict, *, out_path: str, workdir: str,
                 library_dir: str | None = None,
                 generate_images: bool = True) -> Rendered:
    """Script in, 9:16 master out.

    Three tiers of background, in strict order of preference: Autenia's own
    footage where the library has something that fits; a generated photograph
    where it does not; typography where even that fails. The order matters —
    real material is the whole point of the format, and generated imagery is
    the stand-in that keeps the short watchable until there is enough of it.
    """
    os.makedirs(workdir, exist_ok=True)
    segments = segments_of(script)
    if not segments:
        raise RenderError("the script has neither hook, scenes nor CTA")

    library = asset_lib.load_library(library_dir) if library_dir else []
    plan = asset_lib.plan_visuals([s.visual_request for s in segments], library)
    for segment, chosen in zip(segments, plan):
        segment.asset_path = chosen.path if chosen else None

    uncovered = [s for s in segments if not s.asset_path]
    if generate_images and uncovered:
        pictures = await images.for_scenes(
            [s.visual_request for s in uncovered], [s.text for s in uncovered])
        for segment, picture in zip(uncovered, pictures):
            segment.image_path = picture

    await narrate(segments, workdir)
    return compose(segments, out_path, workdir)


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
