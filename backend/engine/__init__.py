"""Unified Computer Use engine for Gemini, Claude, and OpenAI.

Provider adapters keep their native tool contracts while sharing the
desktop executor that talks to the sandbox action service.
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import io
import logging
import math
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol

import httpx

from backend.infra.config import config as _app_config
from backend.models.schemas import load_allowed_models_json as _load_allowed_models_json

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lookup_claude_cu_config(model_id: str) -> tuple[str | None, str | None]:
    """Look up cu_tool_version / cu_betas from allowed_models.json.

    Returns (tool_version, beta_flag) or (None, None) if the model is
    absent or does not declare Anthropic computer-use metadata.
    """
    try:
        for m in _load_allowed_models_json():
            if m.get("model_id") == model_id and m.get("provider") == "anthropic":
                tv = m.get("cu_tool_version")
                betas = m.get("cu_betas")
                bf = betas[0] if isinstance(betas, list) and betas else None
                if tv and bf:
                    return tv, bf
    except Exception:
        pass
    return None, None


def default_openai_reasoning_effort_for_model(model: str) -> str:
    """Return the doc-backed OpenAI reasoning default for a model slug.

    OpenAI's GPT-5.4 model page says ``reasoning.effort`` defaults to
    ``none``. OpenAI's GPT-5.5 model page and latest-model guide say the
    default is ``medium``.
    """
    model = str(model or "").lower()
    if model == "gpt-5.4" or re.match(r"^gpt-5\.4-\d{4}-\d{2}-\d{2}$", model):
        return "none"
    return "medium"


def _to_plain_dict(value: Any) -> dict[str, Any]:
    """Convert SDK objects or typed dict-like values into a plain dict."""
    def _to_plain_value(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: _to_plain_value(value) for key, value in item.items()}
        if isinstance(item, list):
            return [_to_plain_value(value) for value in item]
        if isinstance(item, tuple):
            return [_to_plain_value(value) for value in item]
        if hasattr(item, "model_dump"):
            return _to_plain_value(item.model_dump())
        if hasattr(item, "dict"):
            return _to_plain_value(item.dict())
        if hasattr(item, "__dict__"):
            return {
                key: _to_plain_value(value) for key, value in vars(item).items()
                if not key.startswith("_")
            }
        return item

    plain = _to_plain_value(value)
    if isinstance(plain, dict):
        return plain
    return {}


def _extract_openai_output_text(output_items: list[Any]) -> str:
    """Collect assistant text blocks from a Responses API output list."""
    text_parts: list[str] = []
    for item in output_items:
        if getattr(item, "type", None) != "message":
            continue
        for content_part in getattr(item, "content", []) or []:
            if getattr(content_part, "type", None) != "output_text":
                continue
            text = getattr(content_part, "text", None)
            if text:
                text_parts.append(str(text).strip())
    return "\n\n".join(part for part in text_parts if part)


def _append_source_footer(text: str, sources: list[tuple[str, str]]) -> str:
    """Append a compact source list when provider search returned URLs."""
    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for title, url in sources:
        clean_url = str(url or "").strip()
        if not clean_url or clean_url in seen:
            continue
        seen.add(clean_url)
        clean_title = str(title or clean_url).strip() or clean_url
        deduped.append((clean_title, clean_url))
    if not deduped:
        return text
    footer = "\n".join(f"- {title}: {url}" for title, url in deduped)
    base = text.strip() if text else ""
    if base:
        return f"{base}\n\nSources:\n{footer}"
    return f"Sources:\n{footer}"


def _build_openai_computer_call_output(
    call_id: str,
    screenshot_b64: str,
    *,
    acknowledged_safety_checks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the Responses API follow-up item for a computer call."""
    payload: dict[str, Any] = {
        "type": "computer_call_output",
        "call_id": call_id,
        "output": {
            "type": "computer_screenshot",
            "image_url": f"data:image/png;base64,{screenshot_b64}",
            "detail": "original",
        },
    }
    if acknowledged_safety_checks:
        payload["acknowledged_safety_checks"] = acknowledged_safety_checks
    return payload


def _sanitize_openai_response_item_for_replay(item: Any) -> dict[str, Any]:
    """Strip output-only fields before replaying Responses items statelessly."""
    item_dict = _to_plain_dict(item)
    item_type = str(item_dict.get("type", ""))

    if item_type == "message":
        sanitized: dict[str, Any] = {
            "type": "message",
            "role": item_dict.get("role", "assistant"),
            "content": [],
        }
        for part in item_dict.get("content", []) or []:
            if not isinstance(part, dict):
                continue
            part_dict = dict(part)
            part_dict.pop("annotations", None)
            part_dict.pop("logprobs", None)
            sanitized["content"].append(part_dict)
        if item_dict.get("phase") is not None:
            sanitized["phase"] = item_dict["phase"]
        return sanitized

    if item_type == "computer_call":
        sanitized = {
            "type": "computer_call",
            "call_id": item_dict.get("call_id"),
        }
        if item_dict.get("action") is not None:
            sanitized["action"] = item_dict["action"]
        if item_dict.get("actions") is not None:
            sanitized["actions"] = item_dict["actions"]
        return sanitized

    item_dict.pop("status", None)
    item_dict.pop("pending_safety_checks", None)
    if isinstance(item_dict.get("content"), list):
        for part in item_dict["content"]:
            if isinstance(part, dict):
                part.pop("annotations", None)
                part.pop("logprobs", None)
    return item_dict

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GEMINI_NORMALIZED_MAX = 1000  # Gemini CU outputs 0-999 normalized coords
DEFAULT_SCREEN_WIDTH = 1440
DEFAULT_SCREEN_HEIGHT = 900
DEFAULT_TURN_LIMIT = 25


async def _invoke_safety(
    callback: "Callable[[str], Any] | None",
    explanation: str,
) -> bool:
    """Invoke a safety callback that may be sync or async. Returns False if None."""
    if callback is None:
        return False
    result = callback(explanation)
    if asyncio.iscoroutine(result):
        result = await result
    return bool(result)


# ---------------------------------------------------------------------------
# Shared retry helper for provider LLM calls (AI4)
# ---------------------------------------------------------------------------

