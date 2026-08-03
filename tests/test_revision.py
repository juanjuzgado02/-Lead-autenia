"""El control de calidad de lo que genera un modelo.

La regla que gobierna este módulo no es "detectar defectos", es "no dejar sin
vídeo al canal": un revisor que se cae o que se pone quisquilloso hace más daño
que la mano de seis dedos que intentaba evitar.
"""

import json

import pytest

from autenia import revision
from autenia.revision import Veredicto


@pytest.fixture(autouse=True)
def encendido(monkeypatch):
    monkeypatch.setenv("AUTENIA_REVISION_VISUAL", "on")


# -- el veredicto ----------------------------------------------------------

def test_only_serious_defects_block():
    """Bloqueando cualquier defecto se rechazaba el 80% del caché real."""
    leve = Veredicto(leves=["texto legible en un papel"], revisado=True)
    assert leve.apto and bool(leve) is True
    assert "leve" in str(leve)


def test_a_sixth_finger_blocks():
    grave = Veredicto(graves=["la mano derecha tiene seis dedos"], revisado=True)
    assert not grave.apto
    assert "seis dedos" in str(grave)


def test_an_unreviewed_piece_passes():
    """Sin clave o sin red, el material ya pagado sigue su camino."""
    assert Veredicto(revisado=False).apto
    assert str(Veredicto(revisado=False)) == "sin revisar"


# -- no romper nada --------------------------------------------------------

@pytest.mark.asyncio
async def test_a_missing_file_is_not_a_rejection():
    veredicto = await revision.revisar("no-existe.jpg")
    assert veredicto.apto and not veredicto.revisado


@pytest.mark.asyncio
async def test_switched_off_means_switched_off(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTENIA_REVISION_VISUAL", "off")
    imagen = tmp_path / "foto.jpg"
    imagen.write_bytes(b"0" * 2048)

    async def explode(*args, **kwargs):  # pragma: no cover - llegar aquí es el fallo
        raise AssertionError("apagado y aun así preguntó")

    monkeypatch.setattr(revision, "_preguntar", explode)
    assert (await revision.revisar(str(imagen))).revisado is False


@pytest.mark.asyncio
async def test_a_broken_answer_lets_the_material_through(monkeypatch, tmp_path):
    """Un revisor que tumba vídeos cuando falla la red es peor que ninguno."""
    imagen = tmp_path / "foto.jpg"
    imagen.write_bytes(b"0" * 2048)

    async def roto(*args, **kwargs):
        raise ValueError("respuesta rara")

    monkeypatch.setattr(revision, "_preguntar", roto)
    veredicto = await revision.revisar(str(imagen))
    assert veredicto.apto and not veredicto.revisado


# -- sacar del caché lo que no sirve ---------------------------------------

@pytest.mark.asyncio
async def test_discarding_removes_the_file_and_its_entry(tmp_path):
    """Si se borra el fichero y se deja la ficha, la biblioteca miente."""
    clip = tmp_path / "malo.mp4"
    clip.write_bytes(b"0" * 2048)
    sidecar = tmp_path / "library.json"
    sidecar.write_text(json.dumps({"malo.mp4": {"description": "papeleo"},
                                   "bueno.mp4": {"description": "oficina"}}),
                       encoding="utf-8")

    await revision.descartar(str(clip), "tres brazos")

    assert not clip.exists()
    assert json.loads(sidecar.read_text(encoding="utf-8")) == {
        "bueno.mp4": {"description": "oficina"}}
