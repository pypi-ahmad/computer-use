from __future__ import annotations
"""Server-side file storage for the file_search / RAG flow.

Clients can POST files to ``/api/files/upload`` *before* starting an
agent run. Each upload is persisted to a per-process temp directory
keyed by a server-generated id. The id is then passed back to the
caller, included in the ``StartTaskRequest.attached_files`` list, and
ultimately handed to the engine adapters which forward the bytes to the
appropriate provider-side store:

* OpenAI:    ``vector_stores`` + ``vector_stores.files`` (Responses ``file_search`` tool)
* Gemini:    ``file_search_stores`` + one-shot RAG pre-step (Google's
    File Search docs forbid sharing ``file_search`` and ``computer_use``
    in the same call)
* Anthropic: ``beta.files.upload`` (``document`` / inline-text content blocks)

The activation rule is provider-agnostic: when ``attached_files`` is
empty, no provider-side store, document block, or RAG pre-step is
created — the agent runs in its normal flow.

Constraints (per the user-facing contract):
* Allowed extensions: ``.md`` ``.txt`` ``.pdf`` ``.docx``
* Max files per session: 10
* Max bytes per file: 1 GB
* Max bytes total per session: 1 GB (cap defended at upload boundary;
  per-provider API caps may be tighter — Gemini 100 MB/file,
  Anthropic 500 MB/file — and surface as upstream API errors).

The store lives entirely in memory (metadata) + a temp directory
(bytes).  An idle GC thread sweeps entries older than 6 hours so a
crashed frontend can't leak disk forever.
"""


import asyncio
import logging
import os
import secrets
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Limits ────────────────────────────────────────────────────────────────────
ALLOWED_EXTS: frozenset[str] = frozenset({".md", ".txt", ".pdf", ".docx"})
MAX_FILE_BYTES: int = 1 * 1024 * 1024 * 1024  # 1 GB per file
MAX_FILES_PER_SESSION: int = 10
MAX_TOTAL_BYTES: int = 1 * 1024 * 1024 * 1024  # 1 GB combined per session

# How long an upload survives if no agent run picks it up.  Long enough
# to absorb a slow user, short enough that abandoned uploads can't pile
# up indefinitely.
UPLOAD_TTL_SECONDS: int = 6 * 60 * 60  # 6 hours

# Magic bytes for runtime sniffing.  We trust the extension primarily,
# but cross-check the first few bytes so a renamed binary can't bypass
# the allowlist.
_MAGIC_PDF = b"%PDF-"
_MAGIC_DOCX = b"PK\x03\x04"  # docx is a zip


@dataclass
class UploadedFile:
    """One server-side upload.  Bytes live on disk at ``path``."""

    file_id: str
    filename: str
    extension: str
    mime_type: str
    size_bytes: int
    created_at: float = field(default_factory=time.time)
    path: Path = field(default_factory=Path)

    def read_bytes(self) -> bytes:
        """Load the persisted bytes back from disk."""
        return self.path.read_bytes()


