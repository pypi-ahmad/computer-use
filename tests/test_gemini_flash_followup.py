"""Gemini 3 Flash Preview CU follow-ups.

Covers:
  * ``BLOCK_ONLY_HIGH`` safety relaxation is opt-in via
    ``CUA_GEMINI_RELAX_SAFETY=1``.  Default behaviour delegates to
    Google's published default ("Off" for Gemini 2.5/3 per
    safety-settings docs, 2026-04).  The ToS-mandated
    ``require_confirmation`` handshake is unaffected either way.
  * Screenshot pruning retains the goal + last-N turns and strips
    ``inline_data`` from older turns while keeping the turn sequence
    intact so thought-signature round-trip isn't broken.
  * Default viewport is 1440x900 as recommended by the CU guide.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Safety threshold env gate
# ---------------------------------------------------------------------------


class TestGeminiFlashSafetyGate:
    def test_gemini_flash_no_safety_settings_by_default(self, monkeypatch):
        """No ``safety_settings`` attached when the env flag is unset —
        Google's Gemini-3 default ("Off") applies."""
        from backend.engine import GeminiCUClient, Environment

        monkeypatch.delenv("CUA_GEMINI_RELAX_SAFETY", raising=False)
        GeminiCUClient._default_logged = False
        GeminiCUClient._relax_logged = False

        with patch("google.genai.Client"):
            client = GeminiCUClient(
                api_key="k",
                model="gemini-3-flash-preview",
                environment=Environment.BROWSER,
            )

        config = client._build_config()
        # GenerateContentConfig exposes populated kwargs as attributes —
        # an unset ``safety_settings`` is either missing or falsy.
        settings = getattr(config, "safety_settings", None)
        assert not settings, (
            f"safety_settings must be empty/None by default, got {settings!r}"
        )

    def test_gemini_flash_safety_relax_via_env(self, monkeypatch):
        """``CUA_GEMINI_RELAX_SAFETY=1`` restores the previous
        BLOCK_ONLY_HIGH thresholds across the four HarmCategory
        buckets."""
        from backend.engine import GeminiCUClient, Environment
        from google.genai import types

        monkeypatch.setenv("CUA_GEMINI_RELAX_SAFETY", "1")
        GeminiCUClient._default_logged = False
        GeminiCUClient._relax_logged = False

        with patch("google.genai.Client"):
            client = GeminiCUClient(
                api_key="k",
                model="gemini-3-flash-preview",
                environment=Environment.BROWSER,
            )

        config = client._build_config()
        settings = list(getattr(config, "safety_settings", []) or [])
        assert len(settings) == 4, (
            f"expected 4 safety settings (one per HarmCategory), "
            f"got {len(settings)}"
        )
        for s in settings:
            assert s.threshold == types.HarmBlockThreshold.BLOCK_ONLY_HIGH, (
                f"expected BLOCK_ONLY_HIGH, got {s.threshold!r}"
            )

    def test_gemini_flash_safety_off_for_non_one_value(self, monkeypatch):
        """Only the literal string ``"1"`` opts in."""
        from backend.engine import GeminiCUClient, Environment

        for bad in ("0", "true", "yes", ""):
            monkeypatch.setenv("CUA_GEMINI_RELAX_SAFETY", bad)
            GeminiCUClient._default_logged = False
            GeminiCUClient._relax_logged = False

            with patch("google.genai.Client"):
                client = GeminiCUClient(
                    api_key="k",
                    model="gemini-3-flash-preview",
                    environment=Environment.BROWSER,
                )

            config = client._build_config()
            settings = getattr(config, "safety_settings", None)
            assert not settings, (
                f"CUA_GEMINI_RELAX_SAFETY={bad!r} should NOT attach "
                f"safety_settings, got {settings!r}"
            )


# ---------------------------------------------------------------------------
# Screenshot pruning
# ---------------------------------------------------------------------------


class TestGeminiFlashScreenshotPruning:
    def test_gemini_flash_screenshot_pruning(self):
        """Simulate a 20-turn session.  After pruning:

          * the first Content (goal + initial screenshot) is retained,
          * the last ``_CONTEXT_PRUNE_KEEP_RECENT`` Contents are
            retained intact,
          * older Contents have had their ``inline_data`` stripped /
            replaced with a placeholder text Part, and
          * the total number of Contents is unchanged (turn sequence
            preserved so thought-signature round-trip isn't broken).
        """
        from google.genai import types

        from backend.engine import _CONTEXT_PRUNE_KEEP_RECENT
        from backend.engine.gemini import _prune_gemini_context

        def _fake_shot(marker: bytes) -> types.Part:
            return types.Part.from_bytes(
                data=b"\x89PNG\r\n\x1a\n" + marker + b"\x00" * 80,
                mime_type="image/png",
            )

        # Turn 0: user goal + initial screenshot.
        contents: list = [
            types.Content(
                role="user",
                parts=[
                    types.Part(text="goal"),
                    _fake_shot(b"initial"),
                ],
            )
        ]
        # Turns 1..19: alternating model / user with an inline_data
        # screenshot on each user turn (simulating FunctionResponse
        # replay).  We only need inline_data Parts for pruning signal,
        # so keep the shape simple.
        for i in range(1, 20):
            contents.append(
                types.Content(
                    role="user" if i % 2 else "model",
                    parts=[
                        types.Part(text=f"turn-{i}"),
                        _fake_shot(f"turn{i}".encode()),
                    ],
                )
            )

        original_len = len(contents)
        _prune_gemini_context(contents, types, _CONTEXT_PRUNE_KEEP_RECENT)

        # Turn sequence preserved.
        assert len(contents) == original_len, (
            "pruning must not drop Content entries — that would break "
            "thought-signature round-trip"
        )

        # First Content (goal) kept intact.
        first_parts = list(contents[0].parts)
        assert any(getattr(p, "inline_data", None) is not None
                   for p in first_parts), (
            "initial goal screenshot must be retained"
        )

        # Last N Contents kept intact.
        keep_start = original_len - _CONTEXT_PRUNE_KEEP_RECENT
        recent_image_count = sum(
            1
            for content in contents[keep_start:]
            for part in (content.parts or [])
            if getattr(part, "inline_data", None) is not None
        )
        assert recent_image_count >= 1, (
            "recent turns must still carry inline_data screenshots"
        )

        # Middle Contents had inline_data stripped / replaced.
        for content in contents[1:keep_start]:
            for part in content.parts or []:
                if getattr(part, "inline_data", None) is not None:
                    raise AssertionError(
                        "middle-range Content still carries inline_data; "
                        "pruning did not strip old screenshots"
                    )

        # Total image count bounded: 1 (goal) + at most N_recent.
        total_images = sum(
            1
            for content in contents
            for part in (content.parts or [])
            if getattr(part, "inline_data", None) is not None
        )
        assert total_images <= 1 + _CONTEXT_PRUNE_KEEP_RECENT, (
            f"expected <= {1 + _CONTEXT_PRUNE_KEEP_RECENT} inline images "
            f"after pruning, got {total_images}"
        )


# ---------------------------------------------------------------------------
# Viewport default
# ---------------------------------------------------------------------------


class TestGeminiFlashViewportDefault:
    def test_gemini_flash_1440x900_default(self):
        """The CU guide recommends 1440x900 for best coordinate
        accuracy.  The project's default config (used to render the
        prompt viewport and provision the Xvfb display) must match."""
        from backend.config import Config

        cfg = Config()
        assert cfg.screen_width == 1440
        assert cfg.screen_height == 900
