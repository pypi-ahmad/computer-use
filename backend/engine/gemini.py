from __future__ import annotations
"""Gemini Computer Use client — split out of ``backend.engine`` (Q2).

The class body lives here; ``backend.engine`` re-exports it so imports
like ``from backend.engine import GeminiCUClient`` keep working.
"""


import asyncio
import base64
import logging
import os
import time
from typing import Any, AsyncIterator, Callable

from backend.engine import (
    CUActionResult,
    CUTurnRecord,
    SafetyDecision,
    ActionExecutor,
    Environment,
    ModelTurnStarted,
    RunCompleted,
    SafetyRequired,
    ToolBatchCompleted,
    TurnEvent,
    _call_with_retry,
    _invoke_safety,
    _append_source_footer,
    DEFAULT_TURN_LIMIT,
    _CONTEXT_PRUNE_KEEP_RECENT,
    _IMAGE_PNG,
)

logger = logging.getLogger(__name__)


def _extract_gemini_sources(response: Any) -> list[tuple[str, str]]:
    """Collect grounded web sources from Gemini response metadata."""
    sources: list[tuple[str, str]] = []
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return sources
    gm = getattr(candidates[0], "grounding_metadata", None)
    chunks = getattr(gm, "grounding_chunks", None) or []
    for chunk in chunks:
        web = getattr(chunk, "web", None)
        if web is None:
            continue
        url = getattr(web, "uri", None)
        if url:
            sources.append((getattr(web, "title", None) or url, url))
    return sources


# ---------------------------------------------------------------------------
# Gemini Computer Use Client
# ---------------------------------------------------------------------------


def _prune_gemini_context(
    contents: list, types: Any, keep_recent: int,
) -> None:
    """Replace inline screenshot data in old turns with a text placeholder.

    Preserves the first message (goal + initial screenshot) and the last
    *keep_recent* Content entries.  Everything in between has its
    ``FunctionResponseBlob`` data stripped and image ``Part`` objects
    replaced with a text marker.
    """
    if len(contents) <= keep_recent + 1:
        return
    prune_end = len(contents) - keep_recent
    for idx in range(1, prune_end):
        content = contents[idx]
        if not hasattr(content, "parts") or not content.parts:
            continue
        new_parts = []
        pruned = False
        for part in content.parts:
            # Strip inline_data from FunctionResponsePart
            fr = getattr(part, "function_response", None)
            if fr is not None and hasattr(fr, "parts") and fr.parts:
                fr.parts.clear()
                pruned = True
            # Strip standalone image parts (from_bytes)
            if getattr(part, "inline_data", None) is not None:
                new_parts.append(types.Part(text="[screenshot omitted]"))
                pruned = True
            else:
                new_parts.append(part)
        if pruned:
            content.parts[:] = new_parts


# ---------------------------------------------------------------------------
# S4 — Gemini browser routing + Playwright opt-in
# ---------------------------------------------------------------------------

# Google's Gemini 3 Flash Preview reference implementation
# (github.com/google-gemini/computer-use-preview) drives a Chromium instance
# under Playwright.  The repo's default xdotool/full-desktop harness is
# compatible (the model returns normalized 0-999 coordinates which
# ``DesktopExecutor._denormalize_coords`` maps to any viewport), but when
# the agent has a choice of browser for a Gemini session, Chromium is the
# reference match.  This helper mirrors that preference so the Gemini adapter
# (and its tests) can assert "Chromium first, Firefox-ESR fallback with a
# warning" without disturbing the OpenAI / Anthropic paths.

_GEMINI_CHROMIUM_CANDIDATES: tuple[str, ...] = (
    "chromium-browser",
    "chromium",
)
_GEMINI_FIREFOX_FALLBACKS: tuple[str, ...] = (
    "firefox-esr",
    "firefox",
)


def _gemini_resolve_browser_binary(
    which: Callable[[str], str | None] | None = None,
    log: Callable[[str, str], None] | None = None,
) -> str | None:
    """Return the first available browser binary path for a Gemini session.

    Preference: ``chromium-browser`` → ``chromium`` → ``firefox-esr`` →
    ``firefox``.  Emits a single WARNING via *log* (or the module logger)
    when no Chromium flavour is installed and a Firefox fallback is used,
    because Google's reference implementation is Chromium-only.

    *which* defaults to ``shutil.which`` and is overridable for tests.
    Returns ``None`` when no browser is found at all.
    """
    import shutil

    if which is None:
        which = shutil.which

    for name in _GEMINI_CHROMIUM_CANDIDATES:
        path = which(name)
        if path:
            return path

    for name in _GEMINI_FIREFOX_FALLBACKS:
        path = which(name)
        if path:
            msg = (
                "Gemini: Chromium not installed; falling back to %s. "
                "Google's reference implementation uses Chromium." % name
            )
            if log is not None:
                log("warning", msg)
            else:
                logger.warning(msg)
            return path

    return None


