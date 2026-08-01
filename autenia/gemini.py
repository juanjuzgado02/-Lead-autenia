"""Gemini calls: candidate judgments, grounded scripts and speech.

Uses the REST API through ``httpx`` rather than ``google-genai``. The pipeline is
async and the official client's calls are synchronous, so routing them through
this module keeps a render from blocking the event loop. It also means every
request passes one place where secrets get masked and cost gets recorded.

Nothing here raises with a URL or header in the message: ``core_config.sanitize``
runs over anything that reaches a log or an exception.
"""

from __future__ import annotations

import base64
import json
import wave
from dataclasses import dataclass

import httpx

from core_config import sanitize
from core_config import settings as autenia

BASE = "https://generativelanguage.googleapis.com/v1beta"

#: Cheap and good enough for editorial judgment and scripts.
#:
#: Was ``gemini-2.5-flash`` until 2026-08-01, when every search started failing
#: with a 404: *"no longer available to new users"*. The name is still in the
#: model listing, which is why the failure reads as a bug rather than a
#: retirement — the listing is not the same thing as what a key may call.
#: Pinned to a version rather than to ``gemini-flash-latest``: the prompts here
#: are tuned by hand, and an alias that moves under them would change the
#: editorial output on a morning nobody touched the repository.
TEXT_MODEL = "gemini-3.6-flash"
#: Speech. Verified working in Spanish at 24 kHz mono PCM. Deliberately left on
#: 2.5 while the text model moved: Iapetus was chosen by ear on this model, and
#: a newer one is a different voice for the brand, not an upgrade.
TTS_MODEL = "gemini-2.5-flash-preview-tts"

#: Gemini's free tier bills nothing, so cost is recorded as zero rather than
#: guessed. If a paid tier is ever enabled, put the real rate here — the store
#: already attributes whatever this returns to the version.
COST_PER_CALL_CENTS = 0


class GeminiError(RuntimeError):
    """A Gemini call failed. Message is safe to log."""


@dataclass
class Usage:
    """What a call consumed. Recorded per version so budgets stay honest."""

    prompt_tokens: int = 0
    output_tokens: int = 0
    cost_cents: int = COST_PER_CALL_CENTS


def _require_key() -> str:
    autenia.require("editorial")
    return autenia.gemini_api_key


async def request(path: str, body: dict, *, timeout: float = 120.0) -> dict:
    """One request. Errors carry status and a sanitised body, never the key."""
    key = _require_key()
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{BASE}/{path}", headers={"x-goog-api-key": key}, json=body,
        )
    if response.status_code != 200:
        # Truncate: a provider error page can be enormous, and we only need the
        # shape of the failure.
        raise GeminiError(
            f"Gemini {path} returned {response.status_code}: {response.text[:300]}"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise GeminiError(f"Gemini {path} returned a non-JSON body") from exc


def usage_of(payload: dict) -> Usage:
    meta = payload.get("usageMetadata", {})
    return Usage(
        prompt_tokens=meta.get("promptTokenCount", 0),
        output_tokens=meta.get("candidatesTokenCount", 0),
    )


def _first_text(payload: dict) -> str:
    try:
        parts = payload["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError) as exc:
        raise GeminiError(
            f"Gemini returned no candidate: {json.dumps(sanitize(payload))[:300]}"
        ) from exc
    return "".join(part.get("text", "") for part in parts)


async def json_call(prompt: str, schema: dict, *, system: str | None = None) -> tuple[dict, Usage]:
    """A structured call. The schema is enforced by the API, not by parsing hope."""
    body: dict = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": schema,
            "temperature": 0.4,
        },
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}

    payload = await request(f"models/{TEXT_MODEL}:generateContent", body)
    text = _first_text(payload)
    try:
        return json.loads(text), usage_of(payload)
    except json.JSONDecodeError as exc:
        raise GeminiError("Gemini returned malformed JSON despite a schema") from exc


