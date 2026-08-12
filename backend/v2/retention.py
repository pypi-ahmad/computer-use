"""Bounded audit-frame retention with age and byte-budget eviction."""
from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path


class FrameRetentionStore:
    def __init__(self, root: str | Path, *, max_age_seconds: int = 7 * 86_400, max_bytes: int = 1_000_000_000) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_age_seconds = max_age_seconds
        self.max_bytes = max_bytes
        self._enabled: dict[str, bool] = {}
        self._lock = threading.RLock()

    def set_enabled(self, session_id: str, enabled: bool) -> None:
        with self._lock:
            self._enabled[session_id] = enabled

    def is_enabled(self, session_id: str) -> bool:
        with self._lock:
            return self._enabled.get(session_id, True)

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._enabled.pop(session_id, None)

    def put(self, session_id: str, payload: bytes, extension: str) -> tuple[str, Path]:
        if not self.is_enabled(session_id):
            return hashlib.sha256(payload).hexdigest(), self.root
        digest = hashlib.sha256(payload).hexdigest()
        safe_session = "".join(char for char in session_id if char.isalnum() or char in "-_")[:128]
        if not safe_session:
            raise ValueError("Invalid session id")
        directory = self.root / safe_session
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{digest}.{extension.lstrip('.')}"
        path.write_bytes(payload)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        self.evict()
        return digest, path

    def evict(self, *, now: float | None = None) -> list[Path]:
        current = time.time() if now is None else now
        files = sorted((path for path in self.root.rglob("*") if path.is_file()), key=lambda path: path.stat().st_mtime)
        removed: list[Path] = []
        total = sum(path.stat().st_size for path in files)
        for path in files:
            stat = path.stat()
            if current - stat.st_mtime > self.max_age_seconds or total > self.max_bytes:
                total -= stat.st_size
                path.unlink(missing_ok=True)
                removed.append(path)
        return removed

    def purge_session(self, session_id: str) -> int:
        safe_session = "".join(char for char in session_id if char.isalnum() or char in "-_")[:128]
        directory = self.root / safe_session
        if not directory.exists() or not directory.is_dir():
            return 0
        removed = 0
        for path in directory.iterdir():
            if path.is_file():
                path.unlink()
                removed += 1
        directory.rmdir()
        return removed

    def session_files(self, session_id: str) -> list[Path]:
        safe_session = "".join(char for char in session_id if char.isalnum() or char in "-_")[:128]
        directory = self.root / safe_session
        if not directory.is_dir():
            return []
        return sorted(path for path in directory.iterdir() if path.is_file())

    def preview(self, *, now: float | None = None) -> dict[str, int]:
        current = time.time() if now is None else now
        files = [path for path in self.root.rglob("*") if path.is_file()]
        total_bytes = sum(path.stat().st_size for path in files)
        expired = [path for path in files if current - path.stat().st_mtime > self.max_age_seconds]
        return {
            "fileCount": len(files),
            "totalBytes": total_bytes,
            "expiredFileCount": len(expired),
            "expiredBytes": sum(path.stat().st_size for path in expired),
            "maxBytes": self.max_bytes,
            "maxAgeSeconds": self.max_age_seconds,
        }


frame_retention = FrameRetentionStore(os.getenv("CUA_V2_FRAME_PATH", "data/audit-frames"))