def _mime_for(ext: str) -> str:
    """Return the MIME type expected by the provider APIs for *ext*."""
    return {
        ".md":   "text/markdown",
        ".txt":  "text/plain",
        ".pdf":  "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }[ext]


def _validate_magic(ext: str, head: bytes) -> bool:
    """Cross-check magic bytes for binary formats."""
    if ext == ".pdf":
        return head.startswith(_MAGIC_PDF)
    if ext == ".docx":
        return head.startswith(_MAGIC_DOCX)
    # .md / .txt — accept any bytes; encoding is verified at extract time.
    return True


class FileStore:
    """Process-wide in-memory registry of uploads, persisted to disk."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self._root = Path(root) if root else Path(tempfile.gettempdir()) / "cua-uploads"
        self._root.mkdir(parents=True, exist_ok=True)
        self._files: dict[str, UploadedFile] = {}
        self._lock = asyncio.Lock()

    # ── Lifecycle ─────────────────────────────────────────────────────
    async def add(
        self,
        *,
        filename: str,
        data: bytes,
    ) -> UploadedFile:
        """Persist *data* as a new uploaded file.

        Raises:
            ValueError: extension/size/magic validation failure.
        """
        if not filename or len(filename) > 255:
            raise ValueError("filename must be 1-255 characters")
        # Reject path-traversal attempts; we only consume the basename.
        if any(c in filename for c in ('\x00', '/', '\\')):
            raise ValueError("filename contains forbidden characters")

        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXTS:
            raise ValueError(
                f"unsupported extension {ext!r}; allowed: "
                + ", ".join(sorted(ALLOWED_EXTS))
            )

        size = len(data)
        if size <= 0:
            raise ValueError("file is empty")
        if size > MAX_FILE_BYTES:
            raise ValueError(
                f"file exceeds {MAX_FILE_BYTES} bytes "
                f"({size / (1024 * 1024):.1f} MB > 1 GB)"
            )
        if not _validate_magic(ext, data[:8]):
            raise ValueError(f"file content does not match {ext} format")

        file_id = "f_" + secrets.token_urlsafe(18)
        path = self._root / file_id
        path.write_bytes(data)
        rec = UploadedFile(
            file_id=file_id,
            filename=Path(filename).name,
            extension=ext,
            mime_type=_mime_for(ext),
            size_bytes=size,
            path=path,
        )
        async with self._lock:
            self._files[file_id] = rec
        return rec

    async def get(self, file_id: str) -> Optional[UploadedFile]:
        """Return the metadata for *file_id* or ``None`` if unknown."""
        async with self._lock:
            return self._files.get(file_id)

    async def get_many(self, file_ids: list[str]) -> list[UploadedFile]:
        """Return metadata for every id in *file_ids* (skipping unknowns)."""
        async with self._lock:
            return [self._files[fid] for fid in file_ids if fid in self._files]

    async def delete(self, file_id: str) -> bool:
        """Remove an upload from disk + registry.  Returns True on success."""
        async with self._lock:
            rec = self._files.pop(file_id, None)
        if rec is None:
            return False
        try:
            rec.path.unlink(missing_ok=True)
        except OSError:
            logger.warning("failed to delete %s", rec.path)
        return True

    async def list_for_session(self, file_ids: list[str]) -> list[dict]:
        """Return JSON-friendly summaries for the given ids."""
        recs = await self.get_many(file_ids)
        return [
            {
                "file_id": r.file_id,
                "filename": r.filename,
                "size_bytes": r.size_bytes,
                "mime_type": r.mime_type,
            }
            for r in recs
        ]

    async def gc(self, *, now: Optional[float] = None) -> int:
        """Sweep uploads older than ``UPLOAD_TTL_SECONDS``.  Returns count removed."""
        cutoff = (now or time.time()) - UPLOAD_TTL_SECONDS
        async with self._lock:
            stale = [fid for fid, r in self._files.items() if r.created_at < cutoff]
            for fid in stale:
                rec = self._files.pop(fid, None)
                if rec is not None:
                    try:
                        rec.path.unlink(missing_ok=True)
                    except OSError:
                        pass
        return len(stale)

    async def close(self) -> None:
        """Wipe the entire upload root.  Wired into FastAPI shutdown."""
        async with self._lock:
            self._files.clear()
        try:
            shutil.rmtree(self._root, ignore_errors=True)
        except Exception:
            logger.debug("FileStore close: rmtree failed for %s", self._root)


# ── Process-wide singleton ────────────────────────────────────────────
_DEFAULT_ROOT = os.environ.get("CUA_UPLOAD_DIR")
store = FileStore(Path(_DEFAULT_ROOT) if _DEFAULT_ROOT else None)


# ── Text extraction helpers (used by the Claude adapter) ──────────────
def extract_text(rec: UploadedFile) -> str:
    """Return the file's textual content for inline injection.

    Used only by the Anthropic adapter for ``.md`` / ``.docx`` (Anthropic's
    Files API does not support these as document blocks per April 2026 docs:
    https://platform.claude.com/docs/en/build-with-claude/files).

    For ``.txt`` and ``.md`` the content is read as UTF-8 (with a lossy
    fallback for malformed bytes).  For ``.docx`` we use ``python-docx``
    to extract paragraph text.  ``.pdf`` extraction is **not** done here
    — PDFs round-trip through the Files API as native ``document`` blocks
    so Claude can do its own OCR/parsing.
    """
    ext = rec.extension
    if ext in (".txt", ".md"):
        try:
            return rec.path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return rec.path.read_text(encoding="utf-8", errors="replace")
    if ext == ".docx":
        try:
            from docx import Document  # python-docx
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "python-docx is required for .docx extraction. "
                "Install with: pip install python-docx"
            ) from exc
        doc = Document(str(rec.path))
        return "\n\n".join(p.text for p in doc.paragraphs if p.text)
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "pypdf is required for .pdf extraction. Install with: pip install pypdf"
            ) from exc
        reader = PdfReader(str(rec.path))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    raise ValueError(f"cannot extract text from {ext}")
