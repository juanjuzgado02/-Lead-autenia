"""Arreglar un vídeo casi bueno sin volver a pagarlo entero.

Lo que se prueba aquí es sobre todo lo que **no** se compra. Un vídeo son tres
capas montadas al final, y la razón de existir de este módulo es que un defecto
de voz cueste una locución y uno de plano cueste un plano: si un test deja de
mirar eso, lo que queda es un botón caro con otro nombre.
"""

import json

import pytest

from autenia import arreglo, render
from autenia.arreglo import GUION, PLANO, VOZ, Arreglo


def escena(texto, **kwargs):
    return render.Segment(kind="escena", text=texto,
                          visual_request=kwargs.pop("visual", "una oficina"),
                          **kwargs)


@pytest.fixture
def guion_montado():
    """Tres escenas: una con metraje propio, dos compartiendo un clip pagado."""
    return [
        escena("La primera frase.", asset_path="data/library/oficina.mp4"),
        escena("La segunda frase.", clip_path="data/cache/clips/aaa.mp4"),
        escena("La tercera frase.", clip_path="data/cache/clips/aaa.mp4",
               clip_offset=4.0),
    ]


# -- el plan: sin esto no hay arreglo barato -------------------------------

def test_the_plan_survives_the_round_trip(tmp_path, guion_montado):
    """Qué compró cada escena es lo único que separa arreglar de rehacer."""
    guion_montado[0].duration_s = 4.2
    guion_montado[0].audio_path = str(tmp_path / "voz00.wav")
    guion_montado[1].image_paths = ["data/cache/images/x.jpg"]

    render.save_plan(guion_montado, str(tmp_path))
    leido = render.load_plan(str(tmp_path))

    assert leido == guion_montado


def test_a_video_from_before_the_plan_existed_says_so(tmp_path):
    """Devuelve None y no lanza: hay un camino largo para esos."""
    assert render.load_plan(str(tmp_path)) is None


def test_a_half_written_plan_is_not_trusted(tmp_path):
    (tmp_path / render.PLAN).write_text("[{'roto': ", encoding="utf-8")
    assert render.load_plan(str(tmp_path)) is None


def test_a_plan_with_fields_it_does_not_know_still_loads(tmp_path):
    """Un plan de una versión anterior no puede dejar sin arreglo a un vídeo."""
    (tmp_path / render.PLAN).write_text(json.dumps(
        [{"kind": "escena", "text": "hola", "visual_request": "algo",
          "campo_que_ya_no_existe": 3}]), encoding="utf-8")
    plan = render.load_plan(str(tmp_path))
    assert plan and plan[0].text == "hola"


# -- clasificar: la llamada que evita comprar seis clips --------------------

def _responde(monkeypatch, datos):
    async def falso(_ruta, _cuerpo, **kwargs):
        return {"candidates": [{"content": {"parts": [
            {"text": json.dumps(datos)}]}}]}
    monkeypatch.setattr(arreglo, "request", falso)


@pytest.mark.asyncio
async def test_a_voice_defect_is_a_voice_defect(monkeypatch, guion_montado):
    _responde(monkeypatch, {"capa": VOZ, "escena": None,
                            "motivo": "repite una palabra"})
    resultado = await arreglo.clasificar("repite nadie dos veces", guion_montado)
    assert resultado.capa == VOZ and resultado.escena is None
    assert resultado.se_puede


@pytest.mark.asyncio
async def test_a_scene_defect_names_its_scene(monkeypatch, guion_montado):
    _responde(monkeypatch, {"capa": PLANO, "escena": 2, "motivo": "seis dedos"})
    resultado = await arreglo.clasificar("la mano tiene seis dedos",
                                         guion_montado)
    assert resultado.capa == PLANO
    assert resultado.escena == 1  # el operador cuenta desde 1, el código no


@pytest.mark.asyncio
async def test_a_shot_defect_without_a_scene_is_not_cheap_to_fix(
        monkeypatch, guion_montado):
    """Comprar todos los planos por si acaso es justo lo que esto evita."""
    _responde(monkeypatch, {"capa": PLANO, "escena": None, "motivo": "feo"})
    resultado = await arreglo.clasificar("está feo", guion_montado)
    assert resultado.capa == GUION and not resultado.se_puede


@pytest.mark.asyncio
async def test_a_scene_number_that_does_not_exist_is_not_believed(
        monkeypatch, guion_montado):
    _responde(monkeypatch, {"capa": PLANO, "escena": 9, "motivo": "seis dedos"})
    assert (await arreglo.clasificar("x", guion_montado)).capa == GUION


@pytest.mark.asyncio
async def test_the_words_are_not_repaired_by_reassembling(
        monkeypatch, guion_montado):
    _responde(monkeypatch, {"capa": GUION, "escena": None,
                            "motivo": "la cifra no es esa"})
    resultado = await arreglo.clasificar("el 30% no es correcto", guion_montado)
    assert not resultado.se_puede


