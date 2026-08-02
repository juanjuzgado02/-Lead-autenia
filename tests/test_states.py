"""The lifecycle rules, tested where they are cheapest to test.

These are pure-function checks over the transition table; the store tests cover
the same rules against the database.
"""

import pytest

from autenia.states import (
    FROZEN, TERMINAL, ImmutableVersionError, State, StateError, assert_mutable,
    assert_transition, can_transition, is_terminal, spends,
)

LEGAL = [
    (State.CANDIDATO, State.GUION),
    (State.CANDIDATO, State.DESCARTADO),
    (State.GUION, State.EN_REVISION),
    (State.GUION, State.DESCARTADO),
    (State.EN_REVISION, State.APROBADO),
    (State.EN_REVISION, State.DESCARTADO),
    (State.APROBADO, State.RENDERIZANDO),
    (State.APROBADO, State.DESCARTADO),
    (State.RENDERIZANDO, State.REVISION_VIDEO),
    (State.RENDERIZANDO, State.FALLIDO),
    (State.REVISION_VIDEO, State.PUBLICADO),
    (State.REVISION_VIDEO, State.DESCARTADO),
    (State.REVISION_VIDEO, State.FALLIDO),
    # Arreglar un vídeo casi bueno: se compra la capa que falla y se monta otra
    # vez. Vuelve por la puerta del gasto porque gasta, y es esa puerta la que
    # comprueba el presupuesto y la que impide dos arreglos a la vez.
    (State.REVISION_VIDEO, State.RENDERIZANDO),
]


@pytest.mark.parametrize("current,target", LEGAL)
def test_legal_transitions_are_allowed(current, target):
    assert can_transition(current, target)
    assert_transition(current, target)  # does not raise


@pytest.mark.parametrize("current,target", [
    # The one that matters most: nothing reaches publication without review.
    (State.GUION, State.PUBLICADO),
    (State.CANDIDATO, State.RENDERIZANDO),
    (State.EN_REVISION, State.PUBLICADO),
    # Spending cannot happen before a human approved the words.
    (State.GUION, State.RENDERIZANDO),
    (State.CANDIDATO, State.APROBADO),
    # Rendering cannot be re-entered to have another go at spending.
    (State.RENDERIZANDO, State.RENDERIZANDO),
    # The second gate is not optional: a render cannot publish itself.
    (State.RENDERIZANDO, State.PUBLICADO),
])
def test_illegal_transitions_raise(current, target):
    assert not can_transition(current, target)
    with pytest.raises(StateError):
        assert_transition(current, target)


def test_approval_cannot_be_skipped():
    """No path from script to published avoids en_revision."""
    reachable = {State.GUION}
    frontier = [State.GUION]
    while frontier:
        state = frontier.pop()
        for nxt in State:
            if nxt is State.EN_REVISION:
                continue  # pretend review does not exist
            if can_transition(state, nxt) and nxt not in reachable:
                reachable.add(nxt)
                frontier.append(nxt)
    assert State.PUBLICADO not in reachable, (
        "a version can reach publication without passing through review"
    )


@pytest.mark.parametrize("state", sorted(TERMINAL, key=lambda s: s.value))
def test_terminal_states_go_nowhere(state):
    assert is_terminal(state)
    for target in State:
        assert not can_transition(state, target)
    with pytest.raises(StateError, match="terminal"):
        assert_transition(state, State.GUION)


@pytest.mark.parametrize("state", sorted(FROZEN, key=lambda s: s.value))
def test_approved_and_published_are_immutable(state):
    with pytest.raises(ImmutableVersionError):
        assert_mutable(state)


@pytest.mark.parametrize("state", [
    State.CANDIDATO, State.GUION, State.EN_REVISION,
])
def test_everything_before_approval_is_editable(state):
    assert_mutable(state)  # does not raise


def test_only_rendering_spends():
    spending = [state for state in State if spends(state)]
    assert spending == [State.RENDERIZANDO]


def test_every_state_has_a_transition_rule():
    """A state missing from the table would raise KeyError at runtime."""
    from autenia.states import TRANSITIONS
    assert set(TRANSITIONS) == set(State)


# -- the second gate -------------------------------------------------------

def test_the_video_gate_holds_the_words_frozen_too():
    """A rendered video and its script must keep saying the same thing."""
    assert State.REVISION_VIDEO in FROZEN
    with pytest.raises(ImmutableVersionError):
        assert_mutable(State.REVISION_VIDEO)


def test_waiting_on_a_video_is_not_terminal():
    """Every path out of it must reach publicado, descartado or fallido."""
    assert not is_terminal(State.REVISION_VIDEO)


def test_waiting_on_a_video_does_not_spend():
    """The money was spent in renderizando; this state only waits."""
    assert not spends(State.REVISION_VIDEO)
