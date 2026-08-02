"""El control de calidad de lo que genera un modelo.

La regla que gobierna este módulo no es "detectar defectos", es "no dejar sin
vídeo al canal": un revisor que se cae o que se pone quisquilloso hace más daño
que la mano de seis dedos que intentaba evitar.
"""

import base64
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


# -- localizar la queja del operador ---------------------------------------

def _respuesta(coincide: bool, por_que: str = "") -> dict:
    cuerpo = json.dumps({"coincide": coincide, "por_que": por_que})
    return {"candidates": [{"content": {"parts": [{"text": cuerpo}]}}]}


@pytest.fixture
def foto(tmp_path):
    imagen = tmp_path / "escena.jpg"
    imagen.write_bytes(b"0" * 2048)
    return str(imagen)


@pytest.mark.asyncio
async def test_the_piece_with_the_defect_is_named(monkeypatch, foto):
    async def responder(*args, **kwargs):
        return _respuesta(True, "la mano izquierda tiene seis dedos")

    monkeypatch.setattr(revision, "request", responder)
    assert await revision.culpable(foto, "una mano rara") == \
        "la mano izquierda tiene seis dedos"


@pytest.mark.asyncio
async def test_a_clean_piece_survives_someone_elses_defect(monkeypatch, foto):
    """La queja es de una pieza; las otras cuatro no se pagan dos veces."""
    async def responder(*args, **kwargs):
        return _respuesta(False, "aquí no se ven manos")

    monkeypatch.setattr(revision, "request", responder)
    assert await revision.culpable(foto, "una mano rara") is None


@pytest.mark.asyncio
async def test_doubt_keeps_the_piece(monkeypatch, foto):
    """Al revés que `revisar`: aquí la duda no borra, porque borrar cuesta."""
    async def roto(*args, **kwargs):
        raise ValueError("respuesta rara")

    monkeypatch.setattr(revision, "request", roto)
    assert await revision.culpable(foto, "una mano rara") is None


@pytest.mark.asyncio
async def test_an_empty_complaint_asks_nothing(monkeypatch, foto):
    async def explode(*args, **kwargs):  # pragma: no cover - llegar aquí es el fallo
        raise AssertionError("sin queja y aun así preguntó")

    monkeypatch.setattr(revision, "request", explode)
    assert await revision.culpable(foto, "   ") is None


@pytest.mark.asyncio
async def test_only_the_guilty_pieces_come_back(monkeypatch, tmp_path):
    """De cinco piezas se va la que tiene el fallo, no la tanda entera."""
    piezas = []
    for nombre, relleno in (("a.jpg", b"BIEN"), ("b.jpg", b"MALO"),
                            ("c.jpg", b"BIEN")):
        pieza = tmp_path / nombre
        pieza.write_bytes(relleno * 512)
        piezas.append(str(pieza))

    async def responder(_ruta, cuerpo, **kwargs):
        datos = b"".join(
            base64.b64decode(parte["inlineData"]["data"])
            for parte in cuerpo["contents"][0]["parts"] if "inlineData" in parte)
        return _respuesta(b"MALO" in datos, "la mano tiene seis dedos")

    monkeypatch.setattr(revision, "request", responder)

    culpables = await revision.culpables(piezas, "una mano rara")
    assert list(culpables) == [piezas[1]]
    assert culpables[piezas[1]] == "la mano tiene seis dedos"


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