# Transient-error classes surfaced by the vendor SDKs. We catch them by
# class name via the import guard so this module keeps working even if
# a specific SDK version doesn't ship one of these.
def _collect_transient_error_types() -> tuple[type[BaseException], ...]:
    """Return a tuple of exception classes worth retrying."""
    classes: list[type[BaseException]] = []
    try:  # pragma: no cover — best-effort
        # NB: do NOT include ``APIStatusError`` — it is the base class for
        # 4xx errors (auth, bad request, not found, unprocessable) which
        # are not transient. Retrying them just delays the real failure
        # by ~5–10 seconds of backoff. ``InternalServerError`` covers 5xx.
        from anthropic import (
            APIConnectionError as _A_CE,
            APITimeoutError as _A_TE,
            InternalServerError as _A_500,
            RateLimitError as _A_RLE,
        )
        classes += [_A_RLE, _A_CE, _A_TE, _A_500]
    except Exception as exc:
        # C-14: an SDK upgrade renaming any of these would silently turn
        # the retry into a no-op for that vendor. Surface it loudly.
        logger.warning("Anthropic transient-error classes unavailable: %s", exc)
    try:  # pragma: no cover
        from openai import RateLimitError as _O_RLE, APIConnectionError as _O_CE, APITimeoutError as _O_TE
        classes += [_O_RLE, _O_CE, _O_TE]
    except Exception as exc:
        logger.warning("OpenAI transient-error classes unavailable: %s", exc)
    try:  # pragma: no cover
        import httpx as _httpx
        classes += [_httpx.TimeoutException, _httpx.ConnectError]
    except Exception as exc:
        logger.warning("httpx transient-error classes unavailable: %s", exc)
    if not classes:  # pragma: no cover
        logger.error(
            "No transient-error classes resolved; LLM retries disabled "
            "(falling back to broad Exception catch)."
        )
        classes = [Exception]
    return tuple(classes)


_TRANSIENT_ERRORS: tuple[type[BaseException], ...] = _collect_transient_error_types()


async def _call_with_retry(
    coro_factory: "Callable[[], Any]",
    *,
    provider: str = "llm",
    on_log: "Callable[[str, str], None] | None" = None,
    attempts: int = 3,
    base_delay: float = 0.8,
) -> Any:
    """Call the coroutine returned by *coro_factory* with retry on transient errors.

    Exponential backoff with jitter. Non-transient exceptions propagate
    immediately. The factory is invoked fresh on each attempt so the
    underlying HTTP request is re-issued, not replayed.
    """
    import random

    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            coro = coro_factory()
            if asyncio.iscoroutine(coro):
                return await coro
            return coro
        except _TRANSIENT_ERRORS as exc:
            last_exc = exc
            if attempt == attempts:
                break
            delay = base_delay * (2 ** (attempt - 1)) * (0.5 + random.random() / 2)
            if on_log:
                on_log(
                    "warning",
                    f"{provider} transient error ({type(exc).__name__}): {exc}; "
                    f"retrying in {delay:.2f}s (attempt {attempt}/{attempts})",
                )
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# Secret scrubbing for free-text model output (AI6)
# ---------------------------------------------------------------------------

import re as _re

# Known API-key prefixes / shapes. Anything matching these gets redacted
# before being broadcast to the frontend.
_SECRET_PATTERNS: tuple[tuple[str, "_re.Pattern[str]"], ...] = (
    ("openai",     _re.compile(r"sk-[A-Za-z0-9_\-]{16,}")),
    ("anthropic",  _re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}")),
    ("google",     _re.compile(r"AIza[0-9A-Za-z_\-]{20,}")),
    ("github",     _re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}")),
    # AI-5: cover all known AWS access-key prefixes (long-term IAM, STS,
    # role/instance/user/etc.) — the prior pattern only matched ``AKIA``.
    ("aws-access", _re.compile(r"(?:AKIA|ASIA|AROA|AIDA|AIPA|ANPA|ANVA|ABIA|ACCA)[0-9A-Z]{16}")),
    ("slack",      _re.compile(r"xox[aboprs]-[A-Za-z0-9\-]{10,}")),
)


def scrub_secrets(text: str | None) -> str | None:
    """Redact API-key-shaped tokens from free-form text."""
    if not text:
        return text
    out = text
    for label, pat in _SECRET_PATTERNS:
        out = pat.sub(f"[REDACTED:{label}]", out)
    return out

# Anthropic coordinate scaling: images with longest edge >1568px or
# total pixels >1,150,000 are internally downsampled.  We pre-resize
# and scale coordinates to eliminate coordinate drift.
_CLAUDE_MAX_LONG_EDGE = 1568
_CLAUDE_MAX_PIXELS = 1_150_000

# ``computer_20251124`` models accept up to 2576 px on the long edge and
# ~3.75 MP total with native 1:1 coordinates per Anthropic's
# 2025-11-24 computer-use docs.
_CLAUDE_OPUS_47_MAX_LONG_EDGE = 2576
_CLAUDE_HIGH_RES_MAX_PIXELS = 3_750_000


def _is_opus_47(model_id: str) -> bool:
    """Return True if *model_id* is a Claude Opus 4.7 variant."""
    return model_id.startswith("claude-opus-4-7") or model_id.startswith("claude-opus-4.7")


# Models that use the higher resolution limit (no downscaling needed
# at typical screen resolutions). All models on the ``computer_20251124``
# tool version (Opus 4.7 / Opus 4.6 / Sonnet 4.6) receive real pixel
# coordinates with the 2576 px long-edge / ~3.75 MP budget per
# Anthropic's 2025-11-24 computer-use docs. Older models continue on
# the legacy 1568 px / scale-factor path.
_CLAUDE_HIGH_RES_MODELS = (
    "claude-opus-4-7", "claude-opus-4.7",
    "claude-opus-4-6", "claude-opus-4.6",
    "claude-sonnet-4-6", "claude-sonnet-4.6",
)


def _uses_claude_20251124(
    model_id: str = "",
    tool_version: str | None = None,
) -> bool:
    """Return True when the Claude run should use the 2025-11-24 CU path.

    Prefer the explicit tool-version when the caller already knows it
    (e.g. from ``allowed_models.json``). Fall back to model-prefix
    detection for older call sites that only pass a model string.
    """
    if tool_version is not None:
        return tool_version == "computer_20251124"
    return any(model_id.startswith(prefix) for prefix in _CLAUDE_HIGH_RES_MODELS)

# Context pruning: replace screenshots older than this many turns with
# a placeholder to prevent unbounded context growth.
_CONTEXT_PRUNE_KEEP_RECENT = 3

_IMAGE_PNG = "image/png"

# xdotool key tokens that should NOT be lowercased when normalizing
# key combinations for DesktopExecutor.  Built once at module level.
_XDOTOOL_SPECIAL_KEYS: frozenset[str] = frozenset({
    "return", "enter", "backspace", "tab", "escape", "delete",
    "space", "home", "end", "insert", "pause",
    "left", "right", "up", "down",
    "page_up", "page_down", "pageup", "pagedown",
    "print", "scroll_lock", "num_lock", "caps_lock",
    "super", "ctrl", "alt", "shift",
    *(f"f{i}" for i in range(1, 25)),
})


# C8: explicit allowlist of xdotool keysym tokens the model is permitted
# to emit. Anything outside this set (e.g. ``xkill``, ``BackSpace`` in
# combination with ``ctrl+alt``) is rejected before being passed to
# xdotool so a prompt-injected screenshot can't trigger disruptive
# keystrokes on the container.
_ALLOWED_KEY_PUNCTUATION: frozenset[str] = frozenset({
    "minus", "plus", "equal", "comma", "period", "slash", "backslash",
    "semicolon", "apostrophe", "grave", "bracketleft", "bracketright",
    "underscore", "asterisk", "at", "hash", "dollar", "percent",
    "ampersand", "question", "exclam", "colon", "parenleft",
    "parenright", "braceleft", "braceright", "quotedbl",
})


