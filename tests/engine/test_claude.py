# === merged from tests/test_claude_actions.py ===
"""Tests for Claude action dispatch in ClaudeCUClient._execute_claude_action."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.engine import ClaudeCUClient, CUActionResult


class FakeExecutor:
    """Minimal executor stub used to verify Claude action translation."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, name, payload):
        self.calls.append((name, payload))
        return CUActionResult(name=name, extra=payload)


@pytest.fixture
def executor():
    """Create a desktop-style executor stub with real-pixel coordinates."""
    return FakeExecutor()


@pytest.fixture
def client():
    """Create a ClaudeCUClient without a real API key (for action dispatch tests)."""
    with patch("anthropic.Anthropic"):
        return ClaudeCUClient(
            api_key="test-key",
            model="claude-sonnet-4-6",
        )


class TestClaudeActionDispatch:
    """Test _execute_claude_action for all supported Claude actions."""

    @pytest.mark.asyncio
    async def test_click(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "click", "coordinate": [100, 200]},
            executor,
        )
        assert result.success
        assert executor.calls == [("click_at", {"x": 100, "y": 200})]

    @pytest.mark.asyncio
    async def test_double_click(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "double_click", "coordinate": [100, 200]},
            executor,
        )
        assert result.success
        assert executor.calls == [("double_click", {"x": 100, "y": 200})]

    @pytest.mark.asyncio
    async def test_right_click(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "right_click", "coordinate": [100, 200]},
            executor,
        )
        assert result.success
        assert executor.calls == [("right_click", {"x": 100, "y": 200})]

    @pytest.mark.asyncio
    async def test_middle_click(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "middle_click", "coordinate": [100, 200]},
            executor,
        )
        assert result.success
        assert executor.calls == [("middle_click", {"x": 100, "y": 200})]

    @pytest.mark.asyncio
    async def test_triple_click(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "triple_click", "coordinate": [100, 200]},
            executor,
        )
        assert result.success
        assert executor.calls == [("triple_click", {"x": 100, "y": 200})]

    @pytest.mark.asyncio
    async def test_type(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "type", "text": "hello"},
            executor,
        )
        assert result.success
        assert executor.calls == [("type_at_cursor", {"text": "hello", "press_enter": False})]

    @pytest.mark.asyncio
    async def test_key(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "key", "key": "Return"},
            executor,
        )
        assert result.success
        assert executor.calls == [("key_combination", {"keys": "Enter"})]

    @pytest.mark.asyncio
    async def test_scroll(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "scroll", "coordinate": [500, 500], "direction": "down", "amount": 3},
            executor,
        )
        assert result.success
        assert executor.calls == [("scroll_at", {"x": 500, "y": 500, "direction": "down", "magnitude": 600})]

    @pytest.mark.asyncio
    async def test_mouse_move(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "mouse_move", "coordinate": [300, 400]},
            executor,
        )
        assert result.success
        assert executor.calls == [("hover_at", {"x": 300, "y": 400})]

    @pytest.mark.asyncio
    async def test_left_mouse_down(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "left_mouse_down"},
            executor,
        )
        assert result.success
        assert executor.calls == [("left_mouse_down", {})]

    @pytest.mark.asyncio
    async def test_left_mouse_up(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "left_mouse_up"},
            executor,
        )
        assert result.success
        assert executor.calls == [("left_mouse_up", {})]

    @pytest.mark.asyncio
    async def test_hold_key(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "hold_key", "key": "Shift", "duration": 0.1},
            executor,
        )
        assert result.success
        assert executor.calls == [("hold_key", {"key": "Shift", "duration": 0.1})]

    @pytest.mark.asyncio
    async def test_wait(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "wait", "duration": 0.01},
            executor,
        )
        assert result.success

    @pytest.mark.asyncio
    async def test_zoom(self, client, executor):
        # ``zoom`` now takes a required ``region=[x1,y1,x2,y2]`` and
        # delegates to the executor.  Covered more thoroughly in
        # tests/test_opus_47_followup.py.
        result = await client._execute_claude_action(
            {"action": "zoom", "region": [0, 0, 100, 100]},
            executor,
        )
        assert result.success

    @pytest.mark.asyncio
    async def test_screenshot_is_noop(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "screenshot"},
            executor,
        )
        assert result.success
        assert result.name == "screenshot"

    @pytest.mark.asyncio
    async def test_unknown_action_fails(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "nonexistent_action"},
            executor,
        )
        assert not result.success
        assert "Unknown" in result.error

    @pytest.mark.asyncio
    async def test_coordinate_upscaling(self, client, executor):
        """When scaling is active, coordinates should be upscaled."""
        scale = 0.8
        result = await client._execute_claude_action(
            {"action": "click", "coordinate": [80, 160]},
            executor,
            scale_factor=scale,
        )
        assert result.success
        assert executor.calls == [("click_at", {"x": 100, "y": 200})]


