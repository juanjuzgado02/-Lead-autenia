"""The lifecycle of one video, and the rules that keep it from costing twice.

A **content** is an editorial idea. It has N **versions**; the state lives on the
version and the content points at the current one. Feedback never mutates a
version — it creates the next one.

    candidato ─> guion ─> en_revision ─> aprobado ─> renderizando ─> revision_video ─> publicado
                               │                          │     ↑          │
                               └──> descartado            │     └──────────┤  (arreglo)
                                                          └──> fallido     └──> descartado

**There are two gates, and they ask different questions.** The first is on the
script, before a cent is spent: a topic the operator rejects costs nothing, and
edits arrive as text rather than as complaints about a finished video. The
second is on the video that came out of it, before it reaches an audience —
because a script can be approved and still render into something nobody wants
published, and the platform is the one place where "undo" does not exist.

Two rules earn their keep here:

* ``renderizando`` is the only state that spends money. Cost limits are checked
  on the way in, never afterwards, and only one version of a content may be in
  it at a time — that is what stops a double click from paying twice.
* ``aprobado`` freezes the script. What a human approved is exactly what gets
  narrated; any change to the words starts a new version that needs approving
  again. Rendering still attaches its output to that version — recording the
  video it produced is not editing what was approved.
"""

from __future__ import annotations

from enum import Enum


class State(str, Enum):
    """Where a version is in its life. Values are stored in the database."""

    CANDIDATO = "candidato"          # scored idea, nothing spent
    GUION = "guion"                  # script + sources, no video spend yet
    RENDERIZANDO = "renderizando"    # the only state that spends
    EN_REVISION = "en_revision"      # waiting for Telegram
    APROBADO = "aprobado"            # frozen, publishable
    REVISION_VIDEO = "revision_video"  # rendered, waiting to be let out
    PUBLICADO = "publicado"          # terminal
    FALLIDO = "fallido"              # terminal; keeps whatever it burned
    DESCARTADO = "descartado"        # terminal; rejected or superseded


#: The only moves allowed. Anything absent raises rather than passing silently.
TRANSITIONS: dict[State, frozenset[State]] = {
    State.CANDIDATO: frozenset({State.GUION, State.DESCARTADO}),
    State.GUION: frozenset({State.EN_REVISION, State.DESCARTADO}),
    # "Pedir cambios" supersedes this version: the operator's edit becomes the
    # next one at `guion`, and this one ends at `descartado`.
    State.EN_REVISION: frozenset({State.APROBADO, State.DESCARTADO}),
    State.APROBADO: frozenset({State.RENDERIZANDO, State.DESCARTADO}),
    # Rendering never publishes on its own any more: what it produces goes back
    # to the operator, because approving a script is not the same as having
    # seen the video that came out of it.
    State.RENDERIZANDO: frozenset({State.REVISION_VIDEO, State.FALLIDO}),
    # Volver a `renderizando` es cómo se arregla un vídeo casi bueno: se compra
    # la capa que falla —la voz, o el plano de una escena— y se monta otra vez.
    # Vuelve por la puerta del gasto y no por un atajo justamente porque gasta:
    # ahí es donde se mira el presupuesto y donde una segunda pulsación del
    # mismo botón se encuentra con que ya hay algo rindiendo.
    State.REVISION_VIDEO: frozenset({State.PUBLICADO, State.DESCARTADO,
                                     State.FALLIDO, State.RENDERIZANDO}),
    State.PUBLICADO: frozenset(),
    State.FALLIDO: frozenset(),
    State.DESCARTADO: frozenset(),
}

#: Terminal states. A version here never moves again.
TERMINAL = frozenset({State.PUBLICADO, State.FALLIDO, State.DESCARTADO})

#: States where the script is settled. The words cannot change from here on;
#: a rewrite has to become a new version and be approved again.
FROZEN = frozenset({State.APROBADO, State.RENDERIZANDO, State.REVISION_VIDEO,
                    State.PUBLICADO})

#: States that are waiting on the operator. Nothing here costs anything to sit
#: in, but the daily cycle must not stack a second video on top of one.
WAITING = frozenset({State.EN_REVISION, State.REVISION_VIDEO})

#: The single state that can incur provider cost.
SPENDING = State.RENDERIZANDO


class StateError(RuntimeError):
    """An illegal move. Raised, never swallowed."""


class ImmutableVersionError(StateError):
    """An attempt to change a version a human already approved."""


class ConcurrentRenderError(StateError):
    """Another version of this content is already spending."""


def can_transition(current: State, target: State) -> bool:
    return target in TRANSITIONS[current]


def assert_transition(current: State, target: State) -> None:
    """Raise unless ``current -> target`` is a legal move."""
    if can_transition(current, target):
        return
    if current in TERMINAL:
        raise StateError(
            f"{current.value} is terminal; a version there cannot move to {target.value}"
        )
    allowed = ", ".join(sorted(s.value for s in TRANSITIONS[current])) or "nothing"
    raise StateError(
        f"illegal transition {current.value} -> {target.value}; allowed: {allowed}"
    )


def assert_script_mutable(current: State) -> None:
    """Raise if the script is settled. Callers must create a new version instead.

    Guards the *words* — script, caption, sources. Rendering may still record
    the video it produced against an approved version; that is the output of
    what was approved, not a change to it.
    """
    if current in FROZEN:
        raise ImmutableVersionError(
            f"the script of a version in {current.value} cannot be changed — "
            f"create a new version so it goes through review again"
        )


#: Kept for callers written against the old name.
assert_mutable = assert_script_mutable


def is_terminal(state: State) -> bool:
    return state in TERMINAL


def spends(state: State) -> bool:
    """True for the state that may call a paid provider."""
    return state is SPENDING