def _is_allowed_key_token(token: str) -> bool:
    """Return True if *token* is an allowlisted xdotool keysym."""
    t = token.strip()
    if not t:
        return False
    lower = t.lower()
    # Single letter or digit.
    if len(t) == 1 and (t.isalnum() or t in "-=[];',./`\\"):
        return True
    # Named special keys (function keys, arrows, modifiers, etc.).
    if lower in _XDOTOOL_SPECIAL_KEYS:
        return True
    if lower in _ALLOWED_KEY_PUNCTUATION:
        return True
    # Common named keys accepted by xdotool that weren't in the compact
    # special-keys set above.
    if lower in {"menu", "prtsc", "prtscr", "printscreen", "capslock", "numlock"}:
        return True
    return False


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Provider(str, Enum):
    """LLM provider selection for the CU agent loop."""

    GEMINI = "gemini"
    CLAUDE = "claude"
    OPENAI = "openai"


class Environment(str, Enum):
    """Execution environment selector for Computer Use execution."""

    BROWSER = "browser"
    DESKTOP = "desktop"


def _normalize_search_provider(provider: Provider | str) -> str:
    """Normalize server and engine provider names for search validation."""
    raw = provider.value if isinstance(provider, Provider) else str(provider)
    normalized = {
        "google": "gemini",
        "gemini": "gemini",
        "anthropic": "claude",
        "claude": "claude",
        "openai": "openai",
    }.get(raw.strip().lower())
    if normalized is None:
        raise ValueError(f"Unsupported provider for built-in search validation: {raw!r}")
    return normalized


def _get_gemini_builtin_search_sdk_error() -> str | None:
    """Return a compatibility error when Gemini combo tooling is unavailable."""
    try:
        from google.genai import types as genai_types
    except Exception:
        return "Gemini google_search + computer_use requires the google-genai SDK to be installed."

    if getattr(genai_types, "GoogleSearch", None) is None:
        return (
            "Gemini google_search was requested but the installed google-genai "
            "SDK does not expose GoogleSearch."
        )

    try:
        params = inspect.signature(genai_types.GenerateContentConfig).parameters
    except (TypeError, ValueError):
        params = {}
    if "include_server_side_tool_invocations" not in params:
        return (
            "Gemini google_search + computer_use requires "
            "include_server_side_tool_invocations=True and "
            "tool_config.function_calling_config.mode=VALIDATED, but the "
            "installed google-genai SDK does not expose "
            "include_server_side_tool_invocations."
        )

    if "tool_config" not in params:
        return (
            "Gemini google_search + computer_use requires "
            "include_server_side_tool_invocations=True and "
            "tool_config.function_calling_config.mode=VALIDATED, but the "
            "installed google-genai SDK does not expose tool_config on "
            "GenerateContentConfig."
        )

    if getattr(genai_types, "ToolConfig", None) is None:
        return (
            "Gemini google_search + computer_use requires "
            "include_server_side_tool_invocations=True and "
            "tool_config.function_calling_config.mode=VALIDATED, but the "
            "installed google-genai SDK does not expose ToolConfig."
        )

    if getattr(genai_types, "FunctionCallingConfig", None) is None:
        return (
            "Gemini google_search + computer_use requires "
            "include_server_side_tool_invocations=True and "
            "tool_config.function_calling_config.mode=VALIDATED, but the "
            "installed google-genai SDK does not expose FunctionCallingConfig."
        )

    _mode_enum = getattr(genai_types, "FunctionCallingConfigMode", None)
    if _mode_enum is None or getattr(_mode_enum, "VALIDATED", None) is None:
        return (
            "Gemini google_search + computer_use requires "
            "include_server_side_tool_invocations=True and "
            "tool_config.function_calling_config.mode=VALIDATED, but the "
            "installed google-genai SDK does not expose "
            "FunctionCallingConfigMode.VALIDATED."
        )
    return None


def validate_builtin_search_config(
    *,
    provider: Provider | str,
    model: str,
    use_builtin_search: bool,
    reasoning_effort: str | None = None,
    search_max_uses: int | None = None,
    search_allowed_domains: list[str] | None = None,
    search_blocked_domains: list[str] | None = None,
    allowed_callers: list[str] | None = None,
) -> None:
    """Validate provider-native search settings before any API call is built."""
    has_domain_filters = bool(search_allowed_domains) or bool(search_blocked_domains)
    if not use_builtin_search:
        if search_max_uses is not None or has_domain_filters or allowed_callers is not None:
            raise ValueError("Search options require use_builtin_search=true.")
        return

    provider_key = _normalize_search_provider(provider)

    if provider_key == "claude":
        if search_allowed_domains and search_blocked_domains:
            raise ValueError(
                "Anthropic web search accepts either search_allowed_domains or "
                "search_blocked_domains, not both.",
            )
        return

    if provider_key == "gemini":
        if not model.startswith("gemini-3"):
            raise ValueError(
                "Gemini combined computer_use + google_search is documented only "
                "for Gemini 3 models.",
            )
        if search_max_uses is not None or has_domain_filters or allowed_callers is not None:
            raise ValueError(
                "Gemini google_search does not support search_max_uses or domain "
                "filters in the fetched API docs; allowed_callers is also unsupported.",
            )
        sdk_error = _get_gemini_builtin_search_sdk_error()
        if sdk_error:
            raise ValueError(sdk_error)
        return

    if provider_key == "openai":
        openai_effort = (reasoning_effort or "").lower()
        if openai_effort == "none":
            openai_effort = "minimal"
        if search_max_uses is not None:
            raise ValueError("OpenAI web_search does not support search_max_uses.")
        if allowed_callers is not None:
            raise ValueError(
                "OpenAI web_search does not support allowed_callers.",
            )
        if model.startswith("gpt-5") and openai_effort == "minimal":
            raise ValueError(
                "OpenAI web_search is not supported with gpt-5 models at minimal reasoning.",
            )
        return


class SafetyDecision(str, Enum):
    """Gemini safety-gate verdict attached to CU actions."""

    ALLOWED = "allowed"
    REQUIRE_CONFIRMATION = "require_confirmation"
    BLOCKED = "blocked"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CUActionResult:
    """Result of executing a single CU action."""
    name: str
    success: bool = True
    error: str | None = None
    safety_decision: SafetyDecision | None = None
    safety_explanation: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class CUTurnRecord:
    """Record of one agent-loop turn, emitted via on_turn callback."""
    turn: int
    model_text: str
    actions: list[CUActionResult]
    screenshot_b64: str | None = None


# ---------------------------------------------------------------------------
# Provider turn events
# ---------------------------------------------------------------------------
#
# Per-engine ``iter_turns`` is an ``AsyncIterator[TurnEvent]`` for code
# that wants a per-turn provider stream. The default app path drives the
# native Computer Use client directly through ``execute_task``.

