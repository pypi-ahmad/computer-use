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

from __future__ import annotations

from pathlib import Path
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
    def test_gemini_playwright_path_opt_in_only(self, monkeypatch):
        """Without the env flag, the Playwright path MUST stay off
        regardless of whether the package is installed — otherwise a
        dev install would silently change runtime behaviour."""
        monkeypatch.delenv("CUA_GEMINI_USE_PLAYWRIGHT", raising=False)
        assert _gemini_playwright_enabled() is False

        # Even setting to a non-"1" truthy-looking value must be off.
        monkeypatch.setenv("CUA_GEMINI_USE_PLAYWRIGHT", "true")
        assert _gemini_playwright_enabled() is False
        monkeypatch.setenv("CUA_GEMINI_USE_PLAYWRIGHT", "yes")
        assert _gemini_playwright_enabled() is False

    def test_gemini_playwright_falls_back_when_package_missing(
        self, monkeypatch,
    ):
        """Flag on + package not installed → return False and log
        error, so the caller degrades to the xdotool path."""
        monkeypatch.setenv("CUA_GEMINI_USE_PLAYWRIGHT", "1")

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
