"""Composition: the parts that must hold without invoking ffmpeg.

The renderer is the only module that spends real money and produces the thing
the world sees, so the rules encoded here are about what ends up on screen —
not about ffmpeg's exit code, which the smoke render checks by hand.
"""

import os

import pytest

from autenia import formats, render
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
                      asset_path="/lib/dashboard.png", image_paths=["/cache/x.jpg"])
    assert segment.background == "/lib/dashboard.png"


def test_a_generated_picture_is_used_when_the_library_has_nothing():
    segment = Segment(kind="escena", text="t", visual_request="v",
                      image_paths=["/cache/x.jpg"])
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
                        lambda segments, out, workdir, **_:
                        render.Rendered(out, 1.0, 0.0, segments))

    await render.render({"hook": "Hola."}, out_path=str(tmp_path / "o.mp4"),
                        workdir=str(tmp_path), generate_images=False)
    assert not called


async def _noop(segments, workdir, voice_name=None):
    for segment in segments:
        segment.duration_s = 1.0


# -- montaje ---------------------------------------------------------------

def test_a_long_take_is_one_shot_when_the_format_does_not_cut():
    segment = Segment(kind="escena", text="t", visual_request="v",
                      clip_path="/cache/a.mp4", duration_s=6.0)
    assert len(segment.shots(formats.CONTINUO)) == 1


def test_cutting_footage_advances_through_the_clip_instead_of_repeating_it():
    """The point of the cut is a second angle, not the same seconds twice."""
    segment = Segment(kind="escena", text="t", visual_request="v",
                      clip_path="/cache/a.mp4", duration_s=6.0, clip_offset=2.0)
    shots = segment.shots(formats.RAPIDO)

    assert len(shots) == 3
    assert [round(s.offset, 2) for s in shots] == [2.0, 4.0, 6.0]
    assert sum(s.seconds for s in shots) == pytest.approx(6.0)
    assert shots[0].punch != shots[1].punch, "a cut with no change is not a cut"


def test_a_scene_barely_over_the_limit_is_not_cut_into_a_flash():
    segment = Segment(kind="escena", text="t", visual_request="v",
                      clip_path="/cache/a.mp4", duration_s=2.1)
    assert len(segment.shots(formats.RAPIDO)) == 1


def test_shots_always_add_up_to_the_spoken_length():
    """Anything else desynchronises the picture from the voice for the rest."""
    segment = Segment(kind="escena", text="t", visual_request="v",
                      image_paths=["/a.jpg", "/b.jpg"], duration_s=5.3)
    for fmt in formats.PRESETS.values():
        assert sum(s.seconds for s in segment.shots(fmt)) == pytest.approx(5.3)


# -- subtítulos ------------------------------------------------------------

def test_the_calm_format_shows_the_whole_sentence_at_once():
    chunks = render._caption_chunks("Una frase entera de prueba.", 4.0,
                                    formats.CONTINUO)
    assert chunks == [("Una frase entera de prueba.", 0.0, 4.0)]


def test_grouped_captions_cover_the_segment_without_gaps_or_overlap():
    text = "El absentismo laboral alcanzó un máximo histórico del siete coma siete"
    chunks = render._caption_chunks(text, 6.0, formats.RAPIDO)

    assert len(chunks) > 1
    assert chunks[0][1] == 0.0
    assert chunks[-1][2] == pytest.approx(6.0)
    for before, after in zip(chunks, chunks[1:]):
        assert before[2] == pytest.approx(after[1])
    assert " ".join(c[0] for c in chunks) == text


def test_no_caption_group_is_left_with_a_single_orphan_word():
    chunks = render._caption_chunks("una dos tres cuatro cinco", 5.0,
                                    formats.RAPIDO)
    assert len(chunks[-1][0].split()) > 1


def test_a_group_ends_where_the_sentence_does():
    """"el 90% de los" torn across a cut makes the eye stop to repair it."""
    chunks = render._caption_chunks(
        "No es solo un PDF, es un formato que exige la ley.", 6.0,
        formats.TITULAR)
    assert chunks[0][0].endswith(","), [c[0] for c in chunks]


# -- cifras ----------------------------------------------------------------

def test_only_figures_are_picked_out():
    assert render._is_figure("7,7%")
    assert render._is_figure("2026")
    assert render._is_figure("1.200€")
    assert not render._is_figure("absentismo")
    assert not render._is_figure("obligatoria")


def test_a_figure_is_painted_in_the_brand_colour(tmp_path):
    """Colour everything and nothing is emphasised, so this checks both ways."""
    from PIL import Image

    def colours(text, fmt):
        path, _ = caption = render.caption_png(
            text, str(tmp_path / f"{fmt.name}.png"), fmt=fmt)
        assert caption
        image = Image.open(path).convert("RGBA")
        return {image.getpixel((x, y))[:3]
                for x in range(image.width) for y in range(image.height)
                if image.getpixel((x, y))[3] > 200}

    accent = tuple(int(render.ACCENT[i:i + 2], 16) for i in (1, 3, 5))
    assert any(_close(c, accent) for c in colours("Sube al 7,7%", formats.MARCADO))
    assert not any(_close(c, accent)
                   for c in colours("Sube al 7,7%", formats.RAPIDO)), (
        "a format that does not ask for emphasis must not invent it")


