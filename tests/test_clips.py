"""Generated footage: the rules that keep it from bankrupting the channel.

Veo is billed by the second, so every test here is about money leaving the
account when it should not, or about the reuse that makes it affordable at all
actually happening.
"""

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

from autenia import clips


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(clips, "CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("AUTENIA_VIDEO_CLIPS", "on")


def _fake_clip(directory, name, description):
    path = os.path.join(directory, name)
    with open(path, "wb") as handle:
        handle.write(b"0" * 20_000)
    sidecar = os.path.join(directory, "library.json")
    known = {}
    if os.path.isfile(sidecar):
        with open(sidecar, encoding="utf-8") as handle:
            known = json.load(handle)
    known[name] = {"description": description}
    with open(sidecar, "w", encoding="utf-8") as handle:
        json.dump(known, handle)
    return path


# -- the switch ------------------------------------------------------------

@pytest.mark.asyncio
async def test_nothing_is_filmed_while_the_switch_is_off(monkeypatch):
    """Photographs cost a twentieth as much. Footage is opt-in, always."""
    monkeypatch.delenv("AUTENIA_VIDEO_CLIPS", raising=False)
    spy = AsyncMock()
    with patch.object(clips, "generate", spy):
        got = await clips.for_scenes(["un escritorio con facturas"])
    assert got == [None]
    spy.assert_not_called()


# -- the budget ------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_video_never_films_more_than_its_budget():
    """A day where every scene is new must not spend a month in one run."""
    with patch.object(clips, "generate",
                      AsyncMock(side_effect=lambda p, **k: "/cache/new.mp4")):
        got = await clips.for_scenes(
            ["escritorio", "reunión", "teléfono", "almacén", "furgoneta"],
            budget=2)
    assert sum(1 for path in got if path) == 2


@pytest.mark.asyncio
async def test_the_hook_gets_first_claim_on_the_budget():
    """The opening shot decides whether anyone sees the rest."""
    with patch.object(clips, "generate",
                      AsyncMock(side_effect=lambda p, **k: "/cache/new.mp4")):
        got = await clips.for_scenes(["gancho", "escena dos", "escena tres"],
                                     budget=1)
    assert got[0] is not None
    assert got[1] is None and got[2] is None


@pytest.mark.asyncio
async def test_reuse_is_free_and_unrationed(tmp_path):
    """Only new film is rationed; footage already paid for is not."""
    for name in ("facturas-papel-escritorio.mp4", "reunion-equipo-oficina.mp4",
                 "telefono-atencion-cliente.mp4"):
        _fake_clip(str(tmp_path), name, name.replace("-", " ").replace(".mp4", ""))

    spy = AsyncMock(side_effect=lambda p, **k: "/cache/new.mp4")
    with patch.object(clips, "generate", spy):
        got = await clips.for_scenes(
            ["facturas en papel sobre un escritorio",
             "una reunión de equipo en la oficina",
             "atención al cliente por teléfono"],
            budget=0)

    assert all(path is not None for path in got), "todo debería salir de la caché"
    spy.assert_not_called()


# -- reuse actually working -------------------------------------------------

def test_footage_is_found_by_meaning_not_by_exact_wording(tmp_path):
    """The reuse promise was empty until this worked.

    Keying only on the prompt hash hits when two scenes ask in *identical*
    words, which two different news stories never do. Without meaning-based
    matching every video would film everything again, and "pay once" would be
    marketing rather than behaviour.
    """
    _fake_clip(str(tmp_path), "facturas-papel-escritorio-pyme.mp4",
               "facturas en papel acumuladas en un escritorio")
    found = clips.library()
    assert len(found) == 1

    from autenia import assets as asset_lib
    plan = asset_lib.plan_visuals(
        ["Un escritorio lleno de facturas en papel"], found)
    assert plan[0] is not None


def test_a_clip_records_what_it_shows(tmp_path):
    clips._remember(str(tmp_path / "x.mp4"), "manos ordenando documentos")
    with open(tmp_path / "library.json", encoding="utf-8") as handle:
        assert "manos ordenando documentos" in json.load(handle)["x.mp4"]["description"]


def test_a_broken_sidecar_is_replaced_not_propagated(tmp_path):
    (tmp_path / "library.json").write_text("{roto", encoding="utf-8")
    clips._remember(str(tmp_path / "y.mp4"), "una oficina")
    with open(tmp_path / "library.json", encoding="utf-8") as handle:
        assert "y.mp4" in json.load(handle)


# -- length ----------------------------------------------------------------

def test_clips_are_long_enough_not_to_loop():
    """A clip shorter than its scene is played on loop, and a visible loop
    looks worse than a photograph. Scenes measured at 2.6-6.3 seconds."""
    assert clips.CLIP_SECONDS >= 6


def test_clips_stay_within_what_veo_accepts():
    """Ten seconds is refused outright; eight is the ceiling."""
    assert clips.CLIP_SECONDS <= 8


# -- cost ------------------------------------------------------------------

def test_only_new_footage_is_charged():
    before = {"/cache/a.mp4"}
    assert clips.new_cost_cents(["/cache/a.mp4", "/cache/b.mp4", None],
                                before) == clips.CLIP_COST_CENTS