class TestClaudeToolConfig:
    """Test ClaudeCUClient tool configuration."""

    def test_tool_version_comes_from_registry_for_sonnet(self):
        with patch("anthropic.AsyncAnthropic"):
            client = ClaudeCUClient(api_key="test", model="claude-sonnet-4-6")
        assert client._tool_version == "computer_20251124"
        assert client._beta_flag == "computer-use-2025-11-24"

    def test_tool_version_comes_from_registry_for_opus(self):
        with patch("anthropic.AsyncAnthropic"):
            client = ClaudeCUClient(api_key="test", model="claude-opus-4-7")
        assert client._tool_version == "computer_20251124"
        assert client._beta_flag == "computer-use-2025-11-24"

    def test_unregistered_future_model_raises_explicit_registry_error(self):
        with patch("anthropic.AsyncAnthropic"):
            with pytest.raises(ValueError, match="not in registry"):
                ClaudeCUClient(api_key="test", model="claude-sonnet-5-0-future")

    def test_explicit_tool_version_overrides(self):
        with patch("anthropic.AsyncAnthropic"):
            client = ClaudeCUClient(
                api_key="test", model="some-model",
                tool_version="computer_20250124",
                beta_flag="computer-use-2025-01-24",
            )
        assert client._tool_version == "computer_20250124"
        assert client._beta_flag == "computer-use-2025-01-24"

    def test_build_tools_includes_zoom(self):
        with patch("anthropic.AsyncAnthropic"):
            client = ClaudeCUClient(api_key="test", model="claude-sonnet-4-6")
        tools = client._build_tools(1200, 800)
        assert tools[0]["type"] == "computer_20251124"
        assert tools[0]["enable_zoom"] is True
        assert tools[0]["display_width_px"] == 1200
        assert tools[0]["display_height_px"] == 800

# === merged from tests/test_opus_47_followup.py ===
"""Opus 4.7-specific follow-ups: zoom action handler, opt-in prompt
caching, and CU-prompt audit.

Covers:
  * ``zoom`` action handler crops the screenshot to the model's region
    and rejects inverted rectangles.
  * Claude adapter adds ``cache_control: ephemeral`` to the
    ``computer_20251124`` tool definition only when
    ``CUA_CLAUDE_CACHING=1`` is set.
  * The CU system prompt for Opus 4.7 has self-verification scaffolding
    stripped; Sonnet 4.6 keeps the scaffolded prompt as a negative
    control.
"""


from io import BytesIO
from unittest.mock import patch

import pytest

from backend.engine import CUActionResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _png_bytes(width: int, height: int) -> bytes:
    """Build a real PNG whose header advertises width x height."""
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# zoom action handler
# ---------------------------------------------------------------------------