def _close(a, b, tolerance=26):
    return all(abs(x - y) <= tolerance for x, y in zip(a, b))


# -- gancho escrito --------------------------------------------------------

def test_the_hook_card_takes_its_seconds_from_the_hook_not_from_the_video():
    """Adding seconds would slide the whole narration out of sync."""
    segment = Segment(kind="hook", text="¿Pierdes horas en papeleo?",
                      visual_request="v", clip_path="/cache/a.mp4",
                      duration_s=4.0)
    shots = segment.shots(formats.TITULAR)

    assert shots[0].background is None, "the hook opens on type"
    assert shots[0].seconds == pytest.approx(formats.TITULAR.hook_card)
    assert sum(s.seconds for s in shots) == pytest.approx(4.0)
    assert all(s.background for s in shots[1:]), "the rest is footage"


def test_a_hook_too_short_to_share_is_left_whole():
    segment = Segment(kind="hook", text="Ya está aquí.", visual_request="v",
                      clip_path="/cache/a.mp4", duration_s=1.2)
    assert len(segment.shots(formats.TITULAR)) == 1


def test_only_the_hook_gets_the_card():
    segment = Segment(kind="escena", text="Una escena cualquiera.",
                      visual_request="v", clip_path="/cache/a.mp4",
                      duration_s=4.0)
    assert all(s.background for s in segment.shots(formats.TITULAR))


# -- barra y marca ---------------------------------------------------------

def test_the_progress_bar_knows_where_its_shot_starts(tmp_path):
    """`t` restarts at every cut; a bar that ignored that would refill each time."""
    _, chain = render._furniture(formats.TITULAR, str(tmp_path), 12.0, 24.0)
    assert "drawbox" in chain
    assert "12.000+t" in chain.replace(" ", "")
    assert "24.000" in chain


def test_a_format_without_furniture_adds_nothing_to_the_graph(tmp_path):
    args, chain = render._furniture(formats.CONTINUO, str(tmp_path), 3.0, 20.0)
    assert (args, chain) == ([], "")


def test_the_bar_never_overfills_on_the_last_shot(tmp_path):
    _, chain = render._furniture(formats.TITULAR, str(tmp_path), 24.0, 24.0)
    assert "min(1," in chain


