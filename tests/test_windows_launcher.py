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


def test_dashboard_waits_for_backend_health_before_opening(monkeypatch) -> None:
    probed: list[str] = []
    opened: list[str] = []

    def probe(url: str, *, timeout: float) -> _Response:
        probed.append(url)
        assert timeout == 1.0
        return _Response()

    monkeypatch.setattr(dev.urllib.request, "urlopen", probe)
    monkeypatch.setattr(dev.webbrowser, "open", lambda url: opened.append(url))

    assert dev.open_dashboard_when_ready(
        "http://127.0.0.1:8505",
        ready_url="http://127.0.0.1:8100/api/health",
    )
    assert probed == ["http://127.0.0.1:8100/api/health"]
    assert opened == ["http://127.0.0.1:8505"]


def test_dashboard_timeout_does_not_open_browser(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(dev.webbrowser, "open", lambda url: opened.append(url))

    assert not dev.open_dashboard_when_ready("http://127.0.0.1:3000", timeout=0)
    assert opened == []


def test_http_wait_retries_until_backend_is_ready(monkeypatch) -> None:
    attempts = 0

    class Process:
        def poll(self) -> None:
            return None

    def probe(_url: str, *, timeout: float) -> _Response:
        nonlocal attempts
        attempts += 1
        assert timeout == 1.0
        if attempts == 1:
            raise urllib.error.URLError("not ready")
        return _Response()

    monkeypatch.setattr(dev.urllib.request, "urlopen", probe)
    monkeypatch.setattr(dev.time, "sleep", lambda _seconds: None)

    dev._wait_for_http("http://127.0.0.1:8100/api/health", Process(), timeout=5.0)  # type: ignore[arg-type]
    assert attempts == 2


def test_service_exit_stops_sibling_and_compose(monkeypatch) -> None:
    class Process:
        def __init__(self, exit_code: int | None) -> None:
            self.exit_code = exit_code

        def poll(self) -> int | None:
            return self.exit_code

    backend = Process(0)
    frontend = Process(None)
    stopped: list[str] = []
    compose_down: list[bool] = []
    monkeypatch.setattr(dev, "_terminate_process", lambda _process, *, label: stopped.append(label))
    monkeypatch.setattr(dev, "_compose_down", lambda: compose_down.append(True))

    assert dev._watch_processes(backend, frontend) == 0
    assert stopped == ["frontend", "backend"]
    assert compose_down == [True]


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
    assert "Get-FileHash" not in setup
    assert "[Security.Cryptography.SHA256]::Create()" in setup
    assert "npm rebuild esbuild --foreground-scripts" in setup


def test_docker_build_context_keeps_required_readme() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "!README.md" in dockerignore


def test_vite_listens_on_the_same_address_the_launcher_opens() -> None:
    vite_config = (ROOT / "frontend" / "vite.config.ts").read_text(encoding="utf-8")

    assert "host: '127.0.0.1'" in vite_config


def test_windows_starts_vite_without_an_interactive_batch_wrapper() -> None:
    launcher = (ROOT / "dev.py").read_text(encoding="utf-8")

    assert 'FRONTEND_DIR / "node_modules" / "vite" / "bin" / "vite.js"' in launcher
