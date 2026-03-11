"""Tests for Pydantic models and data contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.models import (
    ActionType,
    AgentSession,
    SessionStatus,
    StartTaskRequest,
    StructuredError,
)


class TestActionType:
    """Verifies ActionType enum contains CU-native, canonical, and terminal values."""

    def test_cu_native_actions_exist(self):
        """All Gemini CU-native actions should be in the enum."""
        native = [
            "click_at", "hover_at", "type_text_at", "scroll_at",
            "drag_and_drop", "key_combination", "navigate",
            "open_web_browser", "scroll_document", "search", "wait_5_seconds",
        ]
        for a in native:
            assert ActionType(a) is not None

    def test_canonical_actions_exist(self):
        canonical = [
            "click", "double_click", "right_click", "hover",
            "type", "key", "scroll", "drag", "open_url",
            "go_back", "go_forward", "wait",
        ]
        for a in canonical:
            assert ActionType(a) is not None

    def test_terminal_actions(self):
        assert ActionType.DONE.value == "done"
        assert ActionType.ERROR.value == "error"


class TestStartTaskRequest:
    """Validates StartTaskRequest defaults, max-length, and max-steps bounds."""

    def test_valid_request(self):
        req = StartTaskRequest(
            task="Search for something",
            model="gemini-3-flash-preview",
            mode="browser",
            provider="google",
        )
        assert req.execution_target == "docker"
        assert req.engine == "computer_use"

    def test_task_max_length(self):
        with pytest.raises(ValidationError):
            StartTaskRequest(
                task="x" * 10_001,
                model="gemini-3-flash-preview",
                mode="browser",
                provider="google",
            )

    def test_max_steps_bounds(self):
        with pytest.raises(ValidationError):
            StartTaskRequest(
                task="test",
                model="gemini-3-flash-preview",
                mode="browser",
                provider="google",
                max_steps=0,
            )
        with pytest.raises(ValidationError):
            StartTaskRequest(
                task="test",
                model="gemini-3-flash-preview",
                mode="browser",
                provider="google",
                max_steps=201,
            )


class TestStructuredError:
    """Tests StructuredError default values and to_dict serialization."""

    def test_default_values(self):
        err = StructuredError()
        assert err.step == 0
        assert err.errorCode == "unknown_error"

    def test_to_dict(self):
        err = StructuredError(step=5, action="click", errorCode="timeout", message="Timed out")
        d = err.to_dict()
        assert d["step"] == 5
        assert d["errorCode"] == "timeout"


class TestAgentSession:
    """Checks AgentSession defaults for model and status fields."""

    def test_default_model(self):
        session = AgentSession(session_id="abc", task="test")
        assert session.model == "gemini-3-flash-preview"

    def test_default_status(self):
        session = AgentSession(session_id="abc", task="test")
        assert session.status == SessionStatus.IDLE


class TestStartTaskRequestNoUnusedFields:
    """Verify the request model doesn't accept misleading fields."""

    def test_no_system_prompt_field(self):
        """StartTaskRequest should NOT have a system_prompt field that is never used."""
        assert "system_prompt" not in StartTaskRequest.model_fields

    def test_no_allowed_domains_field(self):
        """StartTaskRequest should NOT have an allowed_domains field that is never used."""
        assert "allowed_domains" not in StartTaskRequest.model_fields