class TestZoomActionHandler:
    @pytest.mark.asyncio
    async def test_zoom_action_crops_region(self):
        """Claude adapter's zoom dispatch must call the executor with the
        validated region and surface the cropped PNG back to the caller."""
        pytest.importorskip("PIL")
        from backend.engine import ClaudeCUClient

        captured_args: dict = {}

        class FakeExecutor:
            screen_width = 1440
            screen_height = 900

            async def execute(self, name, args):
                captured_args["name"] = name
                captured_args["args"] = args
                # Simulate agent_service: return a PNG whose dimensions
                # match (x2-x1) x (y2-y1).
                region = args["region"]
                w = region[2] - region[0]
                h = region[3] - region[1]
                return CUActionResult(
                    name="zoom",
                    extra={"image_bytes": _png_bytes(w, h)},
                )

        with patch("anthropic.AsyncAnthropic"):
            client = ClaudeCUClient(api_key="k", model="claude-opus-4-7")

        result = await client._execute_claude_action(
            {"action": "zoom", "region": [100, 100, 300, 200]},
            FakeExecutor(),
        )

        assert result.success is True
        assert result.name == "zoom"
        assert captured_args["name"] == "zoom"
        assert captured_args["args"]["region"] == [100, 100, 300, 200]

        # The returned PNG must match the requested region dimensions
        # (200 x 100), NOT the full display (1440 x 900).
        from PIL import Image
        img = Image.open(BytesIO(result.extra["image_bytes"]))
        assert img.size == (200, 100)

    @pytest.mark.asyncio
    async def test_zoom_action_rejects_inverted_region(self):
        """An inverted region must fail-fast without calling the
        executor — no crash, no full-screen fallback."""
        from backend.engine import ClaudeCUClient

        executor_called = False

        class FakeExecutor:
            screen_width = 1440
            screen_height = 900

            async def execute(self, name, args):
                nonlocal executor_called
                executor_called = True
                return CUActionResult(name=name)

        with patch("anthropic.AsyncAnthropic"):
            client = ClaudeCUClient(api_key="k", model="claude-opus-4-7")

        result = await client._execute_claude_action(
            {"action": "zoom", "region": [100, 100, 50, 50]},
            FakeExecutor(),
        )

        assert result.success is False
        assert result.error is not None
        assert "inverted" in result.error or "empty" in result.error
        assert executor_called is False, (
            "executor must not be called for an invalid region"
        )

    @pytest.mark.asyncio
    async def test_zoom_action_rejects_wrong_shape(self):
        from backend.engine import ClaudeCUClient

        class FakeExecutor:
            screen_width = 1440
            screen_height = 900

            async def execute(self, name, args):
                raise AssertionError("executor must not be called")

        with patch("anthropic.AsyncAnthropic"):
            client = ClaudeCUClient(api_key="k", model="claude-opus-4-7")

        # Missing region
        result = await client._execute_claude_action(
            {"action": "zoom"}, FakeExecutor(),
        )
        assert result.success is False
        # Wrong length
        result = await client._execute_claude_action(
            {"action": "zoom", "region": [0, 0, 10]}, FakeExecutor(),
        )
        assert result.success is False


# ---------------------------------------------------------------------------
# Prompt caching env flag
# ---------------------------------------------------------------------------


class TestClaudeCachingEnvFlag:
    def test_claude_caching_env_flag_adds_cache_control(self, monkeypatch):
        from backend.engine import ClaudeCUClient

        monkeypatch.setenv("CUA_CLAUDE_CACHING", "1")
        # Reset the one-shot log flag so the test is order-independent.
        ClaudeCUClient._caching_logged = False

        with patch("anthropic.AsyncAnthropic"):
            client = ClaudeCUClient(api_key="k", model="claude-opus-4-7")

        tools = client._build_tools(1440, 900)
        assert len(tools) == 1
        assert tools[0]["type"] == "computer_20251124"
        assert tools[0].get("cache_control") == {"type": "ephemeral"}

    def test_claude_caching_disabled_by_default(self, monkeypatch):
        from backend.engine import ClaudeCUClient

        monkeypatch.delenv("CUA_CLAUDE_CACHING", raising=False)

        with patch("anthropic.AsyncAnthropic"):
            client = ClaudeCUClient(api_key="k", model="claude-opus-4-7")

        tools = client._build_tools(1440, 900)
        assert len(tools) == 1
        assert "cache_control" not in tools[0]

    def test_claude_caching_off_for_unknown_env_value(self, monkeypatch):
        """Any value other than exactly '1' keeps caching off."""
        from backend.engine import ClaudeCUClient

        for bad in ("0", "true", "yes", "", "on"):
            monkeypatch.setenv("CUA_CLAUDE_CACHING", bad)
            with patch("anthropic.AsyncAnthropic"):
                client = ClaudeCUClient(api_key="k", model="claude-opus-4-7")
            tools = client._build_tools(1440, 900)
            assert "cache_control" not in tools[0], (
                f"cache_control unexpectedly set for CUA_CLAUDE_CACHING={bad!r}"
            )


# ---------------------------------------------------------------------------
# Opus 4.7 prompt audit
# ---------------------------------------------------------------------------


# Scaffolding phrases that Opus 4.7's more-literal interpreter reads as
# explicit instructions and that adaptive thinking already subsumes.
_SCAFFOLDING_PHRASES = (
    "step by step",
    "double-check",
    "verify before returning",
)


