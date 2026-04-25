"""Hermetic adapter tests for the official provider-native web-search tools.

Asserts that each provider's ``_build_tools`` / ``_build_config`` emits
the exact tool shape documented by the provider as of April 2026 when
``use_builtin_search`` is enabled, and is a no-op otherwise.

Reference shapes:
    * OpenAI Responses API:  ``{"type": "web_search"}``
    * Anthropic Messages:    ``{"type": "web_search_20250305", "name": "web_search", "max_uses": N}``
    * Gemini GenerateContent: ``Tool(google_search=GoogleSearch())`` plus
      ``include_server_side_tool_invocations=True`` on the config.

All provider SDKs are mocked; no network calls are made.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------


class TestOpenAIWebSearch:
    """OpenAI Responses API web_search tool."""

    def _make(self, **kwargs):
        from backend.engine import OpenAICUClient
        with patch("openai.AsyncOpenAI"):
            return OpenAICUClient(api_key="k", model=kwargs.pop("model", "gpt-5.4"), **kwargs)

    def test_disabled_emits_only_computer_tool(self):
        client = self._make(use_builtin_search=False)
        tools = client._build_tools(1440, 900)
        assert tools == [{"type": "computer"}]

    def test_enabled_appends_web_search_tool(self):
        client = self._make(use_builtin_search=True)
        tools = client._build_tools(1440, 900)
        assert {"type": "computer"} in tools
        assert {"type": "web_search"} in tools

    def test_enabled_with_domain_filters(self):
        client = self._make(
            use_builtin_search=True,
            search_allowed_domains=["example.com"],
            search_blocked_domains=["bad.test"],
        )
        tools = client._build_tools(1440, 900)
        ws = next(t for t in tools if t.get("type") == "web_search")
        assert ws["filters"]["allowed_domains"] == ["example.com"]
        assert ws["filters"]["blocked_domains"] == ["bad.test"]

    def test_nano_with_search_skips_tool_and_logs(self):
        client = self._make(model="gpt-5.4-nano", use_builtin_search=True)
        logs: list[tuple[str, str]] = []
        tools = client._build_tools(1440, 900, on_log=lambda lvl, msg: logs.append((lvl, msg)))
        assert all(t.get("type") != "web_search" for t in tools)
        assert any("web_search" in m for _, m in logs)


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


class TestClaudeWebSearch:
    """Anthropic web_search_20250305 server tool."""

    def _make(self, **kwargs):
        from backend.engine import ClaudeCUClient
        with patch("anthropic.AsyncAnthropic"):
            return ClaudeCUClient(api_key="k", model=kwargs.pop("model", "claude-sonnet-4-6"), **kwargs)

    def test_disabled_emits_only_computer_tool(self):
        client = self._make(use_builtin_search=False)
        tools = client._build_tools(1440, 900)
        assert len(tools) == 1
        assert tools[0]["name"] == "computer"

    def test_enabled_appends_web_search_tool(self):
        client = self._make(use_builtin_search=True)
        tools = client._build_tools(1440, 900)
        ws = next(t for t in tools if t.get("name") == "web_search")
        assert ws["type"] == "web_search_20250305"
        assert ws["max_uses"] == 5  # default

    def test_enabled_respects_max_uses_and_allowed_domains(self):
        client = self._make(
            use_builtin_search=True,
            search_max_uses=10,
            search_allowed_domains=["docs.python.org"],
        )
        tools = client._build_tools(1440, 900)
        ws = next(t for t in tools if t.get("name") == "web_search")
        assert ws["max_uses"] == 10
        assert ws["allowed_domains"] == ["docs.python.org"]
        assert "blocked_domains" not in ws

    def test_blocked_domains_used_when_no_allowlist(self):
        client = self._make(
            use_builtin_search=True,
            search_blocked_domains=["bad.test"],
        )
        tools = client._build_tools(1440, 900)
        ws = next(t for t in tools if t.get("name") == "web_search")
        assert ws["blocked_domains"] == ["bad.test"]
        assert "allowed_domains" not in ws


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------


class TestGeminiGoogleSearch:
    """Gemini google_search grounding tool."""

    def _make(self, **kwargs):
        from backend.engine import GeminiCUClient
        with patch("google.genai.Client"):
            return GeminiCUClient(api_key="k", **kwargs)

    def test_disabled_emits_only_computer_use_tool(self):
        client = self._make(use_builtin_search=False)
        config = client._build_config()
        # Exactly one tool: computer_use
        assert len(config.tools) == 1
        assert config.tools[0].computer_use is not None

    def test_enabled_appends_google_search_tool(self):
        client = self._make(use_builtin_search=True)
        config = client._build_config()
        # Two tools, one of which has google_search set
        assert len(config.tools) == 2
        has_search = any(getattr(t, "google_search", None) is not None for t in config.tools)
        assert has_search, "google_search tool not present"

    def test_enabled_sets_include_server_side_tool_invocations(self):
        client = self._make(use_builtin_search=True)
        config = client._build_config()
        # The flag is required for combined tool execution per Gemini docs.
        # If the SDK supports the field, it must be True.
        if hasattr(config, "include_server_side_tool_invocations"):
            assert config.include_server_side_tool_invocations is True