@dataclass
class ModelTurnStarted:
    """Model call has produced text + pending tool uses for this turn.

    Consumers should execute the pending tool uses and then read the
    following ``ToolBatchCompleted`` event from the iterator.
    """
    turn: int
    model_text: str
    pending_tool_uses: int


@dataclass
class ToolBatchCompleted:
    """All tool uses for the current turn have been executed on the desktop."""
    turn: int
    model_text: str
    results: list[CUActionResult]
    screenshot_b64: str | None = None


@dataclass
class SafetyRequired:
    """The provider asked the client to confirm a require_confirmation action.

    The decision comes back to ``iter_turns`` via ``agen.asend(bool)``.
    """
    explanation: str


@dataclass
class RunCompleted:
    """The provider loop exited cleanly with a final text response."""
    final_text: str


@dataclass
class RunFailed:
    """The provider loop raised a non-retryable error.

    Transient retries are handled inside ``_call_with_retry`` before any
    event is yielded, so reaching this event means the run should abort.
    """
    error: str


TurnEvent = ModelTurnStarted | ToolBatchCompleted | SafetyRequired | RunCompleted | RunFailed


# ---------------------------------------------------------------------------
# Shim: drive a legacy ``run_loop`` through the iter_turns contract.
# ---------------------------------------------------------------------------

async def iter_turns_via_run_loop(
    run_loop: "Callable[..., Any]",
    *,
    goal: str,
    executor: "ActionExecutor",
    turn_limit: int,
    on_safety: "Callable[[str], Any] | None",
    on_log: "Callable[[str, str], None] | None",
):
    """Adapt a provider's callback-driven ``run_loop`` to ``iter_turns``.

    Used for engines (currently OpenAI) that have not yet been inverted
    to a native ``iter_turns`` generator. The engine's ``on_turn`` callback
    is pushed onto an ``asyncio.Queue`` which this generator drains,
    yielding one ``ModelTurnStarted`` + ``ToolBatchCompleted`` pair per
    turn and a terminal ``RunCompleted`` / ``RunFailed``.

    ``on_safety`` is forwarded to ``run_loop`` verbatim — safety
    approvals for these engines flow through the
    ``backend.agent.safety`` asyncio.Event registry.
    """
    import asyncio as _asyncio
    queue: "_asyncio.Queue[tuple[str, Any]]" = _asyncio.Queue()

    def _on_turn(rec: "CUTurnRecord") -> None:
        # Keep it strictly non-blocking — queue is unbounded so put_nowait is safe.
        queue.put_nowait(("turn", rec))

    def _on_log(level: str, msg: str) -> None:
        if on_log is not None:
            try:
                on_log(level, msg)
            except Exception:
                pass

    async def _runner() -> None:
        try:
            final = await run_loop(
                goal=goal,
                executor=executor,
                turn_limit=turn_limit,
                on_safety=on_safety,
                on_turn=_on_turn,
                on_log=_on_log,
            )
            queue.put_nowait(("done", final or ""))
        except _asyncio.CancelledError:
            queue.put_nowait(("cancel", "Run cancelled"))
            raise
        except Exception as exc:
            queue.put_nowait(("error", f"{type(exc).__name__}: {exc}"))

    task = _asyncio.create_task(_runner())
    try:
        while True:
            kind, payload = await queue.get()
            if kind == "turn":
                rec: CUTurnRecord = payload
                yield ModelTurnStarted(
                    turn=rec.turn,
                    model_text=rec.model_text,
                    pending_tool_uses=len(rec.actions),
                )
                yield ToolBatchCompleted(
                    turn=rec.turn,
                    model_text=rec.model_text,
                    results=rec.actions,
                    screenshot_b64=rec.screenshot_b64,
                )
            elif kind == "done":
                yield RunCompleted(final_text=str(payload))
                return
            elif kind == "error":
                yield RunFailed(error=str(payload))
                return
            elif kind == "cancel":
                yield RunCompleted(final_text="Stopped by user.")
                return
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except BaseException:
                pass


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------

def denormalize_x(x: int, screen_width: int = DEFAULT_SCREEN_WIDTH) -> int:
    """Convert Gemini normalized x (0-999) to pixel coordinate."""
    return int(x / GEMINI_NORMALIZED_MAX * screen_width)


def denormalize_y(y: int, screen_height: int = DEFAULT_SCREEN_HEIGHT) -> int:
    """Convert Gemini normalized y (0-999) to pixel coordinate."""
    return int(y / GEMINI_NORMALIZED_MAX * screen_height)


def get_claude_scale_factor(
    width: int,
    height: int,
    model: str = "",
    *,
    tool_version: str | None = None,
) -> float:
    """Compute Anthropic screenshot scale factor per official docs.

    Returns a factor <=1.0 that the screenshot should be pre-resized by.
    Claude's API internally downsamples images exceeding the thresholds;
    by pre-resizing and reporting the scaled dimensions, we ensure
    coordinates returned by Claude map 1:1 to the reported display size.

    All ``computer_20251124`` models use the higher 2576px / 3.75MP
    limits by default. Legacy models stay on the 1568px / 1.15MP path.
    Opus 4.7's long-edge-only override is handled separately in
    ``ClaudeCUClient`` when ``CUA_OPUS47_HIRES=1`` is enabled.
    """
    if _uses_claude_20251124(model, tool_version):
        max_long_edge = _CLAUDE_OPUS_47_MAX_LONG_EDGE
        max_pixels = _CLAUDE_HIGH_RES_MAX_PIXELS
    else:
        max_long_edge = _CLAUDE_MAX_LONG_EDGE
        max_pixels = _CLAUDE_MAX_PIXELS
    long_edge = max(width, height)
    total_pixels = width * height
    return min(
        1.0,
        max_long_edge / long_edge,
        math.sqrt(max_pixels / total_pixels),
    )


def resize_screenshot_for_claude(
    png_bytes: bytes, scale: float,
) -> tuple[bytes, int, int]:
    """Resize a PNG screenshot by *scale* factor.

    Returns (resized_png_bytes, new_width, new_height).
    Uses Pillow if available; returns original bytes if scale >= 1.0.
    """
    if scale >= 1.0:
        # No resize needed — extract dimensions from PNG header
        # PNG IHDR: bytes 16-20 = width, 20-24 = height (big-endian)
        w = int.from_bytes(png_bytes[16:20], "big")
        h = int.from_bytes(png_bytes[20:24], "big")
        return png_bytes, w, h

    try:
        from PIL import Image
    except ImportError:
        logger.warning(
            "Pillow not installed — cannot resize screenshots for Claude. "
            "Coordinate drift may occur. Install: pip install Pillow"
        )
        w = int.from_bytes(png_bytes[16:20], "big")
        h = int.from_bytes(png_bytes[20:24], "big")
        return png_bytes, w, h

    img = Image.open(io.BytesIO(png_bytes))
    new_w = int(img.width * scale)
    new_h = int(img.height * scale)
    img_resized = img.resize((new_w, new_h), Image.LANCZOS)
    buf = io.BytesIO()
    img_resized.save(buf, format="PNG")
    return buf.getvalue(), new_w, new_h