class TestPromptAudit:
    def test_opus_47_prompts_stripped_of_scaffolding(self):
        from backend.agent.prompts import get_system_prompt

        prompt = get_system_prompt(
            "computer_use", "desktop",
            provider="anthropic", model="claude-opus-4-7",
        )
        lowered = prompt.lower()
        for phrase in _SCAFFOLDING_PHRASES:
            assert phrase not in lowered, (
                f"Opus 4.7 prompt unexpectedly contains {phrase!r}"
            )

    def test_sonnet_46_prompt_keeps_scaffolding(self):
        """Negative control: non-4.7 Claude models still benefit from
        the scaffolding and must retain it."""
        from backend.agent.prompts import get_system_prompt

        prompt = get_system_prompt(
            "computer_use", "desktop",
            provider="anthropic", model="claude-sonnet-4-6",
        )
        lowered = prompt.lower()
        scaffolding_present = any(p in lowered for p in _SCAFFOLDING_PHRASES)
        assert scaffolding_present, (
            "Sonnet 4.6 prompt should retain 4.6-era scaffolding — "
            f"none of {_SCAFFOLDING_PHRASES} found"
        )

    def test_opus_46_gets_scaffolded_prompt(self):
        """Opus 4.6 (computer_20251124 tool but older reasoning) still
        benefits from scaffolding — only 4.7 is more literal."""
        from backend.agent.prompts import get_system_prompt

        prompt = get_system_prompt(
            "computer_use", "desktop",
            provider="anthropic", model="claude-opus-4-6",
        )
        assert any(p in prompt.lower() for p in _SCAFFOLDING_PHRASES)

    def test_anthropic_without_model_defaults_to_scaffolded(self):
        """Callers that don't pass a model id must get the conservative
        scaffolded prompt."""
        from backend.agent.prompts import get_system_prompt

        prompt = get_system_prompt(
            "computer_use", "desktop", provider="anthropic",
        )
        assert any(p in prompt.lower() for p in _SCAFFOLDING_PHRASES)

# === merged from tests/test_sandbox_opus47.py ===
"""Sandbox / Opus 4.7 hi-res tests.

Covers:
  * ``docker/Dockerfile`` installs the union of Anthropic reference
    packages (xvfb, xdotool, scrot, imagemagick, mutter, x11vnc,
    firefox-esr, xterm, x11-apps, xpdf, tint2, sudo,
    build-essential, software-properties-common, netcat-openbsd)
    alongside the existing XFCE4 stack.
  * Default viewport is 1440x900 (the union-of-best-practice across
    all four CU providers) and ``WIDTH`` / ``HEIGHT`` aliases are set.
  * ``CUA_OPUS47_HIRES=1`` drops the 3.75 MP total-pixel cap for
    Opus 4.7 so 2560x1600 keeps 1:1 coordinates, and is ignored for
    every other model.
"""


from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Dockerfile pins
# ---------------------------------------------------------------------------


_DOCKERFILE = Path("docker/Dockerfile")

# Anthropic computer-use-demo reference packages that must be present.
# Order is the install-order they appear in the Dockerfile; mutter /
# xfce4 coexist per the repo's WM choice.
_REQUIRED_PACKAGES = (
    # Core / network / build
    "build-essential",
    "netcat-openbsd",
    "software-properties-common",
    "sudo",
    # Desktop / X11 / apps
    "firefox-esr",
    "imagemagick",
    "mutter",
    "scrot",
    "tint2",
    "x11-apps",
    "x11vnc",
    "xdotool",
    "xpdf",
    "xterm",
    "xvfb",
)


class TestDockerfileReferencePackages:
    def test_dockerfile_has_anthropic_reference_packages(self):
        text = _DOCKERFILE.read_text(encoding="utf-8")
        missing = [pkg for pkg in _REQUIRED_PACKAGES if pkg not in text]
        assert not missing, (
            f"docker/Dockerfile missing Anthropic reference packages: "
            f"{missing}"
        )

    def test_dockerfile_preserves_xfce4_wm(self):
        """XFCE4 is the project's WM; mutter is installed alongside
        for Anthropic-reference parity but XFCE4 must NOT be removed."""
        text = _DOCKERFILE.read_text(encoding="utf-8")
        assert "xfce4" in text
        assert "xfce4-goodies" in text


class TestDockerfileViewportDefault:
    def test_dockerfile_default_viewport_1440x900(self):
        text = _DOCKERFILE.read_text(encoding="utf-8")
        assert "ENV SCREEN_WIDTH=1440" in text
        assert "ENV SCREEN_HEIGHT=900" in text

    def test_dockerfile_width_height_aliases(self):
        """``WIDTH`` / ``HEIGHT`` are exposed as aliases of
        ``SCREEN_WIDTH`` / ``SCREEN_HEIGHT`` for parity with the
        Anthropic quickstart container."""
        text = _DOCKERFILE.read_text(encoding="utf-8")
        assert "ENV WIDTH=1440" in text
        assert "ENV HEIGHT=900" in text

    def test_entrypoint_xvfb_uses_screen_dims(self):
        text = Path("docker/entrypoint.sh").read_text(encoding="utf-8")
        assert "${SCREEN_WIDTH}x${SCREEN_HEIGHT}x${SCREEN_DEPTH}" in text


