"""One way to ask for speech, whichever service produces it.

The voice is Autenia's on every video it publishes, so the choice belongs to
whoever listens to it — not to whatever was wired in first. Both providers write
the same thing (a mono WAV at ``out_path``) and are selected by
``AUTENIA_VOICE_PROVIDER``, so switching costs one variable and no code.

* ``gemini`` (default) — no second credential, no subscription.
* ``elevenlabs`` — better Spanish prosody, needs a paid plan.
"""

from __future__ import annotations

import base64
import difflib
import os
import re
import shutil
import unicodedata
import wave
from dataclasses import dataclass, replace

import httpx

from core_config import settings as autenia

from . import gemini

#: ElevenLabs' multilingual model handles Spanish; v2 is the stable one.
ELEVENLABS_MODEL = "eleven_multilingual_v2"

#: The only ElevenLabs voices this channel uses, chosen by Juan on 2026-08-02.
#:
#: All four are **native Spanish from Spain**, which is the whole reason the
#: previous list is gone: it held ElevenLabs' English premade voices reading
#: Spanish through the multilingual model, and the account's own 28 voices are
#: 22 English ones plus a handful of Spanish that an audition truncated to
#: "the first eight" never reached.
#:
#: They are library voices, not account voices, and they were verified on
#: 2026-08-02 to synthesise directly from their public id with this key — so
#: nothing has to be added to the account for them to work.
#:
#: **Do not widen this list without Juan hearing the alternative.** It is the
#: voice on every video the company publishes.
ELEVENLABS_VOICES = (
    ("dq5fzy66iCKSIQWm5YMU", "Cadalso — redonda, agradable y creíble"),
    ("ZCh4e9eZSUf41K4cmCEL", "Emilio — cálida, sólida y convincente"),
    ("TOFW0dONbX4o9MmkxwBB", "Ernesto — cercana, dinámica y natural"),
    ("FmAk3rEwAp8LKP5FV4ao", "Marco Cruz — sólida, persuasiva e inteligente"),
)

#: Which one speaks when nobody says otherwise. First of the four, and a
#: placeholder in the honest sense: the choice between them is made by
#: listening, with ``python autenia_bot.py voces``, and the winner goes in
#: AUTENIA_VOICE_NAME.
ELEVENLABS_DEFAULT_VOICE = ELEVENLABS_VOICES[0][0]

#: So the ``.env`` can say ``AUTENIA_VOICE_NAME=Cadalso`` instead of carrying a
#: twenty-character id that nobody can check at a glance.
ELEVENLABS_ALIASES = {
    "cadalso": ELEVENLABS_VOICES[0][0],
    "emilio": ELEVENLABS_VOICES[1][0],
    "ernesto": ELEVENLABS_VOICES[2][0],
    "marco": ELEVENLABS_VOICES[3][0],
    "marco cruz": ELEVENLABS_VOICES[3][0],
}


#: La voz de las historias. Marco Cruz lee distinto un relato que un dato, y un
#: guion con protagonista pide a alguien contándolo, no a alguien informando.
VOZ_HISTORIA = ELEVENLABS_VOICES[3][0]

#: Las tres que se reparten lo demás. Rotan por día del año, no al azar: un
#: canal que suena igual todos los días cansa, y uno que cambia de voz sin
#: patrón suena a que lo llevan tres personas distintas.
VOCES_NOTICIA = (ELEVENLABS_VOICES[0][0], ELEVENLABS_VOICES[1][0],
                 ELEVENLABS_VOICES[2][0])


def for_script(script: dict, *, provider: str | None = None,
               when=None) -> str | None:
    """Qué voz lee este guion, o ``None`` para dejar la configurada.

    Dos reglas, decididas por Juan el 2 de agosto de 2026: las historias las
    lee Marco Cruz, y el resto se reparte entre las otras tres rotando por día.

    Una voz fijada a mano en ``AUTENIA_VOICE_NAME`` gana siempre: si alguien la
    escribió es porque quiere esa y no un turno.

    La rotación es por día, así que dos vídeos del mismo día comparten voz. Es
    el caso raro —el canal publica uno al día— y la alternativa, rotar por
    vídeo, obligaría a esta función a consultar la base de datos para decidir
    algo que se oye en dos segundos.
    """
    chosen = provider or autenia.voice_provider
    if chosen != "elevenlabs" or autenia.voice_name:
        return None

    if (script or {}).get("genero") == "historia":
        return VOZ_HISTORIA

    from datetime import date  # noqa: PLC0415
    dia = (when or date.today()).toordinal()
    return VOCES_NOTICIA[dia % len(VOCES_NOTICIA)]


