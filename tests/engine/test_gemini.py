from __future__ import annotations
# === merged from tests/test_gemini_flash_followup.py ===
"""Gemini 3 Flash Preview CU follow-ups.

Covers:
  * ``BLOCK_ONLY_HIGH`` safety relaxation is opt-in via
    ``CUA_GEMINI_RELAX_SAFETY=1``.  Default behaviour delegates to
    Google's published default ("Off" for Gemini 2.5/3 per
    safety-settings docs, 2026-04).  The ToS-mandated
        ``require_confirmation`` handshake is unaffected either way.
    * History pruning keeps a configurable recent-turn window, never
        strips parts within a kept turn, and always preserves the most
        recent assistant turn intact for thought-signature replay.
  * Default viewport is 1440x900 as recommended by the CU guide.
"""


from copy import deepcopy
from dataclasses import dataclass, field
from unittest.mock import patch



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
# History pruning
# ---------------------------------------------------------------------------


@dataclass(eq=True)
class _FakePart:
    text: str | None = None
    inline_data: bytes | None = None
    function_call: dict | None = None
    function_response: dict | None = None
    tool_call: dict | None = None
    tool_response: dict | None = None
    thought_signature: str | None = None
    id: str | None = None
    tool_type: str | None = None


@dataclass(eq=True)
class _FakeContent:
    role: str
    parts: list[_FakePart] = field(default_factory=list)


class TestGeminiHistoryPruning:
    @staticmethod
    def _make_turn(turn_number: int, role: str) -> _FakeContent:
        marker = f"turn-{turn_number}"
        return _FakeContent(
            role=role,
            parts=[
                _FakePart(text=f"text-{marker}", thought_signature=f"sig-text-{marker}"),
                _FakePart(
                    tool_call={"id": f"tool-call-{marker}", "tool_type": "GOOGLE_SEARCH_WEB"},
                    thought_signature=f"sig-tool-call-{marker}",
                    id=f"tool-call-{marker}",
                    tool_type="GOOGLE_SEARCH_WEB",
                ),
                _FakePart(
                    tool_response={
                        "id": f"tool-response-{marker}",
                        "tool_type": "GOOGLE_SEARCH_WEB",
                        "response": {"search_suggestions": [marker]},
                    },
                    thought_signature=f"sig-tool-response-{marker}",
                    id=f"tool-response-{marker}",
                    tool_type="GOOGLE_SEARCH_WEB",
                ),
                _FakePart(
                    function_call={"name": f"fn-{marker}", "id": f"function-call-{marker}"},
                    thought_signature=f"sig-function-call-{marker}",
                    id=f"function-call-{marker}",
                    tool_type="COMPUTER_USE",
                ),
                _FakePart(
                    function_response={
                        "name": f"fn-{marker}",
                        "id": f"function-response-{marker}",
                        "response": {"ok": True, "turn": turn_number},
                    },
                    thought_signature=f"sig-function-response-{marker}",
                    id=f"function-response-{marker}",
                    tool_type="COMPUTER_USE",
                ),
                _FakePart(
                    inline_data=f"image-{marker}".encode(),
                    thought_signature=f"sig-image-{marker}",
                ),
            ],
        )

    def test_default_bound_preserves_last_10_turns_whole(self):
        from backend.engine import GeminiCUClient, Environment
        from backend.engine.gemini import _prune_gemini_context

        with patch("google.genai.Client"):
            client = GeminiCUClient(
                api_key="k",
                model="gemini-3-flash-preview",
                environment=Environment.BROWSER,
            )

        contents = [
            self._make_turn(i, "model" if i % 2 == 0 else "user")
            for i in range(1, 13)
        ]
        expected = deepcopy(contents[2:])

        _prune_gemini_context(contents, client._max_history_turns)

        assert client._max_history_turns == 10
        assert contents == expected

    def test_max_history_turns_3_prunes_turn_1_entirely_when_turn_4_arrives(self):
        from backend.engine import GeminiCUClient, Environment
        from backend.engine.gemini import _prune_gemini_context

        with patch("google.genai.Client"):
            client = GeminiCUClient(
                api_key="k",
                model="gemini-3-flash-preview",
                environment=Environment.BROWSER,
                max_history_turns=3,
            )

        contents = [
            self._make_turn(1, "user"),
            self._make_turn(2, "model"),
            self._make_turn(3, "user"),
            self._make_turn(4, "model"),
        ]

        _prune_gemini_context(contents, client._max_history_turns)

        assert client._max_history_turns == 3
        assert len(contents) == 3
        assert all(content.parts[0].text != "text-turn-1" for content in contents)
        assert [content.parts[0].text for content in contents] == [
            "text-turn-2",
            "text-turn-3",
            "text-turn-4",
        ]

    def test_most_recent_assistant_turn_is_never_pruned_even_at_1(self):
        from backend.engine.gemini import _prune_gemini_context

        assistant_turn = self._make_turn(2, "model")
        trailing_user_turn = self._make_turn(3, "user")
        contents = [
            self._make_turn(1, "user"),
            assistant_turn,
            trailing_user_turn,
        ]

        _prune_gemini_context(contents, 1)

        assert contents == [assistant_turn, trailing_user_turn]

    def test_pruning_never_strips_parts_within_a_kept_turn(self):
        from backend.engine.gemini import _prune_gemini_context

        contents = [
            self._make_turn(1, "user"),
            self._make_turn(2, "model"),
            self._make_turn(3, "user"),
            self._make_turn(4, "model"),
        ]
        expected = deepcopy(contents[1:])

        _prune_gemini_context(contents, 3)

        assert contents == expected
        kept_assistant = contents[-1]
        assert len(kept_assistant.parts) == 6
        assert kept_assistant.parts[1].tool_call == {
            "id": "tool-call-turn-4",
            "tool_type": "GOOGLE_SEARCH_WEB",
        }
        assert kept_assistant.parts[2].tool_response == {
            "id": "tool-response-turn-4",
            "tool_type": "GOOGLE_SEARCH_WEB",
            "response": {"search_suggestions": ["turn-4"]},
        }
        assert kept_assistant.parts[3].function_call == {
            "name": "fn-turn-4",
            "id": "function-call-turn-4",
        }
        assert kept_assistant.parts[4].function_response == {
            "name": "fn-turn-4",
            "id": "function-response-turn-4",
            "response": {"ok": True, "turn": 4},
        }
        assert kept_assistant.parts[5].inline_data == b"image-turn-4"