# ---------------------------------------------------------------------------
# Executor Protocol
# ---------------------------------------------------------------------------

class ActionExecutor(Protocol):
    """Interface implemented by the supported computer-use executor."""

    screen_width: int
    screen_height: int

    async def execute(self, name: str, args: dict[str, Any]) -> CUActionResult: ...
    async def capture_screenshot(self) -> bytes: ...
    def get_current_url(self) -> str: ...


# ---------------------------------------------------------------------------
# DesktopExecutor — remote execution via agent_service HTTP API
# ---------------------------------------------------------------------------

# P11: process-wide shared httpx clients keyed by agent_service URL.
# All DesktopExecutor instances targeting the same container share a
# single connection pool so keep-alive actually kicks in (most sessions
# issue 10-100 short POSTs per turn). The lock serializes lazy creation
# so two coroutines starting concurrently don't race past the ``None``
# check and both build a client.
_SHARED_HTTPX_CLIENTS: dict[str, "httpx.AsyncClient"] = {}
_SHARED_HTTPX_LOCK = asyncio.Lock()


async def close_shared_executor_clients() -> None:
    """Close every shared httpx client. Wire into FastAPI shutdown."""
    async with _SHARED_HTTPX_LOCK:
        for url, client in list(_SHARED_HTTPX_CLIENTS.items()):
            try:
                if not client.is_closed:
                    await client.aclose()
            except Exception:  # noqa: BLE001
                logger.debug("Failed closing shared httpx client for %s", url)
            _SHARED_HTTPX_CLIENTS.pop(url, None)


