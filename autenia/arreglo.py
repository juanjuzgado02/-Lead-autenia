"""Arreglar un vídeo terminado sin volver a pagarlo entero.

El segundo control tenía dos salidas: se publica o no se publica. Ninguna de las
dos sirve para lo que pasa de verdad la mayoría de las veces, que es un vídeo
casi bueno con un fallo concreto — la voz repite una palabra, en una escena
salen seis dedos. Tirarlo entero cuesta lo que costó: la locución, las fotos y,
sobre todo, el metraje generado, que es lo caro.

Pero un vídeo no es una pieza, son tres capas que se montan al final:

* **la voz** — una locución, céntimos;
* **el plano de una escena** — un clip o una foto, y sólo el de esa escena;
* **las palabras** — el guion, que ya aprobó una persona y no se toca aquí.

``render.prepare()`` compra y ``render.compose()`` monta, así que cambiar una
capa y volver a montar es exactamente lo que hace falta. Lo único que faltaba
era saber qué compró el render anterior, y eso lo guarda ahora ``plan.json``.

Así que el arreglo es: leer el plan, preguntar a un modelo barato de qué capa
habla el defecto, comprar esa pieza y montar otra vez. Un defecto de voz cuesta
una locución. Un defecto de plano cuesta un plano. Montar es gratis.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from . import clips, images, render, revision, voice
from .formats import Format
from .gemini import TEXT_MODEL, GeminiError, request

#: Las tres cosas que puede tener un vídeo mal, y lo que cuesta arreglarlas.
VOZ = "voz"          # una locución nueva
PLANO = "plano"      # un clip o una foto, de una escena
GUION = "guion"      # las palabras: no se arregla aquí, vuelve a revisión

_SISTEMA = """\
Clasificas el defecto que una persona ha visto en un vídeo ya terminado, para
decidir qué hay que volver a comprar. Cada capa cuesta un dinero distinto, así
que acertar importa.

- "voz": lo que se OYE. Repite una palabra, se come una palabra o una frase,
  tartamudea, pronuncia mal, lee raro, va muy rápido o muy lento, suena mal.
- "plano": lo que se VE en una escena concreta. Manos o cuerpos deformes, caras
  raras, un logotipo, una imagen que no tiene nada que ver con lo que se dice,
  un plano feo o repetido. Di en qué escena, por su número.
- "guion": las PALABRAS están mal. Un dato equivocado, una cifra que no es, una
  fuente que no dice eso, un gancho que no funciona, algo que no se debería
  decir. Es lo único que obliga a reescribir.

En la duda entre "plano" y "guion": si la frase está bien dicha y lo que falla
es lo que se ve mientras se dice, es "plano".

