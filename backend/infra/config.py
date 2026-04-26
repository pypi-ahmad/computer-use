"""Application configuration with environment-based settings."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load .env file from project root (does NOT override existing system env vars)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"
if _ENV_FILE.exists():
    load_dotenv(_ENV_FILE, override=False)
    logger.debug("Loaded .env from %s", _ENV_FILE)


@dataclass
class Config:
    """Runtime configuration — values come from env vars or runtime overrides."""

    # Gemini
    gemini_model: str = "gemini-3-flash-preview"

    # Docker container
    container_name: str = "cua-environment"
    container_image: str = "cua-ubuntu:latest"

    # Agent service inside container
    agent_service_host: str = "127.0.0.1"
    agent_service_port: int = 9222
    agent_mode: str = "desktop"

    # Screenshot
    screen_width: int = 1440
    screen_height: int = 900

    # Agent
    max_steps: int = 50
    step_timeout: float = 30.0

    # Server
    #
    # Default binds to loopback only. The REST and WebSocket surfaces
    # are unauthenticated by default (any local client can start
    # agents, read screenshots, and consume API-key quota), so binding
    # to every interface would expose that surface to the whole LAN.
    # Override with ``HOST=0.0.0.0`` only when you have also set
    # ``CUA_WS_TOKEN`` and fronted the REST endpoints with auth.
    host: str = "127.0.0.1"
    port: int = 8100
    debug: bool = False
    # Hot-reload is strictly a development-time feature. Previously
    # DEBUG=1 also turned on uvicorn --reload, which was dangerous in
    # any non-local setup (watchfiles fires on disk changes). Set
    # ``CUA_RELOAD=1`` explicitly if you want reload.
    reload: bool = False

    # WebSocket
    ws_screenshot_interval: float = 1.5
    # P-PUB — suspend periodic screenshot capture when zero clients
    # are subscribed (e.g. every viewer is on the noVNC interactive
    # surface). The single per-process publisher task stays alive but
    # goes idle until a subscriber re-registers. Set to False to keep
    # the old "always capture" behaviour; the loop still dedupes so
    # bandwidth impact is minimal either way.
    ws_screenshot_suspend_when_idle: bool = True

    # Engine action timings (seconds)
    ui_settle_delay: float = 0.3
    screenshot_settle_delay: float = 0.12
    post_action_screenshot_delay: float = 5.0

    # Container readiness (D-READY) — total budget for the in-container
    # agent service's ``/health`` endpoint to respond with 200 after
    # ``docker run`` returns. When exhausted, ``_wait_for_service``
    # returns ``ready=False`` — the caller MUST NOT assume the sandbox
    # is usable just because the container process is alive.
    container_ready_timeout: float = 30.0
    # Initial backoff between health-check attempts. Each attempt
    # doubles the delay (capped at ``container_ready_poll_cap``) and
    # adds 0.5–1.0× jitter so multiple concurrent starts don't
    # synchronise into a thundering herd against the agent service.
    container_ready_poll_base: float = 0.5
    container_ready_poll_cap: float = 3.0

    @property
    def agent_service_url(self) -> str:
        """Full HTTP URL for the in-container agent service."""
        return f"http://{self.agent_service_host}:{self.agent_service_port}"

    @classmethod
    def from_env(cls) -> Config:
        """Create a Config instance from environment variables.

        Numeric values read from the environment are clamped to safe
        ranges so a typo or hostile override can't produce e.g. a
        multi-gigapixel virtual display, an out-of-range TCP port, or
        an agent that runs for 2^31 steps.
        """
        return cls(
            gemini_model=os.getenv("GEMINI_MODEL", cls.gemini_model),
            container_name=os.getenv("CONTAINER_NAME", cls.container_name),
            agent_service_host=os.getenv("AGENT_SERVICE_HOST", cls.agent_service_host),
            agent_service_port=_clamp_int(
                "AGENT_SERVICE_PORT", cls.agent_service_port, lo=1, hi=65535,
            ),
            agent_mode=os.getenv("AGENT_MODE", cls.agent_mode),
            screen_width=_clamp_int(
                "SCREEN_WIDTH", cls.screen_width, lo=640, hi=4096,
            ),
            screen_height=_clamp_int(
                "SCREEN_HEIGHT", cls.screen_height, lo=480, hi=4096,
            ),
            max_steps=_clamp_int(
                "MAX_STEPS", cls.max_steps, lo=1, hi=200,
            ),
            step_timeout=_clamp_float(
                "STEP_TIMEOUT", cls.step_timeout, lo=1.0, hi=600.0,
            ),
            host=os.getenv("HOST", cls.host),
            port=_clamp_int("PORT", cls.port, lo=1, hi=65535),
            debug=os.getenv("DEBUG", "").lower() in ("1", "true", "yes"),
            reload=os.getenv("CUA_RELOAD", "").lower() in ("1", "true", "yes"),
            ws_screenshot_interval=_clamp_float(
                "CUA_WS_SCREENSHOT_INTERVAL", cls.ws_screenshot_interval, lo=0.05, hi=60.0,
            ),
            ws_screenshot_suspend_when_idle=_env_bool(
                "CUA_WS_SCREENSHOT_SUSPEND_WHEN_IDLE", cls.ws_screenshot_suspend_when_idle,
            ),
            ui_settle_delay=_clamp_float(
                "CUA_UI_SETTLE_DELAY", cls.ui_settle_delay, lo=0.0, hi=30.0,
            ),
            screenshot_settle_delay=_clamp_float(
                "CUA_SCREENSHOT_SETTLE_DELAY", cls.screenshot_settle_delay, lo=0.0, hi=30.0,
            ),
            post_action_screenshot_delay=_clamp_float(
                "CUA_POST_ACTION_SCREENSHOT_DELAY", cls.post_action_screenshot_delay, lo=0.0, hi=60.0,
            ),
            container_ready_timeout=_clamp_float(
                "CUA_CONTAINER_READY_TIMEOUT", cls.container_ready_timeout, lo=1.0, hi=300.0,
            ),
            container_ready_poll_base=_clamp_float(
                "CUA_CONTAINER_READY_POLL_BASE", cls.container_ready_poll_base, lo=0.05, hi=10.0,
            ),
            container_ready_poll_cap=_clamp_float(
                "CUA_CONTAINER_READY_POLL_CAP", cls.container_ready_poll_cap, lo=0.1, hi=30.0,
            ),
        )


def _clamp_int(var: str, default: int, *, lo: int, hi: int) -> int:
    """Read ``var`` as int, falling back to ``default``, then clamp to [lo, hi].

    Non-integer or out-of-range values are logged and coerced into range
    so a hostile/typo env value can't produce pathological behaviour
    (e.g. ``SCREEN_WIDTH=2147483647`` or ``PORT=-1``).
    """
    raw = os.getenv(var)
    if raw is None or raw == "":
        return max(lo, min(default, hi))
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s=%r is not an integer; using default %d", var, raw, default)
        return max(lo, min(default, hi))
    clamped = max(lo, min(value, hi))
    if clamped != value:
        logger.warning("%s=%d out of [%d, %d]; clamped to %d", var, value, lo, hi, clamped)
    return clamped


def _env_bool(var: str, default: bool) -> bool:
    """Read ``var`` as a boolean-like env override, falling back to ``default``."""
    raw = os.getenv(var)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _clamp_float(var: str, default: float, *, lo: float, hi: float) -> float:
    """Read ``var`` as float with the same clamping semantics as :func:`_clamp_int`."""
    raw = os.getenv(var)
    if raw is None or raw == "":
        return max(lo, min(default, hi))
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s=%r is not a float; using default %s", var, raw, default)
        return max(lo, min(default, hi))
    clamped = max(lo, min(value, hi))
    if clamped != value:
        logger.warning("%s=%s out of [%s, %s]; clamped to %s", var, value, lo, hi, clamped)
    return clamped


# Singleton
config = Config.from_env()


# ── API Key Resolution ────────────────────────────────────────────────────────

# Maps provider name → tuple of accepted env var names for API keys.
# The first entry is the canonical/preferred name; later entries are
# aliases checked in order (e.g. ``GEMINI_API_KEY`` for Google).
_PROVIDER_KEY_ENV_VARS: dict[str, tuple[str, ...]] = {
    "google": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
}


@dataclass
class KeyStatus:
    """Resolution status for a single provider's API key."""

    provider: str
    available: bool = False
    source: str = "none"          # "none" | "env" | "dotenv" | "ui"
    masked_key: str = ""


