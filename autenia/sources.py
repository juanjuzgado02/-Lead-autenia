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

from .editorial import Candidate
from .gemini import TEXT_MODEL, GeminiError, Usage, json_call, request, usage_of

#: How far back a story can be and still count as "today's news".
MAX_AGE_DAYS = 14

#: Searches aim at the viewer's problem, not at the technology.
#:
#: Measured 2026-07-29: technology-shaped angles ("agentes de IA para
#: empresas") returned a consultancy's own promo, a piece on NIST regulation
#: and an Oracle launch — all correctly scored near zero, because none of them
#: describes something a manager suffers on a Monday. Ask about the pain and
#: the evidence instead, and the results are usable.
SEARCH_ANGLES = (
    "estudio horas que pierden las empresas en tareas administrativas repetitivas",
    "informe productividad pymes españolas cifras tiempo perdido",
    "datos sobre errores al introducir información manualmente entre sistemas",
    "estudio consultas repetidas atención al cliente porcentaje empresas",
    "informe digitalización pyme española datos y porcentajes",
)

_SEARCH_PROMPT = """\
Busca estudios, informes y artículos recientes en español (últimos {days} días)
sobre:
{angles}

Buscas EVIDENCIA sobre un problema que una pyme ya sufre —horas perdidas,
errores, trabajo repetitivo, tiempo de respuesta— no novedades tecnológicas.

Para cada resultado indica: titular exacto, medio, fecha de publicación y dos o
tres datos concretos y verificables que aparezcan en el texto (cifras,
porcentajes, plazos).

DESCARTA sin excepción:
- Lanzamientos y anuncios de producto de cualquier fabricante.
- Notas de prensa de financiación o de resultados de empresa.
- Páginas comerciales de consultoras y agencias que se promocionan.
- Regulación, normativa y estándares.
- Artículos sin ninguna cifra.

Si algo no describe un problema medible que sufra una empresa, no lo incluyas.\
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


async def _search(days: int) -> tuple[str, list[dict], Usage]:
    """Grounded search. Returns the prose, the grounding chunks and usage."""
    prompt = _SEARCH_PROMPT.format(
        days=days,
        angles="\n".join(f"- {angle}" for angle in SEARCH_ANGLES),
    )
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


async def _resolve(client: httpx.AsyncClient, uri: str) -> str | None:
    """Follow a grounding redirect to the canonical URL, or None if it dies."""
    try:
        response = await client.head(uri)
        if response.status_code >= 400:
            response = await client.get(uri)
        if response.status_code >= 400:
            return None
        return str(response.url)
    except httpx.HTTPError:
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
                  exclude_hashes: set[str] | None = None) -> tuple[list[Candidate], Usage]:
    """Today's candidates, deduplicated and with canonical sources.

    Returns an empty list rather than inventing filler when the search finds
    nothing usable — a blank day is a valid outcome and the caller is expected
    to report the skip.
    """
    text, chunks, search_usage = await _search(days)
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