# --------------------------------------------------------------------------
# Editorial judgment
# --------------------------------------------------------------------------

_JUDGE_SYSTEM = """\
Eres el editor de contenidos de Autenia, una consultora española de ingeniería de
IA y automatización que trabaja con pymes.

El público son gerentes y fundadores de pymes españolas. No son técnicos: no
hablan de RAG ni de embeddings, hablan de "me como las horas contestando lo
mismo" o "los informes los hago a mano cada lunes".

Puntúas candidatos de 0 a 1 en cuatro señales. Sé severo: la mayoría de noticias
del sector no dan un vídeo útil, y un día sin publicar es preferible a un vídeo
mediocre.

- dolor: ¿describe un trabajo manual que el espectador YA sufre? 1 = lo sufre
  cada semana y lo reconoce al instante. 0 = abstracto o ajeno.
- hook: ¿se puede abrir con esto en un segundo y que alguien pare el scroll?
  1 = el titular ya es el hook. 0 = necesita tres frases de contexto.
- visual: ¿se puede DEMOSTRAR en pantalla con una captura o una grabación?
  1 = se ve funcionando. 0 = solo se puede contar.
- conversion: ¿lleva de forma natural a "¿esto se puede hacer en mi empresa?"
  1 = el espectador querrá preguntar. 0 = curiosidad sin intención.

Nunca inventes cifras. Nunca premies una promesa exagerada.\
"""

_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "judgments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "dolor": {"type": "number"},
                    "hook": {"type": "number"},
                    "visual": {"type": "number"},
                    "conversion": {"type": "number"},
                    "motivo": {"type": "string"},
                },
                "required": ["url", "dolor", "hook", "visual", "conversion"],
            },
        }
    },
    "required": ["judgments"],
}


