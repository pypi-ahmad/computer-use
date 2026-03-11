"""Agent loop — the core orchestrator for the Computer Use engine.

Delegates to the native CU protocol (Gemini / Claude) for the
perceive → act → screenshot cycle.  Manages session lifecycle,
callbacks, and Playwright browser acquisition for browser mode.
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
    "hover_at": ActionType.HOVER, "type_text_at": ActionType.TYPE,
    "type_at_cursor": ActionType.TYPE, "key_combination": ActionType.KEY,
    "scroll_document": ActionType.SCROLL, "scroll_at": ActionType.SCROLL,
    "drag_and_drop": ActionType.DRAG, "navigate": ActionType.OPEN_URL,
    "open_web_browser": ActionType.OPEN_URL, "search": ActionType.OPEN_URL,
    "go_back": ActionType.GO_BACK, "go_forward": ActionType.GO_FORWARD,
    "wait_5_seconds": ActionType.WAIT,
}


class AgentLoop:
    """Runs the perceive → think → act loop for a CUA session."""

    def __init__(
        self,
        task: str,
        api_key: str,
        model: str | None = None,
        max_steps: int | None = None,
        mode: str = "browser",
        engine: str = "computer_use",
        provider: str = "google",
        execution_target: str = "docker",
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
        self._action_history: list[AgentAction] = []
        self._stop_requested = False
        self._consecutive_errors = 0
        self.structured_errors: list[StructuredError] = []  # structured error log

        # Callbacks for real-time streaming
        self._on_step = on_step
        self._on_log = on_log
        self._on_screenshot = on_screenshot

        # Playwright lifecycle refs (cleaned up on session end)
        self._pw = None
        self._browser = None
        self._context = None

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
        """Execute the full agent loop. Returns the final session state."""
        self.session.status = SessionStatus.RUNNING
        self._emit_log("info", f"Agent starting — task: {self.session.task}")
        self._emit_log("info", f"Model: {self.session.model} | Max steps: {self.session.max_steps} | Mode: {self._mode} | Engine: {self._engine} | Provider: {self._provider} | Target: {self._execution_target}")

        # Pre-flight: check agent service health
        healthy = await check_service_health()
        if not healthy:
            self._emit_log("warning", "Agent service not responding, will retry during execution")

        # Delegate to the native Computer Use protocol engine
        return await self._run_computer_use_engine()

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
        provider_map = {"google": Provider.GEMINI, "anthropic": Provider.CLAUDE}
        cu_provider = provider_map.get(self._provider)
        if cu_provider is None:
            self._emit_log("error", f"Unsupported CU provider: {self._provider}")
            self.session.status = SessionStatus.ERROR
            return self.session

        # Map mode string → CU Environment enum
        env_map = {"browser": Environment.BROWSER, "desktop": Environment.DESKTOP}
        cu_env = env_map.get(self._mode, Environment.BROWSER)

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
        )

        # For browser mode, acquire a Playwright page from the agent service
        page = None
        if cu_env == Environment.BROWSER:
            page = await self._acquire_playwright_page()
            if page is None:
                self._emit_log("error", "Failed to acquire Playwright page for CU engine")
                self.session.status = SessionStatus.ERROR
                return self.session

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
            # Use the asyncio.Event from server.py's safety infrastructure.
            # The /api/agent/safety-confirm endpoint signals the event.
            from backend.server import _safety_events, _safety_decisions
            sid = self.session.session_id
            evt = _safety_events.get(sid)
            if evt is None:
                evt = asyncio.Event()
                _safety_events[sid] = evt
            else:
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
                    result_holder[0] = _safety_decisions.pop(sid, False)
                except asyncio.TimeoutError:
                    result_holder[0] = False
                finally:
                    _safety_events.pop(sid, None)
                    done_flag.set()

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_wait_for_decision())
                done_flag.wait(timeout=65.0)
            except Exception:
                self._emit_log("warning", "Safety confirmation timed out, denying action")
                _safety_events.pop(sid, None)
                _safety_decisions.pop(sid, None)
                return False

            decision = result_holder[0]
            self._emit_log("info", f"Safety confirmation result: {decision}")
            return decision

        try:
            final_text = await engine.execute_task(
                goal=self.session.task,
                page=page,
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
        finally:
            await self._cleanup_playwright()

        return self.session

    async def _acquire_playwright_page(self):
        """Acquire a Playwright page for the CU browser engine.

        Connects to the agent_service's Chromium browser via CDP.  The
        container's Playwright instance is launched with
        ``--remote-debugging-port=9223`` so the host backend can attach
        to it.  If the agent_service exposes ``cdp_url`` in its health
        response we use that; otherwise we construct a default from the
        well-known debugging port.

        Returns the Page object or None on failure.
        """
        try:
            import httpx

            # 1. Ask agent_service for a CDP URL (may or may not be present)
            cdp_url: str | None = None
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(f"{config.agent_service_url}/health")
                    health = resp.json()
                    cdp_url = health.get("cdp_url")
            except Exception as exc:
                self._emit_log("warning", f"Agent service health check failed: {exc}")

            # 2. Fallback: well-known debugging endpoint on container
            if not cdp_url:
                cdp_url = f"http://127.0.0.1:9223"
                self._emit_log(
                    "info",
                    "No cdp_url in health response, trying default "
                    f"debugging endpoint: {cdp_url}",
                )

            # 3. Connect via CDP
            from playwright.async_api import async_playwright
            pw = await async_playwright().start()
            self._pw = pw
            try:
                browser = await pw.chromium.connect_over_cdp(cdp_url)
            except Exception as cdp_exc:
                self._emit_log("error", f"CDP connect failed ({cdp_exc}). Container may not be ready.")
                # Retry with backoff instead of falling back to host browser
                for attempt in range(3):
                    await asyncio.sleep(2 * (attempt + 1))
                    self._emit_log("info", f"CDP retry attempt {attempt + 1}/3")
                    try:
                        browser = await pw.chromium.connect_over_cdp(cdp_url)
                        break
                    except Exception:
                        continue
                else:
                    self._emit_log("error", "CDP connection failed after 3 retries")
                    await pw.stop()
                    self._pw = None
                    return None
            self._browser = browser

            contexts = browser.contexts
            if contexts:
                pages = contexts[0].pages
                if pages:
                    self._emit_log("info", "Acquired Playwright page via CDP")
                    return pages[0]
                page = await contexts[0].new_page()
            else:
                ctx = await browser.new_context()
                self._context = ctx
                page = await ctx.new_page()
            self._emit_log("info", "Created new Playwright page")
            return page

        except Exception as exc:
            self._emit_log("error", f"Failed to acquire Playwright page: {exc}")
            return None

    async def _cleanup_playwright(self) -> None:
        """Close Playwright context, browser, and process safely."""
        for label, obj in [
            ("context", self._context),
            ("browser", self._browser),
            ("playwright", self._pw),
        ]:
            if obj is None:
                continue
            try:
                if label == "playwright":
                    await obj.stop()
                else:
                    await obj.close()
            except Exception:
                logger.debug("Error closing %s", label, exc_info=True)
        self._context = None
        self._browser = None
        self._pw = None

    def _fire_callback(self, cb: Optional[Callable], *args) -> None:
        """Invoke a callback, swallowing exceptions to keep the loop alive."""
        if cb:
            try:
                cb(*args)
            except Exception:
                logger.debug("Callback %r raised an exception", cb, exc_info=True)

