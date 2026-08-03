"""The library of Autenia's own footage, and how a scene finds its shot.

A scene in the script asks for something concrete ("captura de un formulario de
ERP"). This module answers with a file from Autenia's own material, or with
nothing — and nothing is a real answer: the renderer falls back to typography
rather than showing footage that does not match what the narration claims.

Two rules that are not negotiable:

* **Only Autenia's own material.** No stock, no scraped screenshots. Rights are
  one reason; the other is that generic footage is exactly what makes an
  automation consultancy look like every other automation consultancy.
* **A weak match is worse than none.** A "dashboard" clip under narration about
  WhatsApp support reads as padding, and padding is what viewers scroll past.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass, field

VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".mkv"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

#: Below this share of matched words, the shot is not about the same thing.
#: Deliberately high: the fallback is honest typography, not a blank screen.
MIN_MATCH = 0.34

#: Words that carry no meaning for matching. Without this, "de" and "un" in a
#: filename would match every scene equally.
STOPWORDS = {
    "de", "del", "la", "el", "los", "las", "un", "una", "unos", "unas", "y", "o",
    "en", "con", "sin", "por", "para", "que", "se", "su", "sus", "al", "lo",
    "captura", "pantalla", "video", "clip", "grabacion", "imagen", "plano",
    "mostrando", "muestra", "vista",
}

#: Words that mean the same thing to a viewer. Keyed by what a script tends to
#: say, valued by what a filename tends to say.
SYNONYMS = {
    "cuadro": {"dashboard", "panel"},
    "mando": {"dashboard", "panel"},
    "dashboard": {"panel", "cuadro", "mando"},
    "panel": {"dashboard", "cuadro", "mando"},
    "hoja": {"excel", "calculo", "spreadsheet"},
    "calculo": {"excel", "hoja", "spreadsheet"},
    "excel": {"hoja", "calculo"},
    "chat": {"whatsapp", "conversacion", "mensaje"},
    "whatsapp": {"chat", "conversacion", "mensaje"},
    "conversacion": {"chat", "whatsapp", "mensaje"},
    "agente": {"bot", "asistente", "chatbot"},
    "bot": {"agente", "asistente", "chatbot"},
    "formulario": {"form", "campos", "alta"},
    "pedido": {"pedidos", "order", "compra"},
    "factura": {"facturas", "facturacion"},
    "informe": {"informes", "reporte", "reporting"},
    "flujo": {"workflow", "automatizacion", "proceso"},
    "automatizacion": {"workflow", "flujo", "proceso"},
    "datos": {"data", "registros", "informacion"},
    "correo": {"email", "bandeja", "mail"},
    "email": {"correo", "bandeja", "mail"},
}


def _normalise(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def keywords(text: str) -> set[str]:
    """Meaningful words, accent-free and expanded with their synonyms."""
    words = {
        word for word in re.findall(r"[a-z0-9]+", _normalise(text))
        if len(word) > 2 and word not in STOPWORDS
    }
    expanded = set(words)
    for word in words:
        expanded |= SYNONYMS.get(word, set())
    return expanded


@dataclass
class Asset:
    """One piece of Autenia's own footage."""

    path: str
    tags: set[str] = field(default_factory=set)
    description: str = ""

    @property
    def is_video(self) -> bool:
        return os.path.splitext(self.path)[1].lower() in VIDEO_SUFFIXES

    @property
    def terms(self) -> set[str]:
        """Everything this asset can be matched on."""
        stem = os.path.splitext(os.path.basename(self.path))[0]
        return keywords(f"{stem} {self.description} {' '.join(self.tags)}")


@dataclass
class Match:
    asset: Asset
    score: float


def load_library(directory: str) -> list[Asset]:
    """Read a folder of footage. Missing folder means an empty library.

    Tags come from the filename, so ``agente-whatsapp-contestando.mp4`` needs no
    configuration at all. A sidecar ``library.json`` can add descriptions for
    clips whose filename cannot carry enough meaning.
    """
    if not directory or not os.path.isdir(directory):
        return []

    sidecar: dict = {}
    sidecar_path = os.path.join(directory, "library.json")
    if os.path.isfile(sidecar_path):
        try:
            with open(sidecar_path, encoding="utf-8") as handle:
                sidecar = json.load(handle)
        except (json.JSONDecodeError, OSError):
            # A broken sidecar must not take the whole library down: filenames
            # alone still match well enough to render.
            sidecar = {}

    assets: list[Asset] = []
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        if os.path.splitext(name)[1].lower() not in VIDEO_SUFFIXES | IMAGE_SUFFIXES:
            continue
        meta = sidecar.get(name, {})
        assets.append(Asset(
            path=path,
            tags=set(meta.get("tags", [])),
            description=meta.get("description", ""),
        ))
    return assets


