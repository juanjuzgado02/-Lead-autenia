"""Finding what happened today that Autenia can honestly talk about.

Two calls, because Gemini refuses to combine them: grounded search cannot be
used with a response schema (``Tool use with a response mime type:
'application/json' is unsupported``). So the first call searches and answers in
prose, and a second, cheap call turns that prose into structured candidates.

The URLs grounding returns are ``vertexaisearch.cloud.google.com`` redirects,
not canonical addresses. They are resolved here: a source that cannot be cited
by its real URL is not a source, and the brief requires the canonical one.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx

from . import urls
from .editorial import MAX_AGE_DAYS as editorial_window
from .editorial import Candidate
from .gemini import TEXT_MODEL, GeminiError, Usage, json_call, request, usage_of

#: How far back a story can be and still count as "today's news". Defined in
#: :mod:`editorial`, because the freshness curve there has to agree with it.
MAX_AGE_DAYS = editorial_window

#: Searches aim at the viewer's problem, not at the technology.
#:
#: Measured 2026-07-29: technology-shaped angles ("agentes de IA para
#: empresas") returned a consultancy's own promo, a piece on NIST regulation
#: and an Oracle launch — all correctly scored near zero, because none of them
#: describes something a manager suffers on a Monday. Ask about the pain and
#: the evidence instead, and the results are usable.
#:
#: Measured 2026-07-30: five near-identical phrasings of ONE angle ("estudio con
#: cifras sobre tiempo perdido en tareas administrativas"), all demanded at once
#: in a single query, returned zero results — the model reported that nothing
#: satisfied every condition simultaneously. Variety across genuinely different
#: pains matters more than precision within one of them.
#: Two families, mixed on purpose.
#:
#: The AI block was added on 2026-07-30: Autenia wants to be the channel people
#: hear about this from, and scoring it higher (``editorial.subject_score``)
#: does nothing if the collector never brings any back. They are phrased around
#: what a tool *does for a business*, not around who released it, because
#: "novedades de IA" returns a wall of launch write-ups.
#:
#: The pain block stays. A week of nothing but model releases is a tech channel,
#: not a consultancy's, and the viewer is a manager who does not follow this.
SEARCH_ANGLES = (
    # What AI can now do for a small company
    "herramientas de inteligencia artificial para pequeñas empresas casos de uso",
    "agentes de IA que atienden clientes resultados y datos",
    "qué tareas de oficina puede hacer ya la inteligencia artificial",
    "adopción de inteligencia artificial en pymes españolas cifras",
    "inteligencia artificial aplicada a facturación contabilidad y administración",
    "novedades de IA que cambian el trabajo de una empresa esta semana",
    "riesgos y límites reales de la inteligencia artificial en empresas",
    # What the viewer already suffers
    "horas que pierden las empresas españolas en tareas administrativas",
    "carga burocrática y administrativa que soportan las pymes en España",
    "facturación electrónica obligatoria pymes España plazos y coste de adaptación",
    "morosidad y plazos de pago entre empresas en España",
    "absentismo y bajas laborales coste para las empresas españolas",
    "escasez de personal cualificado y tiempo de contratación en pymes",
    "errores administrativos y su coste en las empresas",
    "tiempo de respuesta a clientes y consultas repetidas en atención al cliente",
    "productividad de la pyme española comparada con Europa",
    "digitalización de la pyme española datos y porcentajes",
    "ciberseguridad en pymes españolas incidentes y coste",
)

#: How many of each family a run asks about, so a day is never all one thing.
AI_ANGLES = 7

#: How many angles one run searches. Each is a separate grounded call, because
#: asking for all of them at once makes the model look for their intersection —
#: which is empty — instead of their union.
ANGLES_PER_RUN = 3

_SEARCH_PROMPT = """\
Busca en español artículos, estudios, informes y estadísticas oficiales sobre:

{angle}

Prioriza lo publicado desde {since}. Si algo es más antiguo pero sigue siendo el
dato de referencia, inclúyelo indicando su fecha real.

Buscas EVIDENCIA sobre un problema que una empresa española ya sufre —horas
perdidas, coste, errores, trabajo repetitivo, plazos, obligaciones que consumen
tiempo— no novedades tecnológicas.

Para cada resultado indica: titular exacto, medio, fecha de publicación y dos o
tres datos concretos y verificables que aparezcan en el texto (cifras,
porcentajes, plazos).

DESCARTA:
- Lanzamientos y anuncios de producto de cualquier fabricante.
- Notas de prensa de financiación o de resultados de empresa.
- Páginas comerciales de consultoras y agencias que se promocionan.
- Artículos de opinión sin ningún dato.

Una obligación legal o normativa SÍ vale cuando el artículo describe el trabajo
o el coste que impone a las empresas: eso es un problema que sufren, no un
anuncio de producto.

Devuelve lo que encuentres aunque sea poco. No hace falta que un resultado
cumpla todas las condiciones a la vez.\
"""

_EXTRACT_SYSTEM = """\
Conviertes un resumen de búsqueda en datos estructurados. No inventas nada.

