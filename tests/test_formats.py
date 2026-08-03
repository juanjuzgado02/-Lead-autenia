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


# -- quitar el subtítulo ---------------------------------------------------
#
# Un subtítulo que va por delante de lo que se dice es peor que no tenerlo: el
# ojo lee antes de que llegue la voz. Poder quitarlo convierte un vídeo que se
# iba a tirar en uno publicable sin pagar nada.

def test_taking_the_subtitle_off_changes_only_the_subtitle():
    for original in formats.PRESETS.values():
        mudo = formats.sin_subtitulos(original)
        assert mudo.caption == "ninguno"
        # Todo lo demás es el mismo montaje: ritmo, cámara, marca, gancho.
        assert mudo.name == original.name
        assert mudo.max_shot_s == original.max_shot_s
        assert mudo.punch == original.punch
        assert mudo.hook_card == original.hook_card
        assert mudo.brand == original.brand


def test_the_env_takes_them_off_any_preset(monkeypatch):
    monkeypatch.setenv("AUTENIA_SUBTITULOS", "off")
    for nombre in formats.PRESETS:
        monkeypatch.setenv("AUTENIA_FORMATO", nombre)
        assert formats.current().caption == "ninguno"


def test_subtitles_are_on_unless_somebody_says_otherwise(monkeypatch):
    monkeypatch.delenv("AUTENIA_SUBTITULOS", raising=False)
    monkeypatch.delenv("AUTENIA_FORMATO", raising=False)
    assert formats.current().caption != "ninguno"
    assert formats.subtitulos_activos()
