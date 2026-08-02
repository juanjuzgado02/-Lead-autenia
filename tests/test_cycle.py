"""La segunda puerta: qué pasa cuando el vídeo ya está hecho y algo va mal.

Aquí no se juega dinero de golpe, se juega peor: un vídeo casi bueno que se
aprueba «arreglado» sin haber arreglado nada sale igual que el que se acababa
de rechazar, y se paga el montaje otra vez para verlo.
"""

import json
from dataclasses import dataclass, field

import pytest
import pytest_asyncio

from autenia import cycle, revision, store, telegram
from autenia.models import Version
from autenia.render import Segment
from autenia.states import State


@pytest_asyncio.fixture
async def db(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTENIA_DB_PATH", str(tmp_path / "autenia.db"))
    await store.dispose_db()
    await store.init_db()
    yield
    await store.dispose_db()


@dataclass
class _Rendered:
    path: str = "short.mp4"
    duration_s: float = 42.0
    coverage: float = 0.0
    segments: list = field(default_factory=list)


@pytest.fixture
def mudo(monkeypatch):
    """Telegram, callado y anotando lo que habría dicho."""
    dichos: list[str] = []

    async def decir(text):
        dichos.append(text)
        return {}

    async def revisar(*args, **kwargs):
        return {}

    monkeypatch.setattr(telegram, "send_message", decir)
    monkeypatch.setattr(telegram, "send_review", revisar)
    return dichos


# -- de qué está hecho un vídeo --------------------------------------------

def test_only_generated_pieces_are_listed():
    """El metraje propio no entra: esta lista existe para poder borrar."""
    result = _Rendered(segments=[
        Segment(kind="hook", text="", visual_request="",
                asset_path="data/library/oficina.mp4"),
        Segment(kind="escena", text="", visual_request="",
                clip_path="cache/clip-a.mp4", image_paths=["cache/foto-1.png"]),
        Segment(kind="cta", text="", visual_request="",
                image_paths=["cache/foto-2.png"]),
    ])

    assert cycle._generated_assets(result) == [
        "cache/clip-a.mp4", "cache/foto-1.png", "cache/foto-2.png"]


def test_a_piece_used_twice_is_listed_once():
    result = _Rendered(segments=[
        Segment(kind="escena", text="", visual_request="",
                clip_path="cache/clip-a.mp4"),
        Segment(kind="escena", text="", visual_request="",
                clip_path="cache/clip-a.mp4"),
    ])
    assert cycle._generated_assets(result) == ["cache/clip-a.mp4"]


# -- el pie del vídeo ------------------------------------------------------

def test_the_caption_fits_and_keeps_the_way_out(monkeypatch):
    """Telegram corta a 1024 y lo último es la línea que explica cómo pedir
    un arreglo: si se cae por el borde, el operador sólo ve dos botones."""
    monkeypatch.setattr(cycle.publish, "configured", lambda: ("youtube",))
    monkeypatch.setattr(cycle.publish, "preview", lambda platform, *, title, caption: {
        "youtube_title": title,
        "youtube_description": "palabra " * 900,
        "privacyStatus": "public",
    })

    texto = cycle._publish_preview({"titulo": "Un título"}, "x" * 3000,
                                   _Rendered())

    assert len(texto) <= cycle.CAPTION_LIMIT
    assert texto.rstrip().endswith("</i>")
    assert "Escríbelo aquí" in texto


def test_a_short_description_is_shown_whole(monkeypatch):
    monkeypatch.setattr(cycle.publish, "configured", lambda: ("youtube",))
    monkeypatch.setattr(cycle.publish, "preview", lambda platform, *, title, caption: {
        "youtube_title": title,
        "youtube_description": "Automatiza tu inventario con IA.",
        "privacyStatus": "public",
    })

    texto = cycle._publish_preview({"titulo": "Un título"}, "", _Rendered())

    assert "Automatiza tu inventario con IA." in texto
    assert "…" not in texto.split("Automatiza")[1][:40]


# -- contestar por escrito a un vídeo --------------------------------------

async def _video_esperando(generados: list[str]) -> str:
    """Una versión renderizada, parada en la segunda puerta."""
    async with store.session() as sess:
        content = await store.create_content(
            sess, title="Inventario", topic_hash="t1")
        version = await sess.get(Version, content.current_version_id)
        version.script = json.dumps({"titulo": "Inventario", "hook": "Mira"})
        version.asset_hashes = json.dumps({"generados": generados})
        for destino in (State.GUION, State.EN_REVISION, State.APROBADO,
                        State.RENDERIZANDO, State.REVISION_VIDEO):
            await store.transition(sess, version, destino)
        return version.id


@pytest.mark.asyncio
async def test_the_defective_piece_leaves_the_cache(db, mudo, monkeypatch):
    """Sin esto el segundo montaje pide la misma escena y le dan el mismo
    fichero: el vídeo «arreglado» sale idéntico al rechazado."""
    version_id = await _video_esperando(["cache/clip-a.mp4", "cache/foto-1.png"])
    borrados: list[tuple[str, str]] = []

    async def culpables(paths, queja):
        assert queja == "la mano tiene seis dedos"
        return {"cache/clip-a.mp4": "seis dedos en la mano derecha"}

    async def descartar(path, motivo):
        borrados.append((path, motivo))

    monkeypatch.setattr(revision, "culpables", culpables)
    monkeypatch.setattr(revision, "descartar", descartar)

    await cycle._video_defect(telegram.Action(
        kind="texto", version_id=version_id, text="la mano tiene seis dedos"))

    assert [p for p, _ in borrados] == ["cache/clip-a.mp4"]
    assert "seis dedos" in borrados[0][1]
    assert any("He quitado 1 pieza" in d for d in mudo)


@pytest.mark.asyncio
async def test_the_same_script_comes_back_for_approval(db, mudo, monkeypatch):
    """El guion no tenía la culpa, así que se propone otra vez tal cual."""
    version_id = await _video_esperando([])

    async def culpables(paths, queja):
        return {}

    monkeypatch.setattr(revision, "culpables", culpables)

    await cycle._video_defect(telegram.Action(
        kind="texto", version_id=version_id, text="se ve borroso"))

    async with store.session() as sess:
        previa = await sess.get(Version, version_id)
        assert State(previa.state) is State.DESCARTADO
        nueva = (await store.waiting_for_operator(sess))[0]
        assert State(nueva.state) is State.EN_REVISION
        assert nueva.script == previa.script
        assert nueva.number == previa.number + 1


@pytest.mark.asyncio
async def test_nothing_removed_is_said_out_loud(db, mudo, monkeypatch):
    """Un «arreglado» que no ha arreglado nada se aprueba a ciegas."""
    version_id = await _video_esperando(["cache/clip-a.mp4"])

    async def culpables(paths, queja):
        return {}

    async def explode(*args, **kwargs):  # pragma: no cover - llegar aquí es el fallo
        raise AssertionError("no había culpable y aun así borró")

    monkeypatch.setattr(revision, "culpables", culpables)
    monkeypatch.setattr(revision, "descartar", explode)

    await cycle._video_defect(telegram.Action(
        kind="texto", version_id=version_id, text="la voz suena metálica"))

    assert any("no he borrado" in d.lower() for d in mudo)
