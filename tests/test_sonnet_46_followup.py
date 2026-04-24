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

from __future__ import annotations

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
            Path("backend/allowed_models.json").read_text(encoding="utf-8")
        )
        entry = next(
            m for m in data["models"] if m.get("model_id") == SONNET_46
        )
        assert entry["supports_computer_use"] is True
        assert entry["cu_tool_version"] == "computer_20251124"
        assert entry["cu_betas"] == ["computer-use-2025-11-24"]
