"""Claude Computer Use client — split out of ``backend.engine`` (Q2).

The class body lives here; ``backend.engine`` re-exports it so imports
like ``from backend.engine import ClaudeCUClient`` keep working.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import time
from typing import Any, Callable

from backend.executor import ActionExecutor, CUActionResult
from backend.infra.config import config as _app_config
from backend.engine import (
    CUTurnRecord,
    ModelTurnStarted,
    ToolBatchCompleted,
    RunCompleted,
    TurnEvent,
    _call_with_retry,
    _is_opus_47,
    get_claude_scale_factor,
    resize_screenshot_for_claude,
    _append_source_footer,
    validate_builtin_search_config,
    DEFAULT_TURN_LIMIT,
    _CONTEXT_PRUNE_KEEP_RECENT,
    _CLAUDE_OPUS_47_MAX_LONG_EDGE,
    _IMAGE_PNG,
    _lookup_claude_cu_config,
)
from typing import AsyncIterator

logger = logging.getLogger(__name__)

_ANTHROPIC_WEB_SEARCH_CONSOLE_URL = "https://platform.claude.com/settings/privacy"
_ANTHROPIC_WEB_SEARCH_PROBE_TTL_SECONDS = 24 * 60 * 60
_ANTHROPIC_WEB_SEARCH_BASIC_TOOL = "web_search_20250305"
_ANTHROPIC_WEB_SEARCH_DIRECT_TOOL = "web_search_20260209"
_ANTHROPIC_ALLOWED_CALLERS_DIRECT = "direct"
_ANTHROPIC_WEB_SEARCH_PROBE_CACHE: dict[str, tuple[bool, float]] = {}
_ANTHROPIC_WEB_SEARCH_PROBE_LOCKS: dict[str, asyncio.Lock] = {}
_ANTHROPIC_WEB_SEARCH_PROBE_LOCKS_GUARD = asyncio.Lock()


def _anthropic_web_search_cache_key(api_key: str) -> str:
    """Hash an API key before using it as an in-process cache key."""
    return hashlib.sha256(api_key.encode("utf-8", "replace")).hexdigest()


def _anthropic_web_search_error_message() -> str:
    return (
        "Anthropic web search is not enabled for this organization's API access. "
        "An organization admin must enable web search in Claude Console before "
        f"using use_builtin_search with Anthropic models: {_ANTHROPIC_WEB_SEARCH_CONSOLE_URL}"
    )


def _is_anthropic_web_search_enablement_error(exc: Exception) -> bool:
    """Return True when *exc* looks like Claude Console web-search disablement."""
    msg = str(exc or "").lower()
    if "web search" not in msg:
        return False
    return any(
        token in msg for token in (
            "not enabled",
            "enable web search",
            "organization",
            "organisation",
            "admin",
            "claude console",
            "settings/privacy",
            "privacy settings",
        )
    )


async def _anthropic_web_search_probe_lock(key: str) -> asyncio.Lock:
    async with _ANTHROPIC_WEB_SEARCH_PROBE_LOCKS_GUARD:
        lock = _ANTHROPIC_WEB_SEARCH_PROBE_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _ANTHROPIC_WEB_SEARCH_PROBE_LOCKS[key] = lock
        return lock


def _extract_claude_sources(content_blocks: list[Any]) -> list[tuple[str, str]]:
    """Collect cited/source URLs from Anthropic assistant content blocks."""
    sources: list[tuple[str, str]] = []
    for block in content_blocks:
        if hasattr(block, "model_dump"):
            block_dict = block.model_dump()
        elif hasattr(block, "__dict__"):
            block_dict = {
                key: value for key, value in vars(block).items()
                if not key.startswith("_")
            }
        elif isinstance(block, dict):
            block_dict = dict(block)
        else:
            block_dict = {}

        for citation in block_dict.get("citations", []) or []:
            if not isinstance(citation, dict):
                continue
            url = citation.get("url")
            if url:
                sources.append((citation.get("title") or url, url))

        if block_dict.get("type") == "web_search_tool_result":
            for result in block_dict.get("content", []) or []:
                if not isinstance(result, dict):
                    continue
                url = result.get("url")
                if url:
                    sources.append((result.get("title") or url, url))
    return sources

# Per-turn Claude max_tokens budget. Opus 4.7 long-plan tasks frequently
# truncate at 16k; bumping to 32k removes the artificial ceiling while
# staying well inside the model's response limit. Override via
# ``CUA_CLAUDE_MAX_TOKENS`` if a deployment needs a tighter bound.
import os as _os
try:
    _CLAUDE_MAX_TOKENS = max(1024, min(int(_os.getenv("CUA_CLAUDE_MAX_TOKENS", "32768")), 65536))
except ValueError:
    _CLAUDE_MAX_TOKENS = 32768

# ---------------------------------------------------------------------------
# Claude Computer Use Client
# ---------------------------------------------------------------------------

class ClaudeCUClient:
    """Native Claude computer-use tool protocol.

    API contract (as of 2026-04):
        - Resolves tool version from registry metadata in
            ``backend/models/allowed_models.json`` unless explicit
            ``tool_version`` / ``beta_flag`` overrides are supplied.
        - Claude Opus 4.7 / Sonnet 4.6 use ``computer_20251124`` with beta
            ``computer-use-2025-11-24``. Coordinates are 1:1 with the
            reported display pixels (no scale-factor math at typical
            resolutions) and extended thinking only accepts
            ``{"type": "adaptive"}``. Sampling params
            (``temperature``/``top_p``/``top_k``) are rejected by the API and
            are not sent.
        - Unsupported / unregistered Anthropic CU models raise an explicit
            configuration error instead of guessing.
    - Uses ``client.beta.messages.create()`` (beta endpoint required).
    - Sends screenshots as base64 in ``tool_result`` content.
    - ``display_number`` is intentionally omitted.
    - Actions (``computer_20251124``): screenshot, click, double_click,
      type, key, scroll, mouse_move, left_click_drag, triple_click,
      right_click, middle_click, left_mouse_down, left_mouse_up,
      hold_key, wait, zoom.
    - TODO: task budgets (beta ``task-budgets-2026-03-13`` +
      ``output_config.effort`` / ``output_config.task_budget``) are
      not wired up yet. See Anthropic computer-use docs.
    """

    # One-shot flag so we only log "caching enabled" once per process.
    _caching_logged: bool = False

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        system_prompt: str | None = None,
        tool_version: str | None = None,
        beta_flag: str | None = None,
        use_builtin_search: bool = False,
        search_max_uses: int | None = None,
        search_allowed_domains: list[str] | None = None,
        search_blocked_domains: list[str] | None = None,
        allowed_callers: list[str] | None = None,
        attached_file_ids: list[str] | None = None,
    ):
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError(
                "anthropic is required. Install: pip install anthropic"
            ) from exc

        self._anthropic = anthropic
        # AsyncAnthropic avoids the per-call thread-pool hop we used to
        # do via ``asyncio.to_thread(sync_client.beta.messages.create, ...)``.
        # The agent loop spawns up to 3 concurrent sessions and each one
        # blocks a worker for tens of seconds on Opus — the threadpool
        # pressure was real.
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._api_key = api_key
        self._model = model
        self._system_prompt = system_prompt or ""

        if bool(tool_version) != bool(beta_flag):
            raise ValueError(
                "Anthropic computer-use overrides must provide both tool_version and beta_flag."
            )

        # Use explicit overrides when provided. Otherwise require a
        # registry entry so future / retired models cannot silently pick
        # a guessed tool version from their name.
        if tool_version and beta_flag:
            self._tool_version = tool_version
            self._beta_flag = beta_flag
        else:
            resolved_tool_version, resolved_beta_flag = _lookup_claude_cu_config(model)
            if not resolved_tool_version or not resolved_beta_flag:
                raise ValueError(
                    f"Anthropic computer-use model '{model}' is not in registry "
                    "(backend/models/allowed_models.json). Add cu_tool_version/cu_betas metadata "
                    "or pass explicit tool_version and beta_flag."
                )
            self._tool_version = resolved_tool_version
            self._beta_flag = resolved_beta_flag

        validate_builtin_search_config(
            provider="claude",
            model=model,
            use_builtin_search=use_builtin_search,
            search_max_uses=search_max_uses,
            search_allowed_domains=search_allowed_domains,
            search_blocked_domains=search_blocked_domains,
            allowed_callers=allowed_callers,
        )

        # Official Anthropic web_search server tool (April 2026:
        # tool type ``web_search_20250305`` by default. When
        # ``allowed_callers`` is supplied, switch to the documented ZDR
        # workaround shape for ``web_search_20260209`` with dynamic
        # filtering disabled.
        # When enabled the adapter advertises it alongside the
        # computer-use tool and the model invokes it server-side
        # (no client-side execution). Domain-filter validation happens
        # up front so unsupported combinations fail explicitly.
        self._use_builtin_search = bool(use_builtin_search)
        self._search_max_uses = int(search_max_uses) if search_max_uses else 5
        self._search_allowed_domains = list(search_allowed_domains) if search_allowed_domains else None
        self._search_blocked_domains = list(search_blocked_domains) if search_blocked_domains else None
        self._allowed_callers = list(allowed_callers) if allowed_callers is not None else None
        self._warn_on_unknown_allowed_callers()

        # Anthropic Files API integration. Per the official Computer
        # Use docs there is no sibling ``file_search`` tool on Claude
        # (unlike OpenAI / Gemini). Files uploaded via
        # ``client.beta.files.upload`` are referenced from user
        # messages with ``document`` content blocks of the shape
        # ``{"type": "document", "source": {"type": "file",
        # "file_id": "..."}}``. The Files API is itself a beta and
        # requires the ``files-api-2025-04-14`` beta header to be
        # combined with the computer-use beta.
        #
        # Per Anthropic's Files API guide
        # (https://platform.claude.com/docs/en/build-with-claude/files):
        #   * Document blocks support ``application/pdf`` and
        #     ``text/plain`` only.
        #   * ``.csv``, ``.md``, ``.docx``, ``.xlsx`` must be converted
        #     to plain text and inlined in the message.
        # The IDs received here are local ``backend.files`` IDs
        # (``f_...``); :func:`_prepare_attached_files` resolves them
        # against the store, uploads the document-eligible ones to
        # Anthropic on first use, and pre-extracts the inline-only
        # ones to text. The result is cached per client instance so
        # multi-turn runs do not re-upload.
        self._attached_file_ids = list(attached_file_ids or [])
        self._anthropic_file_cache: dict[str, str] = {}
        self._inline_text_cache: dict[str, tuple[str, str]] = {}

    async def _probe_anthropic_web_search_enablement(self) -> None:
        """Send a minimal Messages API request with web_search enabled.

        Anthropic's docs require org-level enablement in Claude Console.
        A successful response means the API key is allowed to attach the
        web-search tool; a provider-side disablement error is rewritten
        into an actionable local error.
        """
        await self._client.messages.create(
            model=self._model,
            max_tokens=16,
            messages=[{"role": "user", "content": "Reply with the single word ok."}],
            tools=[self._build_web_search_tool(max_uses=1)],
        )

    async def _ensure_anthropic_web_search_enabled(
        self,
        on_log: Callable[[str, str], None] | None = None,
    ) -> None:
        """Probe Claude web-search availability once per key per TTL window."""
        if not self._use_builtin_search:
            return

        cache_key = _anthropic_web_search_cache_key(self._api_key)
        now = time.monotonic()

        if (
            _app_config.anthropic_web_search_enabled
            or os.getenv("CUA_ANTHROPIC_WEB_SEARCH_ENABLED", "").strip() == "1"
        ):
            _ANTHROPIC_WEB_SEARCH_PROBE_CACHE[cache_key] = (
                True,
                now + _ANTHROPIC_WEB_SEARCH_PROBE_TTL_SECONDS,
            )
            if on_log:
                on_log(
                    "info",
                    "Anthropic web search probe skipped due to CUA_ANTHROPIC_WEB_SEARCH_ENABLED=1 override.",
                )
            return

        cached = _ANTHROPIC_WEB_SEARCH_PROBE_CACHE.get(cache_key)
        if cached and cached[1] > now:
            if cached[0]:
                return
            raise ValueError(_anthropic_web_search_error_message())

        lock = await _anthropic_web_search_probe_lock(cache_key)
        async with lock:
            now = time.monotonic()
            cached = _ANTHROPIC_WEB_SEARCH_PROBE_CACHE.get(cache_key)
            if cached and cached[1] > now:
                if cached[0]:
                    return
                raise ValueError(_anthropic_web_search_error_message())

            try:
                await self._probe_anthropic_web_search_enablement()
            except Exception as exc:
                if _is_anthropic_web_search_enablement_error(exc):
                    _ANTHROPIC_WEB_SEARCH_PROBE_CACHE[cache_key] = (
                        False,
                        now + _ANTHROPIC_WEB_SEARCH_PROBE_TTL_SECONDS,
                    )
                    raise ValueError(_anthropic_web_search_error_message()) from exc
                raise

            _ANTHROPIC_WEB_SEARCH_PROBE_CACHE[cache_key] = (
                True,
                now + _ANTHROPIC_WEB_SEARCH_PROBE_TTL_SECONDS,
            )

    async def _prepare_attached_files(
        self,
        on_log: Callable[[str, str], None] | None = None,
    ) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
        """Resolve local upload IDs into Anthropic-ready content.

        Returns a pair ``(document_blocks, inline_text_pairs)`` where:

        * ``document_blocks`` is a list of ``{"type": "document", ...}``
          content blocks ready to splice into the initial user message.
          One per ``.pdf`` / ``.txt`` upload.
        * ``inline_text_pairs`` is a list of ``(filename, text)`` pairs
          extracted from ``.md`` / ``.docx`` uploads (the Anthropic Files
          API does not accept those as document blocks per the official
          docs, so the adapter inlines them as plain text in the goal
          message).

        Lookups and provider uploads are resolved by ``backend.files``.
        Uploads to Anthropic are cached on the client instance so a
        multi-turn run does not re-upload on every call.
        """
        if not self._attached_file_ids:
            return [], []

        from backend.files import prepare_anthropic_documents
        return await prepare_anthropic_documents(
            self._client,
            self._attached_file_ids,
            file_cache=self._anthropic_file_cache,
            inline_text_cache=self._inline_text_cache,
            on_log=on_log,
        )

    def _warn_on_unknown_allowed_callers(self) -> None:
        """Warn when callers request an undocumented allowed_callers value."""
        if self._allowed_callers is None:
            return
        unknown = [value for value in self._allowed_callers if value != _ANTHROPIC_ALLOWED_CALLERS_DIRECT]
        if unknown:
            logger.warning(
                "Anthropic web search allowed_callers contains undocumented values %s; "
                "official docs currently show only %r.",
                unknown,
                _ANTHROPIC_ALLOWED_CALLERS_DIRECT,
            )

    def _build_web_search_tool(self, *, max_uses: int | None = None) -> dict[str, Any]:
        """Build the Anthropic web_search server-tool definition."""
        tool_type = (
            _ANTHROPIC_WEB_SEARCH_DIRECT_TOOL
            if self._allowed_callers is not None
            else _ANTHROPIC_WEB_SEARCH_BASIC_TOOL
        )
        ws_tool: dict[str, Any] = {
            "type": tool_type,
            "name": "web_search",
            "max_uses": self._search_max_uses if max_uses is None else max_uses,
        }
        if self._search_allowed_domains and self._search_blocked_domains:
            raise ValueError(
                "Anthropic web search accepts either search_allowed_domains "
                "or search_blocked_domains, not both.",
            )
        if self._search_allowed_domains:
            ws_tool["allowed_domains"] = self._search_allowed_domains
        if self._search_blocked_domains:
            ws_tool["blocked_domains"] = self._search_blocked_domains
        if self._allowed_callers is not None:
            ws_tool["allowed_callers"] = list(self._allowed_callers)
        return ws_tool

    def _build_tools(self, sw: int, sh: int) -> list[dict]:
        """Build the Claude computer-use tool definition with display dimensions."""
        tool: dict[str, Any] = {
            "type": self._tool_version,
            "name": "computer",
            "display_width_px": sw,
            "display_height_px": sh,
        }
        # Enable zoom action for computer_20251124 tool version
        if self._tool_version == "computer_20251124":
            tool["enable_zoom"] = True
        # Optional prompt caching on the tool definition.  Anthropic caches
        # the tool block across turns when cache_control is present, cutting
        # repeated tool-def tokens to ~10% of first-turn cost on multi-turn
        # sessions.  Opt-in via env var to stay zero-risk at deploy; emit a
        # one-shot INFO log the first time it takes effect per process so
        # operators can confirm.  System-prompt caching is a separate,
        # larger follow-up (different test requirements).
        if os.environ.get("CUA_CLAUDE_CACHING") == "1":
            tool["cache_control"] = {"type": "ephemeral"}
            if not ClaudeCUClient._caching_logged:
                logger.info(
                    "Claude CU prompt caching enabled (CUA_CLAUDE_CACHING=1); "
                    "tool definition marked ephemeral.",
                )
                ClaudeCUClient._caching_logged = True
        tools: list[dict[str, Any]] = [tool]
        if self._use_builtin_search:
            tools.append(self._build_web_search_tool())
        return tools

    async def iter_turns(
        self,
        goal: str,
        executor: ActionExecutor,
        *,
        turn_limit: int = DEFAULT_TURN_LIMIT,
        on_log: Callable[[str, str], None] | None = None,
    ) -> AsyncIterator[TurnEvent]:
        """Async-generator contract for consumers that need per-turn events.

        Yields per-turn events (``ModelTurnStarted`` → ``ToolBatchCompleted``)
        until the run terminates, at which point a final ``RunCompleted``
        is yielded and the generator returns.

        Claude's safety model is server-side refusal via
        ``stop_reason=="refusal"`` — there is no client-side
        ``require_confirmation`` handshake — so this generator never
        yields ``SafetyRequired``. For consistency with Gemini/OpenAI
        callers can still handle a ``SafetyRequired`` event uniformly
        if another provider yields one.
        """
        # Compute screenshot scaling to prevent coordinate drift.
        scale = get_claude_scale_factor(
            executor.screen_width,
            executor.screen_height,
            self._model,
            tool_version=self._tool_version,
        )
        # Opus 4.7 hi-res opt-in.  The default ``get_claude_scale_factor``
        # enforces BOTH the 2576px long-edge cap AND the 3.75 MP total-
        # pixel cap; a 2560x1600 hi-fidelity desktop (4.10 MP) therefore
        # gets silently downscaled even though Opus 4.7's native ceiling
        # on the long edge is 2576.  When ``CUA_OPUS47_HIRES=1`` AND the
        # model is Opus 4.7, drop the pixel-count cap and enforce only
        # the long-edge ceiling so hi-res sessions keep 1:1 coordinates.
        if (
            os.environ.get("CUA_OPUS47_HIRES") == "1"
            and _is_opus_47(self._model)
        ):
            long_edge = max(executor.screen_width, executor.screen_height)
            scale = min(1.0, _CLAUDE_OPUS_47_MAX_LONG_EDGE / long_edge)
            if on_log:
                on_log(
                    "info",
                    "CUA_OPUS47_HIRES=1: long-edge-only scaling for Opus 4.7",
                )
        scaled_w = int(executor.screen_width * scale)
        scaled_h = int(executor.screen_height * scale)
        if scale < 1.0 and on_log:
            on_log("info", f"Claude screenshot scale={scale:.3f} → {scaled_w}x{scaled_h}")

        await self._ensure_anthropic_web_search_enabled(on_log)

        tools = self._build_tools(scaled_w, scaled_h)

        screenshot_bytes = await executor.capture_screenshot()
        # Mirror the OpenAI + Gemini adapters' empty-screenshot guard
        # so a broken agent_service doesn't fail deep inside the
        # Anthropic SDK with a cryptic image-validation error.
        if not screenshot_bytes or len(screenshot_bytes) < 100:
            if on_log:
                on_log("error", "Initial screenshot capture failed or returned empty bytes")
            yield RunCompleted(final_text="Error: Could not capture initial screenshot")
            return
        screenshot_bytes, _, _ = resize_screenshot_for_claude(screenshot_bytes, scale)
        screenshot_b64 = base64.standard_b64encode(screenshot_bytes).decode()

        # Initial user content: goal text (with any inline-only
        # ``.md`` / ``.docx`` extracts prepended), document refs for
        # ``.pdf`` / ``.txt`` uploaded to the Anthropic Files API,
        # then the screenshot.
        document_blocks, inline_pairs = await self._prepare_attached_files(on_log)

        if inline_pairs:
            inline_sections = "\n\n".join(
                f"<attached_file name=\"{name}\">\n{text}\n</attached_file>"
                for name, text in inline_pairs
            )
            goal_text = (
                f"{inline_sections}\n\n"
                f"The above attached files are provided as plain-text "
                f"context. User goal:\n\n{goal}"
            )
        else:
            goal_text = goal

        initial_content: list[dict[str, Any]] = [{"type": "text", "text": goal_text}]
        initial_content.extend(document_blocks)
        initial_content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": _IMAGE_PNG,
                "data": screenshot_b64,
            },
        })

        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": initial_content,
            }
        ]

        _turn_start: float | None = None
        saw_computer_action = False
        nudged_for_computer_use = False

        for turn in range(turn_limit):
            if _turn_start is not None and on_log:
                on_log("info", f"turn_duration_ms={int((time.monotonic()-_turn_start)*1000)} provider=anthropic model={self._model}")
            _turn_start = time.monotonic()
            if on_log:
                on_log("info", f"Claude CU turn {turn + 1}/{turn_limit}")

            _prune_claude_context(messages, _CONTEXT_PRUNE_KEEP_RECENT)

            # All ``computer_20251124`` models reject
            # ``{"type":"enabled","budget_tokens":N}`` and require
            # adaptive thinking. Legacy ``computer_20250124`` models
            # keep the older fixed-budget path.
            if self._tool_version == "computer_20251124":
                thinking_cfg: dict[str, Any] = {"type": "adaptive"}
            else:
                thinking_cfg = {"type": "enabled", "budget_tokens": 4096}
            # Attach the Files API beta header alongside computer-use
            # only when documents are referenced. Keeps the wire
            # surface minimal for sessions that don't use files.
            _betas = [self._beta_flag]
            if self._attached_file_ids:
                _betas.append("files-api-2025-04-14")
            response = await _call_with_retry(
                lambda: self._client.beta.messages.create(
                    model=self._model,
                    max_tokens=_CLAUDE_MAX_TOKENS,
                    system=self._system_prompt,
                    tools=tools,
                    messages=messages,
                    betas=_betas,
                    thinking=thinking_cfg,
                ),
                provider="anthropic",
                on_log=on_log,
            )

            assistant_content = response.content
            messages.append({"role": "assistant", "content": assistant_content})

            # Only client-side ``tool_use`` blocks are actionable.
            # ``server_tool_use`` (web_search invocations) and the
            # corresponding ``web_search_tool_result`` blocks are
            # already executed by Anthropic; they live inside the
            # assistant content and we forward them verbatim by virtue
            # of having appended ``assistant_content`` above.
            tool_uses = [b for b in assistant_content if b.type == "tool_use"]
            text_blocks = [b.text for b in assistant_content
                          if hasattr(b, "text") and b.text]
            turn_text = " ".join(text_blocks)

            stop = response.stop_reason
            if stop == "pause_turn":
                # Long-running server tool (e.g. web_search) paused the
                # turn before yielding tool_use blocks. Per Anthropic
                # docs we resume by re-issuing the request with the
                # current message history unchanged.
                if on_log:
                    on_log("info", f"Claude pause_turn at turn {turn + 1}; resuming.")
                continue
            if stop == "refusal":
                refusal_reason = turn_text or "Model refused to continue (safety refusal)."
                if on_log:
                    on_log("warning", f"Claude refused: {refusal_reason[:200]}")
                # Emit an empty-actions tool-batch event so consumers
                # still get a step record for this turn.
                yield ToolBatchCompleted(
                    turn=turn + 1, model_text=refusal_reason,
                    results=[], screenshot_b64=None,
                )
                yield RunCompleted(final_text=refusal_reason)
                return
            if stop == "model_context_window_exceeded":
                final_text = "Error: context window exceeded. Task too long."
                if on_log:
                    on_log("error", "Claude context window exceeded")
                yield ToolBatchCompleted(
                    turn=turn + 1, model_text=final_text,
                    results=[], screenshot_b64=None,
                )
                yield RunCompleted(final_text=final_text)
                return
            if stop in ("max_tokens", "stop_sequence"):
                final_text = turn_text or f"Response truncated (stop_reason={stop})."
                if on_log:
                    on_log("warning", f"Claude stop_reason={stop}")
                yield ToolBatchCompleted(
                    turn=turn + 1, model_text=final_text,
                    results=[], screenshot_b64=None,
                )
                yield RunCompleted(final_text=final_text)
                return
            if stop == "end_turn" or not tool_uses:
                if (self._use_builtin_search or self._attached_file_ids) and not saw_computer_action and not nudged_for_computer_use:
                    if on_log:
                        on_log(
                            "info",
                            "Claude CU: retrieval-only turn before any computer action; nudging the model to continue with the computer tool.",
                        )
                    try:
                        refreshed_screenshot = await executor.capture_screenshot()
                    except Exception:
                        refreshed_screenshot = screenshot_bytes
                    refreshed_screenshot, _, _ = resize_screenshot_for_claude(
                        refreshed_screenshot, scale,
                    )
                    messages.append({
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Use any retrieved search/file context to continue, but do not stop yet. "
                                    "This app's purpose is computer use: the task is not complete until you perform "
                                    "the requested action with the computer tool on the current screen. "
                                    "Continue with computer actions now."
                                ),
                            },
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": _IMAGE_PNG,
                                    "data": base64.standard_b64encode(refreshed_screenshot).decode(),
                                },
                            },
                        ],
                    })
                    nudged_for_computer_use = True
                    continue
                final_text = _append_source_footer(
                    turn_text,
                    _extract_claude_sources(assistant_content),
                )
                if on_log:
                    on_log("info", f"Claude CU completed: {final_text[:200]}")
                yield ToolBatchCompleted(
                    turn=turn + 1, model_text=turn_text,
                    results=[], screenshot_b64=None,
                )
                yield RunCompleted(final_text=final_text)
                return

            saw_computer_action = True

            # Emit the "model call done, about to run tools" boundary event.
            yield ModelTurnStarted(
                turn=turn + 1,
                model_text=turn_text,
                pending_tool_uses=len(tool_uses),
            )

            tool_result_parts: list[dict[str, Any]] = []
            results: list[CUActionResult] = []

            for tu in tool_uses:
                result = await self._execute_claude_action(
                    tu.input, executor, scale_factor=scale,
                )
                results.append(result)

                screenshot_bytes = await executor.capture_screenshot()
                screenshot_bytes, _, _ = resize_screenshot_for_claude(
                    screenshot_bytes, scale,
                )
                screenshot_b64 = base64.standard_b64encode(screenshot_bytes).decode()

                content: list[dict] = []
                if result.error:
                    content.append({"type": "text", "text": f"Error: {result.error}"})
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": _IMAGE_PNG,
                        "data": screenshot_b64,
                    },
                })

                tool_result_parts.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": content,
                })

            yield ToolBatchCompleted(
                turn=turn + 1,
                model_text=turn_text,
                results=results,
                screenshot_b64=screenshot_b64,
            )

            messages.append({"role": "user", "content": tool_result_parts})

        if _turn_start is not None and on_log:
            on_log("info", f"turn_duration_ms={int((time.monotonic()-_turn_start)*1000)} provider=anthropic model={self._model}")
        yield RunCompleted(
            final_text=f"Claude CU reached the turn limit ({turn_limit}) without a final response."
        )

    async def run_loop(
        self,
        goal: str,
        executor: ActionExecutor,
        *,
        turn_limit: int = DEFAULT_TURN_LIMIT,
        on_safety: Callable[[str], bool] | None = None,
        on_turn: Callable[[CUTurnRecord], None] | None = None,
        on_log: Callable[[str, str], None] | None = None,
    ) -> str:
        """Legacy callback-driven driver — now a thin wrapper over ``iter_turns``.

        Preserves the original return contract (final text) and
        ``on_turn`` / ``on_log`` callback shape for existing callers
        (tests, benchmarks). ``on_safety`` is accepted for signature
        parity but is unused — Claude never emits a client-side safety
        prompt.
        """
        del on_safety  # explicit: Claude never emits SafetyRequired
        final_text = ""
        pending_turn_text = ""

        async for event in self.iter_turns(
            goal, executor, turn_limit=turn_limit, on_log=on_log,
        ):
            if isinstance(event, ModelTurnStarted):
                pending_turn_text = event.model_text
            elif isinstance(event, ToolBatchCompleted):
                if on_turn:
                    on_turn(CUTurnRecord(
                        turn=event.turn,
                        model_text=event.model_text or pending_turn_text,
                        actions=event.results,
                        screenshot_b64=event.screenshot_b64,
                    ))
                pending_turn_text = ""
            elif isinstance(event, RunCompleted):
                final_text = event.final_text
        return final_text

    async def _execute_claude_action(
        self, action_input: dict, executor: ActionExecutor,
        *, scale_factor: float = 1.0,
    ) -> CUActionResult:
        """Map Claude computer tool actions to executor calls.

        Claude actions (computer_20251124): screenshot, click, double_click,
        type, key, scroll, mouse_move, left_click_drag, triple_click,
        right_click, middle_click, left_mouse_down, left_mouse_up,
        hold_key, wait, zoom.

        Claude uses REAL pixel coordinates — no denormalization.
        When screenshot scaling is active, coordinates are upscaled by
        dividing by scale_factor before passing to the executor.
        """
        action = action_input.get("action", "")

        if action == "screenshot":
            return CUActionResult(name="screenshot")

        def _upscale_coord(coord: list[int] | None) -> list[int] | None:
            """Upscale Claude's coordinates back to real screen pixels."""
            if coord is None or scale_factor >= 1.0:
                return coord
            return [int(c / scale_factor) for c in coord]

        # Build args in the CU format the executor expects
        coord = _upscale_coord(action_input.get("coordinate"))
        args: dict[str, Any] = {}

        if action in ("click", "double_click", "right_click", "triple_click", "middle_click"):
            if coord:
                args["x"], args["y"] = coord[0], coord[1]
            if action in ("double_click", "right_click", "triple_click", "middle_click"):
                return await self._special_click(action, coord, executor)
            return await executor.execute("click_at", args)

        elif action == "type":
            text = action_input.get("text", "")
            try:
                result = await executor.execute("type_at_cursor", {
                    "text": text,
                    "press_enter": False,
                })
                return CUActionResult(
                    name="type", success=result.success,
                    error=result.error, extra={"text": text},
                )
            except Exception as exc:
                return CUActionResult(name="type", success=False, error=str(exc))

        elif action == "key":
            key = action_input.get("key", "")
            KEY_MAP = {"Return": "Enter", "space": "Space"}
            args["keys"] = KEY_MAP.get(key, key)
            return await executor.execute("key_combination", args)

        elif action == "scroll":
            if coord:
                args["x"], args["y"] = coord[0], coord[1]
            args["direction"] = action_input.get("direction", "down")
            amount = action_input.get("amount", 3)
            args["magnitude"] = min(999, amount * 200)
            return await executor.execute("scroll_at", args)

        elif action == "mouse_move":
            if coord:
                args["x"], args["y"] = coord[0], coord[1]
            return await executor.execute("hover_at", args)

        elif action == "left_click_drag":
            start = _upscale_coord(
                action_input.get("start_coordinate", coord or [0, 0])
            )
            end = _upscale_coord(action_input.get("coordinate", [0, 0]))
            args["x"], args["y"] = start[0], start[1]
            args["destination_x"], args["destination_y"] = end[0], end[1]
            return await executor.execute("drag_and_drop", args)

        elif action == "left_mouse_down":
            return await executor.execute("left_mouse_down", {})

        elif action == "left_mouse_up":
            return await executor.execute("left_mouse_up", {})

        elif action == "hold_key":
            key = action_input.get("key", "")
            duration = action_input.get("duration", 1)
            return await executor.execute("hold_key", {"key": key, "duration": duration})

        elif action == "wait":
            duration = action_input.get("duration", 5)
            await asyncio.sleep(min(duration, 30))
            return CUActionResult(name="wait", extra={"duration": duration})

        elif action == "zoom":
            # Opus 4.7 computer_20251124 zoom action — the model requests
            # a cropped region of the current screen.  We validate, clamp
            # to display bounds, reject inverted rectangles, and delegate
            # to the executor which returns the cropped PNG in
            # ``extra['image_bytes']``.  On executor failure we fall back
            # to a full-screen screenshot with a success=False note so
            # the model can still make forward progress.
            region = action_input.get("region")
            if (not isinstance(region, (list, tuple))
                    or len(region) != 4
                    or not all(isinstance(v, int) for v in region)):
                return CUActionResult(
                    name="zoom", success=False,
                    error="zoom requires region=[x1, y1, x2, y2] of ints",
                )
            x1, y1, x2, y2 = region
            if x1 >= x2 or y1 >= y2:
                return CUActionResult(
                    name="zoom", success=False,
                    error=f"zoom region is inverted or empty: {region!r}",
                )
            sw = getattr(executor, "screen_width", None) or 0
            sh = getattr(executor, "screen_height", None) or 0
            if sw and sh:
                x1 = max(0, min(x1, sw - 1))
                y1 = max(0, min(y1, sh - 1))
                x2 = max(x1 + 1, min(x2, sw))
                y2 = max(y1 + 1, min(y2, sh))
            try:
                return await executor.execute(
                    "zoom", {"region": [x1, y1, x2, y2]},
                )
            except Exception as exc:
                return CUActionResult(
                    name="zoom", success=False,
                    error=f"zoom failed: {exc}",
                )

        else:
            return CUActionResult(name=action, success=False,
                                  error=f"Unknown Claude action: {action}")

    async def _special_click(
        self, action: str, coord: list[int] | None, executor: ActionExecutor,
    ) -> CUActionResult:
        """Handle double_click, right_click, triple_click, and middle_click."""
        x, y = (coord[0], coord[1]) if coord else (0, 0)
        try:
            return await executor.execute(action, {"x": x, "y": y})
        except Exception as exc:
            return CUActionResult(name=action, success=False, error=str(exc))



# ---------------------------------------------------------------------------
# Claude context pruning
# ---------------------------------------------------------------------------


def _prune_claude_context(messages: list[dict], keep_recent: int) -> None:
    """Replace base64 screenshot data in old turns with a placeholder.

    Keeps the first user message (goal + initial screenshot) and the last
    *keep_recent* message pairs intact.  Older tool_result images are
    replaced with ``[screenshot omitted]``.
    """
    if len(messages) <= keep_recent + 1:
        return
    prune_end = len(messages) - keep_recent
    for idx in range(1, prune_end):
        msg = messages[idx]
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            # tool_result → strip images inside "content" list
            if part.get("type") == "tool_result" and isinstance(part.get("content"), list):
                new_inner: list[dict] = []
                for inner in part["content"]:
                    if isinstance(inner, dict) and inner.get("type") == "image":
                        new_inner.append({"type": "text", "text": "[screenshot omitted]"})
                    else:
                        new_inner.append(inner)
                part["content"] = new_inner
            # Standalone images in user messages
            elif part.get("type") == "image":
                part.clear()
                part["type"] = "text"
                part["text"] = "[screenshot omitted]"