class DesktopExecutor:
    """Translates CU actions into ``POST /action`` calls to the agent_service.

    All commands are executed inside the Docker container by sending
    HTTP requests to the agent_service (port 9222 by default), so the
    backend can run on **any host OS** — including Windows — while
    ``xdotool`` and ``scrot`` run in the Linux container.

    Screenshots are retrieved via ``GET /screenshot?mode=desktop`` on the
    same agent_service.  If the service is unreachable, a ``docker exec``
    fallback is used for screenshots only.
    """

    def __init__(
        self,
        screen_width: int = DEFAULT_SCREEN_WIDTH,
        screen_height: int = DEFAULT_SCREEN_HEIGHT,
        normalize_coords: bool = True,
        agent_service_url: str = "http://127.0.0.1:9222",
        container_name: str = "cua-environment",
    ):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self._normalize = normalize_coords
        self._service_url = agent_service_url
        self._container = container_name
        self._current_action_id: str | None = None
        self._current_action_substep: int = 0
        # P11: httpx client is shared across DesktopExecutor instances
        # in the same process via the ``_SHARED_HTTPX_CLIENTS`` dict
        # keyed by service URL. Previously every executor opened its
        # own TCP connection pool, which defeats keep-alive and also
        # leaked file descriptors when a session ended abnormally
        # without calling ``aclose``.

    def _px(self, x: int, y: int) -> tuple[int, int]:
        """Convert raw coordinates to pixel values, denormalizing if needed."""
        if self._normalize:
            return denormalize_x(x, self.screen_width), denormalize_y(y, self.screen_height)
        return x, y

    async def _get_client(self) -> httpx.AsyncClient:
        """Return the per-service-URL shared httpx client (P11)."""
        async with _SHARED_HTTPX_LOCK:
            client = _SHARED_HTTPX_CLIENTS.get(self._service_url)
            if client is None or client.is_closed:
                client = httpx.AsyncClient(timeout=15.0)
                _SHARED_HTTPX_CLIENTS[self._service_url] = client
            return client

    @staticmethod
    def _auth_headers() -> dict[str, str]:
        """Return the shared-secret header for authenticated agent_service calls."""
        token = os.environ.get("AGENT_SERVICE_TOKEN", "").strip()
        return {"X-Agent-Token": token} if token else {}

    async def _post_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST an action to the agent_service and return the JSON result."""
        client = await self._get_client()
        final_payload = dict(payload)
        if self._current_action_id:
            final_payload["action_id"] = f"{self._current_action_id}:{self._current_action_substep}"
            self._current_action_substep += 1
        resp = await client.post(
            f"{self._service_url}/action",
            json=final_payload,
            headers=self._auth_headers(),
        )
        resp.raise_for_status()
        return resp.json()

    # ── ActionExecutor interface ──────────────────────────────────────

    async def aclose(self) -> None:
        """Intentional no-op (P11).

        The underlying ``httpx.AsyncClient`` is shared process-wide via
        ``_SHARED_HTTPX_CLIENTS`` keyed by service URL, so closing it
        per-instance would defeat connection pooling and break
        sibling executors that target the same agent_service.
        Sockets are released by the module-level
        :func:`close_shared_executor_clients`, wired into the FastAPI
        shutdown hook.
        """
        return None

    async def execute(self, name: str, args: dict[str, Any]) -> CUActionResult:
        """Map a CU action to the agent_service ``/action`` endpoint."""
        handler = getattr(self, f"_act_{name}", None)
        if handler is None:
            return CUActionResult(
                name=name, success=False,
                error=f"Unimplemented desktop action: {name}",
            )
        try:
            handler_args = dict(args or {})
            previous_action_id = self._current_action_id
            previous_substep = self._current_action_substep
            self._current_action_id = str(handler_args.pop("action_id", "") or "") or None
            self._current_action_substep = 0
            try:
                extra = await handler(handler_args) or {}
            finally:
                self._current_action_id = previous_action_id
                self._current_action_substep = previous_substep
            # Detect agent_service returning {"success": false}
            if isinstance(extra, dict) and extra.get("success") is False:
                return CUActionResult(
                    name=name, success=False,
                    error=extra.get("message", "Action failed"),
                    extra=extra,
                )
            await asyncio.sleep(_app_config.ui_settle_delay)  # UI settle delay
            return CUActionResult(name=name, success=True, extra=extra)
        except Exception as exc:
            # S7: exc_info=True leaks the full traceback into the log
            # stream (and thus any log aggregator that ingests it).
            # Stack frames can expose local variables, file paths, and
            # provider SDK internals. Log the exception class + message
            # instead; re-enable with CUA_DEBUG_TB=1 when triaging.
            if _app_config.debug or os.getenv("CUA_DEBUG_TB") == "1":
                logger.error("DesktopExecutor %s failed: %s", name, exc, exc_info=True)
            else:
                logger.error("DesktopExecutor %s failed: %s: %s",
                             name, type(exc).__name__, exc)
            return CUActionResult(name=name, success=False, error=str(exc))

    # ── Desktop-level actions (via agent_service) ─────────────────────

    async def _act_click_at(self, a: dict) -> dict:
        """Click at coordinates via agent_service xdotool."""
        px, py = self._px(a["x"], a["y"])
        result = await self._post_action({
            "action": "click", "coordinates": [px, py], "mode": "desktop",
        })
        return {"pixel_x": px, "pixel_y": py, **result}

    async def _act_double_click(self, a: dict) -> dict:
        """Double-click at coordinates via agent_service xdotool."""
        px, py = self._px(a["x"], a["y"])
        result = await self._post_action({
            "action": "double_click", "coordinates": [px, py], "mode": "desktop",
        })
        return {"pixel_x": px, "pixel_y": py, **result}

    async def _act_right_click(self, a: dict) -> dict:
        """Right-click at coordinates via agent_service xdotool."""
        px, py = self._px(a["x"], a["y"])
        result = await self._post_action({
            "action": "right_click", "coordinates": [px, py], "mode": "desktop",
        })
        return {"pixel_x": px, "pixel_y": py, **result}

    async def _act_middle_click(self, a: dict) -> dict:
        """Middle-click at coordinates via agent_service xdotool."""
        px, py = self._px(a["x"], a["y"])
        result = await self._post_action({
            "action": "middle_click", "coordinates": [px, py], "mode": "desktop",
        })
        return {"pixel_x": px, "pixel_y": py, **result}

    async def _act_triple_click(self, a: dict) -> dict:
        """Simulate triple-click (select paragraph/line) via 3 rapid clicks."""
        px, py = self._px(a["x"], a["y"])
        await self._post_action({
            "action": "double_click", "coordinates": [px, py], "mode": "desktop",
        })
        result = await self._post_action({
            "action": "click", "coordinates": [px, py], "mode": "desktop",
        })
        return {"pixel_x": px, "pixel_y": py, **result}

    async def _act_hover_at(self, a: dict) -> dict:
        """Move cursor to coordinates via agent_service xdotool."""
        px, py = self._px(a["x"], a["y"])
        result = await self._post_action({
            "action": "hover", "coordinates": [px, py], "mode": "desktop",
        })
        return {"pixel_x": px, "pixel_y": py, **result}

    async def _act_move(self, a: dict) -> dict:
        """Alias for pointer movement used by OpenAI computer actions."""
        return await self._act_hover_at(a)

    async def _act_type_text_at(self, a: dict) -> dict:
        """Click at coordinates via agent_service, clear field, type text, and optionally press Enter."""
        px, py = self._px(a["x"], a["y"])
        text = a["text"]
        press_enter = a.get("press_enter", True)
        clear_before = a.get("clear_before_typing", True)
        await self._post_action({
            "action": "click", "coordinates": [px, py], "mode": "desktop",
        })
        if clear_before:
            await self._post_action({
                "action": "hotkey", "text": "ctrl+a", "mode": "desktop",
            })
            await self._post_action({
                "action": "key", "text": "BackSpace", "mode": "desktop",
            })
        await self._post_action({
            "action": "type", "text": text, "mode": "desktop",
        })
        if press_enter:
            await self._post_action({
                "action": "key", "text": "Return", "mode": "desktop",
            })
        return {"pixel_x": px, "pixel_y": py, "text": text}

    async def _act_key_combination(self, a: dict) -> dict:
        """Press a key combination via agent_service xdotool, normalizing modifier names."""
        keys = a["keys"]
        xdo_keys = (keys.replace("Control", "ctrl").replace("Alt", "alt")
                        .replace("Shift", "shift").replace("Meta", "super"))
        parts = xdo_keys.split("+")
        normalized = []
        for part in parts:
            stripped = part.strip()
            if len(stripped) == 1 and stripped.isalpha():
                normalized.append(stripped.lower())
            elif stripped.lower() in _XDOTOOL_SPECIAL_KEYS:
                normalized.append(stripped)
            else:
                normalized.append(stripped)
        # C8: reject combinations that include a token outside the
        # allowlist so a prompt-injected screenshot can't emit e.g.
        # ``super+l`` (lock screen) or ``ctrl+alt+BackSpace`` (zap X).
        for part in normalized:
            if not _is_allowed_key_token(part):
                logger.warning("Rejected disallowed key token: %r (full combo=%r)", part, keys)
                return {
                    "success": False,
                    "message": f"Disallowed key token: {part!r}",
                }
        xdo_keys = "+".join(normalized)
        await self._post_action({
            "action": "key", "text": xdo_keys, "mode": "desktop",
        })
        return {"keys": keys}

    async def _act_scroll_document(self, a: dict) -> dict:
        """Scroll the page in the given direction via desktop input events."""
        direction = a["direction"]
        await self._post_action({
            "action": "scroll", "text": direction, "mode": "desktop",
        })
        return {"direction": direction}

    async def _act_left_mouse_down(self, a: dict) -> dict:
        """Hold the left mouse button down at the current cursor position."""
        result = await self._post_action({
            "action": "left_mouse_down", "mode": "desktop",
        })
        return result

    async def _act_left_mouse_up(self, a: dict) -> dict:
        """Release the left mouse button at the current cursor position."""
        result = await self._post_action({
            "action": "left_mouse_up", "mode": "desktop",
        })
        return result

    async def _act_hold_key(self, a: dict) -> dict:
        """Hold a key for a short duration via xdotool keydown/keyup.

        C8: ``hold_key`` accepts a single key token from the model and
        emits a paired keydown/keyup. Without an allowlist a prompt
        injection could hold disruptive keysyms (e.g. ``XF86PowerOff``,
        ``XF86Launch1``) or compound chords. Restrict to the same
        ``_is_allowed_key_token`` set used by ``_act_key_combination``
        and reject anything that contains a ``+`` (hold_key is
        single-key only — chords go through ``key_combination``).
        """
        key = str(a.get("key", "")).strip()
        if "+" in key or not _is_allowed_key_token(key):
            logger.warning("Rejected disallowed hold_key token: %r", key)
            return {
                "success": False,
                "message": f"Disallowed key token: {key!r}",
            }
        duration = min(max(float(a.get("duration", 1)), 0.0), 10.0)
        await self._post_action({
            "action": "keydown", "text": key, "mode": "desktop",
        })
        await asyncio.sleep(duration)
        result = await self._post_action({
            "action": "keyup", "text": key, "mode": "desktop",
        })
        return {"key": key, "duration": duration, **result}

    async def _act_scroll_at(self, a: dict) -> dict:
        """Scroll at specific coordinates via agent_service xdotool."""
        px, py = self._px(a["x"], a["y"])
        direction = a["direction"]
        await self._post_action({
            "action": "scroll", "coordinates": [px, py],
            "text": direction, "mode": "desktop",
        })
        return {"pixel_x": px, "pixel_y": py, "direction": direction}

    async def _act_drag_and_drop(self, a: dict) -> dict:
        """Drag from source to destination via agent_service xdotool."""
        sx, sy = self._px(a["x"], a["y"])
        dx, dy = self._px(a["destination_x"], a["destination_y"])
        await self._post_action({
            "action": "drag", "coordinates": [sx, sy, dx, dy], "mode": "desktop",
        })
        return {"from": (sx, sy), "to": (dx, dy)}

    async def _act_navigate(self, a: dict) -> dict:
        """Open a URL via agent_service desktop browser."""
        url = a["url"]
        await self._post_action({
            "action": "open_url", "text": url, "mode": "desktop",
        })
        return {"url": url}

    async def _act_open_web_browser(self, a: dict) -> dict:
        """Open Google homepage via agent_service desktop browser."""
        await self._post_action({
            "action": "open_url", "text": "https://www.google.com", "mode": "desktop",
        })
        return {}

    async def _act_wait_5_seconds(self, a: dict) -> dict:
        """Sleep for post_action_screenshot_delay seconds (model-requested pause)."""
        await asyncio.sleep(_app_config.post_action_screenshot_delay)
        return {}

    async def _act_zoom(self, a: dict) -> dict:
        """Crop the desktop screenshot to ``region=[x1, y1, x2, y2]``.

        Backs the Claude ``computer_20251124`` zoom action.  The adapter
        has already validated/clamped the region.  The agent_service
        endpoint translates [x1,y1,x2,y2] -> scrot's [x,y,w,h] shape and
        returns the cropped PNG as base64 under ``screenshot``.  We
        surface it back in ``extra['image_bytes']`` so the Claude
        tool-result builder can attach it verbatim to the next turn.
        """
        region = a.get("region") or []
        if len(region) != 4:
            return {"success": False, "message": "zoom requires region=[x1,y1,x2,y2]"}
        result = await self._post_action({
            "action": "zoom",
            "coordinates": [int(region[0]), int(region[1]), int(region[2]), int(region[3])],
            "mode": "desktop",
        })
        extra: dict[str, Any] = {"region": [int(region[0]), int(region[1]), int(region[2]), int(region[3])]}
        if isinstance(result, dict):
            extra.update({k: v for k, v in result.items() if k != "screenshot"})
            b64 = result.get("screenshot")
            if b64:
                try:
                    extra["image_bytes"] = base64.b64decode(b64)
                except Exception:
                    pass
        return extra

    async def _act_go_back(self, a: dict) -> dict:
        """Press Alt+Left to go back in desktop browser history."""
        await self._post_action({
            "action": "key", "text": "alt+Left", "mode": "desktop",
        })
        return {}

    async def _act_go_forward(self, a: dict) -> dict:
        """Press Alt+Right to go forward in desktop browser history."""
        await self._post_action({
            "action": "key", "text": "alt+Right", "mode": "desktop",
        })
        return {}

    async def _act_type_at_cursor(self, a: dict) -> dict:
        """Type text at the current cursor position without clicking."""
        text = a["text"]
        press_enter = a.get("press_enter", False)
        await self._post_action({
            "action": "type", "text": text, "mode": "desktop",
        })
        if press_enter:
            await self._post_action({
                "action": "key", "text": "Return", "mode": "desktop",
            })
        return {"text": text}

    async def _act_search(self, a: dict) -> dict:
        """Open Google homepage via agent_service desktop browser."""
        await self._post_action({
            "action": "open_url", "text": "https://www.google.com", "mode": "desktop",
        })
        return {}

    # ── Screenshot ────────────────────────────────────────────────────

    async def capture_screenshot(self) -> bytes:
        """Capture a screenshot via the agent_service, with docker exec fallback."""
        try:
            client = await self._get_client()
            resp = await client.get(
                f"{self._service_url}/screenshot",
                params={"mode": "desktop"},
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            b64 = data["screenshot"]
            return base64.b64decode(b64)
        except Exception as exc:
            logger.warning(
                "Agent service screenshot failed (%s), falling back to docker exec", exc,
            )
            return await self._fallback_screenshot()

    async def _fallback_screenshot(self) -> bytes:
        """Grab a screenshot via ``docker exec scrot`` as last resort."""
        path = "/tmp/cu_screenshot.png"
        # Run scrot inside the container
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec",
            "-e", "DISPLAY=:99",
            self._container, "scrot", "-z", "-o", path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        # Read the resulting PNG back
        proc_read = await asyncio.create_subprocess_exec(
            "docker", "exec", self._container, "cat", path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc_read.communicate()
        if proc_read.returncode != 0 or not stdout:
            raise RuntimeError(
                f"Fallback screenshot failed: {stderr.decode(errors='replace')}"
            )
        return stdout

    def get_current_url(self) -> str:
        """Desktop executor has no URL context — always empty."""
        return ""


# ---------------------------------------------------------------------------
# Gemini Computer Use Client
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Unified ComputerUseEngine facade
# ---------------------------------------------------------------------------

class ComputerUseEngine:
    """Single entry point for native Computer Use across providers and environments.

    The single engine for this application. Uses the native CU protocol
    from Gemini, Claude, or OpenAI with the desktop executor.

    Usage::

        engine = ComputerUseEngine(
            provider=Provider.GEMINI,
            api_key="AIza...",
            environment=Environment.DESKTOP,
        )
        final_text = await engine.execute_task(
            "Search for flights to Paris",
        )
    """

    def __init__(
        self,
        provider: Provider,
        api_key: str,
        model: str | None = None,
        environment: Environment = Environment.DESKTOP,
        screen_width: int = DEFAULT_SCREEN_WIDTH,
        screen_height: int = DEFAULT_SCREEN_HEIGHT,
        system_instruction: str | None = None,
        excluded_actions: list[str] | None = None,
        container_name: str = "cua-environment",
        agent_service_url: str = "http://127.0.0.1:9222",
        reasoning_effort: str | None = None,
        use_builtin_search: bool = False,
        search_max_uses: int | None = None,
        search_allowed_domains: list[str] | None = None,
        search_blocked_domains: list[str] | None = None,
        allowed_callers: list[str] | None = None,
        attached_files: list[str] | None = None,
    ):
        self.provider = provider
        self._last_completion_payload: dict[str, Any] | None = None
        self.environment = environment
        self.screen_width = screen_width
        self.screen_height = screen_height
        self._container_name = container_name
        self._agent_service_url = agent_service_url
        self._attached_file_ids = list(attached_files or [])
        if provider == Provider.GEMINI and self._attached_file_ids:
            raise ValueError(
                "Reference files are supported for OpenAI and Anthropic computer-use "
                "sessions only; Gemini File Search cannot be combined with Computer Use.",
            )

        # Bundle the optional search options once so each adapter
        # receives the same shape via a single kwarg.
        search_kwargs: dict[str, Any] = {
            "use_builtin_search": bool(use_builtin_search),
            "search_max_uses": search_max_uses,
            "search_allowed_domains": list(search_allowed_domains) if search_allowed_domains else None,
            "search_blocked_domains": list(search_blocked_domains) if search_blocked_domains else None,
        }
        # Reference-file activation rule: only attach provider-native
        # document grounding when the user explicitly uploaded files.
        # Gemini is excluded above because its File Search tool is not
        # compatible with Computer Use.
        file_kwargs: dict[str, Any] = {"attached_file_ids": self._attached_file_ids}

        if provider == Provider.GEMINI:
            self._client: Any = GeminiCUClient(
                api_key=api_key,
                model=model or "gemini-3-flash-preview",
                environment=environment,
                excluded_actions=excluded_actions,
                system_instruction=system_instruction,
                **search_kwargs,
                **file_kwargs,
            )
        elif provider == Provider.CLAUDE:
            # Look up tool_version / beta_flag from allowed_models.json
            # so the canonical allowlist drives the Claude CU routing.
            # ClaudeCUClient raises if the model lacks registry metadata
            # and no explicit override is supplied.
            _tv, _bf = _lookup_claude_cu_config(model or "claude-sonnet-4-6")
            self._client = ClaudeCUClient(
                api_key=api_key,
                model=model or "claude-sonnet-4-6",
                system_prompt=system_instruction,
                tool_version=_tv,
                beta_flag=_bf,
                allowed_callers=list(allowed_callers) if allowed_callers is not None else None,
                **search_kwargs,
                **file_kwargs,
            )
        elif provider == Provider.OPENAI:
            self._client = OpenAICUClient(
                api_key=api_key,
                model=model or "gpt-5.5",
                system_prompt=system_instruction,
                reasoning_effort=reasoning_effort,
                **search_kwargs,
                **file_kwargs,
            )
        else:
            raise ValueError(f"Unsupported provider: {provider}")

    def _build_executor(self, page: Any = None) -> ActionExecutor:
        """Build the action executor for this session.

        Unified Computer Use surface: a single X11 sandbox where
        Chromium is pre-installed. The provider's CU tool decides
        whether to drive desktop applications or Chromium itself, so
        we always return the xdotool-backed ``DesktopExecutor``.
        """
        # Gemini uses normalized 0-999 coords; Claude/OpenAI use real pixels
        normalize = self.provider == Provider.GEMINI
        return DesktopExecutor(
            screen_width=self.screen_width,
            screen_height=self.screen_height,
            normalize_coords=normalize,
            agent_service_url=self._agent_service_url,
            container_name=self._container_name,
        )

    async def execute_task(
        self,
        goal: str,
        page: Any = None,
        *,
        turn_limit: int = DEFAULT_TURN_LIMIT,
        on_safety: Callable[[str], bool] | None = None,
        on_turn: Callable[[CUTurnRecord], None] | None = None,
        on_log: Callable[[str, str], None] | None = None,
    ) -> str:
        """Execute a CU task end-to-end using the native tool protocol.

        Args:
            goal: Natural language task description.
            page: Unused legacy parameter retained for compatibility.
            turn_limit: Maximum agent loop iterations.
            on_safety: Callback for safety confirmations.
            on_turn: Progress callback per turn.
            on_log: Logging callback(level, message).

        Returns:
            Final text response from the model.
        """
        from backend.providers import ProviderTools, runner_for

        executor = self._build_executor(page)
        self._last_completion_payload = None
        try:
            final_text = ""
            provider_key = {
                Provider.GEMINI: "google",
                Provider.CLAUDE: "anthropic",
                Provider.OPENAI: "openai",
            }[self.provider]
            tools = ProviderTools(
                web_search=bool(getattr(self._client, "_use_builtin_search", False)),
                search_allowed_domains=getattr(self._client, "_search_allowed_domains", None),
                search_blocked_domains=getattr(self._client, "_search_blocked_domains", None),
                allowed_callers=getattr(self._client, "_allowed_callers", None),
            )
            async for event in runner_for(provider_key)(
                goal,
                tools=tools,
                files=self._attached_file_ids,
                on_event=None,
                on_safety=on_safety,
                executor=executor,
                turn_limit=turn_limit,
                client=self._client,
            ):
                if event.type == "turn" and on_turn:
                    on_turn(event.data)
                elif event.type == "log" and on_log:
                    data = event.data or {}
                    on_log(data.get("level", "info"), data.get("message", ""))
                elif event.type == "final":
                    data = event.data or {}
                    final_text = str(data.get("text") or "")
                    self._last_completion_payload = data.get("completion_payload") or {}
            return final_text
        finally:
            # Close httpx client to prevent resource leaks
            if hasattr(executor, 'aclose'):
                try:
                    await executor.aclose()
                except Exception:
                    logger.debug("Error closing executor", exc_info=True)

    @property
    def last_completion_payload(self) -> dict[str, Any] | None:
        """Provider-specific completion payload for the most recent run."""
        return self._last_completion_payload

    async def iter_turns(
        self,
        goal: str,
        *,
        turn_limit: int = DEFAULT_TURN_LIMIT,
        on_safety: Callable[[str], Any] | None = None,
        on_log: Callable[[str, str], None] | None = None,
    ):
        """Dispatch to the provider's ``iter_turns`` contract.

        Claude / Gemini: use their native async-generator contracts.
        OpenAI: adapted from the callback-driven ``run_loop`` via
        :func:`iter_turns_via_run_loop` (legacy safety flow preserved).
        """
        executor = self._build_executor()
        try:
            if self.provider in {Provider.CLAUDE, Provider.GEMINI}:
                async for ev in self._client.iter_turns(
                    goal=goal,
                    executor=executor,
                    turn_limit=turn_limit,
                    on_log=on_log,
                ):
                    yield ev
            else:
                async for ev in iter_turns_via_run_loop(
                    self._client.run_loop,
                    goal=goal,
                    executor=executor,
                    turn_limit=turn_limit,
                    on_safety=on_safety,
                    on_log=on_log,
                ):
                    yield ev
        finally:
            if hasattr(executor, "aclose"):
                try:
                    await executor.aclose()
                except Exception:
                    logger.debug("Error closing executor", exc_info=True)


# ---------------------------------------------------------------------------
# Per-provider client re-exports (Q2 — class bodies live in their own files)
# ---------------------------------------------------------------------------

from backend.engine.gemini import GeminiCUClient, _prune_gemini_context  # noqa: E402
from backend.engine.claude import (  # noqa: E402
    ClaudeCUClient,
    _prune_claude_context,
)
from backend.engine.openai import OpenAICUClient  # noqa: E402

__all__ = [
    "Provider",
    "Environment",
    "SafetyDecision",
    "CUActionResult",
    "CUTurnRecord",
    "ModelTurnStarted",
    "ToolBatchCompleted",
    "SafetyRequired",
    "RunCompleted",
    "RunFailed",
    "TurnEvent",
    "iter_turns_via_run_loop",
    "ActionExecutor",
    "DesktopExecutor",
    "GeminiCUClient",
    "ClaudeCUClient",
    "OpenAICUClient",
    "ComputerUseEngine",
    "denormalize_x",
    "denormalize_y",
    "get_claude_scale_factor",
    "resize_screenshot_for_claude",
    "_prune_claude_context",
    "_prune_gemini_context",
    "_extract_openai_output_text",
    "_build_openai_computer_call_output",
    "_sanitize_openai_response_item_for_replay",
    "_lookup_claude_cu_config",
    "_call_with_retry",
    "scrub_secrets",
    "close_shared_executor_clients",
]
