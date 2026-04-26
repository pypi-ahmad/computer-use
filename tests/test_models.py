# === merged from tests/test_models.py ===
"""Tests for Pydantic models and data contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.models.schemas import (
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

# === merged from tests/test_certifier.py ===
"""Regression test for the ``backend.models.validation`` CLI default schema path.

The previous default resolved ``engine_capabilities.json`` at the repo
root (``parent.parent``) while the file actually lives next to
``backend/engine_capabilities.py``, so ``python -m backend.models.validation``
raised ``FileNotFoundError`` on every clean checkout unless an explicit
``--schema`` argument was passed. This test locks in the fix.
"""


import json
import subprocess
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Invoke the certifier CLI out-of-process so ``-m`` path resolution
    is exercised exactly as a real user would hit it."""
    return subprocess.run(
        [sys.executable, "-m", "backend.models.validation", *args],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=10,
    )


class TestCertifierCli:
    """Covers the default-path bug fix and the ``--schema`` override."""

    def test_default_path_resolves_without_filenotfound(self):
        """``python -m backend.models.validation --json`` must NOT raise
        ``FileNotFoundError`` on a clean checkout. We only assert the
        schema loaded (exit code 0 = all healthy, 1 = schema loaded but
        env checks failed on this host) — any FileNotFoundError surfaces
        as exit code 2 with the traceback on stderr."""
        result = _run_cli("--json")
        assert "FileNotFoundError" not in result.stderr, (
            f"Default schema path still broken.\nstderr:\n{result.stderr}"
        )
        # Exit 0 (healthy) or 1 (unhealthy but schema-loaded) are both
        # acceptable — the point of the fix is that we got past the
        # load step. Exit 2+ means argparse/import/other crash.
        assert result.returncode in (0, 1), (
            f"Unexpected exit={result.returncode}\nstdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
        report = json.loads(result.stdout)
        assert report["schema_version"]
        assert report["engine_count"] >= 1

    def test_explicit_schema_override_still_errors_on_missing_file(self):
        """``--schema`` must still override the default. Passing a path
        that doesn't exist must produce a non-zero exit and a clear
        error (not silently fall back to the default)."""
        bad_schema = _REPO_ROOT / "__missing_certifier_schema__.json"
        assert not bad_schema.exists()

        result = _run_cli("--schema", str(bad_schema))
        assert result.returncode != 0
        # The error message must name the missing path so operators
        # know they typo'd rather than hit an unrelated bug.
        combined = result.stdout + result.stderr
        assert str(bad_schema) in combined, (
            f"Error message did not reference the bad path.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

# === merged from tests/test_model_policy.py ===
"""Tests for allowed_models.json integrity and model policy."""


from pathlib import Path

import pytest

_MODELS_PATH = Path(__file__).resolve().parent.parent / "backend" / "models" / "allowed_models.json"


@pytest.fixture(scope="module")
def models_data() -> dict:
    """Load and return parsed JSON from allowed_models.json."""
    with open(_MODELS_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def models(models_data) -> list[dict]:
    """Extract the models list from loaded models_data."""
    return models_data.get("models", [])


class TestAllowedModelsSchema:
    """Structural integrity of allowed_models.json."""

    def test_file_exists(self):
        assert _MODELS_PATH.exists()

    def test_has_models_key(self, models_data):
        assert "models" in models_data
        assert isinstance(models_data["models"], list)
        assert len(models_data["models"]) > 0

    def test_required_fields(self, models):
        required = {"provider", "model_id", "display_name", "supports_computer_use"}
        for m in models:
            missing = required - set(m.keys())
            assert not missing, f"Model {m.get('model_id', '?')} missing: {missing}"

    def test_valid_providers(self, models):
        valid = {"google", "anthropic", "openai"}
        for m in models:
            assert m["provider"] in valid, f"Invalid provider: {m['provider']}"


class TestModelPolicy:
    """Business rules for model allowlist."""

    def test_claude_models_have_cu_metadata(self, models):
        """Anthropic models with CU support must declare tool version and betas."""
        for m in models:
            if m["provider"] == "anthropic" and m["supports_computer_use"]:
                assert "cu_tool_version" in m, f"{m['model_id']} missing cu_tool_version"
                assert "cu_betas" in m, f"{m['model_id']} missing cu_betas"
                assert isinstance(m["cu_betas"], list) and len(m["cu_betas"]) > 0

    def test_gemini_3_flash_is_cu_capable(self, models):
        for m in models:
            if m["model_id"] == "gemini-3-flash-preview":
                assert m["supports_computer_use"] is True

    def test_claude_sonnet_46_is_cu_capable(self, models):
        for m in models:
            if m["model_id"] == "claude-sonnet-4-6":
                assert m["supports_computer_use"] is True
                assert m["cu_tool_version"] == "computer_20251124"

    def test_claude_opus_47_is_cu_capable(self, models):
        for m in models:
            if m["model_id"] == "claude-opus-4-7":
                assert m["supports_computer_use"] is True
                assert m["cu_tool_version"] == "computer_20251124"

    def test_gpt_54_is_cu_capable(self, models):
        for m in models:
            if m["model_id"] == "gpt-5.4":
                assert m["provider"] == "openai"
                assert m["supports_computer_use"] is True

    def test_removed_legacy_model_ids_are_not_listed(self, models):
        removed = {
            "claude-opus-4-6",
            "claude-sonnet-4-5",
            "gpt-5",
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-3.1-pro-preview",
            "gemini-2.5-computer-use-preview-10-2025",
        }
        listed = {m["model_id"] for m in models}
        assert removed.isdisjoint(listed)

    def test_discontinued_gemini_3_pro_preview_is_not_listed(self, models):
        assert all(m["model_id"] != "gemini-3-pro-preview" for m in models)

    def test_no_duplicate_model_ids(self, models):
        ids = [m["model_id"] for m in models]
        assert len(ids) == len(set(ids)), f"Duplicate model_ids: {ids}"

