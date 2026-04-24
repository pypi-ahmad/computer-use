"""Tests for allowed_models.json integrity and model policy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_MODELS_PATH = Path(__file__).resolve().parent.parent / "backend" / "allowed_models.json"


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

    def test_gemini_31_pro_preview_not_cu_until_google_enables(self, models):
        """gemini-3.1-pro-preview does NOT have CU enabled per Google
        forum 2026-03-12 + official CU docs 2026-03-25 (which list only
        gemini-2.5-computer-use-preview-10-2025 and gemini-3-flash-preview).
        Keep this asserted until Google re-enables. See CHANGELOG for
        the full reference."""
        entry = next(
            m for m in models if m["model_id"] == "gemini-3.1-pro-preview"
        )
        assert entry["supports_computer_use"] is False

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

    def test_claude_opus_46_remains_supported(self, models):
        for m in models:
            if m["model_id"] == "claude-opus-4-6":
                assert m["supports_computer_use"] is True
                assert m["cu_tool_version"] == "computer_20251124"

    def test_claude_sonnet_45_remains_supported(self, models):
        for m in models:
            if m["model_id"] == "claude-sonnet-4-5":
                assert m["supports_computer_use"] is True
                assert m["cu_tool_version"] == "computer_20250124"

    def test_gpt_54_is_cu_capable(self, models):
        for m in models:
            if m["model_id"] == "gpt-5.4":
                assert m["provider"] == "openai"
                assert m["supports_computer_use"] is True

    def test_gpt_5_compatibility_id_remains_supported(self, models):
        for m in models:
            if m["model_id"] == "gpt-5":
                assert m["provider"] == "openai"
                assert m["supports_computer_use"] is True

    def test_gemini_25_compatibility_ids_remain_supported(self, models):
        seen = {
            m["model_id"] for m in models
            if m["model_id"] in {"gemini-2.5-pro", "gemini-2.5-flash"}
        }
        assert seen == {"gemini-2.5-pro", "gemini-2.5-flash"}

    def test_discontinued_gemini_3_pro_preview_is_not_listed(self, models):
        assert all(m["model_id"] != "gemini-3-pro-preview" for m in models)

    def test_no_duplicate_model_ids(self, models):
        ids = [m["model_id"] for m in models]
        assert len(ids) == len(set(ids)), f"Duplicate model_ids: {ids}"
