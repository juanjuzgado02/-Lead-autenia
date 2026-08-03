"""The daily cycle, and what each button does when it is pressed.

This is the piece that makes the parts a system: it finds a topic, writes a
script, asks for approval, and — once approved — renders and publishes without
further involvement.

Every decision that costs money or reaches an audience is guarded by the state
machine rather than by the order of calls in this file, so a bug here fails
loudly instead of spending twice or publishing something unapproved.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass

from core_config import settings as autenia

from . import assets as asset_lib
from . import (arreglo, editorial, formats, gemini, preflight, publish, render,
               sources, store, telegram, voice)
from .models import Content, Version
from .states import State, StateError

#: Where rendered videos and the footage library live.
WORK_ROOT = os.environ.get("AUTENIA_WORK_DIR", "data/videos")
LIBRARY_DIR = os.environ.get("AUTENIA_LIBRARY_DIR", "data/library")


@dataclass
class CycleResult:
    """What one run of the cycle did, for the log and for the operator."""

    outcome: str          # "en_revision" | "sin_candidatos" | "bajo_umbral" |
                          # "preflight" | "ciclo_activo" | "presupuesto"
    detail: str = ""
    version_id: str | None = None


# --------------------------------------------------------------------------
# Producing today's script
# --------------------------------------------------------------------------

async def run_cycle(*, force: bool = False) -> CycleResult:
    """Find something worth making and put its script in front of the operator.

    Returns without producing anything when nothing clears the bar. A blank day
    is a real outcome: the brief prefers it to a mediocre video, and the caller
    reports the skip rather than lowering the threshold.
    """
    async with store.session() as sess:
        if not force and await store.has_active_cycle(sess):
            return CycleResult("ciclo_activo",
                               "ya hay un guion en revisión o un vídeo en curso")
        known = await _known_topics(sess)

    candidates, _usage = await sources.collect_with_fallback(exclude_hashes=known)
    if not candidates:
        return CycleResult("sin_candidatos", "la búsqueda no encontró nada usable")

    alive = [c for c in candidates if editorial.survives(c)]
    if not alive:
        return CycleResult("sin_candidatos",
                           f"los {len(candidates)} candidatos cayeron en los filtros")

    judgments, _usage = await gemini.judge(alive)
    chosen = editorial.pick(alive, judgments=judgments)
    if not chosen:
        return CycleResult("bajo_umbral",
                           f"ninguno de {len(alive)} superó el umbral editorial")

    candidate, score = chosen
    script, _usage = await gemini.write_script(candidate)
    narration = gemini.narration_text(script)
    check = preflight.check(script, narration=narration,
                            estimated_cents=voice.estimate_cents(narration))

    async with store.session() as sess:
        content = await store.create_content(
            sess, title=candidate.title, topic_hash=candidate.topic_hash)
        version = await sess.get(Version, content.current_version_id)
        version.script = json.dumps(script, ensure_ascii=False)
        version.sources = json.dumps(
            {"url": candidate.url, "publisher": candidate.publisher,
             "facts": candidate.facts}, ensure_ascii=False)
        version.caption = script.get("caption", "")
        await store.transition(sess, version, State.GUION)

        if not check.ok:
            # Keep the failed version: it is the record of what the model got
            # wrong, and the next prompt fix is judged against it.
            await store.transition(sess, version, State.DESCARTADO,
                                   note=str(check))
            return CycleResult("preflight", str(check), version.id)

        await store.transition(sess, version, State.EN_REVISION)
        version_id, number = version.id, version.number

    coverage = _expected_coverage(script)
    await telegram.send_review(
        script, version_id, candidate=candidate, version_number=number,
        estimated_seconds=check.estimated_seconds,
        estimated_cents=check.estimated_cents, coverage=coverage,
    )
    return CycleResult("en_revision", f"puntuación {score.total:.2f}", version_id)


async def _known_topics(sess) -> set[str]:
    from sqlalchemy import select
    rows = await sess.scalars(select(Content.topic_hash))
    return set(rows)


def _expected_coverage(script: dict) -> float:
    """How much of this script the footage library can actually show."""
    library = asset_lib.load_library(LIBRARY_DIR)
    requests = [s.get("visual", "") for s in script.get("escenas", [])]
    return asset_lib.coverage(asset_lib.plan_visuals(requests, library))


# --------------------------------------------------------------------------
# Reacting to the operator
# --------------------------------------------------------------------------

async def handle(action: telegram.Action) -> None:
    """Apply one authorised decision."""
    handlers = {
        "aprobar": _approve,
        "cambios": _ask_for_changes,
        "rechazar": _reject,
        "regenerar": _regenerate,
        "texto": _feedback,
        "tema": _on_request,
        "publicar": _publish_video,
        "descartar": _discard_video,
        "defectuoso": _ask_for_defect,
        "ocioso": _idle_text,
    }
    handler = handlers.get(action.kind)
    if handler is None:
        return
    await handler(action)


async def _load(version_id: str, *, expect: State | None = None):
    """Fetch a version, or None when the callback refers to a settled one.

    Telegram redelivers and buttons stay tappable on old messages, so a
    callback naming a version that has already moved on is normal, not an error.
    """
    async with store.session() as sess:
        version = await sess.get(Version, version_id)
        if version is None:
            return None
        if expect is not None and State(version.state) is not expect:
            return None
        return version


async def _approve(action: telegram.Action) -> None:
    version = await _load(action.version_id, expect=State.EN_REVISION)
    if version is None:
        await telegram.answer_callback(action.callback_id,
                                       "Ese guion ya estaba resuelto")
        return

    await telegram.answer_callback(
        action.callback_id, "Aprobado — renderizando. Te lo paso antes de subirlo")
    async with store.session() as sess:
        fresh = await sess.get(Version, version.id)
        try:
            await store.transition(sess, fresh, State.APROBADO)
            await store.transition(sess, fresh, State.RENDERIZANDO)
        except (StateError, store.BudgetExceededError) as exc:
            await telegram.send_message(f"⚠️ No se puede renderizar: {exc}")
            return
        script = json.loads(fresh.script)
        version_id = fresh.id

    # Un mensaje y no sólo el aviso del botón: el aviso se desvanece en dos
    # segundos y el render tarda minutos, así que quien aprueba se queda mirando
    # un chat en el que no ha pasado nada y vuelve a pulsar.
    await telegram.send_message(
        "🎬 <b>Renderizando.</b> Tarda unos minutos.\n"
        "<i>No hace falta que vuelvas a darle: te llega el vídeo aquí en cuanto "
        "esté.</i>")
    await _render_and_publish(version_id, script)


async def _ask_for_changes(action: telegram.Action) -> None:
    version = await _load(action.version_id, expect=State.EN_REVISION)
    if version is None:
        await telegram.answer_callback(action.callback_id,
                                       "Ese guion ya estaba resuelto")
        return
    await telegram.answer_callback(action.callback_id)
    await telegram.send_message(
        "✏️ <b>Dime qué cambio.</b>\n"
        "Escribe aquí lo que quieras corregir — el hook, una escena, el tono, "
        "la duración. Se regenera solo lo que afecte tu comentario."
    )


async def _reject(action: telegram.Action) -> None:
    version = await _load(action.version_id, expect=State.EN_REVISION)
    if version is None:
        await telegram.answer_callback(action.callback_id,
                                       "Ese guion ya estaba resuelto")
        return
    async with store.session() as sess:
        fresh = await sess.get(Version, version.id)
        await store.transition(sess, fresh, State.DESCARTADO,
                               note="rechazado por el operador")
    await telegram.answer_callback(action.callback_id, "Descartado")
    await telegram.send_message("🗑 Descartado. No se ha gastado nada.")


async def _regenerate(action: telegram.Action) -> None:
    await telegram.answer_callback(action.callback_id, "Regenerando")
    await _rewrite(action.version_id, feedback=None)


async def _feedback(action: telegram.Action) -> None:
    """Un texto suelto. Qué significa depende de qué esté esperando.

    Sobre un guion es una corrección de las palabras. Sobre un vídeo ya
    renderizado es un defecto de lo que salió —"la mano tiene seis dedos"— y eso
    no se arregla reescribiendo: se arregla comprando la capa que falla y
    montando otra vez, que cuesta una fracción de lo que costó el vídeo.
    """
    async with store.session() as sess:
        version = await sess.get(Version, action.version_id)
        estado = State(version.state) if version else None

    if estado is State.REVISION_VIDEO:
        await _repair_video(action)
        return
    await _rewrite(action.version_id, feedback=action.text)


async def _idle_text(action: telegram.Action) -> None:
    """Un texto cuando no hay nada esperando. No se archiva, pero se contesta.

    Ocurrió el 2 de agosto: Juan descartó un vídeo con el botón y después
    escribió el fallo que le había visto. El texto llegó cuando ya no había
    nada en cola, así que el bot lo ignoró — correcto por dentro y roto por
    fuera, porque él se quedó esperando una reacción que nunca iba a llegar.
    """
    await telegram.send_message(
        "🤔 No tengo nada esperando ahora mismo, así que no sé a qué se refiere "
        "eso.\n\n"
        "· <code>/guion &lt;tema&gt;</code> — escribo uno sobre lo que me digas\n"
        "· Si acabas de descartar un vídeo, su guion ya está cerrado: pídeme "
        "otro con <code>/guion</code> y el fallo que viste no se repetirá, "
        "porque ese material ya no está en el caché.\n\n"
        "<i>Cuando haya un guion o un vídeo esperando, un mensaje suelto sí es "
        "una corrección y lo aplico.</i>")


async def _ask_for_defect(action: telegram.Action) -> None:
    """El botón «Defectuoso»: sólo pide el defecto, no toca nada todavía.

    Un botón de Telegram no puede recoger texto, así que esto abre la
    conversación y el mensaje siguiente es el que trabaja — el mismo camino que
    «Pedir cambios» sobre un guion.
    """
    version = await _load(action.version_id, expect=State.REVISION_VIDEO)
    if version is None:
        await telegram.answer_callback(action.callback_id,
                                       "Ese vídeo ya estaba resuelto")
        return
    await telegram.answer_callback(action.callback_id)
    await telegram.send_message(
        "🔧 <b>¿Qué le has visto?</b>\n"
        "Escríbelo tal cual: «repite una palabra», «en la escena 2 la mano "
        "tiene seis dedos», «la foto no pega con lo que dice».\n\n"
        "<i>Se vuelve a comprar sólo eso — una locución, o el plano de esa "
        "escena — y se monta otra vez. El resto del vídeo ya está pagado y no "
        "se toca.</i>")


async def _repair_video(action: telegram.Action) -> None:
    """Comprar la capa que falla y montar otra vez. Nada más.

    Un vídeo casi bueno es el caso normal, no la excepción, y hasta ahora
    costaba lo mismo que uno malo: tirarlo entero y volver a pagar la voz, las
    fotos y el metraje. Pero un vídeo son tres capas montadas al final, y el
    plan del render dice cuál es cuál, así que un defecto de voz cuesta una
    locución y uno de plano cuesta un plano.

    Se pasa por ``renderizando`` porque esto gasta: ahí es donde se mira el
    presupuesto, y donde una segunda pulsación se encuentra la puerta cerrada.
    Y de ahí no se sale a ningún sitio que no sea otra vez el vídeo delante de
    una persona — el arreglo no publica, como no publica un render.
    """
    defecto = (action.text or "").strip()
    version = await _load(action.version_id, expect=State.REVISION_VIDEO)
    if version is None:
        return

    workdir = os.path.join(WORK_ROOT, action.version_id)
    segmentos = render.load_plan(workdir)
    if not segmentos:
        # Un vídeo de antes de que existiera el plan. El camino largo sigue ahí.
        await _defect_rewrites(action)
        return

    arreglo_ = await arreglo.clasificar(defecto, segmentos)
    if not arreglo_.se_puede:
        await telegram.send_message(
            f"📝 Eso no se arregla montando otra vez: «{telegram.escape(arreglo_.motivo[:120])}» "
            f"está en las palabras, y las palabras ya las aprobaste.\n"
            f"<i>Te devuelvo el guion para que lo corrijas.</i>")
        await _defect_rewrites(action)
        return

    async with store.session() as sess:
        fresh = await sess.get(Version, action.version_id)
        try:
            await store.transition(sess, fresh, State.RENDERIZANDO,
                                   note=f"arreglando: {defecto[:180]}")
        except (StateError, store.BudgetExceededError) as exc:
            await telegram.send_message(f"⚠️ No se puede arreglar: {exc}")
            return
        script = json.loads(fresh.script) if fresh.script else {}
        caption = fresh.caption or ""

    await telegram.send_message(
        f"🔧 Anotado: «{telegram.escape(defecto[:120])}».\n"
        + ("<i>Eso se arregla montando otra vez. No cuesta nada.</i>"
           if arreglo_.gratis
           else f"<i>Es la {arreglo_.capa}. Compro eso y lo monto otra vez.</i>"))

    fmt = formats.current()
    if arreglo_.capa == arreglo.SUBTITULOS:
        fmt = formats.sin_subtitulos(fmt)
    out_path = os.path.join(workdir, "short.mp4")
    # Se monta al lado y se sustituye al final. Un arreglo que se cae a medias
    # no puede llevarse por delante el vídeo que había, que era publicable.
    provisional = os.path.join(workdir, "arreglado.mp4")
    try:
        rehecho = await arreglo.aplicar(arreglo_, segmentos, workdir=workdir,
                                        script=script, fmt=fmt)
        result = render.compose(segmentos, provisional, workdir, fmt=fmt)
        os.replace(provisional, out_path)
        result.path = out_path
    except Exception as exc:  # noqa: BLE001 - cualquier fallo tiene que aterrizar
        # De vuelta al segundo control, no a `fallido`: el vídeo de antes sigue
        # ahí y sigue siendo publicable. Un intento de mejorarlo no puede ser
        # la forma de perderlo.
        async with store.session() as sess:
            fresh = await sess.get(Version, action.version_id)
            await store.transition(sess, fresh, State.REVISION_VIDEO,
                                   note=f"arreglo fallido: {str(exc)[:180]}")
        await telegram.send_message(
            f"❌ El arreglo ha fallado: {exc}\n"
            f"<i>El vídeo que tenías sigue intacto y sus botones siguen "
            f"valiendo.</i>")
        return

    async with store.session() as sess:
        fresh = await sess.get(Version, action.version_id)
        fresh.video_path = result.path
        fresh.duration_s = result.duration_s
        await store.transition(sess, fresh, State.REVISION_VIDEO,
                               note=f"arreglado: {rehecho}")

    await telegram.send_video(
        result.path,
        f"🔧 <b>{rehecho}.</b>\n" + _publish_preview(script, caption, result),
        width=render.WIDTH, height=render.HEIGHT, duration=result.duration_s,
        version_id=action.version_id)


async def _defect_rewrites(action: telegram.Action) -> None:
    """El camino largo: se tira el vídeo y el mismo guion vuelve a la cola.

    Para lo que no se arregla montando —las palabras— y para los vídeos que se
    renderizaron antes de que hubiera un plan que consultar. No se reescribe el
    guion, que puede no tener la culpa: se propone otra vez, y aprobarlo vuelve
    a montar sin el material que causó el fallo.
    """
    defecto = (action.text or "").strip()

    async with store.session() as sess:
        previa = await sess.get(Version, action.version_id)
        nueva = await store.next_version(
            sess, previa, note=f"vídeo rechazado: {defecto[:200]}")
        await store.transition(sess, nueva, State.EN_REVISION)
        script = json.loads(nueva.script) if nueva.script else {}
        nueva_id, numero = nueva.id, nueva.number

    await telegram.send_message(
        f"🔁 Anotado: «{telegram.escape(defecto[:120])}».\n"
        f"<i>El guion no tenía la culpa, así que te lo propongo igual. "
        f"Aprobar vuelve a montarlo, y el material defectuoso ya no está.</i>")
    await telegram.send_review(
        script, nueva_id, version_number=numero,
        estimated_seconds=preflight.estimate_seconds(
            gemini.narration_text(script)),
        coverage=_expected_coverage(script))


async def _rewrite(version_id: str, *, feedback: str | None) -> None:
    """Turn feedback into a new version and send it back for review."""
    version = await _load(version_id, expect=State.EN_REVISION)
    if version is None:
        return

    async with store.session() as sess:
        fresh = await sess.get(Version, version.id)
        source = json.loads(fresh.sources) if fresh.sources else {}
        old_script = json.loads(fresh.script) if fresh.script else {}

    candidate = editorial.Candidate(
        title=old_script.get("titulo", ""),
        url=source.get("url", ""),
        publisher=source.get("publisher", ""),
        published_at=fresh.created_at,
        fetched_at=fresh.created_at,
        facts=source.get("facts", []),
        summary="",
    )

    note = feedback or "regenerar sin cambios concretos"
    script, _usage = await gemini.write_script(candidate)
    narration = gemini.narration_text(script)
    check = preflight.check(script, narration=narration,
                            estimated_cents=voice.estimate_cents(narration))

    async with store.session() as sess:
        previous = await sess.get(Version, version.id)
        new = await store.next_version(
            sess, previous, note=note,
            script=json.dumps(script, ensure_ascii=False))
        new.caption = script.get("caption", "")
        if not check.ok:
            await store.transition(sess, new, State.DESCARTADO, note=str(check))
            await telegram.send_message(
                f"⚠️ La nueva versión no pasa el preflight:\n{check}")
            return
        await store.transition(sess, new, State.EN_REVISION)
        new_id, number = new.id, new.number

    await telegram.send_review(
        script, new_id, candidate=candidate, version_number=number,
        estimated_seconds=check.estimated_seconds,
        estimated_cents=check.estimated_cents,
        coverage=_expected_coverage(script),
    )


# --------------------------------------------------------------------------
# A script the operator asked for
# --------------------------------------------------------------------------

_BRIEF_HELP = (
    "✍️ <b>Dime sobre qué.</b>\n"
    "Escribe <code>/guion</code> y el tema, por ejemplo:\n"
    "<code>/guion lo que cuesta contestar a mano los mismos WhatsApps</code>\n\n"
    "<i>Sin noticia detrás no hay fuentes, así que el guion no llevará cifras. "
    "Si quieres un dato, dámelo con su origen: «según el INE, el 7,7%».</i>"
)


async def _on_request(action: telegram.Action) -> None:
    """Write a script about whatever the operator typed.

    Deliberately outside :func:`run_cycle` and outside its active-cycle guard.
    That guard exists so the *scheduler* does not stack unapproved videos; a
    person asking for a second script has already decided they want it, and
    being told "ya hay un guion en revisión" in answer to a direct request
    reads as the bot refusing to work.
    """
    brief = (action.text or "").strip()
    if not brief:
        await telegram.send_message(_BRIEF_HELP)
        return

    await telegram.send_message(
        f"✍️ Escribiendo un guion sobre «{telegram.escape(brief[:90])}»…")

    script, _usage = await gemini.write_brief_script(brief)
    narration = gemini.narration_text(script)
    check = preflight.check(script, narration=narration,
                            estimated_cents=voice.estimate_cents(narration))

    async with store.session() as sess:
        content = await store.create_content(
            sess,
            title=(script.get("titulo") or brief)[:300],
            # Not the hash of the words: the deduplication rule exists so the
            # news is not produced twice, and asking twice for the same subject
            # is a decision, not an accident.
            topic_hash=f"peticion-{uuid.uuid4().hex[:24]}",
            angle="petición del operador",
        )
        version = await sess.get(Version, content.current_version_id)
        version.script = json.dumps(script, ensure_ascii=False)
        version.sources = json.dumps(
            {"url": "", "publisher": "petición del operador", "facts": [brief]},
            ensure_ascii=False)
        version.caption = script.get("caption", "")
        await store.transition(sess, version, State.GUION)

        if not check.ok:
            await store.transition(sess, version, State.DESCARTADO,
                                   note=f"petición: {brief[:200]} / {check}")
            await telegram.send_message(
                f"⚠️ Ese guion no pasa el preflight:\n{check}\n\n"
                f"<i>Vuelve a pedírmelo con otro enfoque, o dame el dato con su "
                f"fuente si quieres que lleve cifras.</i>")
            return

        await store.transition(sess, version, State.EN_REVISION)
        version_id, number = version.id, version.number

    await telegram.send_review(
        script, version_id, version_number=number,
        estimated_seconds=check.estimated_seconds,
        estimated_cents=check.estimated_cents,
        coverage=_expected_coverage(script),
    )


# --------------------------------------------------------------------------
# Render and publish
# --------------------------------------------------------------------------

def _publish_preview(script: dict, caption: str, result) -> str:
    """What the second gate shows: the video, and exactly what would be posted.

    The title and the description come from the same function that builds the
    real request, so approving here cannot approve something different from
    what leaves the machine. Nobody should have to trust a summary of a post
    they are about to make.
    """
    title = script.get("titulo") or script.get("hook") or "Autenia"
    lines = [f"🎬 <b>Listo.</b> {result.duration_s:.0f}s · "
             f"{result.coverage:.0%} material propio"]

    visibilidad = {"public": "público", "unlisted": "no listado",
                   "private": "privado"}

    for platform in publish.configured():
        campos = publish.preview(platform, title=title, caption=caption)
        if platform == "youtube":
            formato = ("Short" if publish.is_short(result.duration_s,
                                                   render.WIDTH, render.HEIGHT)
                       else "vídeo normal (no cumple para Short)")
            estado = campos["privacyStatus"]
            # Telegram caps a video caption at 1024 characters and cuts what
            # goes over. A description trimmed here ends where it decides to,
            # rather than mid-word at whatever the limit happens to land on.
            descripcion = campos["youtube_description"]
            if len(descripcion) > 600:
                descripcion = descripcion[:600].rsplit(" ", 1)[0] + "…"
            lines += [
                "",
                f"📺 <b>YouTube</b> · {formato} · "
                f"{visibilidad.get(estado, estado)}",
                f"<b>{telegram.escape(campos['youtube_title'])}</b>",
                f"<i>{telegram.escape(descripcion)}</i>",
            ]
        else:
            lines += ["", f"📱 <b>{platform}</b> · "
                          f"{telegram.escape(campos['title'])}"]

    lines.append("")
    lines.append("🧪 <b>Simulación:</b> «Publicar» no enviará nada."
                 if autenia.publish_dry_run
                 else "<b>Publicar</b> lo sube tal cual. Eso no se deshace.")
    return "\n".join(lines)


async def _render_and_publish(version_id: str, script: dict) -> None:
    """Render, then hand the video back. Publishing is a separate decision."""
    workdir = os.path.join(WORK_ROOT, version_id)
    out_path = os.path.join(workdir, "short.mp4")

    try:
        result = await render.render(
            script, out_path=out_path, workdir=workdir,
            library_dir=LIBRARY_DIR if os.path.isdir(LIBRARY_DIR) else None,
            fmt=formats.current())
    except Exception as exc:  # noqa: BLE001 - any failure must land in the log
        async with store.session() as sess:
            fresh = await sess.get(Version, version_id)
            await store.transition(sess, fresh, State.FALLIDO, note=str(exc))
        await telegram.send_message(f"❌ El render ha fallado: {exc}")
        return

    async with store.session() as sess:
        fresh = await sess.get(Version, version_id)
        fresh.video_path = result.path
        fresh.duration_s = result.duration_s
        caption = fresh.caption or ""
        # Out of `renderizando` as soon as the spending is over: that state
        # allows one version per content and blocks the next cycle while it is
        # occupied, and waiting for a human is not rendering.
        await store.transition(sess, fresh, State.REVISION_VIDEO)

    await telegram.send_video(
        result.path, _publish_preview(script, caption, result),
        # Told, not guessed: Telegram does not read them off the file, and a
        # vertical master with no dimensions arrives looking squashed.
        width=render.WIDTH, height=render.HEIGHT, duration=result.duration_s,
        version_id=version_id)


async def _publish_video(action: telegram.Action) -> None:
    """The second gate: the operator has watched it and lets it out."""
    version = await _load(action.version_id, expect=State.REVISION_VIDEO)
    if version is None:
        await telegram.answer_callback(action.callback_id,
                                       "Ese vídeo ya estaba resuelto")
        return

    await telegram.answer_callback(
        action.callback_id,
        "Simulando…" if autenia.publish_dry_run else "Publicando…")

    async with store.session() as sess:
        fresh = await sess.get(Version, version.id)
        script = json.loads(fresh.script) if fresh.script else {}
        version_id, path, caption = fresh.id, fresh.video_path, fresh.caption or ""

    title = script.get("titulo") or script.get("hook") or "Autenia"

    try:
        outcomes = await publish.publish(
            path, version_id=version_id, title=title, caption=caption)
    except publish.PublishError as exc:
        # `revision_video` is not terminal either: a version left there blocks
        # every future cycle. Failing before the attempt is still an end.
        async with store.session() as sess:
            fresh = await sess.get(Version, version_id)
            await store.transition(sess, fresh, State.FALLIDO, note=str(exc))
        await telegram.send_message(
            f"❌ No se ha podido publicar: {exc}\n"
            f"<i>El vídeo está hecho y lo tienes arriba; puedes subirlo a "
            f"mano.</i>")
        return

    lines = [outcome.summary for outcome in outcomes]
    if autenia.publish_dry_run:
        lines.append("")
        lines.append("Para publicar de verdad: <code>AUTENIA_PUBLISH_DRY_RUN=false</code>, "
                     "<code>UPLOAD_POST_API_KEY</code> y <code>UPLOAD_POST_USER</code>.")
    await telegram.send_message("\n".join(lines))

    async with store.session() as sess:
        fresh = await sess.get(Version, version_id)
        if all(outcome.ok for outcome in outcomes):
            note = "simulado (dry-run): no se envió a ninguna red" if autenia.publish_dry_run else None
            await store.transition(sess, fresh, State.PUBLICADO, note=note)
        else:
            failed = ", ".join(o.platform for o in outcomes if not o.ok)
            await store.transition(sess, fresh, State.FALLIDO,
                                   note=f"publicación fallida en {failed}")


async def _discard_video(action: telegram.Action) -> None:
    """The operator watched it and it does not go out.

    Terminal, and the file stays on disk: it was paid for, and the next
    argument about what the format should look like is better had over a video
    that exists than over a memory of one.
    """
    version = await _load(action.version_id, expect=State.REVISION_VIDEO)
    if version is None:
        await telegram.answer_callback(action.callback_id,
                                       "Ese vídeo ya estaba resuelto")
        return

    async with store.session() as sess:
        fresh = await sess.get(Version, version.id)
        path = fresh.video_path
        await store.transition(sess, fresh, State.DESCARTADO,
                               note="el operador no lo publicó")
    await telegram.answer_callback(action.callback_id, "No se publica")
    await telegram.send_message(
        f"🗑 No se publica. El vídeo se queda en <code>{path}</code> por si "
        f"quieres usarlo o compararlo.")


# --------------------------------------------------------------------------
# The listener
# --------------------------------------------------------------------------

async def awaiting_ids() -> list[str]:
    """Versions in review, oldest first.

    A list rather than a set because the order carries meaning: a plain text
    message is feedback on the most recent thing sent, and with `/guion` there
    can be several waiting at once.

    Incluye los vídeos esperando permiso, no sólo los guiones: a un vídeo
    también se le contesta por escrito, y ese texto es un defecto que hay que
    poder contar.
    """
    async with store.session() as sess:
        return [v.id for v in await store.waiting_for_operator(sess)]


async def listen() -> None:
    """Run the review bot until stopped."""
    await store.init_db()
    pending: list[str] = await awaiting_ids()

    async def handler(action: telegram.Action) -> None:
        await handle(action)
        pending[:] = await awaiting_ids()

    await telegram.poll(handler, awaiting=lambda: pending)