def _mask_key(key: str) -> str:
    """Return a masked version of an API key for safe display."""
    if len(key) <= 8:
        return "****"
    return key[:4] + "..." + key[-4:]


def _detect_key_source(env_var: str) -> tuple[str | None, str]:
    """Detect where an API key comes from.

    Returns ``(key_value, source_label)``.  Source is ``"env"`` for system
    environment variables, ``"dotenv"`` for .env file values, or ``"none"``
    if not found.

    Heuristic: if the .env file contains the variable, we label it ``"dotenv"``.
    If the variable is set but NOT in the .env file, it's a system env var.
    """
    value = os.environ.get(env_var, "").strip()
    if not value:
        return None, "none"

    # Check if .env file defines this variable
    if _ENV_FILE.exists():
        try:
            env_text = _ENV_FILE.read_text(encoding="utf-8")
            for line in env_text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or "=" not in stripped:
                    continue
                var_name = stripped.split("=", 1)[0].strip()
                if var_name == env_var:
                    return value, "dotenv"
        except OSError:
            pass

    return value, "env"


def resolve_api_key(provider: str, ui_key: str | None = None) -> tuple[str | None, str]:
    """Resolve the API key for *provider* using the priority chain.

    Priority: UI input > .env file > system environment variable.

    Returns ``(key, source)`` where *source* is one of
    ``"ui"``, ``"dotenv"``, ``"env"``, or ``"none"``.
    """
    # 1. UI-provided key (highest priority)
    if ui_key and ui_key.strip():
        return ui_key.strip(), "ui"

    # 2. Environment (.env file or system env var) — try each alias in order
    env_vars = _PROVIDER_KEY_ENV_VARS.get(provider, ())
    for env_var in env_vars:
        value, source = _detect_key_source(env_var)
        if value:
            return value, source

    return None, "none"


def get_all_key_statuses() -> list[dict]:
    """Return the availability status of API keys for all providers."""
    statuses: list[dict] = []
    for provider, env_vars in _PROVIDER_KEY_ENV_VARS.items():
        value: str | None = None
        source = "none"
        for env_var in env_vars:
            value, source = _detect_key_source(env_var)
            if value:
                break
        status = KeyStatus(
            provider=provider,
            available=bool(value),
            source=source,
            masked_key=_mask_key(value) if value else "",
        )
        statuses.append({
            "provider": status.provider,
            "available": status.available,
            "source": status.source,
            "masked_key": status.masked_key,
        })
    return statuses
