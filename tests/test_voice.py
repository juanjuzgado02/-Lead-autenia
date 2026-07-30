"""Which voice speaks, and how it can be changed.

The voice is Autenia's on every published video, so the rules here are about
nobody changing it by accident — including a future refactor that "tidies up" a
default it does not realise was chosen by listening.
"""

import pytest

from autenia import gemini, voice


def test_the_default_voice_is_the_one_juan_chose():
    """Picked by ear on 2026-07-30 from five male voices reading one script.

    A test rather than a comment because the previous default (Kore, female)
    was never a decision — it was the first thing wired in, and it stayed for
    weeks until somebody listened to a finished video.
    """
    assert gemini.DEFAULT_VOICE == "Iapetus"


def test_an_experiment_can_override_it_without_touching_code(monkeypatch):
    monkeypatch.setenv("AUTENIA_VOICE_NAME", "Charon")
    from core_config import settings
    assert settings.voice_name == "Charon"


def test_gemini_is_the_default_provider(monkeypatch):
    """No second credential and no subscription to produce a video."""
    monkeypatch.delenv("AUTENIA_VOICE_PROVIDER", raising=False)
    from core_config import settings
    assert settings.voice_provider == "gemini"


def test_an_unknown_provider_is_refused_rather_than_guessed(monkeypatch):
    monkeypatch.setenv("AUTENIA_VOICE_PROVIDER", "openai")
    from core_config import ConfigError, settings
    with pytest.raises(ConfigError):
        settings.voice_provider


def test_the_gemini_voice_costs_nothing_to_estimate():
    """Budgets read this before spending, so free must estimate as free."""
    assert voice.estimate_cents("una frase cualquiera", provider="gemini") == 0


def test_elevenlabs_is_estimated_generously_not_optimistically():
    """An estimate that is too low lets a video through the budget it breaks."""
    short = voice.estimate_cents("hola", provider="elevenlabs")
    long = voice.estimate_cents("hola " * 500, provider="elevenlabs")
    assert long > short >= 0