def elevenlabs_voice(name: str | None = None) -> str:
    """The voice id to speak with: an alias, a raw id, or the default.

    A raw id that is not one of the four is passed through rather than refused —
    it is how an experiment happens — but it says so, because the difference
    between "I am testing a voice" and "the brand voice changed and nobody
    noticed" is whether anybody was told.
    """
    chosen = (name or autenia.voice_name or "").strip()
    if not chosen:
        return ELEVENLABS_DEFAULT_VOICE
    known = ELEVENLABS_ALIASES.get(chosen.lower())
    if known:
        return known
    if chosen not in {vid for vid, _ in ELEVENLABS_VOICES}:
        print(f"[voice] {chosen} no es una de las cuatro voces de Autenia; "
              f"se usa igualmente, pero no es la voz de la marca")
    return chosen

#: Gemini's male prebuilt voices, with the character each one advertises.
#: Sampled 2026-07-30; Juan chose Iapetus.
GEMINI_MALE_VOICES = (
    ("Charon", "informativa"), ("Orus", "firme"), ("Iapetus", "clara"),
    ("Achird", "cercana"), ("Alnilam", "rotunda"), ("Algenib", "grave"),
    ("Rasalgethi", "didáctica"), ("Sadaltager", "experta"),
)

#: The line every candidate voice reads. Same words for all of them, or the
#: comparison is between scripts rather than between voices.
AUDITION_TEXT = (
    "La factura electrónica obligatoria ya está aquí. No es solo un PDF: es un "
    "formato estructurado que exige la ley. Si esto te suena, en la web hay un "
    "cuestionario de un minuto."
)

#: Roughly what ElevenLabs charges per character on the entry plan, in cents.
#: Used only to estimate before spending; the real figure settles afterwards.
ELEVENLABS_CENTS_PER_1K_CHARS = 3

#: A qué ritmo lee cada proveedor, en palabras por segundo. **Medido, no
#: supuesto**: el 2 de agosto de 2026, sobre los seis vídeos ya renderizados con
#: Gemini (2,21 a 2,81; media 2,49) y sobre el primer guion locutado con
#: Cadalso (2,06).
#:
#: Antes había un único 2,6 escrito en tres sitios, y erraba hacia el lado malo:
#: el preflight estima segundos dividiendo por este número, así que un ritmo
#: demasiado alto hace que un guion largo parezca corto y pase un límite que
#: existe para pararlo. Se elige por debajo de la media de cada proveedor por el
#: mismo motivo que ``estimate_cents`` redondea hacia arriba: una estimación
#: optimista es un freno que no frena.
PALABRAS_POR_SEGUNDO = {"gemini": 2.4, "elevenlabs": 2.05}

#: El de por defecto cuando no se sabe qué proveedor habla.
PALABRAS_POR_SEGUNDO_DEFECTO = 2.4


def words_per_second(provider: str | None = None) -> float:
    """Palabras por segundo del proveedor que vaya a hablar."""
    try:
        chosen = provider or autenia.voice_provider
    except Exception:  # noqa: BLE001 - un .env roto no debe romper una estimación
        chosen = "gemini"
    return PALABRAS_POR_SEGUNDO.get(chosen, PALABRAS_POR_SEGUNDO_DEFECTO)


#: Símbolos que un TTS se salta o lee mal, y cómo se dicen en voz alta.
#:
#: Detectado el 2 de agosto de 2026 escuchando el primer vídeo con ElevenLabs:
#: el guion decía "reduce un 30% los costes" y la locución dijo "reduce un
#: treinta costes". El símbolo desapareció, y con él la unidad — que en un canal
#: que vive de cifras es justo la palabra que había que oír.
#:
#: Se arregla aquí y no en el prompt del guion porque el guion se escribe para
#: leerse: el subtítulo debe seguir diciendo "30%", que es como se lee más
#: rápido en pantalla. Lo que cambia es solo lo que se manda a la voz.
_HABLADO = (
    (re.compile(r"(\d)\s*%"), r"\1 por ciento"),
    (re.compile(r"(\d)\s*€"), r"\1 euros"),
    (re.compile(r"(\d)\s*\$"), r"\1 dólares"),
    (re.compile(r"(\d)\s*h\b"), r"\1 horas"),
    (re.compile(r"(\d)\s*km\b"), r"\1 kilómetros"),
    (re.compile(r"\bnº\s*", re.IGNORECASE), "número "),
    (re.compile(r"\betc\.", re.IGNORECASE), "etcétera"),
    # Un símbolo suelto sin número delante: se dice igual.
    (re.compile(r"%"), " por ciento"),
    (re.compile(r"€"), " euros"),
)