# ---------------------------------------------------------------------------
# Opus 4.7 hi-res env flag
# ---------------------------------------------------------------------------


class TestOpus47HiresEnvFlag:
    @staticmethod
    async def _capture_scale_log(model: str, screen_w: int, screen_h: int,
                                  env_value: str | None) -> dict:
        """Drive one turn through ``iter_turns`` and capture the
        ``tools=[{display_width_px, display_height_px}]`` block + any
        CUA_OPUS47_HIRES log line."""
        from backend.engine import ClaudeCUClient

        # Lightweight 1x1 PNG that passes the >=100 B guard.
        png = (
            b"\x89PNG\r\n\x1a\n"
            + b"\x00" * 8
            + screen_w.to_bytes(4, "big")
            + screen_h.to_bytes(4, "big")
            + b"\x00" * 100
        )

        class FakeExecutor:
            def __init__(self):
                self.screen_width = screen_w
                self.screen_height = screen_h

            async def capture_screenshot(self):
                return png

            async def execute(self, name, args):
                from backend.engine import CUActionResult
                return CUActionResult(name=name)

            def get_current_url(self):
                return ""

        captured: dict = {}
        logs: list[tuple[str, str]] = []

        class FakeResponse:
            stop_reason = "end_turn"
            content: list = []

        async def fake_create(**kwargs):
            captured.update(kwargs)
            return FakeResponse()

        class FakeMessages:
            create = staticmethod(fake_create)

        class FakeBeta:
            messages = FakeMessages()

        class FakeClient:
            beta = FakeBeta()

        with patch.dict("os.environ",
                        {"CUA_OPUS47_HIRES": env_value} if env_value is not None
                        else {}, clear=False):
            # If env_value is None, make sure the key is truly absent.
            if env_value is None:
                import os as _os
                _os.environ.pop("CUA_OPUS47_HIRES", None)

            with patch("backend.engine.claude.resize_screenshot_for_claude",
                       return_value=(png, screen_w, screen_h)):
                with patch("anthropic.AsyncAnthropic") as AA:
                    AA.return_value = FakeClient()
                    client = ClaudeCUClient(api_key="k", model=model)
                    async for _ in client.iter_turns(
                        "noop", FakeExecutor(), turn_limit=1,
                        on_log=lambda lvl, msg: logs.append((lvl, msg)),
                    ):
                        pass

        return {"create_kwargs": captured, "logs": logs}

    @pytest.mark.asyncio
    async def test_opus47_hires_flag_allows_2560x1600(self, monkeypatch):
        """Opus 4.7 with CUA_OPUS47_HIRES=1 at 2560x1600 keeps 1:1
        coordinates — default ``get_claude_scale_factor`` would have
        downscaled because 2560*1600=4.10 MP exceeds the 3.75 MP cap."""
        monkeypatch.setenv("CUA_OPUS47_HIRES", "1")
        result = await self._capture_scale_log(
            "claude-opus-4-7", 2560, 1600, "1",
        )
        tools = result["create_kwargs"]["tools"]
        # Long edge 2560 <= 2576, so scale is 1.0 and reported dims
        # equal the native framebuffer.
        assert tools[0]["display_width_px"] == 2560
        assert tools[0]["display_height_px"] == 1600

        # A log line confirms the gate took effect.
        assert any("CUA_OPUS47_HIRES" in msg for lvl, msg in result["logs"])

    @pytest.mark.asyncio
    async def test_opus47_hires_flag_clamps_at_2576(self, monkeypatch):
        """Long edge > 2576 still clamps — the flag only drops the
        pixel-count cap, not the long-edge ceiling."""
        monkeypatch.setenv("CUA_OPUS47_HIRES", "1")
        result = await self._capture_scale_log(
            "claude-opus-4-7", 4000, 2500, "1",
        )
        tools = result["create_kwargs"]["tools"]
        # 2576/4000 = 0.644 -> 2576 x 1610.
        assert tools[0]["display_width_px"] == 2576
        assert tools[0]["display_height_px"] == 1610

    @pytest.mark.asyncio
    async def test_opus47_hires_flag_ignored_for_sonnet_46(self, monkeypatch):
        """Sonnet 4.6 must NOT honor CUA_OPUS47_HIRES — it downsamples
        internally and the 3.75 MP ceiling is mandatory."""
        monkeypatch.setenv("CUA_OPUS47_HIRES", "1")
        result = await self._capture_scale_log(
            "claude-sonnet-4-6", 2560, 1600, "1",
        )
        tools = result["create_kwargs"]["tools"]
        # Default get_claude_scale_factor picks sqrt(3.75e6 / 4.096e6)
        # ≈ 0.9566 -> 2448 x 1530 (floored).
        assert tools[0]["display_width_px"] < 2560
        assert tools[0]["display_height_px"] < 1600
        # And no hi-res log line.
        assert not any(
            "CUA_OPUS47_HIRES" in msg for lvl, msg in result["logs"]
        )

    @pytest.mark.asyncio
    async def test_opus47_hires_flag_off_by_default(self, monkeypatch):
        """Without the flag, Opus 4.7 at 2560x1600 still downscales
        per the default ``get_claude_scale_factor`` pixel-count cap."""
        monkeypatch.delenv("CUA_OPUS47_HIRES", raising=False)
        result = await self._capture_scale_log(
            "claude-opus-4-7", 2560, 1600, None,
        )
        tools = result["create_kwargs"]["tools"]
        assert tools[0]["display_width_px"] < 2560