- Copia el titular tal cual aparece.
- 'publisher' es el nombre del medio.
- 'published_at' en formato YYYY-MM-DD. Si no consta, usa la fecha de hoy.
- 'facts' son SOLO datos concretos que aparecen en el resumen: cifras,
  porcentajes, plazos. Nada de generalidades como "mejora la eficiencia".
- Si un artículo no tiene ningún dato concreto, no lo incluyas en la salida.
- 'url' debe ser una de las URLs canónicas que te doy, exactamente como está.\
"""

_EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "candidatos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "publisher": {"type": "string"},
                    "published_at": {"type": "string"},
                    "summary": {"type": "string"},
                    "facts": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "url", "publisher", "facts"],
            },
        }
    },
    "required": ["candidatos"],
}


def angles_for(when: datetime | None = None,
               *, count: int = ANGLES_PER_RUN) -> tuple[str, ...]:
    """The angles this run searches: mostly AI, always one about the pain.

    Rotation is the point. Asking the same questions every morning means a
    blank Tuesday is followed by an identical blank Wednesday; walking each
    list means a quiet week still covers everything Autenia can speak to.

    The mix is deliberate. AI is the subject Juan wants the channel known for,
    so it gets the majority of each run — but a day that only asks about model
    releases produces a tech channel, and the viewer is a manager who does not
    follow any of that. One question about what they already suffer keeps the
    other kind of story reachable.
    """
    day = (when or datetime.now(timezone.utc)).date().toordinal()
    ai, pain = SEARCH_ANGLES[:AI_ANGLES], SEARCH_ANGLES[AI_ANGLES:]

    want_pain = 1 if count > 1 and pain else 0
    want_ai = min(count - want_pain, len(ai))

    start = (day * want_ai) % len(ai) if ai else 0
    chosen = [ai[(start + i) % len(ai)] for i in range(want_ai)]
    if want_pain:
        chosen.append(pain[(day * want_pain) % len(pain)])
    return tuple(chosen)


async def _search_one(angle: str, days: int) -> tuple[str, list[dict], Usage]:
    """One grounded search for one angle."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d/%m/%Y")
    prompt = _SEARCH_PROMPT.format(angle=angle, since=since)
    payload = await request(
        f"models/{TEXT_MODEL}:generateContent",
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "tools": [{"google_search": {}}],
        },
        timeout=240.0,
    )

    candidate = payload["candidates"][0]
    parts = candidate.get("content", {}).get("parts")
    if not parts:
        # Grounding occasionally returns a candidate with no content at all.
        raise GeminiError(
            f"grounded search returned no text (finishReason="
            f"{candidate.get('finishReason')})"
        )

    text = "".join(part.get("text", "") for part in parts)
    chunks = candidate.get("groundingMetadata", {}).get("groundingChunks", [])
    return text, chunks, usage_of(payload)


async def _search(days: int,
                  angles: tuple[str, ...] | None = None
                  ) -> tuple[str, list[dict], Usage]:
    """Search several angles at once and pool what they find.

    One call per angle, concurrently. A single call listing every angle asks the
    model for their intersection — measured empty on 2026-07-30 — where what is
    wanted is their union. An angle that fails does not sink the others: a dead
    search is a quiet day for that question, not an error for the run.
    """
    chosen = angles if angles is not None else angles_for()
    results = await asyncio.gather(
        *(_search_one(angle, days) for angle in chosen),
        return_exceptions=True,
    )

    texts: list[str] = []
    chunks: list[dict] = []
    prompt_tokens = output_tokens = 0
    failures = 0
    for angle, result in zip(chosen, results):
        if isinstance(result, BaseException):
            failures += 1
            continue
        text, found, usage = result
        texts.append(f"### {angle}\n{text}")
        chunks.extend(found)
        prompt_tokens += usage.prompt_tokens
        output_tokens += usage.output_tokens

    if failures == len(chosen) and chosen:
        raise GeminiError(f"las {failures} búsquedas fallaron")

    return ("\n\n".join(texts), chunks,
            Usage(prompt_tokens=prompt_tokens, output_tokens=output_tokens))


async def _resolve(client: httpx.AsyncClient, uri: str) -> str | None:
    """Follow a grounding redirect to the canonical URL, or None if it dies.

    The redirect chain is attacker-influenceable: the target is whatever the
    search index points at, and a redirect can land anywhere — including this
    machine's own network. Both ends of the chain are therefore checked against
    the public-address rule before and after following it.
    """
    # The guard resolves DNS, which blocks; off the loop it goes, or a dozen
    # concurrent lookups would serialise the whole collector.
    try:
        await asyncio.to_thread(urls.assert_public_url, uri)
    except urls.UnsafeURLError:
        return None
    try:
        response = await client.head(uri)
        if response.status_code >= 400:
            response = await client.get(uri)
        if response.status_code >= 400:
            return None
        final = str(response.url)
        await asyncio.to_thread(urls.assert_public_url, final)
        return final
    except (httpx.HTTPError, urls.UnsafeURLError):
        return None