def _clamp(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


async def judge(candidates: list) -> tuple[dict[str, dict[str, float]], Usage]:
    """Score the signals only judgment can read, for every candidate at once.

    One call for the whole batch: the per-candidate signals are cheap to reason
    about together and expensive to ask about one by one.

    Returns ``{url: {dolor, hook, visual, conversion}}``. A candidate the model
    skipped is simply absent, and ``editorial.score`` treats absence as neutral.
    """
    if not candidates:
        return {}, Usage()

    listing = "\n\n".join(
        f"[{index}] url: {c.url}\n"
        f"titular: {c.title}\n"
        f"medio: {c.publisher}\n"
        f"resumen: {c.summary or '(sin resumen)'}\n"
        f"hechos: {'; '.join(c.facts) if c.facts else '(ninguno)'}"
        for index, c in enumerate(candidates)
    )
    prompt = (
        f"Puntúa estos {len(candidates)} candidatos. Devuelve una entrada por "
        f"cada uno, con su url exacta.\n\n{listing}"
    )

    payload, usage = await json_call(prompt, _JUDGE_SCHEMA, system=_JUDGE_SYSTEM)

    judgments: dict[str, dict[str, float]] = {}
    for entry in payload.get("judgments", []):
        url = entry.get("url")
        if not url:
            continue
        judgments[url] = {
            signal: _clamp(entry.get(signal))
            for signal in ("dolor", "hook", "visual", "conversion")
        }
    return judgments, usage


# --------------------------------------------------------------------------
# Script
# --------------------------------------------------------------------------

_SCRIPT_SYSTEM = """\
Escribes guiones de vídeo vertical para Autenia, consultora española de IA y
automatización para pymes. Posicionamiento: "experiencia real, no hype".

FORMATO
- Español de España. Tuteo.
- Cuenta las palabras: el presupuesto que te den es un límite duro, no una
  sugerencia. Se lee a unas 2,6 palabras por segundo, así que pasarte de
  palabras es pasarte de segundos y el guion se descarta entero.
- El hook va en el primer segundo. La idea central, en los tres primeros.
- El hook se dice UNA vez. La primera escena continúa la frase del hook, no la
  repite: si el hook ya está en la escena 1, el vídeo dice lo mismo dos veces.
- Entre tres y cinco escenas. Más escenas en 30 segundos es un carrusel, no un
  vídeo.
- Sin intro corporativa. Prohibido "Hola, somos Autenia y hoy...". Abre con el
  problema o con el resultado.

VOZ
- Frases cortas. Directo y concreto. Cero superlativos.
- Sí: "Esto son cuatro horas al mes que no vuelves a tocar."
- No: "Revoluciona tu negocio con el poder transformador de la IA."
- El espectador está ocupado y es competente, pero no conoce esta tecnología.
  Nunca condescendiente, nunca pomposo.
- No expliques cómo funciona la tecnología. Muestra un trabajo manual que
  desaparece.

ATRIBUCIÓN — OBLIGATORIA Y HABLADA
- Toda escena marcada como "hecho" **dice su fuente en voz alta**, dentro de la
  narración: "Según Europa Press…", "Un informe de la CEOE cifra…", "Los datos
  del INE dicen que…".
- No basta con rellenar el campo "fuente": ese campo lo ve el revisor, no el
  espectador. Una cifra sin nombre detrás suena inventada, y es la diferencia
  entre un dato y una promesa de vendedor.
- Dilo de forma natural y breve, al principio de la frase. No lo conviertas en
  una nota al pie: "Según Europa Press, el absentismo cerró el año en el 7,7%."
- Si dos escenas seguidas usan la misma fuente, en la segunda basta con "el
  mismo informe" o "esos datos". Repetir el nombre tres veces suena a locución
  de teletipo.

REGLAS DURAS
- Cada afirmación factual sale de los hechos que te doy, y la marcas como
  "hecho" citando su fuente.
- **Cualquier escena que contenga una cifra —un porcentaje, unas horas, unos
  euros— es "hecho" y necesita fuente. Sin excepción.** Una cifra deducida de
  los datos que te doy (la mitad de ocho son cuatro) sigue siendo un hecho:
  cítale la fuente de la que sale. Una cifra marcada como "opinion" hace que se
  descarte el guion entero.
- "opinion" es para juicios sin número: que algo es tedioso, que es tiempo mal
  invertido, que se puede evitar.
- NUNCA inventes cifras, porcentajes ni retornos. Si no tienes el dato, no lo uses.
- Nada de política, polémica, ataques a competidores ni rumores.
- Ningún dato de cliente identificable.
- Prohibido "link in bio". El CTA lleva al cuestionario de un minuto de la web,
  redactado según la idea, y suena a invitación a diagnóstico, no a compra.
  Ejemplo de tono: "Si esto te suena, en la web hay un cuestionario de un minuto
  que te dice si es automatizable."
- El plano visual de cada escena debe ser una CAPTURA DE PANTALLA real y
  grabable: un agente contestando en WhatsApp, un cuadro de mando, dos
  aplicaciones con datos pasando de una a otra. Descríbelo en una frase corta y
  concreta.
- Prohibido pedir animaciones, actores, metáforas visuales, relojes girando o
  gente frustrada mirando la pantalla: nada de eso se puede grabar aquí.
- No nombres herramientas de terceros en el plano visual. La pantalla es la de
  Autenia.\
"""

_SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "hook": {"type": "string"},
        "escenas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "narracion": {"type": "string"},
                    "visual": {"type": "string"},
                    "tipo": {"type": "string", "enum": ["hecho", "opinion"]},
                    "fuente": {"type": "string"},
                },
                "required": ["narracion", "visual", "tipo"],
            },
        },
        "cta": {"type": "string"},
        "titulo": {"type": "string"},
        "caption": {"type": "string"},
        "duracion_estimada_s": {"type": "number"},
    },
    "required": ["hook", "escenas", "cta", "titulo", "caption", "duracion_estimada_s"],
}