def add_to_library(source: str, directory: str, *, description: str = "",
                   name: str = "") -> str:
    """Meter un fichero en la biblioteca con lo que hace falta para encontrarlo.

    Un fichero suelto en la carpeta ya se empareja por su nombre, pero lo que
    llega de Telegram se llama ``file_12.jpg``, que no significa nada para
    nadie. Así que la descripción se guarda en el ``library.json`` de al lado,
    que es el mecanismo que ya existía para el metraje cuyo nombre no da para
    tanto.

    El nombre en disco sale de la descripción, no de un identificador: una
    biblioteca que se puede leer con ``ls`` es una que alguien puede ordenar a
    mano el día que haga falta.
    """
    os.makedirs(directory, exist_ok=True)
    extension = os.path.splitext(source)[1].lower() or ".jpg"
    base = _slug(name or description) or "material"

    destino = os.path.join(directory, f"{base}{extension}")
    repeticion = 2
    while os.path.exists(destino):
        destino = os.path.join(directory, f"{base}-{repeticion}{extension}")
        repeticion += 1

    with open(source, "rb") as entrada, open(destino, "wb") as salida:
        salida.write(entrada.read())

    if description:
        _remember(directory, os.path.basename(destino), description)
    return destino


def _slug(text: str) -> str:
    """Un nombre de fichero legible a partir de una frase."""
    limpio = _normalise(text)
    palabras = [p for p in limpio.split() if p and p not in STOPWORDS]
    return "-".join(palabras[:6])


def _remember(directory: str, filename: str, description: str) -> None:
    """Anotar la descripción en el sidecar, sin perder lo que ya había."""
    sidecar = os.path.join(directory, "library.json")
    fichas: dict = {}
    if os.path.isfile(sidecar):
        try:
            with open(sidecar, encoding="utf-8") as handle:
                fichas = json.load(handle)
        except (json.JSONDecodeError, OSError):
            fichas = {}

    fichas[filename] = {"description": description}
    parcial = f"{sidecar}.part"
    with open(parcial, "w", encoding="utf-8") as handle:
        json.dump(fichas, handle, ensure_ascii=False, indent=2)
    os.replace(parcial, sidecar)


def match(request: str, library: list[Asset], *,
          exclude: set[str] | None = None) -> Match | None:
    """The best shot for a scene, or None when nothing fits well enough.

    ``exclude`` holds paths already used in this video: reusing one clip for
    three scenes looks like the library is empty, which it then is.
    """
    wanted = keywords(request)
    if not wanted:
        return None

    used = exclude or set()
    best: Match | None = None

    for asset in library:
        if asset.path in used:
            continue
        terms = asset.terms
        if not terms:
            continue
        overlap = wanted & terms
        if not overlap:
            continue
        # Share of the *request* that the asset covers. Scoring against the
        # asset's own words instead would let a one-word filename win
        # everything by being trivially "fully relevant".
        score = len(overlap) / len(wanted)
        if best is None or score > best.score:
            best = Match(asset=asset, score=score)

    if best is None or best.score < MIN_MATCH:
        return None
    return best


def plan_visuals(scene_requests: list[str],
                 library: list[Asset]) -> list[Asset | None]:
    """Pick a shot per scene, without repeating one inside the same video.

    ``None`` means the renderer should use a typographic card for that scene.
    """
    used: set[str] = set()
    chosen: list[Asset | None] = []
    for request in scene_requests:
        found = match(request, library, exclude=used)
        if found is None:
            chosen.append(None)
            continue
        used.add(found.asset.path)
        chosen.append(found.asset)
    return chosen


def coverage(plan: list[Asset | None]) -> float:
    """Share of scenes backed by real footage. Reported in the Telegram review.

    Low coverage is not an error, it is a prompt: it tells the operator which
    videos would improve most from one more screen recording.
    """
    if not plan:
        return 0.0
    return sum(1 for asset in plan if asset is not None) / len(plan)
