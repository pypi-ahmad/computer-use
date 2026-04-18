"""Pydantic models for agent actions, messages, and API contracts."""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ── Action types ──────────────────────────────────────────────────────────────

class ActionType(str, enum.Enum):
    """Supported agent actions for the computer-use engine.

    Three categories:
    1. CU-native — the raw action names produced by Gemini / Claude CU protocol.
    2. Canonical — friendly names that loop.py maps CU actions to for the
       step timeline and frontend rendering.
    3. Terminal — control-flow markers (done / error).
    """

    # ── Computer-Use Native Actions (Gemini / Claude CU protocol) ─────────
    CLICK_AT = "click_at"
    HOVER_AT = "hover_at"
    TYPE_TEXT_AT = "type_text_at"
    SCROLL_AT = "scroll_at"
    DRAG_AND_DROP = "drag_and_drop"
    KEY_COMBINATION = "key_combination"
    NAVIGATE = "navigate"
    OPEN_WEB_BROWSER = "open_web_browser"
    SCROLL_DOCUMENT = "scroll_document"
    SEARCH = "search"
    WAIT_5_SECONDS = "wait_5_seconds"

    # ── Canonical Mapped Actions (timeline-friendly) ──────────────────────
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    HOVER = "hover"
    TYPE = "type"
    KEY = "key"
    SCROLL = "scroll"
    DRAG = "drag"
    OPEN_URL = "open_url"
    GO_BACK = "go_back"
    GO_FORWARD = "go_forward"
    WAIT = "wait"

    # ── Compatibility (used by agent_service.py action dispatch) ─────────
    FILL = "fill"
    EVALUATE_JS = "evaluate_js"
    GET_TEXT = "get_text"

    # ── Terminal / Control ────────────────────────────────────────────────
    DONE = "done"
    ERROR = "error"


class AgentAction(BaseModel):
    """Structured action returned by the LLM."""
    action: ActionType
    target: Optional[str] = None
    coordinates: Optional[list[int]] = Field(default=None, max_length=4)
    text: Optional[str] = None
    reasoning: Optional[str] = None


# ── Session management ────────────────────────────────────────────────────────

class SessionStatus(str, enum.Enum):
    """Lifecycle states for an agent session."""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    # Distinct from COMPLETED: the loop terminated cleanly because the
    # user pressed Stop or the stuck-agent detector fired. Lets the UI
    # and any downstream cost dashboards filter user/loop-terminated
    # runs from natural completions.
    STOPPED = "stopped"
    ERROR = "error"


class StepRecord(BaseModel):
    """One step in the agent loop."""
    step_number: int
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    screenshot_b64: Optional[str] = None  # base64 PNG
    action: Optional[AgentAction] = None
    raw_model_response: Optional[str] = None
    error: Optional[str] = None


class AgentSession(BaseModel):
    """Full state of an agent run."""
    session_id: str
    task: str = Field(min_length=1, max_length=10_000)
    status: SessionStatus = SessionStatus.IDLE
    model: str = Field(default="gemini-3-flash-preview", max_length=64)
    engine: str = Field(default="computer_use", max_length=20)
    steps: list[StepRecord] = Field(default_factory=list)
    max_steps: int = Field(default=50, ge=1, le=200)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ── API request / response ────────────────────────────────────────────────────

class StartTaskRequest(BaseModel):
    """Validated request body for POST /api/agent/start."""

    # C-13: reject typos / unexpected fields with HTTP 422 instead of
    # silently dropping them. Otherwise a misspelled ``reasoning_effor``
    # would default to "low" with no signal to the client.
    model_config = ConfigDict(extra="forbid")

    task: str = Field(min_length=1, max_length=10_000)
    api_key: Optional[str] = Field(default=None, max_length=256)
    model: str = Field(default="gemini-3-flash-preview", max_length=64)
    max_steps: int = Field(default=50, ge=1, le=200)
    mode: str = Field(max_length=20)
    engine: str = Field(default="computer_use", max_length=20)
    provider: str = Field(max_length=20)
    execution_target: str = Field(default="docker", max_length=20)  # only "docker" is supported
    reasoning_effort: Optional[str] = Field(default=None, max_length=10)  # OpenAI only: none|low|medium|high|xhigh


class TaskStatusResponse(BaseModel):
    """Response shape for GET /api/agent/status."""

    session_id: str
    status: SessionStatus
    current_step: int
    total_steps: int
    last_action: Optional[AgentAction] = None


class StructuredError(BaseModel):
    """Uniform error envelope returned by the agent loop and executor.

    Every error produced by the system carries the step number, the action
    that triggered it, a machine-readable ``errorCode``, and a
    human-readable ``message``.
    """

    step: int = 0
    action: str = "unknown"
    errorCode: str = "unknown_error"
    message: str = "An unknown error occurred"

    def to_dict(self) -> dict:
        """Serialize to a plain dict for JSON responses."""
        return self.model_dump()


class LogEntry(BaseModel):
    """Structured log entry emitted over WebSocket."""

    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    level: str = "info"
    message: str
    data: Optional[dict] = None
