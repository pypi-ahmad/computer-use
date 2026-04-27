"""Unified Computer Use engine for Gemini, Claude, and OpenAI.

Provider adapters keep their native tool contracts while sharing the
desktop executor that talks to the sandbox action service.
"""

from __future__ import annotations

import asyncio
import inspect
import io
import logging
import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from backend.executor import (
    ActionExecutor,
    CUActionResult,
    DEFAULT_SCREEN_HEIGHT,
    DEFAULT_SCREEN_WIDTH,
    DesktopExecutor,
    GEMINI_NORMALIZED_MAX,
    SafetyDecision,
    _is_allowed_key_token,
    close_shared_executor_clients,
    denormalize_x,
    denormalize_y,
)
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


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

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
    ``backend.safety`` asyncio.Event registry.
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
            from backend.files import GEMINI_CU_FILE_REJECTION
            raise ValueError(GEMINI_CU_FILE_REJECTION)

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
        from backend.providers import run_client

        executor = self._build_executor(page)
        self._last_completion_payload = None
        try:
            final_text, payload = await run_client(
                self.provider.value,
                goal,
                client=self._client,
                files=self._attached_file_ids,
                executor=executor,
                turn_limit=turn_limit,
                on_safety=on_safety,
                on_turn=on_turn,
                on_log=on_log,
            )
            self._last_completion_payload = payload
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


