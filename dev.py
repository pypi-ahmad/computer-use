from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT / "frontend"
DEFAULT_BACKEND_PORT = 8100
DEFAULT_FRONTEND_PORT = 8505


def _info(message: str) -> None:
    print(f"[INFO] {message}")


def _error(message: str) -> int:
    print(f"[ERROR] {message}", file=sys.stderr)
    return 1


def _python_executable() -> str:
    candidates = [
        ROOT / ".venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _npm_executable() -> str:
    if os.name == "nt":
        return shutil.which("npm.cmd") or "npm.cmd"
    return shutil.which("npm") or "npm"


def _run_checked(command: list[str], *, cwd: Path | None = None) -> None:
    _info("Running: " + " ".join(command))
    subprocess.run(command, cwd=cwd or ROOT, check=True)


def open_dashboard_when_ready(
    url: str,
    *,
    ready_url: str | None = None,
    timeout: float = 90.0,
) -> bool:
    """Open the dashboard once its HTTP server responds."""
    probe_url = ready_url or url
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(probe_url, timeout=1.0):  # noqa: S310 - fixed loopback URL
                webbrowser.open(url)
                return True
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)
    _info(f"Dashboard did not become ready at {url}; open it manually when startup completes.")
    return False


def _wait_for_http(url: str, process: subprocess.Popen[str], *, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            raise RuntimeError(f"Backend exited with code {exit_code} before becoming ready.")
        try:
            with urllib.request.urlopen(url, timeout=1.0):  # noqa: S310 - fixed loopback URL
                return
        except (OSError, urllib.error.URLError):
            time.sleep(0.25)
    raise RuntimeError(f"Backend did not become ready at {url} within {timeout:g} seconds.")


def _dotenv_values() -> dict[str, str]:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return {}
    values: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            values[key] = value
    return values


def _env_int(name: str, default: int, *, dotenv: dict[str, str]) -> int:
    raw = os.getenv(name) or dotenv.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        _info(f"Ignoring invalid {name}={raw!r}; using {default}.")
        return default
    if not 1 <= value <= 65535:
        _info(f"Ignoring out-of-range {name}={value}; using {default}.")
        return default
    return value


def _local_address_matches_port(local_address: str, port: int) -> bool:
    return local_address.endswith(f":{port}")


def _listening_pids_for_port(port: int) -> set[int]:
    if os.name == "nt":
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            check=False,
        )
        pids: set[int] = set()
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) < 5:
                continue
            protocol, local_address, _remote_address, state, pid = parts[:5]
            if protocol.lower() != "tcp" or state.upper() != "LISTENING":
                continue
            if _local_address_matches_port(local_address, port):
                try:
                    pids.add(int(pid))
                except ValueError:
                    pass
        return pids

    lsof = shutil.which("lsof")
    if lsof:
        result = subprocess.run(
            [lsof, "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            check=False,
        )
        return {int(line) for line in result.stdout.splitlines() if line.strip().isdigit()}

    ss = shutil.which("ss")
    if ss:
        result = subprocess.run(
            [ss, "-ltnp", f"sport = :{port}"],
            capture_output=True,
            text=True,
            check=False,
        )
        return {int(match) for match in re.findall(r"pid=(\d+)", result.stdout)}

    return set()


def _process_command_line(pid: int) -> str:
    if os.name == "nt":
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:
            return ""
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        return " ".join(result.stdout.split())

    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        capture_output=True,
        text=True,
        check=False,
    )
    return " ".join(result.stdout.split())


def _terminate_pid(pid: int) -> None:
    if pid == os.getpid():
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.2)
    try:
        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    except ProcessLookupError:
        pass


def _clear_port(port: int) -> None:
    pids = _listening_pids_for_port(port)
    if not pids:
        return
    for pid in sorted(pids):
        command = _process_command_line(pid)
        command_hint = f" — {command[:160]}" if command else ""
        _info(f"Clearing port {port}: stopping pid {pid}{command_hint}")
        _terminate_pid(pid)

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not _listening_pids_for_port(port):
            return
        time.sleep(0.25)
    raise RuntimeError(f"Port {port} is still in use after cleanup.")


def _clear_dev_ports() -> None:
    dotenv = _dotenv_values()
    backend_port = _env_int("PORT", DEFAULT_BACKEND_PORT, dotenv=dotenv)
    frontend_port = DEFAULT_FRONTEND_PORT
    for port in sorted({backend_port, frontend_port}):
        _clear_port(port)