# === merged from tests/test_sandbox_sonnet46.py ===
"""Sonnet 4.6 shares Opus 4.7's sandbox contract.

Prompt S1 established the Anthropic-reference package baseline + 1440x900
viewport + opt-in ``CUA_OPUS47_HIRES`` gate.  Sonnet 4.6 rides the same
``computer_20251124`` tool version and ``computer-use-2025-11-24`` beta
header but does NOT inherit Opus 4.7's 2576 px / 1:1 improvements — it
stays on the 1568 px / 1.15 MP ceiling and downscales internally.

These tests lock the shared-sandbox contract so a future refactor that
forks the Dockerfile per-model or accidentally extends
``CUA_OPUS47_HIRES`` to Sonnet 4.6 fails loudly.
"""


from pathlib import Path
from unittest.mock import patch

import pytest

from backend.engine import CUActionResult


_DOCKERFILE = Path("docker/Dockerfile")


class TestSonnet46SharesSandbox:
    def test_sonnet46_uses_shared_1440x900_default(self):
        """No per-model viewport fork: the single 1440x900 default
        from S1 covers Sonnet 4.6 as well."""
        text = _DOCKERFILE.read_text(encoding="utf-8")
        assert "ENV SCREEN_WIDTH=1440" in text
        assert "ENV SCREEN_HEIGHT=900" in text
        # The viewport comment block must call out Sonnet 4.6 so the
        # shared-default intent is documented at the source.
        assert "Sonnet 4.6" in text, (
            "Dockerfile viewport comment must document that Sonnet 4.6 "
            "shares the 1440x900 default"
        )

    def test_sonnet46_uses_shared_packages(self):
        """The Anthropic computer-use-demo reference package set from
        S1 is the single source of truth for all Claude models.  There
        must be NO Sonnet-4.6-specific package branch."""
        text = _DOCKERFILE.read_text(encoding="utf-8")
        # Sanity: the S1 reference packages are still present.
        for pkg in (
            "xdotool", "scrot", "xvfb", "x11vnc", "mutter",
            "firefox-esr", "imagemagick",
        ):
            assert pkg in text, f"reference package {pkg!r} missing"

        # Negative: no model-specific install line.  If someone later
        # adds ``RUN ... sonnet`` or a Sonnet-only apt block, this
        # catches it.
        lowered = text.lower()
        assert "sonnet-4-6" not in lowered
        assert "claude-sonnet" not in lowered


