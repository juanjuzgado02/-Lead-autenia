"""Server-side configuration for the Autenia internal deployment.

This module is the single place where the Autenia tool reads secrets and
operating limits. It is deliberately independent of ``cloud/``:

* ``cloud/config.py`` is under the separate OpenShorts Commercial License and is
  only imported when ``BILLING_ENABLED`` is set — a flag this deployment must
  never turn on.
* Autenia runs as a single-user internal tool, so secrets live in the server's
  ``.env`` (or the deployment's secret manager) and never in the browser.

Nothing here ever prints, logs or returns a secret value in a diagnostic. Use
:func:`mask` and :func:`sanitize` when you need to show configuration state.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, Optional

from dotenv import load_dotenv

load_dotenv()


class ConfigError(RuntimeError):
    """Raised at startup when required configuration is missing or invalid."""


# --------------------------------------------------------------------------
# Secret hygiene
# --------------------------------------------------------------------------

# Substrings that mark a field as secret in arbitrary payloads. Matched
# case-insensitively against the field name.
_SECRET_NAME_PARTS = ("key", "token", "secret", "password", "passwd",
                      "authorization", "credential", "cookie")

# Header names that carry credentials but whose name does not contain any of the
# parts above.
_SECRET_HEADERS = {"authorization", "proxy-authorization", "xi-api-key"}


def mask(value: Optional[str]) -> str:
    """Render a secret as a diagnostic string that cannot leak it.

    Returns ``"<unset>"``, ``"<set>"`` for anything too short to hint at, or the
    last four characters for longer values so an operator can tell two keys
    apart without the value being usable.
    """
    if not value:
        return "<unset>"
    if len(value) < 12:
        return "<set>"
    return f"…{value[-4:]}"


def is_secret_field(name: str) -> bool:
    """True when a field name should be masked before logging."""
    lowered = name.lower()
    if lowered in _SECRET_HEADERS:
        return True
    return any(part in lowered for part in _SECRET_NAME_PARTS)


def sanitize(payload):
    """Recursively mask secret-looking fields in a dict/list before logging.

    Use this on every provider request and response that gets persisted or
    logged — Telegram payloads, Upload-Post calls, ElevenLabs responses.
    """
    if isinstance(payload, dict):
        return {
            k: (mask(v) if is_secret_field(str(k)) and isinstance(v, str)
                else sanitize(v))
            for k, v in payload.items()
        }
    if isinstance(payload, (list, tuple)):
        return [sanitize(item) for item in payload]
    return payload


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------

_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")


def _env(name: str) -> Optional[str]:
    raw = os.environ.get(name)
    if raw is None:
        return None
    raw = raw.strip()
    return raw or None


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    lowered = raw.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ConfigError(
        f"{name} must be one of {_TRUE + _FALSE}; got an unrecognised value"
    )


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if raw is None:
        return default
    try:
        parsed = float(raw.replace(",", "."))
    except ValueError:
        raise ConfigError(f"{name} must be a number") from None
    if parsed < 0:
        raise ConfigError(f"{name} must not be negative")
    return parsed


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number") from None
    if parsed <= 0:
        raise ConfigError(f"{name} must be positive")
    return parsed


# --------------------------------------------------------------------------
# Features and their required variables
# --------------------------------------------------------------------------

# A "feature" is a vertical slice that only works once its variables exist. The
# app must be able to boot without them so earlier phases stay usable — call
# :func:`require` at the point of use instead of failing the whole process.
FEATURE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "editorial": ("GEMINI_API_KEY",),          # phase 2: research, script, titles
    "voice": ("ELEVENLABS_API_KEY",),          # phase 3: Spanish voiceover
    "review": ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),   # phase 4
    "publish": ("UPLOAD_POST_API_KEY",),       # phase 5
    "avatar": ("FAL_KEY",),                    # optional experiments only
}

_FEATURE_HINTS = {
    "editorial": "Get a free key at https://aistudio.google.com/apikey",
    "voice": "ElevenLabs → Profile → API Keys",
    "review": "Create a bot with @BotFather, then send it a message to learn the chat id",
    "publish": "Upload-Post dashboard → API keys (social accounts must be connected there)",
    "avatar": "Only needed for avatar experiments; the faceless default does not use it",
}


@dataclass(frozen=True)
class Limits:
    """Hard stops. Checked before spending, never after."""

    max_cost_per_video: float
    max_cost_per_month: float
    max_duration_s: int
    timezone: str


class Settings:
    """Live view over the process environment.

    Values are read on access rather than cached at import so tests can patch
    ``os.environ`` and so a restart is not needed after a secret rotation in a
    manager that rewrites the environment.
    """

    # -- secrets -----------------------------------------------------------
    @property
    def gemini_api_key(self) -> Optional[str]:
        return _env("GEMINI_API_KEY")

    @property
    def elevenlabs_api_key(self) -> Optional[str]:
        return _env("ELEVENLABS_API_KEY")

    @property
    def upload_post_api_key(self) -> Optional[str]:
        return _env("UPLOAD_POST_API_KEY")

    @property
    def fal_key(self) -> Optional[str]:
        return _env("FAL_KEY")

    @property
    def telegram_bot_token(self) -> Optional[str]:
        return _env("TELEGRAM_BOT_TOKEN")

    @property
    def telegram_chat_id(self) -> Optional[str]:
        return _env("TELEGRAM_CHAT_ID")

    @property
    def telegram_webhook_secret(self) -> Optional[str]:
        """Only needed when Telegram is driven by webhook instead of polling."""
        return _env("TELEGRAM_WEBHOOK_SECRET")

    # -- operating mode ----------------------------------------------------
    @property
    def internal_mode(self) -> bool:
        """Autenia's single-user private deployment.

        On (the default in this fork) the public galleries are closed, browser
        API keys are ignored and every credential is resolved server-side. Set
        ``AUTENIA_INTERNAL_MODE=false`` to get the upstream OpenShorts
        bring-your-own-key behaviour back.
        """
        return _env_bool("AUTENIA_INTERNAL_MODE", True)

    @property
    def publish_dry_run(self) -> bool:
        """Never publish for real unless this is explicitly turned off.

        Defaults to True and stays True for any unset or malformed value, so a
        typo can only ever be safe.
        """
        return _env_bool("AUTENIA_PUBLISH_DRY_RUN", True)

    @property
    def db_path(self) -> str:
        return _env("AUTENIA_DB_PATH") or "data/autenia.db"

    @property
    def allowed_origins(self) -> list[str]:
        """Browser origins allowed to call the API in internal mode.

        Defaults to the local dev servers only. A deployed UI must add its own
        origin explicitly — the upstream ``["*"]`` wildcard is not acceptable
        once the server holds the credentials.
        """
        raw = _env("AUTENIA_ALLOWED_ORIGINS")
        if not raw:
            return ["http://localhost:5175", "http://localhost:5173",
                    "http://127.0.0.1:5175", "http://127.0.0.1:5173"]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    @property
    def voice_provider(self) -> str:
        """Which service speaks the script: ``gemini`` or ``elevenlabs``.

        Gemini is the default because it needs no second credential and no
        subscription. Switching is a one-variable change, so the choice can be
        made by listening to both rather than by committing up front.
        """
        choice = (_env("AUTENIA_VOICE_PROVIDER") or "gemini").lower()
        if choice not in ("gemini", "elevenlabs"):
            raise ConfigError(
                "AUTENIA_VOICE_PROVIDER must be 'gemini' or 'elevenlabs'"
            )
        return choice

    @property
    def voice_name(self) -> Optional[str]:
        """Provider-specific voice id. Each provider falls back to its default."""
        return _env("AUTENIA_VOICE_NAME")

    @property
    def limits(self) -> Limits:
        return Limits(
            max_cost_per_video=_env_float("AUTENIA_MAX_COST_PER_VIDEO", 0.50),
            max_cost_per_month=_env_float("AUTENIA_MAX_COST_PER_MONTH", 25.00),
            max_duration_s=_env_int("AUTENIA_MAX_DURATION_S", 55),
            timezone=_env("AUTENIA_TZ") or "Europe/Madrid",
        )

    # -- validation --------------------------------------------------------
    def missing(self, *features: str) -> list[str]:
        """Names of the variables a feature needs but does not have."""
        absent: list[str] = []
        for feature in features:
            try:
                names = FEATURE_REQUIREMENTS[feature]
            except KeyError:
                raise ConfigError(f"Unknown feature {feature!r}") from None
            absent.extend(name for name in names if not _env(name))
        return absent

    def has(self, *features: str) -> bool:
        return not self.missing(*features)

    def require(self, *features: str) -> None:
        """Raise a ConfigError naming exactly what to add to ``.env``."""
        absent = self.missing(*features)
        if not absent:
            return
        lines = ["Missing required configuration:"]
        lines += [f"  - {name}" for name in absent]
        hints = [_FEATURE_HINTS[f] for f in features if f in _FEATURE_HINTS
                 and self.missing(f)]
        if hints:
            lines.append("")
            lines += [f"  {hint}" for hint in hints]
        lines.append("")
        lines.append("Set them in the server's .env — never in the browser.")
        raise ConfigError("\n".join(lines))

    # -- diagnostics -------------------------------------------------------
    def describe(self) -> dict:
        """Startup-safe summary. Contains no secret values, by construction."""
        limits = self.limits
        return {
            "internal_mode": self.internal_mode,
            "publish_dry_run": self.publish_dry_run,
            "db_path": self.db_path,
            "timezone": limits.timezone,
            "max_cost_per_video": limits.max_cost_per_video,
            "max_cost_per_month": limits.max_cost_per_month,
            "max_duration_s": limits.max_duration_s,
            "features": {
                name: self.has(name) for name in sorted(FEATURE_REQUIREMENTS)
            },
            "secrets": {
                "GEMINI_API_KEY": mask(self.gemini_api_key),
                "ELEVENLABS_API_KEY": mask(self.elevenlabs_api_key),
                "UPLOAD_POST_API_KEY": mask(self.upload_post_api_key),
                "FAL_KEY": mask(self.fal_key),
                "TELEGRAM_BOT_TOKEN": mask(self.telegram_bot_token),
            },
        }

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return f"<Settings internal_mode={self.internal_mode} dry_run={self.publish_dry_run}>"


settings = Settings()


def validate_startup(features: Iterable[str] = ()) -> list[str]:
    """Validate at boot. Returns human-readable warnings; raises on hard errors.

    Hard errors are configuration that is *present but wrong* (an unparseable
    limit, billing turned on) — those stop the process. Absent optional
    credentials are returned as warnings so the phases that do not need them
    keep working.
    """
    warnings: list[str] = []

    if os.environ.get("BILLING_ENABLED", "").lower() in _TRUE:
        raise ConfigError(
            "BILLING_ENABLED must stay unset in the Autenia deployment: it "
            "activates the separately-licensed cloud/ package."
        )

    # Force-parse every limit so a malformed value fails now, not mid-render.
    settings.limits
    settings.publish_dry_run
    settings.internal_mode

    for feature in features:
        absent = settings.missing(feature)
        if absent:
            warnings.append(
                f"feature '{feature}' is unavailable: missing {', '.join(absent)}"
            )

    if not settings.publish_dry_run:
        warnings.append(
            "AUTENIA_PUBLISH_DRY_RUN is off — approved videos will be posted for real"
        )

    return warnings
