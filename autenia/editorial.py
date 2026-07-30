"""Choosing what to make today, before anything costs money.

Two stages, cheapest first:

1. **Hard filters** — deterministic exclusions from the content brief §6. No
   model call, no cost. A candidate that trips one of these is gone; these are
   not weighted against anything, because "mostly not about politics" is not a
   thing.
2. **Scoring** — a weighted read of the signals that predict a useful video.
   Cheap arithmetic over fields the collector already gathered.

Only what survives both gets a script, and only a script that passes preflight
gets rendered. The whole point is that the expensive step runs last and rarely.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _normalise(text: str) -> str:
    """Lowercase and strip accents, so 'política' and 'politica' both match."""
    lowered = text.lower()
    decomposed = unicodedata.normalize("NFD", lowered)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _has_term(haystack: str, term: str) -> bool:
    """Whole-word match, so 'IA' does not fire inside 'financiera'."""
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", haystack) is not None


# --------------------------------------------------------------------------
# Hard exclusions (brief §6)
# --------------------------------------------------------------------------

POLITICS = (
    "gobierno", "ministro", "ministra", "elecciones", "partido politico",
    "psoe", "pp", "vox", "sumar", "podemos", "congreso", "senado", "moncloa",
    "presidente del gobierno", "ley organica", "referendum", "manifestacion",
)

OUTRAGE = (
    "polemica", "escandalo", "indignacion", "estalla", "arremete", "destroza",
    "ridiculiza", "humilla", "guerra abierta", "se lia", "zasca",
)

RUMOUR = (
    "rumor", "se rumorea", "al parecer", "fuentes no confirmadas", "filtracion",
    "supuestamente", "podria estar", "no confirmado",
)

MISLEADING = (
    "hazte viral", "te hago viral", "garantizado", "ingresos pasivos",
    "hazte rico", "sin esfuerzo", "resultados garantizados", "dinero facil",
    "formula secreta", "truco definitivo",
)

# Naming a competitor to attack it. Discussing a tool factually is fine; the
# collector flags the intent, this catches the obvious phrasings.
ATTACKS = (
    "es una estafa", "es un fraude", "no sirve para nada", "timo",
    "peor que", "destruye a", "acaba con",
)

#: Terms that show a candidate actually touches what Autenia sells. A piece with
#: none of these has no demonstrable relation to the business and is dropped —
#: that is the filter that stops the feed becoming generic AI news.
RELEVANCE = (
    "automatizacion", "automatizar", "agente", "agentes", "chatbot",
    "inteligencia artificial", "ia", "whatsapp business", "crm", "erp",
    "cuadro de mando", "dashboard", "business intelligence", "bi",
    "pipeline de datos", "integracion", "api", "flujo de trabajo", "workflow",
    "atencion al cliente", "captacion de leads", "facturacion", "inventario",
    "informes", "reporting", "productividad", "pyme", "pymes", "autonomo",
)


@dataclass(frozen=True)
class Rejection:
    """Why a candidate was dropped. Kept so the log explains itself."""

    rule: str
    detail: str


@dataclass
class Candidate:
    """A story the collector found, with the provenance the brief requires."""

    title: str
    url: str
    publisher: str
    published_at: datetime
    fetched_at: datetime
    facts: list[str] = field(default_factory=list)
    summary: str = ""

    #: Set by the collector when it can tell; None means "not assessed".
    mentions_client_data: bool | None = None
    usage_rights_ok: bool | None = None

    @property
    def haystack(self) -> str:
        return _normalise(" ".join([self.title, self.summary, *self.facts]))

    @property
    def topic_hash(self) -> str:
        """Stable identity for deduplication across days.

        Hashes the normalised title rather than the URL: the same story
        republished elsewhere is still the same story.
        """
        words = sorted(set(re.findall(r"\w+", _normalise(self.title))))
        return hashlib.sha256(" ".join(words).encode()).hexdigest()[:32]

    def age_days(self, now: datetime | None = None) -> float:
        moment = now or datetime.now(timezone.utc)
        published = self.published_at
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        return max(0.0, (moment - published).total_seconds() / 86400)


def hard_rejections(candidate: Candidate) -> list[Rejection]:
    """Every brief §6 exclusion this candidate trips. Empty means it survives."""
    text = candidate.haystack
    found: list[Rejection] = []

    for rule, terms in (
        ("politica", POLITICS),
        ("polemica", OUTRAGE),
        ("rumor", RUMOUR),
        ("promesa_enganosa", MISLEADING),
        ("ataque", ATTACKS),
    ):
        hit = next((term for term in terms if _has_term(text, term)), None)
        if hit:
            found.append(Rejection(rule, f"contiene '{hit}'"))

    if candidate.mentions_client_data:
        found.append(Rejection(
            "datos_de_cliente",
            "identifica a un cliente sin autorizacion escrita",
        ))

    if candidate.usage_rights_ok is False:
        found.append(Rejection(
            "derechos_de_uso", "el material no se puede reutilizar",
        ))

    if not any(_has_term(text, term) for term in RELEVANCE):
        found.append(Rejection(
            "sin_relacion",
            "no toca ningun servicio de Autenia de forma demostrable",
        ))

    if not candidate.facts:
        found.append(Rejection(
            "sin_evidencia", "no se guardo ningun fragmento factual con fuente",
        ))

    return found


def survives(candidate: Candidate) -> bool:
    return not hard_rejections(candidate)


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

#: What each signal is worth. Fit and pain dominate: a piece that speaks to a
#: real cost the viewer already feels beats a fresher piece that does not.
WEIGHTS = {
    "actualidad": 0.15,
    "dolor": 0.20,
    "encaje": 0.20,
    "hook": 0.15,
    "evidencia": 0.10,
    "visual": 0.10,
    "conversion": 0.10,
}

#: Below this, do not write a script. Tuned to be restrictive: the brief says a
#: blank day beats a mediocre video.
MIN_SCORE = 0.55


@dataclass(frozen=True)
class Score:
    total: float
    parts: dict[str, float]

    def __str__(self) -> str:
        detail = ", ".join(f"{k} {v:.2f}" for k, v in sorted(self.parts.items()))
        return f"{self.total:.2f} ({detail})"


def _freshness(age_days: float) -> float:
    """Full marks for today, fading to nothing over a fortnight."""
    if age_days <= 1:
        return 1.0
    if age_days >= 14:
        return 0.0
    return max(0.0, 1.0 - (age_days - 1) / 13)


def score(candidate: Candidate, *, judgments: dict[str, float] | None = None,
          now: datetime | None = None) -> Score:
    """Rate a candidate from 0 to 1.

    ``judgments`` carries the signals only a model can read (pain, hook
    strength, visual potential, conversion intent), each 0..1. Absent ones
    default to 0.5 rather than 0, so a missing judgment neither rewards nor
    condemns a candidate.
    """
    supplied = judgments or {}
    text = candidate.haystack

    relevance_hits = sum(1 for term in RELEVANCE if _has_term(text, term))

    parts = {
        "actualidad": _freshness(candidate.age_days(now)),
        # More distinct service terms means a tighter fit, saturating at four.
        "encaje": min(1.0, relevance_hits / 4),
        # Evidence is countable: how many sourced facts survived collection.
        "evidencia": min(1.0, len(candidate.facts) / 3),
        "dolor": supplied.get("dolor", 0.5),
        "hook": supplied.get("hook", 0.5),
        "visual": supplied.get("visual", 0.5),
        "conversion": supplied.get("conversion", 0.5),
    }

    total = sum(parts[name] * weight for name, weight in WEIGHTS.items())
    return Score(total=round(total, 4), parts=parts)


def rank(candidates: list[Candidate], *,
         judgments: dict[str, dict[str, float]] | None = None,
         now: datetime | None = None) -> list[tuple[Candidate, Score]]:
    """Survivors, best first. Rejected candidates never appear."""
    supplied = judgments or {}
    scored = [
        (candidate, score(candidate, judgments=supplied.get(candidate.url), now=now))
        for candidate in candidates
        if survives(candidate)
    ]
    return sorted(scored, key=lambda pair: pair[1].total, reverse=True)


def pick(candidates: list[Candidate], *,
         judgments: dict[str, dict[str, float]] | None = None,
         now: datetime | None = None) -> tuple[Candidate, Score] | None:
    """Today's video, or None.

    None is a valid outcome and must not be worked around: the brief prefers a
    blank day to a mediocre video, and the scheduler is expected to report the
    skip rather than lower the bar.
    """
    ranked = rank(candidates, judgments=judgments, now=now)
    if not ranked:
        return None
    best, best_score = ranked[0]
    if best_score.total < MIN_SCORE:
        return None
    return best, best_score
