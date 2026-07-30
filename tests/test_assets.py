"""Matching a scene to Autenia's own footage.

The failure this guards against is subtle: a weak match still renders, still
looks finished, and quietly makes the video worse. So most of these tests are
about *refusing* rather than matching.
"""

import json

import pytest

from autenia.assets import (
    MIN_MATCH, Asset, coverage, keywords, load_library, match, plan_visuals,
)


def asset(path, description="", tags=()):
    return Asset(path=path, description=description, tags=set(tags))


LIBRARY = [
    asset("/lib/agente-whatsapp-contestando-pedido.mp4"),
    asset("/lib/cuadro-de-mando-ventas.mp4"),
    asset("/lib/formulario-erp-alta-pedido.png"),
    asset("/lib/hoja-calculo-rellenandose-sola.mp4"),
]


# -- keywords --------------------------------------------------------------

def test_keywords_ignore_accents_and_filler():
    words = keywords("Captura de la pantalla de un formulario")
    assert "formulario" in words
    # "captura", "pantalla" and the articles carry no meaning for matching.
    assert not words & {"captura", "pantalla", "de", "la", "un"}


def test_keywords_expand_synonyms():
    """A script says 'cuadro de mando'; a filename says 'dashboard'."""
    assert "dashboard" in keywords("cuadro de mando")
    assert "whatsapp" in keywords("una conversación de chat")


# -- matching --------------------------------------------------------------

def test_a_scene_finds_its_shot():
    found = match("Captura de un formulario de alta de pedido en el ERP", LIBRARY)
    assert found is not None
    assert found.asset.path.endswith("formulario-erp-alta-pedido.png")


def test_matching_works_through_a_synonym():
    found = match("Un cuadro de mando actualizándose", LIBRARY)
    assert found is not None
    assert "cuadro-de-mando" in found.asset.path


def test_an_unrelated_request_matches_nothing():
    """Typography is better than footage that contradicts the narration."""
    assert match("Un camión de reparto en una carretera", LIBRARY) is None


def test_a_single_incidental_word_is_not_a_match():
    """One shared word out of many is padding, not relevance."""
    found = match(
        "Una persona firmando un contrato de alquiler en una oficina moderna",
        LIBRARY,
    )
    assert found is None


def test_an_empty_request_matches_nothing():
    assert match("", LIBRARY) is None
    assert match("de la un", LIBRARY) is None


def test_an_empty_library_matches_nothing():
    assert match("Captura de un formulario de ERP", []) is None


def test_the_score_is_the_share_of_the_request_covered():
    found = match("formulario erp", LIBRARY)
    assert found is not None
    assert found.score >= MIN_MATCH


def test_a_one_word_filename_cannot_win_everything():
    """Scoring against the asset's own words would make this beat every scene."""
    library = [asset("/lib/pedido.mp4")]
    found = match(
        "Captura de un agente de WhatsApp contestando sobre el estado del envío",
        library,
    )
    assert found is None


# -- planning a whole video ------------------------------------------------

def test_one_clip_is_not_reused_across_scenes():
    """Repeating a shot makes the library look empty — which it then is."""
    requests = [
        "Captura de un formulario de pedido en el ERP",
        "Otro formulario de pedido en el ERP",
    ]
    plan = plan_visuals(requests, LIBRARY)
    paths = [a.path for a in plan if a is not None]
    assert len(paths) == len(set(paths))


def test_scenes_without_a_match_fall_back_to_typography():
    requests = [
        "Captura de un cuadro de mando de ventas",
        "Un dron sobrevolando un campo de trigo",
    ]
    plan = plan_visuals(requests, LIBRARY)
    assert plan[0] is not None
    assert plan[1] is None


def test_an_empty_library_plans_all_typography():
    plan = plan_visuals(["cuadro de mando", "formulario erp"], [])
    assert plan == [None, None]


def test_coverage_reports_how_much_real_footage_was_used():
    assert coverage([]) == 0.0
    assert coverage([None, None]) == 0.0
    assert coverage([LIBRARY[0], None]) == 0.5
    assert coverage([LIBRARY[0], LIBRARY[1]]) == 1.0


# -- loading ---------------------------------------------------------------

def test_a_missing_folder_is_an_empty_library_not_a_crash():
    assert load_library("/no/existe") == []
    assert load_library("") == []


def test_filenames_alone_are_enough_to_index(tmp_path):
    (tmp_path / "agente-whatsapp.mp4").write_bytes(b"x")
    (tmp_path / "notas.txt").write_text("no es material")

    library = load_library(str(tmp_path))

    assert len(library) == 1
    assert "whatsapp" in library[0].terms


def test_a_sidecar_adds_description_and_tags(tmp_path):
    (tmp_path / "clip01.mp4").write_bytes(b"x")
    (tmp_path / "library.json").write_text(json.dumps({
        "clip01.mp4": {"description": "agente contestando un pedido",
                       "tags": ["whatsapp"]}
    }), encoding="utf-8")

    library = load_library(str(tmp_path))

    assert "whatsapp" in library[0].terms
    assert "pedido" in library[0].terms


def test_a_broken_sidecar_does_not_take_the_library_down(tmp_path):
    """Filenames still match well enough to render."""
    (tmp_path / "cuadro-de-mando.mp4").write_bytes(b"x")
    (tmp_path / "library.json").write_text("{roto", encoding="utf-8")

    library = load_library(str(tmp_path))

    assert len(library) == 1
    assert "mando" in library[0].terms


def test_videos_and_images_are_distinguished(tmp_path):
    (tmp_path / "clip.mp4").write_bytes(b"x")
    (tmp_path / "captura.png").write_bytes(b"x")

    library = {a.path.split("\\")[-1].split("/")[-1]: a
               for a in load_library(str(tmp_path))}

    assert library["clip.mp4"].is_video
    assert not library["captura.png"].is_video
