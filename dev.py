from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT / "frontend"


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
    _run_checked(["docker", "compose", "down"])
    _run_checked(["docker", "compose", "up", "-d"])


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
    )

    _info("Starting frontend...")
    frontend = subprocess.Popen(
        frontend_command,
        cwd=FRONTEND_DIR,
        env=env,
        creationflags=creation_flags,
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
                    other = frontend if process is backend else backend
                    other_label = "frontend" if process is backend else "backend"
                    _terminate_process(other, label=other_label)
                    return exit_code
            time.sleep(0.5)
    except KeyboardInterrupt:
        _info("Received Ctrl+C. Shutting down services...")
        return 0
    finally:
        _terminate_process(frontend, label="frontend")
        _terminate_process(backend, label="backend")


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
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        if args.bootstrap:
            _bootstrap()
        _compose_restart()
        backend, frontend = _spawn_services()
        return _watch_processes(backend, frontend)
    except subprocess.CalledProcessError as exc:
        return _error(f"Command failed with exit code {exc.returncode}: {' '.join(exc.cmd)}")
    except FileNotFoundError as exc:
        return _error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())