class TestSonnet46HiresFlagIgnored:
    @pytest.mark.asyncio
    async def test_sonnet46_does_not_honor_opus47_hires_flag(
        self, monkeypatch,
    ):
        """``CUA_OPUS47_HIRES=1`` must be a no-op for Sonnet 4.6.
        Even at 2560x1600 Sonnet 4.6 must still downscale per the
        default ``get_claude_scale_factor`` (pixel-count cap of
        3.75 MP on the computer_20251124 tool path)."""
        from backend.engine import ClaudeCUClient

        monkeypatch.setenv("CUA_OPUS47_HIRES", "1")

        screen_w, screen_h = 2560, 1600
        png = (
            b"\x89PNG\r\n\x1a\n"
            + b"\x00" * 8
            + screen_w.to_bytes(4, "big")
            + screen_h.to_bytes(4, "big")
            + b"\x00" * 100
        )

        class FakeExecutor:
            def __init__(self):
                self.screen_width = screen_w
                self.screen_height = screen_h

            async def capture_screenshot(self):
                return png

            async def execute(self, name, args):
                return CUActionResult(name=name)

            def get_current_url(self):
                return ""

        captured: dict = {}
        logs: list[tuple[str, str]] = []

        class FakeResponse:
            stop_reason = "end_turn"
            content: list = []

        async def fake_create(**kwargs):
            captured.update(kwargs)
            return FakeResponse()

        class FakeMessages:
            create = staticmethod(fake_create)

        class FakeBeta:
            messages = FakeMessages()

        class FakeClient:
            beta = FakeBeta()

        with patch(
            "backend.engine.claude.resize_screenshot_for_claude",
            return_value=(png, screen_w, screen_h),
        ), patch("anthropic.AsyncAnthropic") as AA:
            AA.return_value = FakeClient()
            client = ClaudeCUClient(api_key="k", model="claude-sonnet-4-6")
            async for _ in client.iter_turns(
                "noop", FakeExecutor(), turn_limit=1,
                on_log=lambda lvl, msg: logs.append((lvl, msg)),
            ):
                pass

        tools = captured["tools"]
        # Downscaled: strictly smaller than the native framebuffer.
        assert tools[0]["display_width_px"] < screen_w
        assert tools[0]["display_height_px"] < screen_h
        # And the Opus-4.7 hi-res gate log line must NOT appear.
        assert not any(
            "CUA_OPUS47_HIRES" in msg for _lvl, msg in logs
        ), "CUA_OPUS47_HIRES must be ignored for Sonnet 4.6"

# === merged from tests/test_sonnet_46_followup.py ===
"""Sonnet 4.6 tool-version + caching + zoom parity with Opus 4.7.

Prompt 3 (Opus 4.7) landed shared machinery — ``_NEW_TOOL_MODELS``
routing, ``CUA_CLAUDE_CACHING`` env-gated ephemeral cache_control,
``enable_zoom`` on ``computer_20251124`` tool definitions, adaptive
thinking on the ``computer_20251124`` branch — that is model-version-
independent.  These tests pin Sonnet 4.6 to that same contract so a
future refactor that inadvertently gates any of it on ``_is_opus_47``
gets caught immediately.

See anthropic-sdk-typescript issue #914 (2026-02-17) for the beta
header regression that motivates the beta-string assertion below:
Sonnet 4.6 returns HTTP 400 for the old ``computer-use-2025-01-24``
beta and must use ``computer-use-2025-11-24`` exclusively.
"""


from io import BytesIO
from unittest.mock import patch

import pytest

from backend.engine import CUActionResult


SONNET_46 = "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Shared helpers (mirrors tests/test_fixes_wave_apr2026.py).
# ---------------------------------------------------------------------------


def _minimal_png(width: int = 1280, height: int = 800) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00" * 8
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x00" * 100
    )


def _real_png(width: int, height: int) -> bytes:
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, format="PNG")
    return buf.getvalue()


class _FakeExecutor:
    screen_width = 1280
    screen_height = 800

    def __init__(self, screenshot: bytes = b"") -> None:
        self._screenshot = screenshot or _minimal_png()

    async def capture_screenshot(self) -> bytes:
        return self._screenshot

    async def execute(self, name, args):
        return CUActionResult(name=name)

    def get_current_url(self) -> str:
        return ""


async def _capture_create_kwargs(model: str) -> dict:
    """Drive ``iter_turns`` for one turn and return the kwargs passed
    to ``client.beta.messages.create``."""
    from backend.engine import ClaudeCUClient

    captured: dict = {}

    class FakeResponse:
        stop_reason = "end_turn"
        content: list = []

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return FakeResponse()

    class FakeMessages:
        create = staticmethod(fake_create)

    class FakeBeta:
        messages = FakeMessages()

    class FakeClient:
        beta = FakeBeta()

    with patch("anthropic.AsyncAnthropic") as AA:
        AA.return_value = FakeClient()
        client = ClaudeCUClient(api_key="k", model=model)
        async for _ in client.iter_turns(
            "noop", _FakeExecutor(screenshot=_minimal_png()), turn_limit=1
        ):
            pass

    return captured


# ---------------------------------------------------------------------------
# Tool version + beta header
# ---------------------------------------------------------------------------


