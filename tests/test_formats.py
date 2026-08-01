"""Los montajes disponibles y cómo se elige uno.

Elegir formato es la única decisión del sistema que se toma mirando, no
razonando, así que lo que se prueba aquí es que cambiar de opinión sea barato y
que equivocarse escribiéndolo no cueste el vídeo del día.
"""

import pytest

from autenia import formats


def test_every_preset_is_reachable_by_its_own_name():
    for name, fmt in formats.PRESETS.items():
        assert formats.get(name) is fmt
        assert fmt.name == name


def test_an_unknown_name_is_a_mistake_worth_raising():
    with pytest.raises(ValueError, match="formato desconocido"):
        formats.get("cinematico")


def test_nothing_asked_for_means_the_default():
    assert formats.get(None) is formats.DEFAULT
    assert formats.get("") is formats.DEFAULT


def test_a_typo_in_the_environment_does_not_cost_the_days_video(monkeypatch):
    """The cycle must survive AUTENIA_FORMATO=rapdio. Losing a short to that
    would be the worst trade in the system: no video, for a letter."""
    monkeypatch.setenv("AUTENIA_FORMATO", "rapdio")
    assert formats.current() is formats.DEFAULT


def test_the_environment_chooses_the_montage(monkeypatch):
    monkeypatch.setenv("AUTENIA_FORMATO", " Kinetico ")
    assert formats.current() is formats.KINETICO


def test_no_montage_cuts_so_fast_that_a_shot_becomes_a_flash():
    for fmt in formats.PRESETS.values():
        assert fmt.max_shot_s >= 1.5, fmt.name


def test_type_without_a_plate_is_the_only_place_the_display_face_is_used():
    """Big type over a photograph needs the heavy face and an outline; small
    type in a plate does not, and would read as shouting."""
    for fmt in formats.PRESETS.values():
        if fmt.plate_alpha == 0:
            assert fmt.caption == "kinetico"
            assert fmt.caption_size >= 70