# ---------------------------------------------------------------------------
# Viewport default
# ---------------------------------------------------------------------------


class TestGeminiFlashViewportDefault:
    def test_gemini_flash_1440x900_default(self):
        """The CU guide recommends 1440x900 for best coordinate
        accuracy.  The project's default config (used to render the
        prompt viewport and provision the Xvfb display) must match."""
        from backend.infra.config import Config

        cfg = Config()
        assert cfg.screen_width == 1440
        assert cfg.screen_height == 900

# === merged from tests/test_sandbox_gemini_flash.py ===
"""Prompt S4 — Google Gemini 3 Flash Preview sandbox alignment tests.

Pins the contract introduced in this commit:

* Dockerfile exposes chromium / chromium-browser aliases backed by the
    installed Chrome binary, because Ubuntu 24.04 only ships snap-backed
    browser shims in apt.
* firefox-esr is retained via Mozilla's ESR tarball; S1 viewport 1440x900
    stays unchanged (Google's exact recommendation).
* Coordinate denormalization (0-999 → pixels) stays in the shared
  executor — regression guard.
"""


from pathlib import Path


from backend.engine import denormalize_x, denormalize_y
from backend.prompts import SYSTEM_PROMPT_GEMINI_CU


_DOCKERFILE = Path("docker/Dockerfile")


class TestSandboxDockerfileGemini:
    def test_dockerfile_exposes_chromium_compatible_binaries(self):
        """Gemini wants a Chromium-class browser even though Noble's apt
        packages are snap-only. The Dockerfile must therefore publish both
        resolver names as aliases to the installed Chrome binary."""
        text = _DOCKERFILE.read_text(encoding="utf-8")
        assert "/usr/local/bin/chromium" in text, (
            "Dockerfile must expose a chromium alias for Gemini sessions"
        )
        assert "/usr/local/bin/chromium-browser" in text, (
            "Dockerfile must expose a chromium-browser alias for Gemini sessions"
        )
        assert "google-chrome-stable" in text

    def test_dockerfile_still_has_firefox_esr(self):
        """Regression guard: Anthropic's reference uses Firefox-ESR.
        This commit must not remove or replace it."""
        text = _DOCKERFILE.read_text(encoding="utf-8")
        assert "firefox-esr" in text

    def test_dockerfile_viewport_still_1440x900(self):
        """Regression guard: Gemini's docs recommend exactly 1440x900,
        which is S1's shared default.  Do not change."""
        text = _DOCKERFILE.read_text(encoding="utf-8")
        assert "ENV SCREEN_WIDTH=1440" in text
        assert "ENV SCREEN_HEIGHT=900" in text


class TestGeminiCoordinateContract:
    def test_gemini_coordinate_denormalization(self):
        """0-999 normalized → pixels.  The scaling helpers in
        ``backend.engine`` are the single source of truth — exercised
        here as a regression guard so a refactor cannot silently drop
        the contract Gemini depends on."""
        # Extremes.
        assert denormalize_x(0, 1440) == 0
        assert denormalize_y(0, 900) == 0
        # Midpoint.
        assert denormalize_x(500, 1440) == 720
        assert denormalize_y(500, 900) == 450
        # Max normalized value (999, not 1000).
        # int(999/1000 * 1440) = int(1438.56) = 1438.
        assert denormalize_x(999, 1440) == 1438
        assert denormalize_y(999, 900) == 899


class TestGeminiSingleTabPrompt:
    def test_single_tab_hint_present(self):
        """Soft single-tab enforcement via the Gemini system prompt."""
        prompt = SYSTEM_PROMPT_GEMINI_CU.lower()
        assert "single-tab" in prompt or "single tab" in prompt
        # And the prompt names the reference so the operator can audit.
        assert "new tab" in prompt


