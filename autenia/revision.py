"""Mirar lo que ha generado el modelo antes de que salga en un vídeo.

Un generador de imágenes acierta casi siempre y falla de una manera muy
concreta: manos con seis dedos, un brazo de más, una cara derretida al fondo.
Nada de eso lo detecta una comprobación de tamaño o de formato — sólo se ve
mirando —, y hasta ahora lo único que miraba era la persona que ya tenía el
vídeo delante.

Así que mira un modelo. Es la misma clase de trabajo que hace el preflight con
el guion: una negativa barata antes de gastar algo caro. Aquí lo caro no es el
dinero, que ya está gastado cuando esto corre, sino publicar bajo el nombre de
Autenia a un ser humano con tres manos.

Dos decisiones que evitan que este módulo cause más daño del que arregla:

* **La duda absuelve.** Si el revisor no contesta, contesta raro, o no hay clave,
  el material pasa. Un revisor que tumba vídeos cuando falla la red es peor que
  no tener revisor: el fallo sería invisible y diario.
* **Lo que se rechaza se borra del caché.** Un clip malo cuesta lo mismo la
  primera vez que la décima; lo que no puede pasar es que se pague una vez y
  vuelva cada semana porque encaja de significado con la escena de turno.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

from core_config import settings as autenia

from .gemini import TEXT_MODEL, GeminiError, request

#: Cuántos fotogramas se miran de un clip. Tres reparten el plano —principio,
#: medio y final— sin convertir la revisión en otro coste por segundo.
FRAMES_POR_CLIP = 3

#: El revisor se puede apagar. Cuesta una llamada por pieza generada y hay días
#: —una prueba de montaje, una tanda de muestras— en los que no aporta nada.
def enabled() -> bool:
    return (os.environ.get("AUTENIA_REVISION_VISUAL", "on").strip().lower()
            not in ("0", "false", "no", "off"))


_SISTEMA = """\
Eres el control de calidad de un canal de vídeo. Miras imágenes generadas por IA
y separas lo que no se puede publicar de lo que simplemente no es perfecto.

GRAVE — hace que el vídeo no se pueda publicar. Es lo que un espectador señala
con el dedo y comparte para reírse:
- Anatomía humana imposible: dedos de más o de menos, dedos fundidos, un pulgar
  en el lado equivocado, tres brazos, dos torsos, una persona duplicada.
- Caras deformes, asimétricas o derretidas, aunque estén desenfocadas al fondo.
- Objetos imposibles y llamativos: algo que flota sin apoyo, un objeto que
  atraviesa otro, un teclado con las teclas fundidas EN PRIMER PLANO.
- Un logotipo de marca real reconocible (WhatsApp, Windows, HP, Excel…).

LEVE — se anota pero no bloquea. Va a estar tapado por el subtítulo, o pasa en
cualquier fotografía de una oficina real:
- Texto, letras o números legibles en papeles, cuadernos o carteles.
- Una interfaz de software genérica en una pantalla, sin marca reconocible.
- Teclas o botones algo imprecisos en segundo plano y desenfocados.

Lo normal es una foto correcta. NO busques defectos donde no los hay: el
desenfoque, el grano o una composición sosa no son defectos de ningún tipo.

Si te dan varios fotogramas, son del mismo plano en movimiento: basta con que
uno tenga un problema grave para que el plano entero lo tenga.

Sé concreto: "la mano derecha tiene seis dedos", no "hay errores anatómicos".\
"""

_ESQUEMA = {
    "type": "object",
    "properties": {
        "graves": {"type": "array", "items": {"type": "string"}},
        "leves": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["graves", "leves"],
}


@dataclass
class Veredicto:
    """Lo que el revisor opina de una pieza.

    Dos listas y no una nota del 1 al 10: la pregunta que hay que contestar es
    binaria —¿sale o no sale?— y lo único que la decide son los defectos graves.
    Medido el 2 de agosto sobre el caché entero: bloqueando cualquier defecto se
    rechazaba el 80 % del material, y un canal cuyos vídeos son todos tarjetas
    de texto no tiene un control de calidad, tiene un apagón.
    """

    graves: list[str] = field(default_factory=list)
    leves: list[str] = field(default_factory=list)
    revisado: bool = False        # False cuando no se pudo mirar

    @property
    def apto(self) -> bool:
        return not self.graves

    def __bool__(self) -> bool:
        return self.apto

    def __str__(self) -> str:
        if not self.revisado:
            return "sin revisar"
        if self.graves:
            return "; ".join(self.graves)
        if self.leves:
            return "apto (leve: " + "; ".join(self.leves) + ")"
        return "apto"


def _jpeg(path: str) -> tuple[str, bytes] | None:
    try:
        with open(path, "rb") as handle:
            return ("image/jpeg" if path.lower().endswith((".jpg", ".jpeg"))
                    else "image/png"), handle.read()
    except OSError:
        return None


def frames_of(path: str, cuantos: int = FRAMES_POR_CLIP) -> list[str]:
    """Fotogramas repartidos por el clip, como ficheros temporales.

    Se extraen en vez de mandar el vídeo entero porque un mp4 de ocho segundos
    son megas de entrada para una pregunta que se contesta mirando tres
    imágenes.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return []

    carpeta = tempfile.mkdtemp(prefix="autenia-revision-")
    salidas: list[str] = []
    for indice in range(cuantos):
        # Repartidos por el clip, evitando el primer y el último fotograma:
        # el arranque de una generación suele ser el momento más limpio y el
        # final el más degradado, y ninguno de los dos representa el plano.
        momento = round(1.0 + indice * 2.5, 2)
        destino = os.path.join(carpeta, f"f{indice}.jpg")
        resultado = subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-ss", str(momento), "-i", path,
             "-frames:v", "1", "-vf", "scale=512:-1", destino],
            capture_output=True)
        if resultado.returncode == 0 and os.path.isfile(destino):
            salidas.append(destino)
    return salidas


