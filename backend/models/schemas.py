# === merged from backend/models.py ===
"""Pydantic models for agent actions, messages, and API contracts."""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any, Optional

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
    SCREENSHOT = "screenshot"
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
    final_text: Optional[str] = None
    gemini_grounding: Optional[dict[str, Any]] = None


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
    # Deprecated: Desktop and Browser are now a single unified surface.
    # The provider's Computer Use tool decides whether to drive a
    # desktop app or Chromium itself. Field is retained for wire
    # compatibility with older clients and is ignored.
    mode: str = Field(default="desktop", max_length=20)
    engine: str = Field(default="computer_use", max_length=20)
    provider: str = Field(max_length=20)
    execution_target: str = Field(default="docker", max_length=20)  # only "docker" is supported
    reasoning_effort: Optional[str] = Field(default=None, max_length=10)  # OpenAI only: minimal|low|medium|high|xhigh (accepts none as a legacy alias)
    # Official provider-native web-search tool toggle.
    # When True, the engine attaches the provider's first-party search
    # tool to every model call:
    #   * OpenAI Responses API → ``{"type": "web_search"}``
    #   * Anthropic Messages    → ``{"type": "web_search_20250305", ...}``
    #   * Gemini GenerateContent → ``Tool(google_search=GoogleSearch())``
    # The model still decides whether to invoke search per turn; this
    # flag only controls availability. Off by default; the frontend
    # toggle enables the provider-native search tool when requested.
    use_builtin_search: bool = False
    search_max_uses: Optional[int] = Field(default=None, ge=1, le=20)  # Anthropic max_uses cap
    search_allowed_domains: Optional[list[str]] = Field(default=None, max_length=64)
    search_blocked_domains: Optional[list[str]] = Field(default=None, max_length=64)
    allowed_callers: Optional[list[str]] = Field(default=None, max_length=16)
    # Server-side file ids previously persisted via POST /api/files/upload.
    # When non-empty the engine adapter creates a provider-side store
    # (OpenAI vector_store / Anthropic Files API uploads) and injects the
    # provider-appropriate grounding path per
    # the official April 2026 docs:
    #   * https://developers.openai.com/api/docs/guides/tools-file-search
    #   * https://platform.claude.com/docs/en/build-with-claude/files
    # Gemini File Search is intentionally excluded because Google's File
    # Search docs do not allow combining it with Computer Use.
    # When empty, no provider-side attachment flow is activated and the
    # agent runs in its normal flow (the activation rule is doc-mandated).
    attached_files: Optional[list[str]] = Field(default=None, max_length=10)


class TaskStatusResponse(BaseModel):
    """Response shape for GET /api/agent/status."""

    session_id: str
    status: SessionStatus
    current_step: int
    total_steps: int
    last_action: Optional[AgentAction] = None
    final_text: Optional[str] = None
    gemini_grounding: Optional[dict[str, Any]] = None


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

# === merged from backend/_models_loader.py ===
"""Shared loader for the canonical Computer Use model allowlist.

Moved out of ``backend.engine`` so both the engine and the HTTP server
can import it without duplicating the helper in two modules.
"""


import json
from pathlib import Path


def load_allowed_models_json() -> list[dict]:
    """Load and return the ``models`` list from ``backend/allowed_models.json``.

    The JSON file is the single source of truth for provider/model ids
    and their CU capabilities.
    """
    fpath = Path(__file__).resolve().parent / "allowed_models.json"
    with open(fpath, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("models", [])

