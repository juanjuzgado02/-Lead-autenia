"""Generated scene imagery.

Three things must hold, and each is a way the video embarrasses Autenia:
a fabricated product screenshot, gibberish text baked into a photograph, or
paying twice for a picture already on disk.
"""

import os
from unittest.mock import AsyncMock, patch

import pytest

from autenia import images


# -- prompt construction ---------------------------------------------------

@pytest.mark.parametrize("request_text", [
    "Un cuadro de mando de Autenia mostrando el 7,7% de absentismo",
    "Pantalla de Autenia con un flujo de trabajo automatizado",
    "Captura de la aplicación con el informe mensual",
    "Una hoja de cálculo con los datos de facturación",
])
def test_a_screen_request_never_asks_for_the_screen(request_text):
    """Generating Autenia's interface would be inventing the product.

    The script writes for a human editor and freely asks for "una pantalla de
    Autenia". An image model answers that with an invented UI carrying
    misspelt numbers, and publishing it claims a product state that does not
    exist. Real screenshots come from data/library or the scene goes without.
    """
    prompt = images.build_prompt(request_text)
    assert "NO la pantalla" in prompt or "desenfocado" in prompt
    assert "sin interfaces de software legibles" in prompt


def test_every_prompt_forbids_text_in_the_image():
    """Garbled lettering is the single clearest tell of a generated image."""
    for request_text in ("un escritorio con facturas", "una reunión de equipo"):
        prompt = images.build_prompt(request_text)
        assert "SIN texto" in prompt
        assert "sin logotipos" in prompt


def test_figures_are_stripped_from_the_prompt():
    """The number is drawn later, sharp and correct, not painted by the model."""
    prompt = images.build_prompt("Gráfico mostrando el 76% de carga administrativa")
    assert "76" not in prompt


def test_an_empty_request_still_produces_a_usable_prompt():
    prompt = images.build_prompt("")
    assert "pyme" in prompt.lower()
    assert len(prompt) > 100


# -- caching ---------------------------------------------------------------

def test_the_same_prompt_maps_to_the_same_file():
    assert images.cache_path("hola") == images.cache_path("hola")
    assert images.cache_path("hola") != images.cache_path("adiós")


@pytest.mark.asyncio
async def test_a_cached_image_is_not_paid_for_again(tmp_path, monkeypatch):
    monkeypatch.setattr(images, "CACHE_DIR", str(tmp_path))
    prompt = "una oficina pequeña"
    existing = images.cache_path(prompt)
    os.makedirs(os.path.dirname(existing) or ".", exist_ok=True)
    with open(existing, "wb") as handle:
        handle.write(b"0" * 4096)

    called = AsyncMock()
    with patch.object(images, "request", called):
        path = await images.generate(prompt)

    assert path == existing
    called.assert_not_called()


@pytest.mark.asyncio
async def test_a_truncated_cache_file_is_regenerated(tmp_path, monkeypatch):
    """An interrupted write must not be served as a picture for ever."""
    monkeypatch.setattr(images, "CACHE_DIR", str(tmp_path))
    prompt = "una oficina pequeña"
    stub = images.cache_path(prompt)
    os.makedirs(os.path.dirname(stub) or ".", exist_ok=True)
    with open(stub, "wb") as handle:
        handle.write(b"tiny")

    import base64
    payload = {"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "image/jpeg",
                        "data": base64.b64encode(b"J" * 4096).decode()}}]}}]}
    with patch.object(images, "request", AsyncMock(return_value=payload)):
        path = await images.generate(prompt)

    assert os.path.getsize(path) == 4096


# -- failure is not fatal --------------------------------------------------

@pytest.mark.asyncio
async def test_one_failed_image_does_not_sink_the_others():
    """A plain scene beats no video at all, and the operator sees it in review."""
    async def flaky(prompt, **kwargs):
        if "reunión" in prompt:
            raise images.ImageError("no")
        return "/tmp/ok.jpg"

    with patch.object(images, "generate", AsyncMock(side_effect=flaky)):
        got = await images.for_scenes(["un escritorio", "una reunión de equipo"])

    assert got[0] == ["/tmp/ok.jpg"]
    assert got[1] == [], "a scene whose image failed falls back to type"


@pytest.mark.asyncio
async def test_a_response_without_an_image_raises_rather_than_caching_junk():
    payload = {"candidates": [{"content": {"parts": [{"text": "lo siento"}]}}]}
    with patch.object(images, "request", AsyncMock(return_value=payload)):
        with pytest.raises(images.ImageError):
            await images.generate("cualquier cosa")


def test_only_new_images_are_charged():
    before = {"/cache/a.jpg"}
    paths = ["/cache/a.jpg", "/cache/b.jpg", None]
    assert images.cost_cents(paths, before=before) == images.IMAGE_COST_CENTS