def _gemini_playwright_enabled() -> bool:
    """Return True when the Playwright path should be used for a Gemini
    browser-mode session.

    Per Google's official Gemini Computer Use docs the recommended
    client-side action handler is Playwright. The unified Docker
    sandbox pre-launches Chromium with CDP exposed on
    ``127.0.0.1:9223`` (see ``docker/entrypoint.sh``) so the backend
    can connect via ``playwright.connect_over_cdp(...)`` without
    abandoning the single-container architecture.

    Defaults: enabled. Set ``CUA_GEMINI_USE_PLAYWRIGHT=0`` to fall
    back to the xdotool ``DesktopExecutor``. The legacy
    ``CUA_GEMINI_USE_PLAYWRIGHT=1`` value is still accepted as an
    explicit opt-in. When ``playwright`` is not importable on the
    backend we log once and return False so the caller falls back to
    the xdotool path cleanly.
    """
    from backend.engine.playwright_executor import browser_playwright_enabled
    return browser_playwright_enabled()


class GeminiCUClient:
    """Native Gemini Computer Use tool protocol.

    API contract:
    - Declares ``types.Tool(computer_use=ComputerUse(...))``
    - Enables ``ThinkingConfig(thinking_level=\"high\")``
    - Sends screenshots inline in ``FunctionResponse`` parts
    - Handles ``safety_decision`` → ``require_confirmation``
    - Supports both ``ENVIRONMENT_BROWSER`` and ``ENVIRONMENT_DESKTOP``
    """

    # One-shot log guards so operators see the safety-threshold choice
    # exactly once per process, not once per turn.
    _relax_logged: bool = False
    _default_logged: bool = False

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3-flash-preview",
        environment: Environment = Environment.DESKTOP,
        excluded_actions: list[str] | None = None,
        system_instruction: str | None = None,
        use_builtin_search: bool = False,
        search_max_uses: int | None = None,
        search_allowed_domains: list[str] | None = None,
        search_blocked_domains: list[str] | None = None,
        # File Search activation per April 2026 docs:
        # https://ai.google.dev/gemini-api/docs/file-search
        # When ``attached_file_ids`` is non-empty, the adapter creates
        # a per-session ``file_search_store``, uploads each file via
        # ``upload_to_file_search_store`` (polling the long-running
        # operation until ``done``), and attaches
        # ``Tool(file_search=FileSearch(file_search_store_names=[...]))``.
        attached_file_ids: list[str] | None = None,
    ):
        try:
            from google import genai
            from google.genai import types as genai_types
        except ImportError as exc:
            raise ImportError(
                "google-genai is required. Install: pip install google-genai"
            ) from exc

        self._genai = genai
        self._types = genai_types
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._environment = environment
        self._excluded = excluded_actions or []
        self._system_instruction = system_instruction
        # AI-3: read CUA_GEMINI_THINKING_LEVEL once at init so subsequent
        # env mutations don't change behaviour mid-session and we don't pay
        # the os.getenv cost on every _build_config call.
        _allowed_levels = {"minimal", "low", "medium", "high"}
        _level = os.getenv("CUA_GEMINI_THINKING_LEVEL", "high").lower()
        self._thinking_level = _level if _level in _allowed_levels else "high"

        # Official Gemini google_search grounding tool (April 2026).
        # When ``use_builtin_search`` is True the adapter declares
        # ``Tool(google_search=GoogleSearch())`` alongside the
        # ``computer_use`` tool and sets
        # ``include_server_side_tool_invocations=True`` so the
        # combined-tool execution model documented at
        # https://ai.google.dev/gemini-api/docs/grounding works.
        # ``search_max_uses`` / domain filters are not supported by the
        # Gemini grounding tool today and are accepted for parity but
        # ignored.
        self._use_builtin_search = bool(use_builtin_search)
        del search_max_uses, search_allowed_domains, search_blocked_domains
        # File Search wiring; provisioned lazily in ``_ensure_file_search_store``.
        # Per https://ai.google.dev/gemini-api/docs/file-search :
        #   "File Search cannot be combined with other tools like
        #    Grounding with Google Search, URL Context, etc."
        # Computer Use is another tool, so we cannot attach
        # ``file_search`` to the same generate_content call as
        # ``computer_use``. Instead we run a one-shot RAG pre-step
        # before entering the CU loop: a single ``generate_content``
        # call with only ``file_search`` attached, asking the model
        # to extract anything from the uploaded documents that is
        # relevant to the user goal. The grounded answer (with
        # citations) is then inlined into the initial user turn of
        # the CU loop as plain-text context.
        self._attached_file_ids: list[str] = list(attached_file_ids or [])
        self._file_search_store_name: str | None = None
        self._file_search_grounded_context: str | None = None

    async def _generate(self, *, contents: list, config: Any) -> Any:
        """Invoke Gemini generate_content via the native async SDK path.

        ``google-genai >= 1.0`` always exposes ``Client.aio.models``;
        the package is pinned in ``requirements.txt`` so this is the
        only supported call shape. The previous ``asyncio.to_thread``
        fallback was kept for older SDKs that no longer match the pin
        and has been removed.
        """
        return await self._client.aio.models.generate_content(
            model=self._model,
            contents=contents,
            config=config,
        )

    def _get_env_enum(self) -> Any:
        """Return the SDK environment constant per official docs.

        Always reports ``ENVIRONMENT_DESKTOP`` since the unified sandbox
        is a full X11 desktop with Chromium pre-installed; the model
        decides whether to drive desktop apps or the browser. Falls
        back to ``ENVIRONMENT_BROWSER`` only if the SDK version lacks
        the desktop constant.
        """
        types = self._types
        desktop_env = getattr(types.Environment, "ENVIRONMENT_DESKTOP", None)
        if desktop_env is not None:
            return desktop_env
        logger.warning(
            "ENVIRONMENT_DESKTOP not available in google-genai SDK; "
            "falling back to ENVIRONMENT_BROWSER. Desktop xdotool "
            "actions will still execute via DesktopExecutor."
        )
        return types.Environment.ENVIRONMENT_BROWSER

    def _build_config(self) -> Any:
        """Build the GenerateContentConfig with CU tools, safety, and thinking settings."""
        types = self._types
        tools = [
            types.Tool(
                computer_use=types.ComputerUse(
                    environment=self._get_env_enum(),
                    excluded_predefined_functions=self._excluded,
                )
            )
        ]
        if self._use_builtin_search:
            # Official Gemini grounding tool. Constructor signature:
            # ``Tool(google_search=GoogleSearch())``. The model decides
            # per turn whether to invoke search; results are surfaced
            # via ``groundingMetadata`` on the candidate.
            #
            # Note: ``file_search`` is intentionally NOT attached to
            # this CU-time config. It is consumed via a RAG pre-step
            # (see ``_run_file_search_pre_step``) because the docs
            # forbid combining file_search with other tools.
            _GoogleSearch = getattr(types, "GoogleSearch", None)
            if _GoogleSearch is not None:
                tools.append(types.Tool(google_search=_GoogleSearch()))
            else:
                logger.warning(
                    "Gemini: google_search requested but GoogleSearch type "
                    "is not available in google-genai SDK; skipping.",
                )

        # Safety-threshold relaxation is opt-in.  Per Google's
        # safety-settings docs (2026-04), the default block threshold
        # on Gemini 2.5 / 3 models is already "Off" when the client
        # omits ``safety_settings``, so any additional relaxation
        # should be an explicit operator choice rather than an
        # implicit default.  Set ``CUA_GEMINI_RELAX_SAFETY=1`` to
        # attach BLOCK_ONLY_HIGH across the four HarmCategory buckets.
        # The ToS-mandated ``require_confirmation`` +
        # ``safety_acknowledgement`` handshake is unaffected either
        # way and remains the authoritative safety gate.
        safety_settings: list[Any] = []
        if os.environ.get("CUA_GEMINI_RELAX_SAFETY") == "1":
            if not GeminiCUClient._relax_logged:
                logger.info(
                    "Gemini CU safety relaxation enabled "
                    "(CUA_GEMINI_RELAX_SAFETY=1); attaching "
                    "BLOCK_ONLY_HIGH thresholds across HarmCategory "
                    "buckets.  ToS handshake unaffected.",
                )
                GeminiCUClient._relax_logged = True
            _HarmCategory = getattr(types, "HarmCategory", None)
            _SafetySetting = getattr(types, "SafetySetting", None)
            _HarmBlockThreshold = getattr(types, "HarmBlockThreshold", None)
            if _HarmCategory and _SafetySetting and _HarmBlockThreshold:
                block_level = getattr(_HarmBlockThreshold, "BLOCK_ONLY_HIGH", None)
                if block_level is not None:
                    for cat_name in (
                        "HARM_CATEGORY_HARASSMENT",
                        "HARM_CATEGORY_HATE_SPEECH",
                        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                        "HARM_CATEGORY_DANGEROUS_CONTENT",
                    ):
                        cat = getattr(_HarmCategory, cat_name, None)
                        if cat is not None:
                            safety_settings.append(
                                _SafetySetting(category=cat, threshold=block_level)
                            )
        elif not GeminiCUClient._default_logged:
            logger.warning(
                "Gemini CU using Google's default safety thresholds "
                "(Off for Gemini 2.5/3 per docs).  Set "
                "CUA_GEMINI_RELAX_SAFETY=1 to restore the previous "
                "BLOCK_ONLY_HIGH behaviour.",
            )
            GeminiCUClient._default_logged = True

        # Use thinking_level (recommended for Gemini 3) instead of
        # legacy include_thoughts / budget_tokens.
        _ThinkingConfig = types.ThinkingConfig
        _thinking_kwargs: dict[str, Any] = {}
        # AI-3: thinking level was resolved once in __init__.
        # Prefer thinking_level=<level> if the SDK supports it
        import inspect as _inspect
        _tc_params = _inspect.signature(_ThinkingConfig).parameters
        if "thinking_level" in _tc_params:
            _thinking_kwargs["thinking_level"] = self._thinking_level
        else:
            # Fallback for older SDK versions
            _thinking_kwargs["include_thoughts"] = True

        kwargs: dict[str, Any] = {
            "tools": tools,
            "thinking_config": _ThinkingConfig(**_thinking_kwargs),
        }
        if self._use_builtin_search:
            # Required to combine google_search with computer_use per
            # https://ai.google.dev/gemini-api/docs/computer-use#tool-combination
            # Guard against older SDK versions that lack the field.
            try:
                _gcc_params = _inspect.signature(
                    self._genai.types.GenerateContentConfig
                ).parameters
            except (TypeError, ValueError):
                _gcc_params = {}
            if "include_server_side_tool_invocations" in _gcc_params:
                kwargs["include_server_side_tool_invocations"] = True
            else:
                logger.warning(
                    "Gemini: include_server_side_tool_invocations not "
                    "supported by the installed google-genai SDK; "
                    "google_search may not combine cleanly with computer_use.",
                )
        if safety_settings:
            kwargs["safety_settings"] = safety_settings
        if self._system_instruction:
            kwargs["system_instruction"] = self._system_instruction
        return self._genai.types.GenerateContentConfig(**kwargs)

    async def _ensure_file_search_store(
        self,
        *,
        on_log: Callable[[str, str], None] | None = None,
    ) -> None:
        """Provision the per-session File Search store and import every file.

        Implements the recipe from
        https://ai.google.dev/gemini-api/docs/file-search:

            store = client.file_search_stores.create(...)
            op = client.file_search_stores.upload_to_file_search_store(
                file=..., file_search_store_name=store.name, ...
            )
            while not op.done: ...

        We poll until ``operation.done`` for each file before declaring
        the store ready, so the first ``generate_content`` call sees a
        queryable store.
        """
        if not self._attached_file_ids or self._file_search_store_name is not None:
            return
        from backend.infra.storage import store as _file_store
        recs = await _file_store.get_many(self._attached_file_ids)
        if not recs:
            if on_log:
                on_log("warning", "Gemini file_search: no readable files; skipping")
            return

        if on_log:
            on_log(
                "info",
                f"Gemini file_search: provisioning store for {len(recs)} file(s)",
            )

        def _create_store_blocking() -> Any:
            return self._client.file_search_stores.create(
                config={"display_name": f"cua-session-{int(time.time())}"},
            )

        store = await asyncio.to_thread(_create_store_blocking)
        store_name = getattr(store, "name", None) or store["name"]

        def _upload_blocking(rec_path: str, rec_filename: str) -> Any:
            op = self._client.file_search_stores.upload_to_file_search_store(
                file=rec_path,
                file_search_store_name=store_name,
                config={"display_name": rec_filename},
            )
            # Poll until the long-running operation finishes.  Per the
            # docs, embeddings are generated server-side here.
            deadline = time.time() + 600  # 10 minutes is plenty
            while not getattr(op, "done", False):
                if time.time() > deadline:
                    raise TimeoutError(
                        f"Gemini file_search: indexing {rec_filename} did not finish in 10 minutes",
                    )
                time.sleep(2)
                op = self._client.operations.get(op)
            return op

        for rec in recs:
            try:
                await asyncio.to_thread(_upload_blocking, str(rec.path), rec.filename)
                if on_log:
                    on_log(
                        "info",
                        f"Gemini file_search: indexed {rec.filename} "
                        f"({rec.size_bytes} bytes)",
                    )
            except Exception as exc:
                if on_log:
                    on_log(
                        "error",
                        f"Gemini file_search: upload failed for {rec.filename}: {exc}",
                    )
                raise

        self._file_search_store_name = store_name

    async def _run_file_search_pre_step(
        self,
        goal: str,
        *,
        on_log: Callable[[str, str], None] | None = None,
    ) -> None:
        """Run a one-shot RAG query over the per-session File Search store.

        Per https://ai.google.dev/gemini-api/docs/file-search the
        ``file_search`` tool **cannot be combined** with other tools
        such as Computer Use. To still ground a CU run on uploaded
        documents we run a single ``generate_content`` call before the
        CU loop with only ``Tool(file_search=FileSearch(...))``
        attached, asking the model to extract anything from the
        uploaded documents relevant to the user goal. The text +
        citations are stashed in ``self._file_search_grounded_context``
        and inlined into the initial user turn of the CU loop.
        """
        if not self._file_search_store_name:
            return
        types = self._types
        _FileSearch = getattr(types, "FileSearch", None)
        if _FileSearch is None:
            if on_log:
                on_log(
                    "warning",
                    "Gemini file_search: FileSearch type unavailable in SDK; "
                    "skipping RAG pre-step",
                )
            return

        prompt = (
            "You are preparing context for a Computer Use agent that will "
            "act on the user's behalf in a sandboxed browser/desktop. "
            "Read the attached documents and extract every fact, "
            "instruction, credential hint, URL, parameter, or constraint "
            "that is relevant to the following user goal. Be concise but "
            "thorough; preserve exact strings (URLs, names, codes) "
            "verbatim. If nothing in the documents is relevant, reply "
            "with the single line: NO_RELEVANT_CONTEXT.\n\n"
            f"User goal:\n{goal}"
        )
        config = types.GenerateContentConfig(
            tools=[types.Tool(
                file_search=_FileSearch(
                    file_search_store_names=[self._file_search_store_name],
                ),
            )],
        )

        if on_log:
            on_log("info", "Gemini file_search: running RAG pre-step")

        try:
            response = await self._generate(contents=prompt, config=config)
        except Exception as exc:
            if on_log:
                on_log(
                    "warning",
                    f"Gemini file_search: RAG pre-step failed: {exc}; "
                    "continuing CU loop without grounded context",
                )
            return

        text = (getattr(response, "text", None) or "").strip()
        if not text or text == "NO_RELEVANT_CONTEXT":
            if on_log:
                on_log(
                    "info",
                    "Gemini file_search: no relevant context found in uploads",
                )
            return

        # Collect citation snippets from grounding metadata, if any.
        citation_lines: list[str] = []
        try:
            candidates = getattr(response, "candidates", None) or []
            if candidates:
                gm = getattr(candidates[0], "grounding_metadata", None)
                chunks = getattr(gm, "grounding_chunks", None) or []
                for chunk in chunks:
                    rc = getattr(chunk, "retrieved_context", None)
                    if rc is None:
                        continue
                    title = getattr(rc, "title", None) or getattr(rc, "uri", None) or ""
                    snippet = (getattr(rc, "text", None) or "").strip()
                    if snippet:
                        # Trim very long chunks to keep the CU prompt small.
                        if len(snippet) > 800:
                            snippet = snippet[:800] + "…"
                        if title:
                            citation_lines.append(f"[{title}] {snippet}")
                        else:
                            citation_lines.append(snippet)
        except Exception:  # pragma: no cover — citations are best-effort
            pass

        sections = [text]
        if citation_lines:
            sections.append(
                "Source excerpts:\n" + "\n---\n".join(citation_lines)
            )
        self._file_search_grounded_context = "\n\n".join(sections)

        if on_log:
            on_log(
                "info",
                f"Gemini file_search: grounded context ready "
                f"({len(self._file_search_grounded_context)} chars, "
                f"{len(citation_lines)} citation(s))",
            )

    async def _cleanup_file_search_store(
        self,
        *,
        on_log: Callable[[str, str], None] | None = None,
    ) -> None:
        """Delete the per-session File Search store at run-loop exit."""
        if not self._file_search_store_name:
            return
        store_name = self._file_search_store_name
        self._file_search_store_name = None
        try:
            def _delete_blocking() -> None:
                self._client.file_search_stores.delete(
                    name=store_name, config={"force": True},
                )
            await asyncio.to_thread(_delete_blocking)
        except Exception as exc:
            if on_log:
                on_log("warning", f"Gemini file_search: cleanup failed: {exc}")

    def _compose_initial_goal_text(self, goal: str) -> str:
        """Prepend any RAG pre-step grounded context to the user goal."""
        ctx = self._file_search_grounded_context
        if not ctx:
            return goal
        return (
            "<attached_documents_context>\n"
            f"{ctx}\n"
            "</attached_documents_context>\n\n"
            "The above context was retrieved from documents the user "
            "uploaded for this session via the Gemini File Search tool. "
            "Treat it as authoritative for facts/URLs/parameters specific "
            "to the user's task.\n\n"
            f"User goal:\n{goal}"
        )

    async def iter_turns(
        self,
        goal: str,
        executor: ActionExecutor,
        *,
        turn_limit: int = DEFAULT_TURN_LIMIT,
        on_log: Callable[[str, str], None] | None = None,
    ) -> AsyncIterator[TurnEvent]:
        """Yield Gemini turn events for the LangGraph driver.

        Wraps :meth:`_iter_turns_core` with provisioning + cleanup of
        the per-session File Search store, while preserving the
        ``agen.asend(bool)`` resume protocol the safety flow relies on.
        Values sent into this generator are forwarded verbatim to the
        inner generator and its yielded events are forwarded back.
        """
        await self._ensure_file_search_store(on_log=on_log)
        await self._run_file_search_pre_step(goal, on_log=on_log)
        inner = self._iter_turns_core(
            goal, executor, turn_limit=turn_limit, on_log=on_log,
        )
        try:
            sent: Any = None
            while True:
                try:
                    ev = await inner.asend(sent)
                except StopAsyncIteration:
                    return
                sent = (yield ev)
        finally:
            try:
                await inner.aclose()
            finally:
                await self._cleanup_file_search_store(on_log=on_log)

    async def _iter_turns_core(
        self,
        goal: str,
        executor: ActionExecutor,
        *,
        turn_limit: int = DEFAULT_TURN_LIMIT,
        on_log: Callable[[str, str], None] | None = None,
    ) -> AsyncIterator[TurnEvent]:
        """Core iter_turns body — see :meth:`iter_turns` for the public contract.

        Safety confirmations are emitted as :class:`SafetyRequired`
        events and resumed via ``agen.asend(bool)`` so the shared graph
        interrupt path owns the approval lifecycle.
        """
        types = self._types
        config = self._build_config()

        # Initial screenshot
        screenshot_bytes = await executor.capture_screenshot()
        if not screenshot_bytes or len(screenshot_bytes) < 100:
            if on_log:
                on_log("error", "Initial screenshot capture failed or returned empty bytes")
            yield RunCompleted(final_text="Error: Could not capture initial screenshot")
            return

        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part(text=self._compose_initial_goal_text(goal)),
                    types.Part.from_bytes(data=screenshot_bytes, mime_type=_IMAGE_PNG),
                ],
            )
        ]

        _turn_start: float | None = None
        saw_computer_action = False
        nudged_for_computer_use = False

        for turn in range(turn_limit):
            if _turn_start is not None and on_log:
                on_log("info", f"turn_duration_ms={int((time.monotonic()-_turn_start)*1000)} provider=google model={self._model}")
            _turn_start = time.monotonic()
            if on_log:
                on_log("info", f"Gemini CU turn {turn + 1}/{turn_limit}")

            # Prune old screenshots to prevent unbounded context growth.
            # Keep the first message (goal + initial screenshot) and
            # the most recent _CONTEXT_PRUNE_KEEP_RECENT turns intact.
            _prune_gemini_context(contents, types, _CONTEXT_PRUNE_KEEP_RECENT)

            try:
                response = await _call_with_retry(
                    lambda: self._generate(contents=contents, config=config),
                    provider="google",
                    on_log=on_log,
                )
            except Exception as api_err:
                error_msg = str(api_err)
                if on_log:
                    on_log("error", f"Gemini API error at turn {turn + 1}: {error_msg}")
                # Try to provide actionable info for common error patterns
                if "INVALID_ARGUMENT" in error_msg:
                    if on_log:
                        on_log("error",
                            "INVALID_ARGUMENT usually means: (1) screenshot too large/corrupt, "
                            "(2) model doesn't support computer_use tool, or "
                            "(3) conversation context exceeded limits. "
                            f"Contents length: {len(contents)} turns, "
                            f"last screenshot: {len(screenshot_bytes)} bytes")
                yield RunCompleted(final_text=f"Gemini API error: {error_msg}")
                return

            if not response.candidates:
                if on_log:
                    on_log("warning", f"Gemini returned no candidates at turn {turn + 1} — retrying with nudge")

                # Retry once: append a user nudge reminding the model to
                # use computer_use tools and re-send with a fresh screenshot.
                try:
                    retry_ss = await executor.capture_screenshot()
                except Exception:
                    retry_ss = screenshot_bytes

                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part(
                                text=(
                                    "Please continue using the computer_use tools to "
                                    "complete the task. Here is the current screen."
                                )
                            ),
                            types.Part.from_bytes(
                                data=retry_ss, mime_type=_IMAGE_PNG
                            ),
                        ],
                    )
                )
                try:
                    response = await _call_with_retry(
                        lambda: self._generate(contents=contents, config=config),
                        provider="google",
                        on_log=on_log,
                        attempts=2,
                    )
                except Exception as retry_err:
                    if on_log:
                        on_log("error", f"Retry also failed: {retry_err}")
                    yield RunCompleted(
                        final_text=f"Error: Gemini returned no candidates and retry failed: {retry_err}",
                    )
                    return

                if not response.candidates:
                    if on_log:
                        on_log("error", f"Gemini returned no candidates even after retry at turn {turn + 1}")
                    yield RunCompleted(final_text="Error: Gemini returned no candidates (after retry)")
                    return

            candidate = response.candidates[0]
            contents.append(candidate.content)

            # Extract function calls and text
            function_calls = [
                p.function_call for p in candidate.content.parts if p.function_call
            ]
            text_parts = [p.text for p in candidate.content.parts if p.text]
            turn_text = " ".join(text_parts)

            # No function calls → model is done
            if not function_calls:
                if (self._use_builtin_search or self._attached_file_ids) and not saw_computer_action and not nudged_for_computer_use:
                    if on_log:
                        on_log(
                            "info",
                            "Gemini CU: retrieval-only turn before any computer action; nudging the model to continue with the computer_use tool.",
                        )
                    try:
                        retry_ss = await executor.capture_screenshot()
                    except Exception:
                        retry_ss = screenshot_bytes
                    contents.append(
                        types.Content(
                            role="user",
                            parts=[
                                types.Part(
                                    text=(
                                        "Use any retrieved search/file context to continue, but do not stop yet. "
                                        "This app's purpose is computer use: the task is not complete until you perform "
                                        "the requested action with the computer_use tool on the current screen. "
                                        "Continue with computer actions now."
                                    )
                                ),
                                types.Part.from_bytes(data=retry_ss, mime_type=_IMAGE_PNG),
                            ],
                        )
                    )
                    nudged_for_computer_use = True
                    continue
                final_text = _append_source_footer(
                    turn_text,
                    _extract_gemini_sources(response),
                )
                if on_log:
                    on_log("info", f"Gemini CU completed: {final_text[:200]}")
                yield RunCompleted(final_text=final_text)
                return

            saw_computer_action = True

            yield ModelTurnStarted(
                turn=turn + 1,
                model_text=turn_text,
                pending_tool_uses=len(function_calls),
            )

            # Execute each function call
            results: list[CUActionResult] = []
            terminated = False

            for fc in function_calls:
                args = dict(fc.args) if fc.args else {}

                # Extract safety_decision BEFORE passing args to executor.
                # This ensures the acknowledgement is tracked regardless of
                # which executor implementation is used.
                safety_confirmed = False
                if "safety_decision" in args:
                    sd = args.pop("safety_decision")
                    if isinstance(sd, dict) and sd.get("decision") == "require_confirmation":
                        confirmed = yield SafetyRequired(
                            explanation=str(sd.get("explanation", "")),
                        )
                        if not confirmed:
                            if on_log:
                                on_log("warning", f"Safety denied for {fc.name}")
                            terminated = True
                            break
                        safety_confirmed = True

                result = await executor.execute(fc.name, args)
                # Stamp safety metadata so FunctionResponse includes
                # safety_acknowledgement when the user confirmed.
                if safety_confirmed:
                    result.safety_decision = SafetyDecision.REQUIRE_CONFIRMATION
                results.append(result)

            # Emit turn record
            try:
                screenshot_bytes = await executor.capture_screenshot()
            except Exception as ss_err:
                if on_log:
                    on_log("warning", f"Screenshot capture failed at turn {turn + 1}: {ss_err}")
                screenshot_bytes = b""

            screenshot_b64 = base64.standard_b64encode(screenshot_bytes).decode() if screenshot_bytes else ""

            if terminated and not results:
                yield RunCompleted(final_text="Agent terminated: safety confirmation denied.")
                return

            yield ToolBatchCompleted(
                turn=turn + 1,
                model_text=turn_text,
                results=results,
                screenshot_b64=screenshot_b64 or None,
            )

            if terminated:
                yield RunCompleted(final_text="Agent terminated: safety confirmation denied.")
                return

            # Build FunctionResponses with inline screenshot per Gemini CU docs:
            # https://ai.google.dev/gemini-api/docs/computer-use
            # Each FunctionResponse embeds the screenshot via
            #   parts=[FunctionResponsePart(inline_data=FunctionResponseBlob(...))]
            # The screenshot must NOT be sent as a separate Part.from_bytes().
            current_url = executor.get_current_url()
            screenshot_ok = bool(screenshot_bytes) and len(screenshot_bytes) >= 100

            function_responses = []
            for r in results:
                resp_data: dict[str, Any] = {"url": current_url}
                if r.error:
                    resp_data["error"] = r.error
                if r.safety_decision == SafetyDecision.REQUIRE_CONFIRMATION:
                    resp_data["safety_acknowledgement"] = "true"
                # Merge extra data, converting non-serializable types (tuples → lists)
                for k, v in r.extra.items():
                    if isinstance(v, tuple):
                        resp_data[k] = list(v)
                    elif isinstance(v, (str, int, float, bool, type(None), list, dict)):
                        resp_data[k] = v
                    else:
                        resp_data[k] = str(v)

                fr_kwargs: dict[str, Any] = {"name": r.name, "response": resp_data}

                if screenshot_ok:
                    fr_kwargs["parts"] = [
                        types.FunctionResponsePart(
                            inline_data=types.FunctionResponseBlob(
                                mime_type=_IMAGE_PNG,
                                data=screenshot_bytes,
                            )
                        )
                    ]

                function_responses.append(types.FunctionResponse(**fr_kwargs))

            # IMPORTANT: send ONLY FunctionResponse parts — no separate image Part
            if not function_responses:
                if on_log:
                    on_log("warning", "No function responses to send; ending loop")
                yield RunCompleted(final_text=turn_text or "Gemini returned no function responses.")
                return

            contents.append(
                types.Content(
                    role="user",
                    parts=[types.Part(function_response=fr) for fr in function_responses],
                )
            )

        if _turn_start is not None and on_log:
            on_log("info", f"turn_duration_ms={int((time.monotonic()-_turn_start)*1000)} provider=google model={self._model}")
        yield RunCompleted(
            final_text=f"Gemini CU reached the turn limit ({turn_limit}) without a final response.",
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
        """Drive the native iterator while preserving the legacy callback API."""
        final_text = ""
        pending_turn_text = ""
        pending_event: TurnEvent | None = None

        agen = self.iter_turns(
            goal,
            executor,
            turn_limit=turn_limit,
            on_log=on_log,
        )

        while True:
            try:
                if pending_event is not None:
                    event = pending_event
                    pending_event = None
                else:
                    event = await agen.__anext__()
            except StopAsyncIteration:
                break

            if isinstance(event, ModelTurnStarted):
                pending_turn_text = event.model_text
                continue

            if isinstance(event, SafetyRequired):
                confirmed = await _invoke_safety(on_safety, event.explanation)
                try:
                    pending_event = await agen.asend(confirmed)
                except StopAsyncIteration:
                    if not final_text and not confirmed:
                        final_text = "Agent terminated: safety confirmation denied."
                    break
                continue

            if isinstance(event, ToolBatchCompleted):
                if on_turn:
                    on_turn(CUTurnRecord(
                        turn=event.turn,
                        model_text=event.model_text or pending_turn_text,
                        actions=event.results,
                        screenshot_b64=event.screenshot_b64,
                    ))
                pending_turn_text = ""
                continue

            if isinstance(event, RunCompleted):
                final_text = event.final_text

        return final_text


# ---------------------------------------------------------------------------
# Claude Computer Use Client
# ---------------------------------------------------------------------------