def speakable(text: str) -> str:
    """El mismo texto, escrito como se pronuncia.

    Solo para el sintetizador: el subtítulo y el guion conservan los símbolos.
    """
    for patron, reemplazo in _HABLADO:
        text = patron.sub(reemplazo, text)
    return " ".join(text.split())


class VoiceError(RuntimeError):
    """Speech synthesis failed. Message is safe to log."""


@dataclass
class Spoken:
    """The result of a synthesis, and what it is expected to have cost."""

    path: str
    duration_s: float
    provider: str
    estimated_cents: int


def estimate_cents(text: str, provider: str | None = None) -> int:
    """What this narration should cost, before calling anyone.

    Budgets act on this, so it must never under-report: an estimate that is too
    low is a hard stop that fails to stop.
    """
    chosen = provider or autenia.voice_provider
    if chosen == "gemini":
        return 0  # free tier
    return -(-len(text) * ELEVENLABS_CENTS_PER_1K_CHARS // 1000)  # ceil


async def synthesize(text: str, *, out_path: str,
                     provider: str | None = None,
                     name: str | None = None) -> Spoken:
    """Speak ``text`` into a mono WAV. Raises VoiceError on any failure.

    ``name`` overrides the configured voice for one call. It exists so an
    audition can read the same line in eight voices without eight restarts.
    """
    if not text or not text.strip():
        raise VoiceError("nothing to speak: the narration is empty")

    # Lo que se dice, no lo que se escribe: el símbolo % no se pronuncia solo.
    dicho = speakable(text)

    chosen = provider or autenia.voice_provider
    if chosen == "gemini":
        path = await _gemini(dicho, out_path, name)
    elif chosen == "elevenlabs":
        path = await _elevenlabs(dicho, out_path, name)
    else:  # pragma: no cover - core_config validates this
        raise VoiceError(f"unknown voice provider {chosen!r}")

    return Spoken(
        path=path,
        duration_s=_duration_s(path),
        provider=chosen,
        estimated_cents=estimate_cents(text, chosen),
    )


async def _gemini(text: str, out_path: str, name: str | None = None) -> str:
    try:
        path, _usage = await gemini.synthesize(
            text, voice=name or autenia.voice_name or gemini.DEFAULT_VOICE,
            out_path=out_path,
        )
    except gemini.GeminiError as exc:
        raise VoiceError(str(exc)) from exc
    return path


async def _elevenlabs(text: str, out_path: str, name: str | None = None) -> str:
    autenia.require("voice")
    voice_id = elevenlabs_voice(name)
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.post(
            url,
            headers={"xi-api-key": autenia.elevenlabs_api_key},
            params={"output_format": "pcm_24000"},
            json={"text": text, "model_id": ELEVENLABS_MODEL},
        )
    if response.status_code != 200:
        raise VoiceError(
            f"ElevenLabs returned {response.status_code}: {response.text[:300]}"
        )

    # pcm_24000 is raw signed 16-bit little-endian, same as Gemini's, so both
    # providers hand the rest of the pipeline an identical WAV.
    with wave.open(out_path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(response.content)
    return out_path


async def catalogue(provider: str | None = None) -> list[tuple[str, str]]:
    """Voices worth auditioning, as ``(id, description)``.

    Both lists are fixed and short on purpose. It used to ask ElevenLabs for
    the account's voices and sort the Spanish ones to the front, which was a
    workaround for a list that was mostly English and mostly irrelevant: an
    audition of "the first eight" sent eight English voices. Choosing between
    four voices somebody already vetted is a better use of a listen than
    ranking twenty-eight that nobody did.
    """
    chosen = provider or autenia.voice_provider
    if chosen == "gemini":
        return [(name, f"{name} — {character}")
                for name, character in GEMINI_MALE_VOICES]
    autenia.require("voice")
    return list(ELEVENLABS_VOICES)


def _duration_s(path: str) -> float:
    with wave.open(path, "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())


# --------------------------------------------------------------------------
# Que lo dicho sea lo escrito
# --------------------------------------------------------------------------
#
# Un sintetizador no falla como falla una API: no devuelve un error, devuelve un
# WAV perfectamente válido en el que se ha comido una palabra o la ha dicho dos
# veces. El 2 de agosto de 2026 salió a revisión un vídeo cuyo guion decía "No se
# trata de despedir a nadie" y cuya locución decía "no se trata de despedir a
# nadie, er a nadie". El texto que se mandó era correcto y el corte en pausas
# reales era exacto — los cinco trozos sumaban al milisegundo lo que duraba la
# toma. El tropiezo venía dentro del audio, y hasta ese momento lo único que
# podía detectarlo era una persona escuchando el vídeo terminado.
#
# Es el mismo trabajo que hace ``revision.py`` con las imágenes, un piso más
# abajo: mirar lo que ha generado el modelo antes de que salga en un vídeo. Y
# con la misma regla, porque la alternativa es peor que el defecto — **la duda
# absuelve**: sin clave, sin red o con una transcripción rara, la toma pasa.

#: Cuántas veces se pide la misma locución antes de quedarse con la menos mala.
#: Tres y no dos porque el tropiezo es aleatorio: si una toma sale mal, la
#: siguiente sale bien casi siempre, y la tercera existe para el día en que no.
#: Cada reintento cuesta una locución entera —céntimos— y unos segundos.
INTENTOS_LOCUCION = 3

#: A partir de qué proporción de palabras **sustituidas** se deja de creer a la
#: transcripción. Un transcriptor que no ha entendido el audio devuelve otras
#: palabras en el mismo sitio, y actuar sobre eso sería pagar locuciones nuevas
#: para arreglar un fallo que no está ahí.
#:
#: Cuenta sustituciones y no diferencias en general porque las otras dos formas
#: de diferir son justo lo que se busca: sobran palabras (tartamudeo) o faltan
#: seguidas (frase comida, o una toma que se corta a la mitad — que sí pasa y
#: es peor que un tropiezo). Medirlas todas juntas hacía que una locución
#: truncada, la más grave, fuese la que más se parecía a un oído roto.
DIVERGENCIA_MAXIMA = 0.4

_TRANSCRIPCION = (
    "Transcribe este audio en español palabra por palabra, exactamente como "
    "suena. Incluye las palabras repetidas, los tartamudeos y las sílabas "
    "sueltas si las hay: se está buscando precisamente eso. No corrijas, no "
    "resumas y no ordenes la frase. Devuelve sólo la transcripción."
)


def revision_voz_enabled() -> bool:
    """Si se escucha lo que se ha sintetizado. Se puede apagar como el visual."""
    return (os.environ.get("AUTENIA_REVISION_VOZ", "on").strip().lower()
            not in ("0", "false", "no", "off"))


def intentos_locucion() -> int:
    """Cuántas tomas se piden como mucho. Un valor ilegible no rompe el ciclo."""
    try:
        return max(1, int(os.environ.get("AUTENIA_INTENTOS_VOZ",
                                         INTENTOS_LOCUCION)))
    except ValueError:
        return INTENTOS_LOCUCION


def _palabras(texto: str) -> list[str]:
    """El texto reducido a lo que se puede comparar entre lo dicho y lo oído.

    Pasa por :func:`speakable` a propósito, y en los dos lados: al sintetizador
    se le manda "30 por ciento" y el transcriptor escribe "30%", así que sin
    esto la comparación denunciaría tres palabras comidas en cada cifra del
    guion — que es justo lo que este canal escribe en todas.
    """
    texto = speakable(texto).lower()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return re.findall(r"[a-z0-9]+", texto)


def tropiezos(dicho: str, oido: str) -> list[str]:
    """Qué le pasó a la voz entre el guion y el audio, en palabras.

    Devuelve una lista vacía cuando la toma sirve. Dos cosas la condenan:

    * **Palabras de más** — el tartamudeo. Un transcriptor casi nunca inventa
      palabras que no ha oído, así que una inserción es una señal limpia.
    * **Dos o más palabras seguidas de menos** — la frase comida. El mínimo de
      dos es lo que separa un defecto real de un transcriptor que se salta un
      "de": una sola palabra perdida se descarta por barata que salga.

    Una sustitución no cuenta como defecto: ahí el transcriptor ha oído algo y
    lo ha escrito distinto, que es su error típico y no el de la voz. Lo que
    hace es lo contrario — si abundan, la que no es de fiar es la
    transcripción, y la toma pasa (:data:`DIVERGENCIA_MAXIMA`).
    """
    dichas, oidas = _palabras(dicho), _palabras(oido)
    if not dichas or not oidas:
        return []

    fallos: list[str] = []
    sustituidas = 0
    for etiqueta, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, dichas, oidas, autojunk=False).get_opcodes():
        if etiqueta == "equal":
            continue
        if etiqueta == "replace":
            sustituidas += max(i2 - i1, j2 - j1)
        if etiqueta == "insert":
            despues = " ".join(dichas[max(0, i1 - 3):i1])
            fallos.append(f'dice de más "{" ".join(oidas[j1:j2])}" '
                          f'después de "{despues}"')
        elif etiqueta == "delete" and i2 - i1 >= 2:
            fallos.append(f'se salta "{" ".join(dichas[i1:i2])}"')

    if sustituidas > len(dichas) * DIVERGENCIA_MAXIMA:
        return []  # la que ha fallado es la transcripción, no la voz
    return fallos


async def transcribir(path: str) -> str | None:
    """Lo que se oye en un WAV, según Gemini. ``None`` si no se pudo escuchar.

    Gemini y no el proveedor que hable: la clave ya está puesta —es la del
    guion y la de la búsqueda—, es la barata, y así la comprobación funciona
    igual cuando quien locuta es ElevenLabs.
    """
    try:
        with open(path, "rb") as handle:
            audio = base64.b64encode(handle.read()).decode()
    except OSError:
        return None

    try:
        payload = await gemini.request(
            f"models/{gemini.TEXT_MODEL}:generateContent",
            {
                "contents": [{"parts": [
                    {"text": _TRANSCRIPCION},
                    {"inlineData": {"mimeType": "audio/wav", "data": audio}},
                ]}],
                "generationConfig": {"temperature": 0.0},
            },
            timeout=180.0,
        )
        oido = "".join(
            parte.get("text", "")
            for parte in payload["candidates"][0]["content"]["parts"])
    except (gemini.GeminiError, KeyError, IndexError, TypeError, ValueError) as exc:
        print(f"[voz] no se pudo escuchar la toma: {str(exc)[:120]}")
        return None
    return oido.strip() or None


async def revisar_toma(path: str, texto: str) -> list[str]:
    """Los tropiezos de una toma ya sintetizada. Nunca lanza."""
    if not revision_voz_enabled():
        return []
    oido = await transcribir(path)
    if not oido:
        return []
    return tropiezos(texto, oido)


async def narracion(text: str, *, out_path: str,
                    provider: str | None = None,
                    name: str | None = None,
                    intentos: int | None = None) -> Spoken:
    """La locución del vídeo: sintetizada, escuchada y repetida si tropieza.

    Lo que :func:`synthesize` para todo lo demás, más la comprobación. Están
    separadas porque una audición no la necesita — ocho voces leyendo la misma
    frase para elegir una se juzgan de oído, que es de lo que va —, y porque
    quien lee esto tiene que poder sintetizar sin pagar tres veces.

    Si ninguna toma sale limpia se queda la que menos tropieza, y se dice. Un
    vídeo con una palabra repetida sigue siendo un vídeo que una persona va a
    ver antes de que se publique; no tenerlo es perder el día.
    """
    total = intentos if intentos is not None else intentos_locucion()
    respaldo = f"{out_path}.mejor"
    mejor: Spoken | None = None
    mejores: list[str] = []

    try:
        for numero in range(1, max(1, total) + 1):
            hablada = await synthesize(text, out_path=out_path,
                                       provider=provider, name=name)
            fallos = await revisar_toma(hablada.path, text)
            if not fallos:
                return hablada
            if mejor is None or len(fallos) < len(mejores):
                # La mejor hasta ahora se guarda aparte: el siguiente intento
                # escribe encima de out_path y no hay forma de recuperarla.
                shutil.copyfile(hablada.path, respaldo)
                mejor, mejores = hablada, fallos
            print(f"[voz] toma {numero} descartada: {'; '.join(fallos)}")

        shutil.copyfile(respaldo, out_path)
        print(f"[voz] ninguna toma salió limpia; se usa la menos mala "
              f"({'; '.join(mejores)})")
        return replace(mejor, path=out_path)
    finally:
        if os.path.exists(respaldo):
            os.remove(respaldo)
