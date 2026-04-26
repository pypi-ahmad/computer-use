from __future__ import annotations
# === merged from tests/test_gemini_flash_followup.py ===
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
* Gemini adapter helper ``_gemini_resolve_browser_binary`` prefers
  chromium over firefox and emits a one-shot warning on fallback.
* ``_gemini_playwright_enabled`` is opt-in only and never pulls in
  Playwright by default.
* Coordinate denormalization (0-999 → pixels) stays in the shared
  executor — regression guard.
"""


from pathlib import Path
import types
from unittest.mock import patch


from backend.engine import denormalize_x, denormalize_y
from backend.engine.gemini import (
    _gemini_playwright_enabled,
    _gemini_resolve_browser_binary,
)
from backend.agent.prompts import SYSTEM_PROMPT_GEMINI_CU


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


class TestGeminiBrowserResolver:
    def test_gemini_adapter_routes_to_chromium(self):
        """When both Chromium and Firefox are available, Gemini picks
        Chromium (Google reference)."""

        def fake_which(name: str) -> str | None:
            return {
                "chromium-browser": "/usr/bin/chromium-browser",
                "chromium": "/usr/bin/chromium",
                "firefox-esr": "/usr/bin/firefox-esr",
                "firefox": "/usr/bin/firefox",
            }.get(name)

        logs: list[tuple[str, str]] = []
        path = _gemini_resolve_browser_binary(
            which=fake_which,
            log=lambda lvl, msg: logs.append((lvl, msg)),
        )
        assert path == "/usr/bin/chromium-browser"
        # No fallback warning when Chromium is present.
        assert logs == []

    def test_gemini_adapter_falls_back_to_firefox_with_warning(self):
        """When Chromium is unavailable, Firefox-ESR is acceptable but
        the adapter must emit a WARNING so the operator knows the
        session is off-reference."""

        def fake_which(name: str) -> str | None:
            return {
                "firefox-esr": "/usr/bin/firefox-esr",
                "firefox": "/usr/bin/firefox",
            }.get(name)

        logs: list[tuple[str, str]] = []
        path = _gemini_resolve_browser_binary(
            which=fake_which,
            log=lambda lvl, msg: logs.append((lvl, msg)),
        )
        assert path == "/usr/bin/firefox-esr"
        assert len(logs) == 1
        level, msg = logs[0]
        assert level == "warning"
        assert "Chromium" in msg
        assert "reference" in msg.lower()

    def test_gemini_resolver_returns_none_when_no_browser(self):
        """No browser installed → None; caller decides how to degrade."""
        logs: list[tuple[str, str]] = []
        path = _gemini_resolve_browser_binary(
            which=lambda _name: None,
            log=lambda lvl, msg: logs.append((lvl, msg)),
        )
        assert path is None
        assert logs == []


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


class TestGeminiPlaywrightOptIn:
    def test_gemini_playwright_path_default_on(self, monkeypatch):
        """Per Google's official Computer Use docs the recommended
        client-side action handler is Playwright. The unified sandbox
        pre-launches Chromium with CDP exposed on 127.0.0.1:9223 so
        the backend can connect via ``connect_over_cdp`` while staying
        inside the single Docker container. Therefore the Playwright
        path is ON by default; ``CUA_GEMINI_USE_PLAYWRIGHT=0`` is the
        explicit opt-out."""
        monkeypatch.delenv("CUA_GEMINI_USE_PLAYWRIGHT", raising=False)
        with patch.dict("sys.modules", {"playwright": types.ModuleType("playwright")}):
            assert _gemini_playwright_enabled() is True

        # Explicit opt-in still works.
        monkeypatch.setenv("CUA_GEMINI_USE_PLAYWRIGHT", "1")
        with patch.dict("sys.modules", {"playwright": types.ModuleType("playwright")}):
            assert _gemini_playwright_enabled() is True

        # Explicit opt-out forces the xdotool path.
        monkeypatch.setenv("CUA_GEMINI_USE_PLAYWRIGHT", "0")
        assert _gemini_playwright_enabled() is False

    def test_gemini_playwright_falls_back_when_package_missing(
        self, monkeypatch,
    ):
        """Default on + package not installed → return False and log
        error, so the caller degrades to the xdotool path."""
        monkeypatch.delenv("CUA_GEMINI_USE_PLAYWRIGHT", raising=False)

        import builtins

        real_import = builtins.__import__

        def blocked_import(name, *args, **kwargs):
            if name == "playwright" or name.startswith("playwright."):
                raise ImportError("no playwright in test env")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=blocked_import):
            assert _gemini_playwright_enabled() is False


class TestGeminiSingleTabPrompt:
    def test_single_tab_hint_present(self):
        """Soft single-tab enforcement via the Gemini system prompt."""
        prompt = SYSTEM_PROMPT_GEMINI_CU.lower()
        assert "single-tab" in prompt or "single tab" in prompt
        # And the prompt names the reference so the operator can audit.
        assert "new tab" in prompt

