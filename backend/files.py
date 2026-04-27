"""Provider-aware reference-file handling for Computer Use runs."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from backend.infra.storage import (
    MAX_FILE_BYTES,
    UploadedFile,
    extract_text,
    store,
)

GEMINI_CU_FILE_REJECTION = (
    "Reference files are supported for OpenAI and Anthropic computer-use "
    "sessions only; Gemini File Search cannot be combined with Computer Use."
)


async def upload_file(*, filename: str, data: bytes) -> UploadedFile:
    """Persist an uploaded reference file and return its opaque handle."""
    return await store.add(filename=filename, data=data)


async def delete_file(file_id: str) -> bool:
    """Delete a previously uploaded reference file."""
    return await store.delete(file_id)


async def close_store() -> None:
    """Close and wipe the process-wide upload store."""
    await store.close()


async def validate_attached_files(provider: str, file_ids: list[str] | None) -> list[str]:
    """Validate provider/file-id compatibility and return deduped ids."""
    if not file_ids:
        return []
    provider_key = str(provider or "").strip().lower()
    if provider_key in {"google", "gemini"}:
        raise ValueError(GEMINI_CU_FILE_REJECTION)

    seen: set[str] = set()
    deduped: list[str] = []
    for fid in file_ids:
        if not isinstance(fid, str) or not fid.startswith("f_"):
            raise ValueError(f"invalid file_id: {fid!r}")
        if fid in seen:
            raise ValueError(f"duplicate file_id: {fid}")
        seen.add(fid)
        if (await store.get(fid)) is None:
            raise ValueError(f"file_id {fid} is unknown or expired; re-upload it")
        deduped.append(fid)
    return deduped


async def resolve_uploaded_files(file_ids: list[str]) -> list[UploadedFile]:
    """Resolve local opaque file ids to upload records."""
    return await store.get_many(file_ids)


async def prepare_openai_file_search(
    client: Any,
    file_ids: list[str],
    *,
    on_log: Callable[[str, str], None] | None = None,
) -> str | None:
    """Create an OpenAI vector store and index uploaded files into it."""
    recs = await resolve_uploaded_files(file_ids)
    if not recs:
        if on_log:
            on_log("warning", "OpenAI file_search: no readable files; skipping")
        return None

    if on_log:
        on_log("info", f"OpenAI file_search: provisioning vector store for {len(recs)} file(s)")
    vs = await client.vector_stores.create(name=f"cua-session-{int(time.time())}")
    vector_store_id = vs.id

    for rec in recs:
        try:
            await client.vector_stores.files.upload_and_poll(
                vector_store_id=vector_store_id,
                file=(rec.filename, rec.read_bytes(), rec.mime_type),
            )
            if on_log:
                on_log(
                    "info",
                    f"OpenAI file_search: indexed {rec.filename} "
                    f"({rec.size_bytes} bytes)",
                )
        except Exception as exc:
            if on_log:
                on_log("error", f"OpenAI file_search: upload failed for {rec.filename}: {exc}")
            raise
    return vector_store_id


async def cleanup_openai_vector_store(
    client: Any,
    vector_store_id: str | None,
    *,
    on_log: Callable[[str, str], None] | None = None,
) -> None:
    """Best-effort cleanup for the per-run OpenAI vector store."""
    if not vector_store_id:
        return
    try:
        await client.vector_stores.delete(vector_store_id=vector_store_id)
    except Exception as exc:
        if on_log:
            on_log("warning", f"OpenAI file_search: vector store cleanup failed: {exc}")


async def prepare_anthropic_documents(
    client: Any,
    file_ids: list[str],
    *,
    file_cache: dict[str, str],
    inline_text_cache: dict[str, tuple[str, str]],
    on_log: Callable[[str, str], None] | None = None,
) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    """Resolve local uploads into Anthropic document blocks and inline text.

    PDFs and TXT files are uploaded to Anthropic's Files API and referenced
    as ``document`` content blocks. Markdown and DOCX are converted to
    inline text because they are not accepted as Anthropic document blocks
    in this app's Computer Use file contract.
    """
    if not file_ids:
        return [], []

    document_blocks: list[dict[str, Any]] = []
    inline_pairs: list[tuple[str, str]] = []

    for rec in await resolve_uploaded_files(file_ids):
        ext = rec.extension
        if ext in (".pdf", ".txt"):
            anthropic_id = file_cache.get(rec.file_id)
            if anthropic_id is None:
                anthropic_id = await upload_to_anthropic(client, rec, on_log=on_log)
                file_cache[rec.file_id] = anthropic_id
            document_blocks.append({
                "type": "document",
                "source": {"type": "file", "file_id": anthropic_id},
                "title": rec.filename,
            })
        elif ext in (".md", ".docx"):
            cached = inline_text_cache.get(rec.file_id)
            if cached is None:
                try:
                    cached = (rec.filename, extract_text(rec))
                except Exception as exc:
                    if on_log:
                        on_log("error", f"Claude inline-text extract failed for {rec.filename}: {exc}")
                    continue
                inline_text_cache[rec.file_id] = cached
            inline_pairs.append(cached)
        elif on_log:
            on_log("warning", f"Claude attached_file: unsupported extension {ext} for {rec.filename}")

    return document_blocks, inline_pairs


async def upload_to_anthropic(
    client: Any,
    rec: UploadedFile,
    *,
    on_log: Callable[[str, str], None] | None = None,
) -> str:
    """Upload one local record to Anthropic's Files API."""
    if on_log:
        on_log("info", f"Claude Files API upload: {rec.filename} ({rec.size_bytes} bytes)")

    def _open() -> Any:
        return open(rec.path, "rb")

    fh = await asyncio.to_thread(_open)
    try:
        result = await client.beta.files.upload(
            file=(rec.filename, fh, rec.mime_type),
        )
    finally:
        await asyncio.to_thread(fh.close)
    return result.id
