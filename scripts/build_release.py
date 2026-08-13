"""Build reproducible release assets without publishing them."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist" / "release"
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
STAGE = DIST / f"computer-use-v{VERSION}"


def run(*command: str, cwd: Path = ROOT) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)


def main() -> int:
    run("npm", "ci", cwd=ROOT / "frontend")
    run("npm", "run", "build", cwd=ROOT / "frontend")
    if DIST.exists():
        shutil.rmtree(DIST)
    STAGE.mkdir(parents=True)

    for name in (
        "README.md",
        "TECHNICAL.md",
        "CHANGELOG.md",
        "LICENSE",
        "pyproject.toml",
        "uv.lock",
    ):
        copy(ROOT / name, STAGE / name)
    for name in (
        "deployment.md",
        "migration-v2.md",
        "rollback-v2.md",
        "release-notes-v3.0.2.md",
        "release-notes-v3.0.1.md",
        "release-notes-v3.0.0.md",
        "research-audit-2026-07-23.md",
    ):
        copy(ROOT / "docs" / name, STAGE / "docs" / name)
    copy(ROOT / "frontend" / "dist", STAGE / "frontend" / "dist")
    copy(ROOT / "backend", STAGE / "backend")
    copy(ROOT / "docker", STAGE / "docker")
    copy(ROOT / "docker-compose.yml", STAGE / "docker-compose.yml")
    for name in ("setup.bat", "setup.sh", "dev.bat", "dev.sh", "dev.py", ".env.example"):
        copy(ROOT / name, STAGE / name)
    copy(
        ROOT / "backend" / "models" / "computer_use_models.v2.json",
        STAGE / "schemas" / "computer_use_models.v2.json",
    )

    sys.path.insert(0, str(ROOT))
    from pydantic import TypeAdapter

    from backend.server import app
    from backend.server.ws_schema import WSEvent

    (STAGE / "schemas" / "openapi.v2.json").write_text(
        json.dumps(app.openapi(), indent=2, sort_keys=True), encoding="utf-8"
    )
    (STAGE / "schemas" / "websocket-events.v2.json").write_text(
        json.dumps(TypeAdapter(WSEvent).json_schema(), indent=2, sort_keys=True),
        encoding="utf-8",
    )

    archive = DIST / f"computer-use-v{VERSION}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in sorted(STAGE.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(DIST))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (DIST / "SHA256SUMS.txt").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    print(f"Created {archive.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
