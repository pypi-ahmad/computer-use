from __future__ import annotations
# === merged from tests/test_sandbox_gpt54.py ===
"""Prompt S3 — OpenAI GPT-5.4 sandbox alignment regression tests.

Pins the OpenAI Computer Use guide requirements onto the shared
sandbox + adapter:

* XFCE4 (guide Option 1 WM).
* Lockscreen / screensaver conflicts removed at image-build time.
* S1's 1440x900 viewport default survives (OpenAI's reference
  Dockerfile hardcodes 1280x800, but the prose prefers 1440x900 /
  1600x900 for downscaled targets).
* Every ``computer_call_output`` item carries
  ``detail: "original"`` \u2014 never ``"high"`` or ``"low"``.
"""


import base64
from pathlib import Path

from backend.engine import _build_openai_computer_call_output


_DOCKERFILE = Path("docker/Dockerfile")
_OPENAI_ADAPTER = Path("backend/engine/openai.py")
_ENGINE_INIT = Path("backend/engine/__init__.py")


class TestSandboxDockerfile:
    def test_dockerfile_has_xfce4(self):
        """OpenAI CU guide Option 1 mandates XFCE4 as the WM."""
        text = _DOCKERFILE.read_text(encoding="utf-8")
        assert "xfce4" in text
        assert "xfce4-goodies" in text

    def test_dockerfile_removes_lockscreen_conflicts(self):
        """Light-locker / xfce4-screensaver / xfce4-power-manager steal
        focus from xdotool and must be removed at build time."""
        text = _DOCKERFILE.read_text(encoding="utf-8")
        # Single RUN line must remove all three; idempotent via ``|| true``.
        assert "apt-get remove" in text
        assert "light-locker" in text
        assert "xfce4-screensaver" in text
        assert "xfce4-power-manager" in text
        assert "|| true" in text

    def test_dockerfile_viewport_still_1440x900(self):
        """Regression guard: S1's shared viewport survives.  OpenAI's
        reference Dockerfile hardcodes 1280x800; we intentionally
        do not because the prose recommends 1440x900."""
        text = _DOCKERFILE.read_text(encoding="utf-8")
        assert "ENV SCREEN_WIDTH=1440" in text
        assert "ENV SCREEN_HEIGHT=900" in text
        assert "ENV WIDTH=1440" in text
        assert "ENV HEIGHT=900" in text
        # Negative: no 1280x800 hardcoded viewport.
        assert "1280x800" not in text
        assert "ENV SCREEN_WIDTH=1280" not in text


class TestGpt54AdapterScreenshotDetail:
    def test_gpt54_adapter_sends_detail_original(self):
        """``_build_openai_computer_call_output`` is the single
        code path that packs screenshots into ``computer_call_output``
        items for the Responses API.  It MUST stamp
        ``detail: \"original\"`` per the OpenAI CU guide."""
        b64 = base64.standard_b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).decode()
        item = _build_openai_computer_call_output(
            call_id="call_abc",
            screenshot_b64=b64,
        )
        assert item["type"] == "computer_call_output"
        assert item["call_id"] == "call_abc"
        output = item["output"]
        assert output["type"] == "computer_screenshot"
        assert output["detail"] == "original"
        assert output["image_url"].startswith("data:image/png;base64,")

    def test_gpt54_adapter_rejects_low_detail_fallback(self):
        """Static-analysis guard: the OpenAI adapter + its helpers must
        never ship ``detail: \"high\"`` or ``detail: \"low\"`` literals.
        The guide says: downscale bytes before sending and remap
        coordinates \u2014 do NOT fall back to ``low``."""
        for path in (_OPENAI_ADAPTER, _ENGINE_INIT):
            text = path.read_text(encoding="utf-8")
            # Look specifically for the JSON-shaped ``detail`` key with
            # a forbidden value.  Allow docstrings/comments to mention
            # the words \u2014 test_*.py strips them, this is a source scan
            # that only blocks actual dict literals.
            assert '"detail": "high"' not in text, (
                f"{path} must never send detail=\"high\" in computer_call_output"
            )
            assert '"detail": "low"' not in text, (
                f"{path} must never send detail=\"low\" in computer_call_output"
            )
            assert "'detail': 'high'" not in text
            assert "'detail': 'low'" not in text


class TestBrowserSecurityPosture:
    """OpenAI CU guide's browser hardening contract: when the agent
    spawns a Chromium-based browser, the process must carry
    ``--disable-extensions`` AND ``--disable-file-system`` AND run with
    a minimal env (no host-secret leakage via environment inheritance)."""

    def test_chrome_flags_include_disable_file_system(self):
        """Regression guard: ``--disable-file-system`` protects the
        sandbox if the renderer is compromised. It must stay in the
        production Chrome-flag list alongside ``--disable-extensions``."""
        # Import the docker agent_service as a standalone module so the
        # test runs without the container being on sys.path.
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "_gpt54_agent_service_check",
            Path(__file__).resolve().parents[2] / "docker" / "agent_service.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        assert "--disable-extensions" in mod._CHROME_FLAGS
        assert "--disable-file-system" in mod._CHROME_FLAGS
        assert "--no-default-browser-check" in mod._CHROME_FLAGS
        # Per-session profile dir marker — exact path may vary, but the
        # flag must be present so extensions can't mutate a shared profile.
        assert any(
            f.startswith("--user-data-dir=") for f in mod._CHROME_FLAGS
        ), "Chrome must launch with an explicit --user-data-dir"

    def test_browser_subprocess_uses_minimal_env(self):
        """The browser subprocess must NOT inherit the full host env
        (it would leak AGENT_SERVICE_TOKEN, operator API keys, etc.).
        ``_browser_minimal_env()`` is the single allowed source of
        env vars passed into ``subprocess.Popen`` for the browser."""
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "_gpt54_agent_service_env_check",
            Path(__file__).resolve().parents[2] / "docker" / "agent_service.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        env = mod._browser_minimal_env()
        # Whitelist: exactly these keys — nothing more.
        assert set(env.keys()) == {
            "DISPLAY", "HOME", "PATH", "LANG", "XDG_RUNTIME_DIR",
        }, (
            f"Browser env must be a tight whitelist, got keys: {set(env.keys())}"
        )
        # Must NOT contain any token / key material even if they exist
        # in the host env (belt-and-braces — the whitelist above already
        # excludes them, but this makes the intent explicit).
        for sensitive in (
            "AGENT_SERVICE_TOKEN", "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY", "GOOGLE_API_KEY", "CUA_WS_TOKEN",
        ):
            assert sensitive not in env

    def test_agent_service_source_does_not_leak_host_env_to_browser(self):
        """Source-scan regression guard: no ``env={**os.environ, ...}``
        on the browser subprocess. Any future refactor that re-introduces
        host-env inheritance into the browser launch path must update
        this test with an explicit justification."""
        from pathlib import Path
        text = (
            Path(__file__).resolve().parents[2] / "docker" / "agent_service.py"
        ).read_text(encoding="utf-8")
        # The forbidden pattern is ``env={**os.environ`` specifically on
        # the browser-launch Popen call. Allow it elsewhere (e.g. inside
        # tests or for non-browser subprocesses) by scoping the assertion
        # to the nearby browser-launch block.
        #
        # Simpler invariant: the dedicated helper function must be the
        # only thing passing env= to the browser Popen. This catches a
        # drive-by refactor that reaches for ``**os.environ`` again.
        assert "env=_browser_minimal_env()" in text, (
            "Browser subprocess must use _browser_minimal_env() "
            "(OpenAI CU guide: do not leak host env to the renderer)."
        )

