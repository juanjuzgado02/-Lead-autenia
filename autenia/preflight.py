"""The last check before anything is rendered or paid for.

Everything here is cheap and everything here is a refusal. A claim without a
source, a script that runs to fifty seconds, a video that would cross the
budget — each is far cheaper to catch now than after it has been rendered, or
after it has been published under Autenia's name.

Preflight returns problems rather than raising, so the caller can report all of
them at once instead of fixing them one round trip at a time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from core_config import settings as autenia

#: Spanish is read at roughly this pace in a short-form voiceover. Used to
#: estimate duration from the script before a single second is synthesised.
WORDS_PER_SECOND = 2.6

#: The brief's window. Outside it the piece is either rushed or padded.
MIN_SECONDS = 20
MAX_SECONDS = 45

#: Phrasing the brief forbids outright.
BANNED_PHRASES = (
    "link in bio",
    "enlace en la bio",
    "enlace está en la bio",
    "hazte viral",
    "garantizado",
    "ingresos pasivos",
)

#: A bare number followed by a unit reads as a factual claim to the viewer.
_NUMERIC_CLAIM = re.compile(
    r"\d+\s*(?:%|por ciento|euros?|€|horas?|minutos?|dias?|días?|semanas?|meses?|veces)",
    re.IGNORECASE,
)


@dataclass
class Problem:
    """One reason not to proceed."""

    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.rule}: {self.detail}"


@dataclass
class Result:
    """The verdict. Falsy when anything blocks, so `if not result:` reads right."""

    problems: list[Problem] = field(default_factory=list)
    estimated_seconds: float = 0.0
    estimated_cents: int = 0
    word_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems

    def __bool__(self) -> bool:
        return self.ok

    def __str__(self) -> str:
        if self.ok:
            return (f"preflight ok: ~{self.estimated_seconds:.0f}s, "
                    f"~{self.estimated_cents / 100:.2f} EUR")
        return "preflight failed:\n" + "\n".join(f"  - {p}" for p in self.problems)


#: Ways a narration can name where a figure came from. The generic ones exist
#: because a script that says "Según Europa Press" three times in a row reads
#: like a teleprinter; the second mention is allowed to be "el mismo informe".
_ATTRIBUTION = re.compile(
    r"\b(seg[uú]n|de acuerdo con|conforme a|un informe|el informe|"
    r"ese informe|el mismo informe|un estudio|el estudio|ese estudio|"
    r"los datos de|esos datos|estos datos|las cifras de|datos del|"
    r"public[oó]|recoge|cifra en|estima)\b",
    re.IGNORECASE)

#: Words too common to prove a source was named. "Blog" or "España" appearing
#: in a narration says nothing about attribution.
_WEAK_SOURCE_WORDS = frozenset({
    "blog", "empresas", "empresa", "espana", "españa", "news", "diario",
    "revista", "digital", "grupo", "el", "la", "los", "las", "de", "del",
})


def _says_its_source(narration: str, source: str) -> bool:
    """Whether the spoken line actually tells the viewer where this came from.

    Accepts either a distinctive word from the source's name ("Según *Europa
    Press*") or an attribution turn of phrase, which covers the repeat case the
    prompt allows.
    """
    lowered = narration.lower()
    for word in re.findall(r"\w{4,}", source.lower()):
        if word not in _WEAK_SOURCE_WORDS and word in lowered:
            return True
    return bool(_ATTRIBUTION.search(narration))


def estimate_seconds(text: str) -> float:
    words = len(re.findall(r"\S+", text))
    return words / WORDS_PER_SECOND if words else 0.0


def check(script: dict, *, narration: str, estimated_cents: int = 0,
          spent_this_video_cents: int = 0,
          spent_this_month_cents: int = 0) -> Result:
    """Everything that must hold before a script becomes a video."""
    problems: list[Problem] = []
    words = len(re.findall(r"\S+", narration))
    seconds = estimate_seconds(narration)

    # -- length ------------------------------------------------------------
    hard_max = autenia.limits.max_duration_s
    if seconds > hard_max:
        problems.append(Problem(
            "duracion", f"~{seconds:.0f}s supera el máximo duro de {hard_max}s"))
    elif seconds > MAX_SECONDS:
        problems.append(Problem(
            "duracion", f"~{seconds:.0f}s supera los {MAX_SECONDS}s del brief"))
    elif seconds < MIN_SECONDS:
        problems.append(Problem(
            "duracion", f"~{seconds:.0f}s no llega a los {MIN_SECONDS}s del brief"))

    # -- claims ------------------------------------------------------------
    scenes = script.get("escenas", [])
    if not scenes:
        problems.append(Problem("estructura", "el guion no tiene escenas"))

    for index, scene in enumerate(scenes):
        kind = scene.get("tipo")
        narracion = scene.get("narracion", "")

        if kind not in ("hecho", "opinion"):
            problems.append(Problem(
                "afirmacion",
                f"escena {index + 1} no declara si es hecho u opinión"))
            continue

        if kind == "hecho" and not (scene.get("fuente") or "").strip():
            problems.append(Problem(
                "afirmacion",
                f"escena {index + 1} afirma un hecho sin citar fuente"))
        elif kind == "hecho" and not _says_its_source(narracion, scene["fuente"]):
            # The `fuente` field is for the reviewer; the viewer never sees it.
            # A figure with no name behind it sounds invented, which is the
            # difference between a fact and a salesman's promise.
            problems.append(Problem(
                "afirmacion",
                f"escena {index + 1} no nombra su fuente en la narración "
                f"(«{scene['fuente']}»): añade «Según…» o similar"))

        # A number inside an opinion is a fact wearing a disguise.
        if kind == "opinion" and _NUMERIC_CLAIM.search(narracion):
            problems.append(Problem(
                "afirmacion",
                f"escena {index + 1} da una cifra pero está marcada como opinión"))

        if not (scene.get("visual") or "").strip():
            problems.append(Problem(
                "visual", f"escena {index + 1} no dice qué se ve en pantalla"))

    # -- forbidden phrasing ------------------------------------------------
    lowered = narration.lower()
    for phrase in BANNED_PHRASES:
        if phrase in lowered:
            problems.append(Problem("prohibido", f"la narración dice '{phrase}'"))

    if not (script.get("hook") or "").strip():
        problems.append(Problem("hook", "el guion no abre con un hook"))

    if not (script.get("cta") or "").strip():
        problems.append(Problem("cta", "el guion no lleva a ninguna parte"))

    # -- money -------------------------------------------------------------
    video_limit = int(round(autenia.limits.max_cost_per_video * 100))
    would_cost = spent_this_video_cents + estimated_cents
    if would_cost > video_limit:
        problems.append(Problem(
            "coste",
            f"llegaría a {would_cost / 100:.2f} EUR, sobre el límite de "
            f"{video_limit / 100:.2f} por vídeo"))

    month_limit = int(round(autenia.limits.max_cost_per_month * 100))
    if spent_this_month_cents + estimated_cents > month_limit:
        problems.append(Problem(
            "coste",
            f"el mes llegaría a "
            f"{(spent_this_month_cents + estimated_cents) / 100:.2f} EUR, sobre "
            f"el límite de {month_limit / 100:.2f}"))

    return Result(
        problems=problems,
        estimated_seconds=round(seconds, 1),
        estimated_cents=estimated_cents,
        word_count=words,
    )