def _bootstrap() -> None:
    setup_script = ROOT / ("setup.bat" if os.name == "nt" else "setup.sh")
    if not setup_script.exists():
        raise FileNotFoundError(f"Setup script not found: {setup_script}")
    if os.name == "nt":
        command = ["cmd", "/c", str(setup_script), "--bootstrap-only"]
    else:
        command = ["bash", str(setup_script), "--bootstrap-only"]
    _run_checked(command)


def _compose_restart() -> None:
    _compose_down()
    # --wait blocks until the compose healthcheck (agent + noVNC) is
    # healthy. Without it the dashboard iframe hits /vnc/vnc.html at
    # 502 and never reloads.
    _run_checked(["docker", "compose", "up", "-d", "--wait", "--wait-timeout", "90"])


def _compose_down() -> None:
    _run_checked(["docker", "compose", "down"])


def _terminate_process(process: subprocess.Popen[str] | None, *, label: str) -> None:
    if process is None or process.poll() is not None:
        return
    _info(f"Stopping {label}...")
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.terminate()
        process.wait(timeout=10)
    except Exception:
        process.kill()
        process.wait(timeout=5)


def _spawn_services() -> tuple[subprocess.Popen[str], subprocess.Popen[str]]:
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")

    backend_command = [_python_executable(), "-m", "backend.main"]
    if os.name == "nt":
        vite_entrypoint = FRONTEND_DIR / "node_modules" / "vite" / "bin" / "vite.js"
        frontend_command = [shutil.which("node") or "node", str(vite_entrypoint)]
    else:
        frontend_command = [_npm_executable(), "run", "dev"]

    creation_flags = 0
    if os.name == "nt":
        creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    _info("Starting backend...")
    backend = subprocess.Popen(
        backend_command,
        cwd=ROOT,
        env=env,
        creationflags=creation_flags,
        text=True,
    )

    backend_port = _env_int("PORT", DEFAULT_BACKEND_PORT, dotenv=_dotenv_values())
    try:
        _wait_for_http(f"http://127.0.0.1:{backend_port}/api/health", backend)
    except Exception:
        _terminate_process(backend, label="backend")
        raise

    _info("Starting frontend...")
    frontend = subprocess.Popen(
        frontend_command,
        cwd=FRONTEND_DIR,
        env=env,
        creationflags=creation_flags,
        text=True,
    )
    return backend, frontend


def _watch_processes(backend: subprocess.Popen[str], frontend: subprocess.Popen[str]) -> int:
    processes = ((backend, "backend"), (frontend, "frontend"))
    try:
        while True:
            for process, label in processes:
                exit_code = process.poll()
                if exit_code is not None:
                    _info(f"{label.capitalize()} exited with code {exit_code}.")
                    return exit_code
            time.sleep(0.5)
    except KeyboardInterrupt:
        _info("Received Ctrl+C. Shutting down services...")
        return 0
    finally:
        _terminate_process(frontend, label="frontend")
        _terminate_process(backend, label="backend")
        try:
            _compose_down()
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            _info(f"Docker cleanup did not complete: {exc}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Restart the sandbox with docker compose and run the FastAPI backend "
            "plus Vite frontend from one command."
        ),
    )
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="Run setup.sh/setup.bat first for a first-time install before starting the stack.",
    )
    parser.add_argument(
        "--no-clear-ports",
        action="store_true",
        help="Do not stop existing listeners on the backend/frontend dev ports before starting.",
    )
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Open the dashboard in the default browser once Vite responds.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        if args.bootstrap:
            _bootstrap()
        if not args.no_clear_ports:
            _clear_dev_ports()
        _compose_restart()
        backend, frontend = _spawn_services()
        if args.open_browser:
            threading.Thread(
                target=open_dashboard_when_ready,
                args=(f"http://127.0.0.1:{DEFAULT_FRONTEND_PORT}",),
                name="dashboard-opener",
                daemon=True,
            ).start()
        return _watch_processes(backend, frontend)
    except subprocess.CalledProcessError as exc:
        return _error(f"Command failed with exit code {exc.returncode}: {' '.join(exc.cmd)}")
    except FileNotFoundError as exc:
        return _error(str(exc))
    except RuntimeError as exc:
        return _error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