"escena" es el número que se te da, o null si el defecto es de todo el vídeo o
no se sabe de cuál. Para "voz" casi siempre es null: la locución es una sola.\
"""

_ESQUEMA = {
    "type": "object",
    "properties": {
        "capa": {"type": "string", "enum": [VOZ, PLANO, GUION]},
        "escena": {"type": "integer", "nullable": True},
        "motivo": {"type": "string"},
    },
    "required": ["capa", "motivo"],
}


@dataclass
class Arreglo:
    """De qué capa habla el defecto, y de qué escena si aplica."""

    capa: str
    escena: int | None = None      # índice en los segmentos, ya en base 0
    motivo: str = ""

    @property
    def se_puede(self) -> bool:
        """Si esto se arregla montando otra vez o hay que reescribir."""
        return self.capa in (VOZ, PLANO)


def _escenas(segmentos: list[render.Segment]) -> str:
    """El vídeo descrito en lo mínimo para poder señalar una escena."""
    return "\n".join(
        f"{indice + 1}. dice: «{s.text[:160]}» / se ve: "
        f"«{(s.visual_request or 'nada, sólo texto')[:120]}»"
        for indice, s in enumerate(segmentos))


async def clasificar(defecto: str, segmentos: list[render.Segment]) -> Arreglo:
    """Qué capa hay que volver a comprar, según lo que ha escrito el operador.

    Una llamada corta y barata: el defecto en una frase y las escenas en una
    línea cada una. Es la llamada que evita volver a comprar seis clips, así
    que es la más rentable del sistema.

    Ante cualquier fallo devuelve ``guion``, que es el camino de siempre: no
    poder clasificar no puede significar gastar de más ni quedarse callado.
    """
    try:
        payload = await request(
            f"models/{TEXT_MODEL}:generateContent",
            {
                "contents": [{"parts": [{"text":
                    f"Escenas del vídeo:\n{_escenas(segmentos)}\n\n"
                    f"Lo que ha visto la persona que lo ha revisado:\n"
                    f"«{defecto[:500]}»"}]}],
                "systemInstruction": {"parts": [{"text": _SISTEMA}]},
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseSchema": _ESQUEMA,
                    "temperature": 0.0,
                },
            },
            timeout=60.0,
        )
        texto = "".join(parte.get("text", "") for parte in
                        payload["candidates"][0]["content"]["parts"])
        datos = json.loads(texto)
    except (GeminiError, KeyError, IndexError, TypeError, ValueError) as exc:
        print(f"[arreglo] no se pudo clasificar el defecto: {str(exc)[:120]}")
        return Arreglo(capa=GUION, motivo=defecto[:200])

    capa = str(datos.get("capa") or GUION)
    if capa not in (VOZ, PLANO, GUION):
        capa = GUION

    escena = datos.get("escena")
    indice: int | None = None
    if isinstance(escena, int) and 1 <= escena <= len(segmentos):
        indice = escena - 1
    elif capa == PLANO:
        # Un plano sin escena no se puede arreglar barato: comprarlos todos es
        # justo lo que este módulo existe para no hacer.
        capa = GUION

    return Arreglo(capa=capa, escena=indice,
                   motivo=str(datos.get("motivo") or defecto)[:200])


def _generado(path: str | None) -> bool:
    """Si esto lo pagó un modelo. El material propio de Autenia no se borra."""
    if not path:
        return False
    entero = os.path.abspath(path)
    return any(entero.startswith(os.path.abspath(carpeta))
               for carpeta in (clips.CACHE_DIR, images.CACHE_DIR))


async def _tirar_lo_visto(segmentos: list[render.Segment], indice: int,
                          motivo: str) -> list[int]:
    """Quitar de en medio el plano defectuoso. Devuelve a qué escenas afecta.

    Lo generado se borra del caché además de soltarse: el caché empareja por
    significado, así que un clip malo que se quede vuelve a salir en la escena
    de papeleo del mes que viene y para entonces nadie recuerda por qué era
    malo. Es la misma regla que aplica el revisor visual.

    El metraje propio de Autenia no se borra nunca — lo grabó alguien y no es
    defectuoso, simplemente no encajaba aquí — pero sí se suelta de la escena.
    """
    defectuoso = segmentos[indice]
    afectadas = {indice}

    clip = defectuoso.clip_path
    if clip:
        # Un clip corre por varias escenas (``_stretch_clips``), así que
        # borrarlo dejaría a las demás apuntando a un fichero que ya no existe.
        for otro, segmento in enumerate(segmentos):
            if segmento.clip_path == clip:
                segmento.clip_path, segmento.clip_offset = None, 0.0
                afectadas.add(otro)
        if _generado(clip):
            await revision.descartar(clip, motivo)

    for foto in defectuoso.image_paths:
        if _generado(foto):
            await revision.descartar(foto, motivo)
    defectuoso.image_paths = []

    # El material propio se suelta pero sobrevive: que no sirviera para esta
    # frase no lo convierte en malo.
    defectuoso.asset_path = None
    return sorted(afectadas)


async def aplicar(arreglo: Arreglo, segmentos: list[render.Segment], *,
                  workdir: str, script: dict, fmt: Format) -> str:
    """Comprar la pieza que falla y nada más. Devuelve qué se ha rehecho.

    No monta: eso lo hace quien llama, con ``render.compose``, que es gratis.
    """
    if arreglo.capa == VOZ:
        # El guion está congelado —lo aprobó una persona— así que se pide la
        # misma frase otra vez. Es la toma la que salió mal, no el texto, y una
        # toma nueva ya se escucha antes de darla por buena.
        await render.narrate(segmentos, workdir, voice.for_script(script))
        render.save_plan(segmentos, workdir)
        return "locución nueva"

    if arreglo.capa == PLANO and arreglo.escena is not None:
        antes = _en_cache()
        afectadas = await _tirar_lo_visto(segmentos, arreglo.escena,
                                          arreglo.motivo)
        await render.buy_visuals(segmentos, afectadas, fmt=fmt)
        render.save_plan(segmentos, workdir)

        comprados = len(_en_cache() - antes)
        donde = (f"la escena {arreglo.escena + 1}" if len(afectadas) == 1
                 else f"{len(afectadas)} escenas")
        pagado = ("del caché, sin pagar" if not comprados
                  else f"{comprados} pieza nueva" if comprados == 1
                  else f"{comprados} piezas nuevas")
        return f"plano nuevo en {donde} ({pagado})"

    raise ValueError(f"un defecto de {arreglo.capa} no se arregla montando")


def _en_cache() -> set[str]:
    """Lo que hay comprado ahora mismo, para saber después qué se ha pagado."""
    encontrado = set()
    for carpeta in (clips.CACHE_DIR, images.CACHE_DIR):
        try:
            encontrado |= {os.path.join(carpeta, n) for n in os.listdir(carpeta)}
        except OSError:
            continue
    return encontrado
