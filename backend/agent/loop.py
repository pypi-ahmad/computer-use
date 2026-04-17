"""Agent loop — the core orchestrator for the Computer Use engine.

Delegates to the native CU protocol for the perceive → act → screenshot
cycle. Manages session lifecycle and callbacks for the desktop-native
runtime path.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Callable, Optional

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
        self._emit_log("info", "Stop requested by user")

    def _emit_log(self, level: str, message: str, data: dict | None = None) -> None:
        """Create a LogEntry and forward it to the log callback."""
        entry = LogEntry(level=level, message=message, data=data)
        logger.log(
            getattr(logging, level.upper(), logging.INFO),
            "[%s] %s",
            self.session.session_id[:8],
            message,
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

        Orchestration (preflight → execute → finalize) is expressed as a
        LangGraph ``StateGraph`` with an in-memory checkpointer. The CU
        engine itself, provider clients, and desktop executor are
        untouched — they run inside the ``execute`` node exactly as
        before.
        """
        self.session.status = SessionStatus.RUNNING
        self._emit_log("info", f"Agent starting — task: {self.session.task}")
        self._emit_log("info", f"Model: {self.session.model} | Max steps: {self.session.max_steps} | Mode: {self._mode} | Engine: {self._engine} | Provider: {self._provider} | Target: {self._execution_target}")

        from backend.agent.graph import build_agent_graph

        async def _preflight_node(state: dict) -> dict:
            healthy = await check_service_health()
            if not healthy:
                self._emit_log(
                    "warning",
                    "Agent service not responding, will retry during execution",
                )
            return {"healthy": healthy, "status": "running"}

        async def _execute_node(state: dict) -> dict:
            # Delegates to the native CU protocol engine. ``_run_computer_use_engine``
            # sets ``self.session.status`` to COMPLETED or ERROR on its own.
            await self._run_computer_use_engine()
            return {"status": self.session.status.value}

        async def _finalize_node(state: dict) -> dict:
            # Persist a screenshot-stripped snapshot of the session into
            # graph state so the sqlite checkpointer becomes the source
            # of truth for post-run status/history lookups.
            snapshot = self.session.model_dump(
                mode="json",
                exclude={"steps": {"__all__": {"screenshot_b64"}}},
            )
            return {"session_snapshot": snapshot}

        graph = build_agent_graph(_preflight_node, _execute_node, _finalize_node)
        await graph.ainvoke(
            {
                "session_id": self.session.session_id,
                "task": self.session.task,
                "max_steps": self.session.max_steps,
            },
            config={"configurable": {"thread_id": self.session.session_id}},
        )
        return self.session

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

        cu_env = Environment.DESKTOP

        system_instruction = get_system_prompt("computer_use", self._mode, provider=self._provider)

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
        )

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
                        reasoning=record.model_text[:500] if record.model_text else None,
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
                raw_model_response=record.model_text,
                action=agent_action,
            )
            self.session.steps.append(step)
            self._fire_callback(self._on_step, step)
            if record.screenshot_b64 and self._on_screenshot:
                self._fire_callback(self._on_screenshot, record.screenshot_b64)

        def _on_log(level: str, message: str) -> None:
            self._emit_log(level, message)

        def _on_safety(explanation: str) -> bool:
            """Safety confirmation callback for CU require_confirmation.

            Broadcasts the safety prompt via WebSocket and waits for user
            response.  Falls back to DENY (False) if no response within
            60 seconds — this satisfies the TOS requirement to never
            silently proceed on require_confirmation.
            """
            self._emit_log(
                "warning",
                f"Safety confirmation required: {explanation}",
                data={"type": "safety_confirmation", "explanation": explanation,
                      "session_id": self.session.session_id},
            )
            # The /api/agent/safety-confirm endpoint signals the shared
            # event; both sides read/write the same registry.
            from backend.agent import safety as safety_registry
            sid = self.session.session_id
            evt = safety_registry.get_or_create_event(sid)
            evt.clear()

            # We are running inside an async context, so schedule a waiter
            # task and block on a threading event for the synchronous
            # callback to return.
            import threading
            done_flag = threading.Event()
            result_holder: list[bool] = [False]

            async def _wait_for_decision():
                try:
                    await asyncio.wait_for(evt.wait(), timeout=60.0)
                    result_holder[0] = safety_registry.decisions.pop(sid, False)
                except asyncio.TimeoutError:
                    result_holder[0] = False
                finally:
                    safety_registry.events.pop(sid, None)
                    done_flag.set()

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_wait_for_decision())
                done_flag.wait(timeout=65.0)
            except Exception:
                self._emit_log("warning", "Safety confirmation timed out, denying action")
                safety_registry.clear(sid)
                return False

            decision = result_holder[0]
            self._emit_log("info", f"Safety confirmation result: {decision}")
            return decision

        try:
            final_text = await engine.execute_task(
                goal=self.session.task,
                turn_limit=self.session.max_steps,
                on_safety=_on_safety,
                on_turn=_on_turn,
                on_log=_on_log,
            )
            self._emit_log("info", f"CU engine completed: {final_text[:300]}")
            self.session.status = SessionStatus.COMPLETED
        except Exception as exc:
            self._emit_log("error", f"CU engine failed: {exc}")
            self.session.status = SessionStatus.ERROR

        return self.session

    def _fire_callback(self, cb: Optional[Callable], *args) -> None:
        """Invoke a callback, swallowing exceptions to keep the loop alive."""
        if cb:
            try:
                cb(*args)
            except Exception:
                logger.debug("Callback %r raised an exception", cb, exc_info=True)

