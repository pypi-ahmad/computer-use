"""Agent loop — the core orchestrator for the Computer Use engine.

Delegates to the native CU protocol for the perceive → act → screenshot
cycle. Manages session lifecycle and callbacks for the desktop-native
runtime path.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from typing import Callable, Optional

# AI6: reuse the shared secret-scrubber from backend.engine so the
# patterns and redaction format stay consistent between what flows
# through the provider clients and what loop.py persists into the
# LangGraph checkpoint + broadcasts over the WebSocket.
from backend.engine import scrub_secrets as _scrub_secrets

from backend.config import config
from backend.models import (
    ActionType,
    AgentAction,
    AgentSession,
    LogEntry,
    SessionStatus,
    StepRecord,
    StructuredError,
)
from backend.agent.screenshot import check_service_health
from backend.logging_ctx import session_id_var

logger = logging.getLogger(__name__)

# CU action name → ActionType best-effort mapping for the step timeline.
# Static mapping, defined once at module level.
_CU_ACTION_MAP: dict[str, ActionType] = {
    "click_at": ActionType.CLICK, "double_click": ActionType.DOUBLE_CLICK,
    "right_click": ActionType.RIGHT_CLICK, "triple_click": ActionType.CLICK,
    "middle_click": ActionType.CLICK,
    "hover_at": ActionType.HOVER, "type_text_at": ActionType.TYPE,
    "type_at_cursor": ActionType.TYPE, "key_combination": ActionType.KEY,
    "scroll_document": ActionType.SCROLL, "scroll_at": ActionType.SCROLL,
    "drag_and_drop": ActionType.DRAG, "navigate": ActionType.OPEN_URL,
    "open_web_browser": ActionType.OPEN_URL, "search": ActionType.OPEN_URL,
    "go_back": ActionType.GO_BACK, "go_forward": ActionType.GO_FORWARD,
    "wait_5_seconds": ActionType.WAIT,
    "click": ActionType.CLICK, "move": ActionType.HOVER,
    "type": ActionType.TYPE, "keypress": ActionType.KEY,
    "scroll": ActionType.SCROLL, "drag": ActionType.DRAG,
    "wait": ActionType.WAIT, "screenshot": ActionType.WAIT,
}


class AgentLoop:
    """Runs the perceive → think → act loop for a CUA session."""

    def __init__(
        self,
        task: str,
        api_key: str,
        model: str | None = None,
        max_steps: int | None = None,
        mode: str = "desktop",
        engine: str = "computer_use",
        provider: str = "google",
        execution_target: str = "docker",
        reasoning_effort: str | None = None,
        use_builtin_search: bool = False,
        search_max_uses: int | None = None,
        search_allowed_domains: list[str] | None = None,
        search_blocked_domains: list[str] | None = None,
        attached_files: list[str] | None = None,
        on_step: Optional[Callable] = None,
        on_log: Optional[Callable] = None,
        on_screenshot: Optional[Callable] = None,
    ):
        """Initialise a new agent loop for *task* using the given provider/model."""
        self.session = AgentSession(
            session_id=str(uuid.uuid4()),
            task=task,
            model=model or config.gemini_model,
            engine=engine,
            max_steps=max_steps or config.max_steps,
        )
        self._api_key = api_key
        self._engine = engine
        self._mode = mode
        self._provider = provider
        self._execution_target = execution_target
        self._reasoning_effort = reasoning_effort
        self._use_builtin_search = use_builtin_search
        self._search_max_uses = search_max_uses
        self._search_allowed_domains = search_allowed_domains
        self._search_blocked_domains = search_blocked_domains
        self._attached_files = attached_files or []
        self._action_history: list[AgentAction] = []
        self._stop_requested = False
        self._consecutive_errors = 0
        self.structured_errors: list[StructuredError] = []  # structured error log

        # Callbacks for real-time streaming
        self._on_step = on_step
        self._on_log = on_log
        self._on_screenshot = on_screenshot

    @property
    def session_id(self) -> str:
        """Return the unique session identifier."""
        return self.session.session_id

    def request_stop(self) -> None:
        """Request the loop to stop after the current step."""
        self._stop_requested = True
        # Cancel the in-flight provider run_loop so it doesn't keep
        # consuming LLM calls until its next turn boundary. The engine
        # run_task coroutine is cancellation-safe; it awaits the
        # executor's action and the next generate_content call, both
        # of which honour asyncio.CancelledError.
        run_task = getattr(self, "_run_task", None)
        if run_task is not None and not run_task.done():
            run_task.cancel()
        self._emit_log("info", "Stop requested by user")

    def _emit_log(self, level: str, message: str, data: dict | None = None) -> None:
        """Create a LogEntry and forward it to the log callback."""
        # AI6: run the message text through the same scrubber used for
        # persisted model output so a leaked secret (either echoed from
        # a screenshot or from the user's task) doesn't land verbatim
        # in log files, WS frames, or the sqlite checkpoint.
        clean_message = _scrub_secrets(message) or message
        entry = LogEntry(level=level, message=clean_message, data=data)
        logger.log(
            getattr(logging, level.upper(), logging.INFO),
            "[%s] %s",
            self.session.session_id[:8],
            clean_message,
        )
        if self._on_log:
            try:
                self._on_log(entry)
            except Exception:
                pass

    def _make_structured_error(
        self,
        *,
        step: int,
        action: str,
        errorCode: str,
        message: str,
    ) -> StructuredError:
        """Create a :class:`StructuredError`, append it to the error log, and return it."""
        err = StructuredError(
            step=step,
            action=action,
            errorCode=errorCode,
            message=message,
        )
        self.structured_errors.append(err)
        return err

    async def run(self) -> AgentSession:
        """Execute the full agent loop. Returns the final session state.

        Orchestration is a LangGraph six-node state machine
        (``preflight → model_turn ⇄ tool_batch → approval_interrupt →
        recover_or_retry → finalize``) assembled in
        :mod:`backend.agent.graph`. This AgentLoop owns only the
        provider/engine construction, callback wiring, and stop
        handshake — all turn-by-turn orchestration lives in the graph.
        """
        self.session.status = SessionStatus.RUNNING
        self._emit_log("info", f"Agent starting — task: {self.session.task}")
        self._emit_log(
            "info",
            f"Model: {self.session.model} | Max steps: {self.session.max_steps} | "
            f"Mode: {self._mode} | Engine: {self._engine} | "
            f"Provider: {self._provider} | Target: {self._execution_target}",
        )

        from backend.agent.graph import build_agent_graph
        from backend import tracing

        bundle = self._build_graph_bundle()
        # OBS: wrap the bundle so every graph-edge event lands in the
        # per-session trace recorder. The wrapper is additive — the
        # original callbacks still fire, so existing logging and WS
        # broadcasts are unchanged.
        tracing.start_session(self.session.session_id, task=self.session.task)
        bundle = tracing.install(bundle, self.session.session_id)
        graph = build_agent_graph(bundle)

        _sid_token = session_id_var.set(self.session.session_id)
        try:
            self._run_task = asyncio.create_task(
                graph.ainvoke(
                    {
                        "session_id": self.session.session_id,
                        "task": self.session.task,
                        "max_steps": self.session.max_steps,
                    },
                    config={"configurable": {"thread_id": self.session.session_id}},
                )
            )
            try:
                final_state = await self._run_task
            except asyncio.CancelledError:
                self._emit_log("info", "Agent run cancelled")
                final_state = None
            if final_state and final_state.get("status") == "error":
                self._emit_log(
                    "error",
                    f"Graph run ended in error: {final_state.get('error', '(unknown)')}",
                )
                self.session.status = SessionStatus.ERROR
            elif self._stop_requested:
                self.session.status = SessionStatus.STOPPED
            else:
                # Only flip to COMPLETED if _run_computer_use_engine
                # hasn't already set a terminal status (e.g. ERROR from
                # a provider-level failure surfaced via RunFailed).
                if self.session.status == SessionStatus.RUNNING:
                    self.session.status = SessionStatus.COMPLETED
        except Exception as exc:
            self._emit_log("error", f"Graph invocation failed: {exc}")
            self.session.status = SessionStatus.ERROR
        finally:
            self._run_task = None
            # Always drop safety-registry state for this session.
            try:
                from backend.agent import safety as safety_registry
                safety_registry.clear(self.session.session_id)
            except Exception:
                pass
            # Drop the iterator registry entry — finalize already does
            # this on the happy path, but a cancelled/error path might
            # not reach finalize.
            try:
                from backend.agent.graph import _drop_iterator
                _drop_iterator(self.session.session_id)
            except Exception:
                pass
            # OBS: flush the session trace to its sidecar JSON file.
            # Runs last so any cleanup above is recorded.
            try:
                tracing.finalize_session(
                    self.session.session_id,
                    status=self.session.status.value,
                )
            except Exception:
                logger.exception(
                    "trace finalize failed for %s", self.session.session_id,
                )
            session_id_var.reset(_sid_token)
        return self.session

    # ── NodeBundle construction (PR 7) ────────────────────────────────────

    def _build_graph_bundle(self) -> "NodeBundle":
        """Build the ``NodeBundle`` of I/O closures for the agent graph.

        The closures bridge graph nodes to this loop's callback set
        (``on_step`` / ``on_log`` / ``on_screenshot``) and to the legacy
        safety-registry signal still used by Gemini / OpenAI.
        """
        from backend.agent.graph import NodeBundle
        from backend.agent import safety as safety_registry
        from backend.engine import (
            ComputerUseEngine,
            Environment,
            Provider,
            ToolBatchCompleted,
        )
        from backend.agent.prompts import get_system_prompt

        provider_map = {
            "google": Provider.GEMINI,
            "anthropic": Provider.CLAUDE,
            "openai": Provider.OPENAI,
        }

        # Engine is lazily constructed on first start_iter invocation
        # so graph preflight can surface configuration errors cleanly.
        last_fingerprints: list[str] = []

        def _fingerprint(action: AgentAction | None) -> str:
            if action is None:
                return ""
            parts = [
                action.action.value
                if hasattr(action.action, "value") else str(action.action)
            ]
            if action.coordinates:
                parts.append(":".join(str(c) for c in action.coordinates))
            if action.text:
                parts.append(
                    hashlib.blake2b(
                        action.text.encode("utf-8", "replace"), digest_size=8,
                    ).hexdigest()
                )
            return "|".join(parts)

        async def _check_health() -> bool:
            try:
                return await check_service_health()
            except Exception:
                return False

        async def _start_iter(session_id: str, task: str, max_steps: int):
            cu_provider = provider_map.get(self._provider)
            if cu_provider is None:
                raise ValueError(f"Unsupported CU provider: {self._provider}")
            system_instruction = get_system_prompt(
                "computer_use", self._mode,
                provider=self._provider,
                model=self.session.model,
            )
            # Browser mode hints Gemini's CU tool with ENVIRONMENT_BROWSER
            # per https://ai.google.dev/gemini-api/docs/computer-use.
            # Anthropic / OpenAI carry the same desktop-style ``computer``
            # tool in either mode (their docs do not expose an env
            # parameter); the prompt template carries the browser focus.
            cu_env = (
                Environment.BROWSER
                if self._mode == "browser" and cu_provider == Provider.GEMINI
                else Environment.DESKTOP
            )
            engine = ComputerUseEngine(
                provider=cu_provider,
                api_key=self._api_key,
                model=self.session.model,
                environment=cu_env,
                screen_width=config.screen_width,
                screen_height=config.screen_height,
                system_instruction=system_instruction,
                container_name=config.container_name,
                agent_service_url=config.agent_service_url,
                reasoning_effort=self._reasoning_effort,
                use_builtin_search=self._use_builtin_search,
                search_max_uses=self._search_max_uses,
                search_allowed_domains=self._search_allowed_domains,
                search_blocked_domains=self._search_blocked_domains,
                attached_files=self._attached_files,
            )

            # Legacy safety callback used by Gemini / OpenAI's run_loop
            # (via iter_turns_via_run_loop). Claude's iter_turns ignores
            # this argument — its safety is server-side refusal only.
            async def _on_safety(explanation: str) -> bool:
                self._emit_log(
                    "warning",
                    f"Safety confirmation required: {explanation}",
                    data={
                        "type": "safety_confirmation",
                        "explanation": explanation,
                        "session_id": self.session.session_id,
                    },
                )
                sid = self.session.session_id
                evt = safety_registry.get_or_create_event(sid)
                evt.clear()
                try:
                    await asyncio.wait_for(evt.wait(), timeout=60.0)
                    decision = bool(safety_registry.decisions.pop(sid, False))
                except asyncio.TimeoutError:
                    self._emit_log(
                        "warning", "Safety confirmation timed out, denying action",
                    )
                    decision = False
                finally:
                    safety_registry.clear(sid)
                self._emit_log("info", f"Safety confirmation result: {decision}")
                return decision

            def _on_engine_log(level: str, message: str) -> None:
                self._emit_log(level, message)

            return engine.iter_turns(
                goal=task,
                turn_limit=max_steps,
                on_safety=_on_safety,
                on_log=_on_engine_log,
            )

        def _emit_step(event: ToolBatchCompleted) -> None:
            """Map a ``ToolBatchCompleted`` event to a session ``StepRecord``."""
            agent_action: AgentAction | None = None
            if event.results:
                first = event.results[0]
                action_type = _CU_ACTION_MAP.get(first.name)
                if action_type:
                    agent_action = AgentAction(
                        action=action_type,
                        reasoning=_scrub_secrets(
                            event.model_text[:500] if event.model_text else None
                        ),
                    )
                    px = first.extra.get("pixel_x")
                    py = first.extra.get("pixel_y")
                    if px is not None and py is not None:
                        agent_action.coordinates = [px, py]
                    if first.extra.get("text"):
                        agent_action.text = str(first.extra["text"])
                else:
                    self._emit_log(
                        "warning",
                        f"Unmapped CU action '{first.name}' — not in ActionType enum",
                    )
            step = StepRecord(
                step_number=event.turn,
                screenshot_b64=event.screenshot_b64,
                raw_model_response=_scrub_secrets(event.model_text),
                action=agent_action,
            )
            self.session.steps.append(step)
            self._fire_callback(self._on_step, step)
            if event.screenshot_b64 and self._on_screenshot:
                self._fire_callback(self._on_screenshot, event.screenshot_b64)

            # AI2: stuck-agent detection.
            fp = _fingerprint(agent_action)
            if fp:
                last_fingerprints.append(fp)
                del last_fingerprints[:-3]
                if len(last_fingerprints) == 3 and len(set(last_fingerprints)) == 1:
                    self._emit_log(
                        "warning",
                        "Stuck-agent detected (3 consecutive identical actions); "
                        "requesting stop.",
                    )
                    self._stop_requested = True
                    run_task = getattr(self, "_run_task", None)
                    if run_task is not None and not run_task.done():
                        run_task.cancel()

        def _emit_log(level: str, message: str, data: dict | None = None) -> None:
            self._emit_log(level, message, data)

        def _build_snapshot() -> dict:
            return self.session.model_dump(
                mode="json",
                exclude={"steps": {"__all__": {"screenshot_b64"}}},
            )

        def _stop_requested() -> bool:
            return self._stop_requested

        return NodeBundle(
            check_health=_check_health,
            start_iter=_start_iter,
            emit_step=_emit_step,
            emit_log=_emit_log,
            build_snapshot=_build_snapshot,
            stop_requested=_stop_requested,
        )

    # ── Computer Use engine delegation ────────────────────────────────────

    async def _run_computer_use_engine(self) -> AgentSession:
        """Delegate the entire task to the native CU protocol engine.

        The CU engine runs its own perceive→act→screenshot loop using the
        structured ``computer_use`` tool from Gemini or ``computer_20250124``
        from Claude — no text parsing needed.
        """
        from backend.engine import (
            ComputerUseEngine,
            CUTurnRecord,
            Environment,
            Provider,
        )
        from backend.agent.prompts import get_system_prompt

        self._emit_log("info", "Delegating to native Computer Use engine")

        # Map provider string → CU Provider enum
        provider_map = {
            "google": Provider.GEMINI,
            "anthropic": Provider.CLAUDE,
            "openai": Provider.OPENAI,
        }
        cu_provider = provider_map.get(self._provider)
        if cu_provider is None:
            self._emit_log("error", f"Unsupported CU provider: {self._provider}")
            self.session.status = SessionStatus.ERROR
            return self.session

        # See ``_start_iter`` — Gemini gets ENVIRONMENT_BROWSER hint when
        # the user picks browser mode; the other two providers ride on
        # the prompt template since their CU tools have no env param.
        cu_env = (
            Environment.BROWSER
            if self._mode == "browser" and cu_provider == Provider.GEMINI
            else Environment.DESKTOP
        )

        system_instruction = get_system_prompt(
            "computer_use", self._mode,
            provider=self._provider,
            model=self.session.model,
        )

        engine = ComputerUseEngine(
            provider=cu_provider,
            api_key=self._api_key,
            model=self.session.model,
            environment=cu_env,
            screen_width=config.screen_width,
            screen_height=config.screen_height,
            system_instruction=system_instruction,
            container_name=config.container_name,
            agent_service_url=config.agent_service_url,
            reasoning_effort=self._reasoning_effort,
            use_builtin_search=self._use_builtin_search,
            search_max_uses=self._search_max_uses,
            search_allowed_domains=self._search_allowed_domains,
            search_blocked_domains=self._search_blocked_domains,
            attached_files=self._attached_files,
        )

        # AI2: loop-detection state. We hash (action_name + coords/text)
        # for each turn; three consecutive identical fingerprints is
        # treated as a stuck-agent and flips ``self._stop_requested``
        # so the engine terminates cleanly on its next turn boundary.
        last_fingerprints: list[str] = []

        def _fingerprint(action: "AgentAction | None") -> str:
            if action is None:
                return ""
            parts = [action.action.value if hasattr(action.action, "value") else str(action.action)]
            if action.coordinates:
                parts.append(":".join(str(c) for c in action.coordinates))
            if action.text:
                # AI-4: hash the *full* text rather than slicing to 64 chars,
                # so a stuck loop typing a long string that diverges only after
                # char 64 is still detected as a duplicate fingerprint.
                parts.append(
                    hashlib.blake2b(action.text.encode("utf-8", "replace"), digest_size=8).hexdigest()
                )
            return "|".join(parts)

        def _on_turn(record: CUTurnRecord) -> None:
            """Map CU turn records to session step records + broadcast."""
            # Build an AgentAction from the first CU action in this turn
            agent_action = None
            if record.actions:
                first = record.actions[0]
                action_type = _CU_ACTION_MAP.get(first.name)
                if action_type:
                    agent_action = AgentAction(
                        action=action_type,
                        reasoning=_scrub_secrets(
                            record.model_text[:500] if record.model_text else None
                        ),
                    )
                    # Attach coordinates/text from extra data if available
                    px = first.extra.get("pixel_x")
                    py = first.extra.get("pixel_y")
                    if px is not None and py is not None:
                        agent_action.coordinates = [px, py]
                    if first.extra.get("text"):
                        agent_action.text = str(first.extra["text"])
                else:
                    # Unknown CU action — log it but still record the step
                    self._emit_log(
                        "warning",
                        f"Unmapped CU action '{first.name}' — not in ActionType enum",
                    )
            step = StepRecord(
                step_number=record.turn,
                screenshot_b64=record.screenshot_b64,
                raw_model_response=_scrub_secrets(record.model_text),
                action=agent_action,
            )
            self.session.steps.append(step)
            self._fire_callback(self._on_step, step)
            if record.screenshot_b64 and self._on_screenshot:
                self._fire_callback(self._on_screenshot, record.screenshot_b64)

            # ── AI2: stuck-agent detection ──────────────────────────
            fp = _fingerprint(agent_action)
            if fp:
                last_fingerprints.append(fp)
                del last_fingerprints[:-3]
                if len(last_fingerprints) == 3 and len(set(last_fingerprints)) == 1:
                    self._emit_log(
                        "warning",
                        "Stuck-agent detected (3 consecutive identical actions); "
                        "requesting stop.",
                    )
                    self._stop_requested = True
                    # Flipping the flag alone does not interrupt the
                    # engine — it would keep running until its turn
                    # limit. Cancel the in-flight task the same way
                    # ``request_stop`` does so the next provider call
                    # raises CancelledError and the loop terminates
                    # cleanly. Guard against the (test-only) case
                    # where ``_run_task`` has not been assigned yet.
                    run_task = getattr(self, "_run_task", None)
                    if run_task is not None and not run_task.done():
                        run_task.cancel()

        def _on_log(level: str, message: str) -> None:
            self._emit_log(level, message)

        async def _on_safety(explanation: str) -> bool:
            """Safety confirmation callback for CU require_confirmation.

            Broadcasts the safety prompt via WebSocket and awaits the
            user's response through :mod:`backend.agent.safety`. Denies
            the action (returns False) on timeout — this satisfies the
            TOS requirement to never silently proceed on
            ``require_confirmation``.
            """
            self._emit_log(
                "warning",
                f"Safety confirmation required: {explanation}",
                data={"type": "safety_confirmation", "explanation": explanation,
                      "session_id": self.session.session_id},
            )
            from backend.agent import safety as safety_registry
            sid = self.session.session_id
            evt = safety_registry.get_or_create_event(sid)
            evt.clear()
            try:
                await asyncio.wait_for(evt.wait(), timeout=60.0)
                decision = bool(safety_registry.decisions.pop(sid, False))
            except asyncio.TimeoutError:
                self._emit_log("warning", "Safety confirmation timed out, denying action")
                decision = False
            finally:
                safety_registry.clear(sid)
            self._emit_log("info", f"Safety confirmation result: {decision}")
            return decision

        try:
            # Run the engine in a tracked task so ``request_stop`` can
            # cancel it immediately (used by ``/api/agent/stop`` and by
            # AI2 stuck-agent detection). A cooperative cancel is much
            # cheaper than letting the engine finish its current turn.
            self._run_task = asyncio.create_task(
                engine.execute_task(
                    goal=self.session.task,
                    turn_limit=self.session.max_steps,
                    on_safety=_on_safety,
                    on_turn=_on_turn,
                    on_log=_on_log,
                )
            )
            try:
                final_text = await self._run_task
            except asyncio.CancelledError:
                final_text = (
                    "Agent stopped by user."
                    if self._stop_requested
                    else "Agent cancelled."
                )
                self._emit_log("info", final_text)
            self._emit_log("info", f"CU engine completed: {final_text[:300]}")
            self.session.status = (
                SessionStatus.STOPPED
                if self._stop_requested
                else SessionStatus.COMPLETED
            )
        except Exception as exc:
            self._emit_log("error", f"CU engine failed: {exc}")
            self.session.status = SessionStatus.ERROR
        finally:
            self._run_task = None

        return self.session

    def _fire_callback(self, cb: Optional[Callable], *args) -> None:
        """Invoke a callback, swallowing exceptions to keep the loop alive."""
        if cb:
            try:
                cb(*args)
            except Exception:
                logger.warning("Callback %r raised an exception", cb, exc_info=True)