@pytest.mark.asyncio
async def test_a_classifier_that_falls_over_takes_the_long_way(
        monkeypatch, guion_montado):
    """No poder clasificar no puede significar gastar de más."""
    async def revienta(*args, **kwargs):
        raise arreglo.GeminiError("sin red")
    monkeypatch.setattr(arreglo, "request", revienta)
    resultado = await arreglo.clasificar("algo", guion_montado)
    assert resultado.capa == GUION and not resultado.se_puede


@pytest.mark.asyncio
async def test_an_invented_layer_is_not_acted_on(monkeypatch, guion_montado):
    _responde(monkeypatch, {"capa": "musica", "motivo": "x"})
    assert (await arreglo.clasificar("x", guion_montado)).capa == GUION


# -- aplicar: lo que se compra y, sobre todo, lo que no --------------------

@pytest.fixture
def sin_comprar(monkeypatch):
    """Nada de esto debería llamarse salvo donde el test lo diga."""
    async def prohibido(*args, **kwargs):  # pragma: no cover
        raise AssertionError("esto habría gastado dinero")

    monkeypatch.setattr(render, "buy_visuals", prohibido)
    monkeypatch.setattr(render, "narrate", prohibido)


@pytest.mark.asyncio
async def test_a_voice_defect_buys_one_take_and_no_pictures(
        monkeypatch, tmp_path, guion_montado, sin_comprar):
    narradas = []

    async def narrate(segmentos, workdir, voz=None):
        narradas.append(len(segmentos))

    monkeypatch.setattr(render, "narrate", narrate)

    hecho = await arreglo.aplicar(
        Arreglo(capa=VOZ), guion_montado, workdir=str(tmp_path),
        script={"genero": "noticia"}, fmt=None)

    assert narradas == [3]          # una toma, la del vídeo entero
    assert "locución" in hecho
    # Los planos siguen donde estaban: no se ha tocado nada de lo que se ve.
    assert guion_montado[1].clip_path == "data/cache/clips/aaa.mp4"
    assert render.load_plan(str(tmp_path)) == guion_montado


@pytest.mark.asyncio
async def test_a_shot_defect_frees_every_scene_that_shared_the_clip(
        monkeypatch, tmp_path, guion_montado):
    """Un clip corre por varias escenas; borrarlo dejaría a las demás sin nada."""
    tirados, comprados = [], []

    async def descartar(path, motivo):
        tirados.append(path)

    async def buy_visuals(segmentos, indices, *, fmt=None):
        comprados.append(list(indices))

    monkeypatch.setattr(arreglo.revision, "descartar", descartar)
    monkeypatch.setattr(render, "buy_visuals", buy_visuals)
    monkeypatch.setattr(arreglo, "_generado", lambda path: bool(path))

    await arreglo.aplicar(Arreglo(capa=PLANO, escena=1, motivo="seis dedos"),
                          guion_montado, workdir=str(tmp_path),
                          script={}, fmt=None)

    assert tirados == ["data/cache/clips/aaa.mp4"]   # una pieza, una vez
    assert comprados == [[1, 2]]                     # las dos que lo compartían
    assert guion_montado[2].clip_path is None and guion_montado[2].clip_offset == 0.0
    # La escena con metraje propio ni se entera.
    assert guion_montado[0].asset_path == "data/library/oficina.mp4"


@pytest.mark.asyncio
async def test_autenias_own_footage_is_never_deleted(
        monkeypatch, tmp_path, guion_montado):
    """Lo grabó alguien. Que no encajara aquí no lo convierte en defectuoso."""
    tirados = []

    async def descartar(path, motivo):  # pragma: no cover - llegar aquí es el fallo
        tirados.append(path)

    async def buy_visuals(segmentos, indices, *, fmt=None):
        pass

    monkeypatch.setattr(arreglo.revision, "descartar", descartar)
    monkeypatch.setattr(render, "buy_visuals", buy_visuals)

    await arreglo.aplicar(Arreglo(capa=PLANO, escena=0, motivo="no pega"),
                          guion_montado, workdir=str(tmp_path),
                          script={}, fmt=None)

    assert tirados == []
    assert guion_montado[0].asset_path is None  # se suelta, pero sigue en disco


def test_only_generated_material_is_deletable(monkeypatch):
    monkeypatch.setattr(arreglo.clips, "CACHE_DIR", "data/cache/clips")
    monkeypatch.setattr(arreglo.images, "CACHE_DIR", "data/cache/images")
    assert arreglo._generado("data/cache/clips/aaa.mp4")
    assert arreglo._generado("data/cache/images/bbb.jpg")
    assert not arreglo._generado("data/library/oficina.mp4")
    assert not arreglo._generado(None)


@pytest.mark.asyncio
async def test_a_script_defect_refuses_to_pretend(tmp_path, guion_montado,
                                                  sin_comprar):
    with pytest.raises(ValueError):
        await arreglo.aplicar(Arreglo(capa=GUION), guion_montado,
                              workdir=str(tmp_path), script={}, fmt=None)
