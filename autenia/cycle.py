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
from . import (editorial, formats, gemini, preflight, publish, render, sources,
               store, telegram, voice)
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

    await telegram.answer_callback(action.callback_id, "Aprobado — renderizando")
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
    await _rewrite(action.version_id, feedback=action.text)


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

async def _render_and_publish(version_id: str, script: dict) -> None:
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

    title = script.get("titulo") or script.get("hook") or "Autenia"
    await telegram.send_video(
        result.path,
        f"{'🧪 Simulando publicación' if autenia.publish_dry_run else '📤 Publicando'}…\n"
        f"{result.duration_s:.0f}s · {result.coverage:.0%} material propio")

    try:
        outcomes = await publish.publish(
            result.path, version_id=version_id, title=title, caption=caption)
    except publish.PublishError as exc:
        await telegram.send_message(f"❌ No se ha podido publicar: {exc}")
        return

    lines = [outcome.summary for outcome in outcomes]
    if autenia.publish_dry_run:
        lines.append("")
        lines.append("Para publicar de verdad: <code>AUTENIA_PUBLISH_DRY_RUN=false</code>, "
                     "<code>UPLOAD_POST_API_KEY</code> y <code>UPLOAD_POST_USER</code>.")
    await telegram.send_message("\n".join(lines))

    # `renderizando` is not terminal, and a version parked there would block
    # tomorrow's cycle for ever. So a dry run still closes the version — with a
    # note that says plainly that nothing left the building.
    async with store.session() as sess:
        fresh = await sess.get(Version, version_id)
        if all(outcome.ok for outcome in outcomes):
            note = "simulado (dry-run): no se envió a ninguna red" if autenia.publish_dry_run else None
            await store.transition(sess, fresh, State.PUBLICADO, note=note)
        else:
            failed = ", ".join(o.platform for o in outcomes if not o.ok)
            await store.transition(sess, fresh, State.FALLIDO,
                                   note=f"publicación fallida en {failed}")


# --------------------------------------------------------------------------
# The listener
# --------------------------------------------------------------------------

async def awaiting_ids() -> list[str]:
    """Versions in review, oldest first.

    A list rather than a set because the order carries meaning: a plain text
    message is feedback on the most recent script, and with `/guion` there can
    now be several waiting at once.
    """
    async with store.session() as sess:
        return [v.id for v in await store.awaiting_review(sess)]


async def listen() -> None:
    """Run the review bot until stopped."""
    await store.init_db()
    pending: list[str] = await awaiting_ids()

    async def handler(action: telegram.Action) -> None:
        await handle(action)
        pending[:] = await awaiting_ids()

    await telegram.poll(handler, awaiting=lambda: pending)