async def _preguntar(imagenes: list[str]) -> Veredicto:
    partes: list[dict] = [{"text": "¿Es publicable esto?"}]
    for imagen in imagenes:
        leido = _jpeg(imagen)
        if leido is None:
            continue
        mime, datos = leido
        partes.append({"inlineData": {"mimeType": mime,
                                      "data": base64.b64encode(datos).decode()}})
    if len(partes) == 1:
        return Veredicto(revisado=False)

    payload = await request(
        f"models/{TEXT_MODEL}:generateContent",
        {
            "contents": [{"parts": partes}],
            "systemInstruction": {"parts": [{"text": _SISTEMA}]},
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": _ESQUEMA,
                "temperature": 0.0,
            },
        },
        timeout=120.0,
    )
    texto = "".join(
        parte.get("text", "")
        for parte in payload["candidates"][0]["content"]["parts"])
    datos = json.loads(texto)
    return Veredicto(graves=[str(p) for p in datos.get("graves", [])],
                     leves=[str(p) for p in datos.get("leves", [])],
                     revisado=True)


async def revisar(path: str) -> Veredicto:
    """¿Se puede publicar esta imagen o este clip?

    Nunca lanza. Cualquier fallo —sin clave, sin red, respuesta rara— devuelve
    un veredicto "sin revisar" que el llamante trata como apto: el material ya
    está pagado y un revisor que se cae no debe dejar al canal sin vídeo.
    """
    if not enabled() or not os.path.isfile(path):
        return Veredicto(revisado=False)

    es_video = path.lower().endswith((".mp4", ".mov", ".webm", ".mkv"))
    imagenes = frames_of(path) if es_video else [path]
    if not imagenes:
        return Veredicto(revisado=False)

    try:
        return await _preguntar(imagenes)
    except (GeminiError, KeyError, IndexError, ValueError, json.JSONDecodeError) as exc:
        print(f"[revision] no se pudo revisar {os.path.basename(path)}: "
              f"{str(exc)[:120]}")
        return Veredicto(revisado=False)
    finally:
        if es_video and imagenes:
            shutil.rmtree(os.path.dirname(imagenes[0]), ignore_errors=True)


async def descartar(path: str, motivo: str) -> None:
    """Sacar del caché una pieza que no se puede usar.

    Borrar y no sólo marcar: el caché se empareja por significado, así que un
    clip defectuoso que se quede ahí vuelve a salir en la escena de papeleo del
    mes que viene, y entonces ya nadie recuerda por qué era malo.
    """
    try:
        os.remove(path)
    except OSError:
        return

    # El sidecar de los clips guarda qué muestra cada uno; si se borra el fichero
    # y se deja la ficha, la biblioteca promete un plano que ya no existe.
    sidecar = os.path.join(os.path.dirname(path), "library.json")
    if not os.path.isfile(sidecar):
        return
    try:
        with open(sidecar, encoding="utf-8") as handle:
            fichas = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return
    if fichas.pop(os.path.basename(path), None) is not None:
        tmp = f"{sidecar}.part"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(fichas, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, sidecar)
    print(f"[revision] descartado {os.path.basename(path)}: {motivo}")


async def revisar_muchos(paths: list[str]) -> dict[str, Veredicto]:
    """Revisar varias piezas a la vez. Usado para repasar el caché entero."""
    veredictos = await asyncio.gather(*(revisar(p) for p in paths),
                                      return_exceptions=True)
    return {
        path: (v if isinstance(v, Veredicto) else Veredicto(revisado=False))
        for path, v in zip(paths, veredictos)
    }