async def resolve_sources(chunks: list[dict]) -> list[dict]:
    """Turn grounding chunks into ``{url, publisher}``, dropping the unreachable.

    Concurrent, because this is a dozen independent HTTP round trips and doing
    them in series is most of the collector's wall time.
    """
    # Pair uri with title before filtering, or a chunk missing a uri shifts every
    # later title onto the wrong article and each fact gets the wrong publisher.
    pairs = [
        (chunk.get("web", {}).get("uri"), chunk.get("web", {}).get("title", ""))
        for chunk in chunks
    ]
    pairs = [(uri, title) for uri, title in pairs if uri]
    titles = [title for _, title in pairs]

    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        resolved = await asyncio.gather(
            *(_resolve(client, uri) for uri, _ in pairs),
            return_exceptions=True,
        )

    sources: list[dict] = []
    seen: set[str] = set()
    for url, title in zip(resolved, titles):
        if not isinstance(url, str) or url in seen:
            continue
        seen.add(url)
        sources.append({
            "url": url,
            "publisher": title or urlparse(url).netloc.removeprefix("www."),
        })
    return sources


def _parse_date(raw: str | None) -> datetime:
    if raw:
        for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(raw[:10], pattern).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return datetime.now(timezone.utc)


async def collect(*, days: int = MAX_AGE_DAYS,
                  exclude_hashes: set[str] | None = None,
                  angles: tuple[str, ...] | None = None
                  ) -> tuple[list[Candidate], Usage]:
    """Today's candidates, deduplicated and with canonical sources.

    Returns an empty list rather than inventing filler when the search finds
    nothing usable — a blank day is a valid outcome and the caller is expected
    to report the skip.
    """
    text, chunks, search_usage = await _search(days, angles)
    sources = await resolve_sources(chunks)
    if not sources:
        return [], search_usage

    listing = "\n".join(f"- {s['publisher']}: {s['url']}" for s in sources)
    payload, extract_usage = await json_call(
        f"Resumen de búsqueda:\n\n{text}\n\n"
        f"URLs canónicas disponibles (usa exactamente estas):\n{listing}",
        _EXTRACT_SCHEMA,
        system=_EXTRACT_SYSTEM,
    )

    usage = Usage(
        prompt_tokens=search_usage.prompt_tokens + extract_usage.prompt_tokens,
        output_tokens=search_usage.output_tokens + extract_usage.output_tokens,
    )

    known = exclude_hashes or set()
    valid_urls = {source["url"] for source in sources}
    now = datetime.now(timezone.utc)
    oldest = now - timedelta(days=days)

    candidates: list[Candidate] = []
    seen_hashes: set[str] = set()

    for entry in payload.get("candidatos", []):
        url = entry.get("url", "")
        # A URL the model did not get from us is a URL it made up.
        if url not in valid_urls:
            continue
        facts = [f.strip() for f in entry.get("facts", []) if f and f.strip()]
        if not facts:
            continue

        published = _parse_date(entry.get("published_at"))
        if published < oldest:
            continue
        # A future date means the model guessed; treat it as today rather than
        # letting it win the freshness score.
        if published > now:
            published = now

        candidate = Candidate(
            title=entry.get("title", "").strip(),
            url=url,
            publisher=entry.get("publisher", "").strip(),
            published_at=published,
            fetched_at=now,
            summary=entry.get("summary", "").strip(),
            facts=facts,
        )
        if not candidate.title:
            continue

        topic = candidate.topic_hash
        if topic in known or topic in seen_hashes:
            continue
        seen_hashes.add(topic)
        candidates.append(candidate)

    return candidates, usage


async def collect_with_fallback(
        *, days: int = MAX_AGE_DAYS,
        exclude_hashes: set[str] | None = None) -> tuple[list[Candidate], Usage]:
    """Today's angles first; the rest of them if today's found nothing.

    A blank day should be a verdict about the news, not about the three
    questions the rotation happened to pick. When the first pass comes back
    empty this asks everything else Autenia can speak to before giving up — and
    only then is "no hay nada" a statement worth sending to the operator.

    The second pass costs one grounded call per remaining angle, and only runs
    on a day that would otherwise produce nothing at all.
    """
    first = angles_for()
    candidates, usage = await collect(
        days=days, exclude_hashes=exclude_hashes, angles=first)
    if candidates:
        return candidates, usage

    rest = tuple(a for a in SEARCH_ANGLES if a not in first)
    if not rest:
        return candidates, usage

    more, extra = await collect(
        days=days, exclude_hashes=exclude_hashes, angles=rest)
    return more, Usage(
        prompt_tokens=usage.prompt_tokens + extra.prompt_tokens,
        output_tokens=usage.output_tokens + extra.output_tokens,
    )
