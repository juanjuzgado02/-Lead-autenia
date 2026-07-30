"""Composition: the parts that must hold without invoking ffmpeg.

The renderer is the only module that spends real money and produces the thing
the world sees, so the rules encoded here are about what ends up on screen —
not about ffmpeg's exit code, which the smoke render checks by hand.
"""

import os

import pytest

from autenia import render
from autenia.render import Segment


def test_a_script_becomes_hook_scenes_and_cta_in_order():
    segments = render.segments_of({
        "hook": "Pierdes horas en papeleo.",
        "escenas": [{"narracion": "Una.", "visual": "un escritorio"},
                    {"narracion": "Dos.", "visual": "una oficina"}],
        "cta": "Hay un cuestionario en la web.",
    })
    assert [s.kind for s in segments] == ["hook", "escena", "escena", "cta"]
    assert segments[0].text == "Pierdes horas en papeleo."


def test_a_scene_with_no_narration_is_dropped():
    segments = render.segments_of({
        "hook": "Hola.", "escenas": [{"narracion": "   ", "visual": "x"}]})
    assert [s.kind for s in segments] == ["hook"]


# -- which background wins -------------------------------------------------

def test_autenias_own_footage_beats_a_generated_picture():
    """The format is built on real material. Generated imagery is the stand-in."""
    segment = Segment(kind="escena", text="t", visual_request="v",
                      asset_path="/lib/dashboard.png", image_path="/cache/x.jpg")
    assert segment.background == "/lib/dashboard.png"


def test_a_generated_picture_is_used_when_the_library_has_nothing():
    segment = Segment(kind="escena", text="t", visual_request="v",
                      image_path="/cache/x.jpg")
    assert segment.background == "/cache/x.jpg"


def test_no_picture_at_all_falls_back_to_type():
    segment = Segment(kind="escena", text="t", visual_request="v")
    assert segment.background is None


# -- captions --------------------------------------------------------------

def test_the_caption_is_one_plate_not_one_per_line(tmp_path):
    """ffmpeg's drawtext box draws a rectangle per line: a staircase, not a plate.

    Measured 2026-07-30 on a three-line Spanish caption. Pillow measures the
    whole block, so the plate is single and the lines centre against each other.
    """
    from PIL import Image

    path, height = render.caption_png(
        "Y la factura electrónica obligatoria añade una capa más de trabajo",
        str(tmp_path / "cap.png"))
    image = Image.open(path)

    assert image.mode == "RGBA"
    assert image.height == height
    # A staircase would leave fully transparent rows between the bands. Down
    # the middle of a single plate every row is covered — by the backing, or by
    # a glyph on top of it. The corners are rounded, so this samples the centre
    # column rather than the edge.
    middle = image.width // 2
    covered = [image.getpixel((middle, y))[3] for y in range(2, image.height - 2)]
    assert min(covered) >= render.CAPTION_PLATE_ALPHA, (
        "the plate must be continuous behind every line")


def test_the_caption_never_reaches_the_frame_edge(tmp_path):
    """Type touching the edge gets cropped by some players and reads as amateur."""
    path, _ = render.caption_png("palabra " * 40, str(tmp_path / "c.png"))
    from PIL import Image
    assert Image.open(path).width <= render.WIDTH - 60


def test_accents_and_punctuation_survive(tmp_path):
    """The old filter-string route mangled exactly these characters."""
    path, _ = render.caption_png(
        "¿Cuánto cuesta? El 76%: años, días… y «papeleo».",
        str(tmp_path / "c.png"))
    assert os.path.getsize(path) > 0


# -- motion ----------------------------------------------------------------

def test_the_still_moves_and_alternates_direction():
    """A photograph held still for four seconds reads as a slideshow."""
    push = render._ken_burns(4.0, 0)
    pull = render._ken_burns(4.0, 1)
    assert "zoompan" in push and "zoompan" in pull
    assert push != pull, "consecutive scenes must not creep the same way"


def test_the_source_is_upscaled_before_zooming():
    """zoompan samples the input frame; zooming a 1080-wide source visibly steps."""
    assert f"scale={render.WIDTH * 2}" in render._ken_burns(3.0, 0)


def test_a_very_short_scene_still_produces_valid_frame_counts():
    assert "d=2" in render._ken_burns(0.01, 0)


@pytest.mark.asyncio
async def test_image_generation_can_be_switched_off(tmp_path, monkeypatch):
    """The flag exists so a test or a rerun never quietly spends money."""
    called = False

    async def spy(*args, **kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(render.images, "for_scenes", spy)
    monkeypatch.setattr(render, "narrate", _noop)
    monkeypatch.setattr(render, "compose",
                        lambda segments, out, workdir: render.Rendered(out, 1.0, 0.0, segments))

    await render.render({"hook": "Hola."}, out_path=str(tmp_path / "o.mp4"),
                        workdir=str(tmp_path), generate_images=False)
    assert not called


async def _noop(segments, workdir):
    for segment in segments:
        segment.duration_s = 1.0