class TestSonnet46ToolRouting:
    @pytest.mark.asyncio
    async def test_sonnet46_uses_new_tool_version_and_beta(self):
        """Sonnet 4.6 must send ``computer_20251124`` +
        ``computer-use-2025-11-24``, never the deprecated
        ``computer-use-2025-01-24`` (HTTP 400 per
        anthropic-sdk-typescript#914)."""
        captured = await _capture_create_kwargs(SONNET_46)

        tools = captured["tools"]
        assert len(tools) == 1
        assert tools[0]["type"] == "computer_20251124"

        betas = list(captured["betas"])
        assert "computer-use-2025-11-24" in betas
        assert "computer-use-2025-01-24" not in betas

    @pytest.mark.asyncio
    async def test_sonnet46_enable_zoom_on_tool_def(self):
        """Zoom is a computer_20251124-era action — Sonnet 4.6 must
        advertise it alongside Opus 4.7."""
        captured = await _capture_create_kwargs(SONNET_46)
        assert captured["tools"][0].get("enable_zoom") is True

    @pytest.mark.asyncio
    async def test_sonnet46_uses_adaptive_thinking(self):
        """``computer_20251124`` rejects legacy
        ``enabled`` + ``budget_tokens``; Sonnet 4.6 must ride the
        adaptive branch (same as Opus 4.7)."""
        captured = await _capture_create_kwargs(SONNET_46)
        assert captured["thinking"] == {"type": "adaptive"}


# ---------------------------------------------------------------------------
# Prompt caching env flag parity
# ---------------------------------------------------------------------------


class TestSonnet46CachingEnvFlag:
    def test_sonnet46_caching_env_flag_adds_cache_control(self, monkeypatch):
        from backend.engine import ClaudeCUClient

        monkeypatch.setenv("CUA_CLAUDE_CACHING", "1")
        ClaudeCUClient._caching_logged = False

        with patch("anthropic.AsyncAnthropic"):
            client = ClaudeCUClient(api_key="k", model=SONNET_46)

        tools = client._build_tools(1280, 800)
        assert tools[0]["type"] == "computer_20251124"
        assert tools[0].get("cache_control") == {"type": "ephemeral"}

    def test_sonnet46_caching_disabled_by_default(self, monkeypatch):
        from backend.engine import ClaudeCUClient

        monkeypatch.delenv("CUA_CLAUDE_CACHING", raising=False)

        with patch("anthropic.AsyncAnthropic"):
            client = ClaudeCUClient(api_key="k", model=SONNET_46)

        tools = client._build_tools(1280, 800)
        assert "cache_control" not in tools[0]


# ---------------------------------------------------------------------------
# Zoom dispatch smoke
# ---------------------------------------------------------------------------


class TestSonnet46ZoomDispatch:
    @pytest.mark.asyncio
    async def test_sonnet46_zoom_action_works(self):
        """The ``zoom`` dispatch branch in ``_execute_claude_action``
        is shared across all ``computer_20251124`` models.  This is
        primarily a smoke test that no model-id gate filters Sonnet
        4.6 out of the zoom path."""
        pytest.importorskip("PIL")
        from backend.engine import ClaudeCUClient

        class FakeExecutor:
            screen_width = 1280
            screen_height = 800

            async def execute(self, name, args):
                region = args["region"]
                w = region[2] - region[0]
                h = region[3] - region[1]
                return CUActionResult(
                    name="zoom",
                    extra={"image_bytes": _real_png(w, h)},
                )

        with patch("anthropic.AsyncAnthropic"):
            client = ClaudeCUClient(api_key="k", model=SONNET_46)

        result = await client._execute_claude_action(
            {"action": "zoom", "region": [50, 50, 250, 150]},
            FakeExecutor(),
        )

        assert result.success is True
        assert result.name == "zoom"
        from PIL import Image
        assert Image.open(BytesIO(result.extra["image_bytes"])).size == (200, 100)


# ---------------------------------------------------------------------------
# allowed_models.json pin
# ---------------------------------------------------------------------------


class TestSonnet46AllowedModelsEntry:
    def test_sonnet46_policy_matches_tool_version_contract(self):
        import json
        from pathlib import Path

        data = json.loads(
            Path("backend/models/allowed_models.json").read_text(encoding="utf-8")
        )
        entry = next(
            m for m in data["models"] if m.get("model_id") == SONNET_46
        )
        assert entry["supports_computer_use"] is True
        assert entry["cu_tool_version"] == "computer_20251124"
        assert entry["cu_betas"] == ["computer-use-2025-11-24"]

