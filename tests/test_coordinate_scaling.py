"""Tests for coordinate scaling and denormalization."""

from __future__ import annotations

import math
import pytest

from backend.engine import (
    GEMINI_NORMALIZED_MAX,
    _CLAUDE_MAX_LONG_EDGE,
    _CLAUDE_MAX_PIXELS,
    denormalize_x,
    denormalize_y,
    get_claude_scale_factor,
    resize_screenshot_for_claude,
)


# ── Gemini denormalization ────────────────────────────────────────────────────

class TestDenormalize:
    """Gemini normalised coords → pixel conversion."""

    def test_zero_maps_to_zero(self):
        assert denormalize_x(0, 1440) == 0
        assert denormalize_y(0, 900) == 0

    def test_max_maps_to_screen_edge(self):
        # 999 / 1000 * 1440 → ~1438
        assert denormalize_x(999, 1440) > 1400
        assert denormalize_y(999, 900) > 890

    def test_mid_point(self):
        x = denormalize_x(500, 1440)
        y = denormalize_y(500, 900)
        assert 690 <= x <= 730  # ~ 720
        assert 440 <= y <= 460  # ~ 450

    def test_custom_screen_size(self):
        assert denormalize_x(500, 1920) == int(500 / GEMINI_NORMALIZED_MAX * 1920)
        assert denormalize_y(500, 1080) == int(500 / GEMINI_NORMALIZED_MAX * 1080)


# ── Claude screenshot scaling ─────────────────────────────────────────────────

class TestClaudeScaleFactor:
    """Scaling factor computation per Anthropic docs."""

    def test_small_screen_no_scaling(self):
        """Screens that fit within both thresholds → scale = 1.0."""
        assert get_claude_scale_factor(800, 600) == 1.0

    def test_1440x900_requires_scaling(self):
        """Default 1440×900 (1.296M pixels) exceeds pixel threshold."""
        scale = get_claude_scale_factor(1440, 900)
        assert scale < 1.0
        # Verify resulting pixels are within limits
        new_w, new_h = int(1440 * scale), int(900 * scale)
        assert new_w * new_h <= _CLAUDE_MAX_PIXELS * 1.01  # small rounding tolerance
        assert max(new_w, new_h) <= _CLAUDE_MAX_LONG_EDGE

    def test_4k_requires_scaling(self):
        """3840×2160 exceeds both edge and pixel limits."""
        scale = get_claude_scale_factor(3840, 2160)
        assert scale < 0.5

    def test_exactly_at_limits(self):
        """Screen exactly at max long edge and pixel limit."""
        w = _CLAUDE_MAX_LONG_EDGE
        h = _CLAUDE_MAX_PIXELS // _CLAUDE_MAX_LONG_EDGE
        scale = get_claude_scale_factor(w, h)
        assert scale <= 1.0

    def test_scale_formula(self):
        """Verify the scale factor matches the documented formula."""
        w, h = 1440, 900
        expected = min(
            1.0,
            _CLAUDE_MAX_LONG_EDGE / max(w, h),
            math.sqrt(_CLAUDE_MAX_PIXELS / (w * h)),
        )
        assert get_claude_scale_factor(w, h) == pytest.approx(expected)

    def test_modern_tool_version_uses_high_res_budget_even_without_model_match(self):
        scale = get_claude_scale_factor(
            1920,
            1200,
            "custom-claude-build",
            tool_version="computer_20251124",
        )
        assert scale == 1.0


class TestResizeScreenshot:
    """Screenshot resize via Pillow."""

    def _make_png(self, w: int, h: int) -> bytes:
        """Create a minimal PNG of given size using Pillow."""
        from PIL import Image
        import io
        img = Image.new("RGB", (w, h), color=(128, 128, 128))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_no_resize_when_scale_gte_1(self):
        png = self._make_png(800, 600)
        result, rw, rh = resize_screenshot_for_claude(png, 1.0)
        assert rw == 800 and rh == 600

    def test_resize_scales_down(self):
        png = self._make_png(1440, 900)
        scale = get_claude_scale_factor(1440, 900)
        result, rw, rh = resize_screenshot_for_claude(png, scale)
        assert rw == int(1440 * scale)
        assert rh == int(900 * scale)
        assert len(result) > 0

    def test_resize_produces_valid_png(self):
        png = self._make_png(1440, 900)
        scale = get_claude_scale_factor(1440, 900)
        result, rw, rh = resize_screenshot_for_claude(png, scale)
        # PNG header: 8-byte signature
        assert result[:4] == b'\x89PNG'
