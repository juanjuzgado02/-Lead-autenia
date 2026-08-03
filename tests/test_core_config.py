"""Configuration resolution and secret hygiene for the Autenia deployment.

The point of these tests is that a secret never reaches a log line and that the
unsafe direction of every toggle requires an explicit, well-formed opt-in.
"""

import pytest

import core_config
from core_config import ConfigError, Settings, mask, sanitize


@pytest.fixture
def env(monkeypatch):
    """A clean environment: no Autenia variable leaks in from the real .env."""
    for name in list(core_config.os.environ):
        if name.startswith("AUTENIA_") or name in (
            "GEMINI_API_KEY", "ELEVENLABS_API_KEY", "UPLOAD_POST_API_KEY",
            "FAL_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
            "TELEGRAM_WEBHOOK_SECRET", "BILLING_ENABLED",
        ):
            monkeypatch.delenv(name, raising=False)
    return monkeypatch


# -- masking ---------------------------------------------------------------

def test_mask_never_returns_the_value():
    secret = "sk-live-abcdefghijklmnop"
    masked = mask(secret)
    assert secret not in masked
    assert masked.endswith("mnop")  # enough to tell two keys apart


def test_mask_hides_short_values_entirely():
    # A short key would be mostly revealed by a suffix, so show nothing.
    assert mask("short") == "<set>"
    assert mask("") == "<unset>"
    assert mask(None) == "<unset>"


def test_sanitize_masks_nested_credentials():
    payload = {
        "url": "https://api.example.com/post",
        "headers": {"Authorization": "Bearer abcdefghijklmnop", "Accept": "application/json"},
        "body": {"api_key": "k-abcdefghijklmnop", "title": "Cómo automatizar tu inventario"},
        "results": [{"xi-api-key": "xi-abcdefghijklmnop"}],
    }
    clean = sanitize(payload)

    flattened = repr(clean)
    assert "abcdefghijklmnop" not in flattened
    # Non-secret fields survive untouched, or the logs become useless.
    assert clean["headers"]["Accept"] == "application/json"
    assert clean["body"]["title"] == "Cómo automatizar tu inventario"


def test_describe_contains_no_secret_values(env):
    env.setenv("GEMINI_API_KEY", "gm-abcdefghijklmnop")
    env.setenv("TELEGRAM_BOT_TOKEN", "12345:abcdefghijklmnop")

    described = repr(Settings().describe())

    assert "abcdefghijklmnop" not in described
    assert "12345" not in described


# -- safe defaults ---------------------------------------------------------

def test_dry_run_is_on_by_default(env):
    assert Settings().publish_dry_run is True


@pytest.mark.parametrize("value", ["flase", "sí", "off?", "TRUE-ish"])
def test_dry_run_stays_on_for_a_malformed_value(env, value):
    """A typo must not be the thing that starts posting to real accounts.

    Every other boolean raises on a value it does not recognise. This one
    cannot: raising turns a typo into a crash loop on the VPS, and a crash loop
    that nobody notices is how a half-configured bot ends up being "fixed" by
    someone deleting the line entirely.
    """
    env.setenv("AUTENIA_PUBLISH_DRY_RUN", value)
    assert Settings().publish_dry_run is True


def test_dry_run_turns_off_only_with_an_explicit_false(env):
    env.setenv("AUTENIA_PUBLISH_DRY_RUN", "false")
    assert Settings().publish_dry_run is False


def test_internal_mode_is_the_default_in_this_fork(env):
    assert Settings().internal_mode is True


def test_nothing_here_serves_http(env):
    """The CORS allowlist is gone because the thing it protected is gone.

    It replaced the upstream's ``["*"]`` wildcard on a FastAPI app that no
    longer exists. Removing the server is a stronger guarantee than configuring
    it, and a setting that guards nothing reads as protection that isn't there.
    """
    assert not hasattr(Settings(), "allowed_origins")


# -- feature validation ----------------------------------------------------

def test_require_names_the_missing_variables(env):
    settings = Settings()
    with pytest.raises(ConfigError) as excinfo:
        settings.require("review")

    message = str(excinfo.value)
    assert "TELEGRAM_BOT_TOKEN" in message
    assert "TELEGRAM_CHAT_ID" in message


def test_require_passes_once_configured(env):
    env.setenv("TELEGRAM_BOT_TOKEN", "12345:abcdefghijklmnop")
    env.setenv("TELEGRAM_CHAT_ID", "987654321")
    Settings().require("review")  # does not raise


def test_blank_value_counts_as_missing(env):
    env.setenv("GEMINI_API_KEY", "   ")
    assert Settings().missing("editorial") == ["GEMINI_API_KEY"]


def test_faceless_does_not_require_fal(env):
    """The default mode must be runnable without paying for avatar generation."""
    env.setenv("GEMINI_API_KEY", "gm-abcdefghijklmnop")
    env.setenv("ELEVENLABS_API_KEY", "el-abcdefghijklmnop")
    assert Settings().has("editorial", "voice")


# -- limits ----------------------------------------------------------------

def test_limits_have_the_brief_defaults(env):
    limits = Settings().limits
    assert limits.max_cost_per_video == 0.50
    assert limits.max_cost_per_month == 25.00
    assert limits.max_duration_s == 55
    assert limits.timezone == "Europe/Madrid"


def test_limits_accept_a_comma_decimal(env):
    env.setenv("AUTENIA_MAX_COST_PER_VIDEO", "0,75")
    assert Settings().limits.max_cost_per_video == 0.75


def test_malformed_limit_fails_at_startup_not_mid_render(env):
    env.setenv("AUTENIA_MAX_COST_PER_MONTH", "twenty euros")
    with pytest.raises(ConfigError):
        core_config.validate_startup()


def test_negative_limit_is_rejected(env):
    env.setenv("AUTENIA_MAX_COST_PER_VIDEO", "-1")
    with pytest.raises(ConfigError):
        Settings().limits


# -- startup ---------------------------------------------------------------

def test_billing_flag_is_refused(env):
    """cloud/ is separately licensed and must stay asleep."""
    env.setenv("BILLING_ENABLED", "true")
    with pytest.raises(ConfigError, match="BILLING_ENABLED"):
        core_config.validate_startup()


def test_startup_warns_about_absent_features_without_failing(env):
    warnings = core_config.validate_startup(["editorial", "review"])
    assert any("editorial" in w for w in warnings)
    assert any("review" in w for w in warnings)


def test_startup_warns_loudly_when_dry_run_is_off(env):
    env.setenv("AUTENIA_PUBLISH_DRY_RUN", "false")
    warnings = core_config.validate_startup()
    assert any("posted for real" in w for w in warnings)
