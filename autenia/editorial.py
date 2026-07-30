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
#: How far back a story can be and still count as "today's news".
#:
#: Lives here, next to the freshness curve that has to agree with it, and is
#: imported by the collector. Raised from 14 on 2026-07-30: Spanish SME
#: reporting is not a daily firehose — the usable pieces are studies and
#: official statistics that land a few times a month — and a fortnight's window
#: kept returning nothing at all.
MAX_AGE_DAYS = 30

#: A candidate must touch at least one of these to count as Autenia's business.
#:
#: The first block is technology-shaped and was the whole list until
#: 2026-07-30, when it rejected "El 76% de las empresas reconoce que la carga
#: administrativa les resta tiempo para crecer" — a real outlet, a real figure,
#: and a textbook description of what Autenia sells against — while letting two
#: vendors' e-invoicing guides through on the word "facturación".
#:
#: The lesson is the same one the search angles learned: **name the problem, not
#: the product.** A manager suffers paperwork and repeated typing; they do not
#: suffer an absent CRM. The second block is that vocabulary.
RELEVANCE = (
    # What Autenia builds
    "automatizacion", "automatizar", "agente", "agentes", "chatbot",
    "inteligencia artificial", "ia", "whatsapp business", "crm", "erp",
    "cuadro de mando", "dashboard", "business intelligence", "bi",
    "pipeline de datos", "integracion", "api", "flujo de trabajo", "workflow",
    "atencion al cliente", "captacion de leads", "facturacion", "inventario",
    "informes", "reporting", "productividad", "pyme", "pymes", "autonomo",
    # What the viewer actually suffers
    "carga administrativa", "tarea administrativa", "tareas administrativas",
    "trabajo administrativo", "gestion administrativa", "burocracia",
    "burocratico", "papeleo", "tramite", "tramites", "gestion documental",
    "documentacion", "tareas repetitivas", "trabajo repetitivo",
    "horas perdidas", "tiempo perdido", "introducir datos", "hoja de calculo",
    "excel", "errores manuales", "cobros", "morosidad", "plazos de pago",
    "presupuestos", "albaranes", "nominas", "obligaciones fiscales",
    "cumplimiento normativo", "digitalizacion", "absentismo",
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

#: Subject matter that Autenia wants to be the channel for: artificial
#: intelligence itself, the tools built on it, and what they can now do.
#:
#: Added 2026-07-30 at Juan's request. It sits deliberately alongside the
#: earlier finding that searches must aim at the *problem* rather than the
#: technology — those are not in conflict. What scored near zero on 2026-07-29
#: was vendor self-promotion, not AI as a subject: a consultancy's own advert,
#: a NIST notice, an Oracle launch. A capability that changed, a tool an SME
#: can actually use, an adoption figure — those are exactly Autenia's ground.
#: The defence against the promo pile is :data:`PROMOTIONAL`, below, not
#: refusing to talk about AI.
AI_TOPICS = (
    "inteligencia artificial", "ia generativa", "ia", "modelo de lenguaje",
    "llm", "chatgpt", "gpt", "copilot", "gemini", "claude", "llama",
    "agente de ia", "agentes de ia", "agente autonomo", "asistente virtual",
    "chatbot", "aprendizaje automatico", "machine learning", "red neuronal",
    "vision artificial", "reconocimiento de voz", "transcripcion automatica",
    "generacion de texto", "generacion de imagenes", "automatizacion inteligente",
    "copiloto", "prompt", "openai", "anthropic", "deepseek", "mistral",
    "herramienta de ia", "herramientas de ia", "aplicacion de ia",
)

#: Marketing dressed as news. Penalised rather than excluded outright: a real
#: story sometimes uses this vocabulary, and a hard filter would take it too.
PROMOTIONAL = (
    "lanza", "lanzamiento", "presenta su", "anuncia el", "nueva version",
    "ya disponible", "el mejor", "los mejores", "guia definitiva",
    "comparativa", "ranking de", "precios y planes", "prueba gratis",
    "descuento", "webinar", "patrocinado", "nota de prensa",
)

#: What each signal is worth. Fit and subject dominate: Autenia wants to be the
#: place people hear about AI, and a piece that speaks to a cost the viewer
#: already feels beats a fresher piece that does not.
#:
#: `tema` was carved out of `encaje` and `actualidad` on 2026-07-30 rather than
#: added on top, because weights that do not sum to one make MIN_SCORE mean
#: something different from one release to the next.
WEIGHTS = {
    "actualidad": 0.12,
    "tema": 0.18,
    "dolor": 0.18,
    "encaje": 0.15,
    "hook": 0.13,
    "evidencia": 0.08,
    "visual": 0.08,
    "conversion": 0.08,
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
    """Full marks for today, fading to nothing at the edge of the window.

    Tied to :data:`MAX_AGE_DAYS` rather than to its own number. They were two
    separate constants until 2026-07-30, when the window widened to 30 days and
    this kept fading to zero at 14: everything from the older fortnight was
    admitted by the collector and then scored as though it were worthless,
    which is the worst of both rules.
    """
    if age_days <= 1:
        return 1.0
    if age_days >= MAX_AGE_DAYS:
        return 0.0
    return max(0.0, 1.0 - (age_days - 1) / (MAX_AGE_DAYS - 1))


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
        "tema": subject_score(candidate),
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


#: A headline is the claim; the summary is context. A story that is *about* AI
#: says so in its title, so a hit there counts double.
_TITLE_BONUS = 2


def subject_score(candidate: Candidate) -> float:
    """How much this is an AI story, minus how much it is an advert.

    Returns 0..1. The floor is 0.35 rather than 0: a piece about administrative
    burden with no mention of AI is still Autenia's business — that is what the
    company sells against — it simply is not the subject Juan asked to favour.
    """
    text = candidate.haystack
    title = _normalise(candidate.title)

    hits = sum(1 for term in AI_TOPICS if _has_term(text, term))
    hits += sum(_TITLE_BONUS for term in AI_TOPICS if _has_term(title, term))
    # Four weighted hits is a piece squarely about AI; more adds nothing.
    subject = min(1.0, hits / 4)

    # Marketing language pulls it back down. A launch write-up can mention AI
    # in every paragraph and still be an advert, and an advert is the one thing
    # this channel cannot afford to repost.
    promo = min(1.0, sum(1 for term in PROMOTIONAL if _has_term(text, term)) / 2)

    return round(max(0.0, 0.35 + 0.65 * subject - 0.45 * promo), 4)


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