def test_the_kinetic_caption_carries_its_own_outline_because_it_has_no_plate(tmp_path):
    """White type over a bright photograph disappears without one."""
    from PIL import Image

    path, _ = render.caption_png("Papeleo", str(tmp_path / "k.png"),
                                 fmt=formats.KINETICO)
    image = Image.open(path)
    corner = image.getpixel((0, 0))

    assert formats.KINETICO.plate_alpha == 0
    assert corner[3] == 0, "there must be no plate behind kinetic type"
    assert any(image.getpixel((x, image.height // 2))[3] > 0
               for x in range(image.width)), "the words must still be drawn"


# -- the master must be a short, and must arrive looking like one -----------

def _stream(**overrides):
    stream = {"codec_type": "video", "width": 1080, "height": 1920,
              "sample_aspect_ratio": "1:1"}
    return {"streams": [{**stream, **overrides}]}


def test_a_correct_master_passes_verification(monkeypatch):
    monkeypatch.setattr(render, "probe", lambda path: _stream())
    render._assert_vertical("short.mp4")   # no raise


def test_a_landscape_master_is_refused(monkeypatch):
    """It looks fine in a log and squashed in the feed, so the pixels decide."""
    monkeypatch.setattr(render, "probe",
                        lambda path: _stream(width=1920, height=1080))
    with pytest.raises(render.RenderError, match="aplastado"):
        render._assert_vertical("short.mp4")


def test_non_square_pixels_are_refused(monkeypatch):
    """1080x1920 with a stretched SAR plays stretched anyway."""
    monkeypatch.setattr(render, "probe",
                        lambda path: _stream(sample_aspect_ratio="4:3"))
    with pytest.raises(render.RenderError, match="estirado"):
        render._assert_vertical("short.mp4")


def test_no_ffprobe_does_not_throw_away_a_good_render(monkeypatch):
    """The encode already worked; a missing tool is not a failed video."""
    def no_binary(path):
        raise render.RenderError("ffprobe is not on PATH")
    monkeypatch.setattr(render, "probe", no_binary)
    render._assert_vertical("short.mp4")   # no raise


def test_telegram_is_told_the_dimensions():
    """Without them a 1080x1920 master arrives in the chat looking squashed."""
    from autenia import telegram
    fields = telegram.video_fields("pie", width=1080, height=1920, duration=29.7)
    assert fields["width"] == "1080"
    assert fields["height"] == "1920"
    assert fields["duration"] == "30"
    assert fields["supports_streaming"] == "true"


# -- dónde se corta la voz -------------------------------------------------
#
# El subtítulo de cada escena sale mientras suena el trozo de voz de esa
# escena. Si el corte no cae en la pausa real, la imagen dice una cosa y la voz
# otra, y el desfase se arrastra hasta el final del vídeo. No había ni un test
# sobre esto, que es exactamente por qué llegó a producción dos veces.

#: Los silencios medidos en la locución del vídeo del 2 de agosto de 2026.
#: Las fronteras de frase de verdad son 5,409 · 12,022 · 16,702 · 20,909.
SILENCIOS_REALES = [
    (0.627, 0.926), (2.236, 2.620), (4.660, 6.159), (7.871, 8.421),
    (11.374, 12.670), (15.920, 17.485), (20.698, 21.121), (23.173, 23.507),
    (24.621, 24.985),
]
TOTAL_REAL = 24.985
GUION_REAL = [
    "Tu equipo pierde mañanas enteras contestando las mismas dudas de clientes.",
    "Según Contact Center Hub, la IA resuelve hasta el noventa por ciento.",
    "Dejas de copiar respuestas para enfocarte solo en vender más.",
    "Tu equipo se libera por completo de la atención repetitiva.",
    "Haz el cuestionario de un minuto en la web y analiza tu empresa.",
]


def _segmentos(textos):
    return [Segment(kind="escena", text=t, visual_request="") for t in textos]


def test_every_cut_lands_on_a_real_pause():
    """El caso que se publicó mal: sólo el primer corte caía en una pausa.

    La primera frase se leyó a 2,01 palabras por segundo y el resto a 2,25-2,47.
    Estimando por palabras y aceptando la pausa más cercana sólo si estaba a
    menos de 1,2 s, el reparto se desviaba y los tres últimos cortes caían en
    mitad de la frase, con 1,7 a 2,0 s de desfase.
    """
    puntos = render._split_points(TOTAL_REAL, _segmentos(GUION_REAL),
                                  SILENCIOS_REALES)
    # Los centros de las cuatro pausas que separan las cinco frases.
    assert puntos == pytest.approx([5.409, 12.022, 16.702, 20.909], abs=0.002)


def test_a_cut_is_never_left_in_the_middle_of_a_sentence():
    """Dicho como lo nota quien lo ve: cada corte, dentro de un silencio."""
    puntos = render._split_points(TOTAL_REAL, _segmentos(GUION_REAL),
                                  SILENCIOS_REALES)
    for punto in puntos:
        assert any(inicio <= punto <= fin for inicio, fin in SILENCIOS_REALES), \
            f"el corte en {punto}s cae mientras se habla"


def test_the_cuts_go_forwards_and_leave_every_scene_something_to_say():
    puntos = render._split_points(TOTAL_REAL, _segmentos(GUION_REAL),
                                  SILENCIOS_REALES)
    bordes = [0.0, *puntos, TOTAL_REAL]
    duraciones = [b - a for a, b in zip(bordes, bordes[1:])]
    assert all(d >= render.MIN_SEGMENT_S for d in duraciones)
    assert puntos == sorted(puntos)


def test_the_last_pause_of_all_is_not_a_cut():
    """Cortar en el silencio final dejaría a la última escena sin voz."""
    puntos = render._split_points(TOTAL_REAL, _segmentos(GUION_REAL),
                                  SILENCIOS_REALES)
    assert max(puntos) < TOTAL_REAL - render.MIN_SEGMENT_S


def test_one_segment_is_not_cut_at_all():
    assert render._split_points(10.0, _segmentos(["Una sola frase."]), []) == []


def test_without_pauses_it_still_divides_the_take():
    """Un corte estimado es peor que uno real; no tener vídeo es peor que los dos."""
    puntos = render._split_points(12.0, _segmentos(["Uno dos.", "Tres cuatro."]), [])
    assert puntos == [6.0]


def test_half_an_assignment_is_not_taken():
    """Con menos pausas que fronteras se reparte por palabras y ya está.

    Mezclar cortes reales con estimados reparte el desfase en vez de quitarlo:
    la escena que se lleva la pausa buena empuja a las demás.
    """
    segmentos = _segmentos(["Uno dos.", "Tres cuatro.", "Cinco seis."])
    puntos = render._split_points(12.0, segmentos, [(7.5, 8.5)])
    assert puntos == [4.0, 8.0]  # proporcional, sin usar la única pausa


def test_a_pause_that_fits_the_estimate_is_used():
    """Lo que ya funcionaba sigue funcionando: la pausa manda sobre el reparto."""
    segmentos = _segmentos(["Uno dos.", "Tres cuatro."])
    puntos = render._split_points(12.0, segmentos, [(6.4, 7.0)])
    assert puntos == [6.7]