#: Spoken pace used to turn a target duration into a word budget. Models judge
#: word counts far better than they judge how long something takes to say, so
#: the prompt asks for words and preflight checks the seconds.
WORDS_PER_SECOND = 2.6


async def write_script(candidate, *, seconds: int = 30) -> tuple[dict, Usage]:
    """Turn a chosen candidate into a grounded script.

    Every scene declares whether it states a fact (with its source) or an
    opinion. That distinction is what lets preflight refuse a claim nobody can
    back up, instead of discovering it after publication.
    """
    budget = int(seconds * WORDS_PER_SECOND)
    lower, upper = int(budget * 0.85), int(budget * 1.05)
    facts = "\n".join(f"- {fact}" for fact in candidate.facts) or "- (ninguno)"
    prompt = (
        f"Escribe el guion de un vídeo de unos {seconds} segundos.\n\n"
        f"PRESUPUESTO DE PALABRAS: entre {lower} y {upper} palabras contando "
        f"hook, narración de todas las escenas y CTA juntos. Cuéntalas antes de "
        f"responder. Pasarte descarta el guion.\n\n"
        f"Titular de partida: {candidate.title}\n"
        f"Medio: {candidate.publisher}\n"
        f"URL: {candidate.url}\n"
        f"Publicado: {candidate.published_at.date().isoformat()}\n"
        f"Resumen: {candidate.summary or '(sin resumen)'}\n\n"
        f"Hechos disponibles (son los ÚNICOS datos que puedes afirmar):\n{facts}\n\n"
        f"No parafrasees el titular como si fuera tuyo: la pieza es propia, la "
        f"noticia solo es el punto de partida."
    )
    return await json_call(prompt, _SCRIPT_SCHEMA, system=_SCRIPT_SYSTEM)


def narration_text(script: dict) -> str:
    """The words the voice will actually say, hook and CTA included."""
    pieces = [script.get("hook", "")]
    pieces += [scene.get("narracion", "") for scene in script.get("escenas", [])]
    pieces.append(script.get("cta", ""))
    return " ".join(piece.strip() for piece in pieces if piece and piece.strip())


# --------------------------------------------------------------------------
# Speech
# --------------------------------------------------------------------------

#: Autenia's voice, chosen by ear on 2026-07-30.
#:
#: Juan listened to five male prebuilt voices reading the same script — Charon,
#: Orus, Iapetus, Achird, Alnilam — and picked this one for being the clearest.
#: It replaced Kore, which is female and was never a decision, just the first
#: thing wired in.
#:
#: **Do not change this without him hearing the alternative.** It is the voice
#: on every video the company publishes; a silent swap changes the brand.
#: ``AUTENIA_VOICE_NAME`` overrides it for an experiment.
DEFAULT_VOICE = "Iapetus"


async def synthesize(text: str, *, voice: str = DEFAULT_VOICE,
                     out_path: str) -> tuple[str, Usage]:
    """Speak ``text`` into a mono WAV at ``out_path``.

    Gemini returns raw signed 16-bit PCM, not a container, so the WAV header is
    written here. The sample rate comes from the response rather than being
    assumed — guessing it makes the voice play at the wrong pitch.
    """
    body = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}
            },
        },
    }
    payload = await request(f"models/{TTS_MODEL}:generateContent", body, timeout=300.0)

    try:
        inline = payload["candidates"][0]["content"]["parts"][0]["inlineData"]
    except (KeyError, IndexError) as exc:
        raise GeminiError("Gemini TTS returned no audio") from exc

    pcm = base64.b64decode(inline["data"])
    rate = 24000
    for chunk in inline.get("mimeType", "").split(";"):
        chunk = chunk.strip()
        if chunk.startswith("rate="):
            rate = int(chunk.split("=", 1)[1])

    with wave.open(out_path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)   # signed 16-bit
        handle.setframerate(rate)
        handle.writeframes(pcm)

    return out_path, usage_of(payload)


def wav_duration_s(path: str) -> float:
    with wave.open(path, "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())
