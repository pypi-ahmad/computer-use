from __future__ import annotations

import urllib.error
from pathlib import Path

import dev

ROOT = Path(__file__).resolve().parents[1]


class _Response:
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_dashboard_opens_after_it_becomes_ready(monkeypatch) -> None:
    attempts = 0
    opened: list[str] = []

    def probe(_url: str, *, timeout: float) -> _Response:
        nonlocal attempts
        attempts += 1
        assert timeout == 1.0
        if attempts == 1:
            raise urllib.error.URLError("not ready")
        return _Response()

    monkeypatch.setattr(dev.urllib.request, "urlopen", probe)
    monkeypatch.setattr(dev.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(dev.time, "sleep", lambda _seconds: None)

    assert dev.open_dashboard_when_ready("http://127.0.0.1:3000", timeout=5.0)
    assert attempts == 2
    assert opened == ["http://127.0.0.1:3000"]


def test_dashboard_timeout_does_not_open_browser(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(dev.webbrowser, "open", lambda url: opened.append(url))

    assert not dev.open_dashboard_when_ready("http://127.0.0.1:3000", timeout=0)
    assert opened == []


def test_start_file_is_the_single_double_click_entrypoint() -> None:
    launcher = (ROOT / "START.bat").read_text(encoding="utf-8")

    assert 'call "%~dp0setup.bat" --bootstrap-only' in launcher
    assert 'call "%~dp0dev.bat" --open-browser' in launcher
    assert "pause" in launcher.lower()


def test_setup_uses_exact_installers_and_secure_local_defaults() -> None:
    setup = (ROOT / "setup.bat").read_text(encoding="utf-8")

    for package in ("astral-sh.uv", "OpenJS.NodeJS.LTS", "Docker.DockerDesktop"):
        assert package in setup
    assert "--exact" in setup
    assert "RandomNumberGenerator" in setup
    assert "AGENT_SERVICE_TOKEN" in setup
    assert "VNC_PASSWORD" in setup
    assert "docker compose build --no-cache" in setup
    assert "docker compose build" in setup


def test_docker_build_context_keeps_required_readme() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "!README.md" in dockerignore
