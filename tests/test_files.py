from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.infra.storage import FileStore


@pytest.mark.asyncio
async def test_gemini_rejects_attached_files_before_lookup():
    from backend.files import GEMINI_CU_FILE_REJECTION, validate_attached_files

    with pytest.raises(ValueError, match="Gemini File Search cannot be combined"):
        await validate_attached_files("google", ["f_missing"])

    assert "OpenAI and Anthropic" in GEMINI_CU_FILE_REJECTION


@pytest.mark.asyncio
async def test_openai_file_search_provisions_vector_store(monkeypatch, tmp_path):
    import backend.files as files

    file_store = FileStore(tmp_path)
    monkeypatch.setattr(files, "store", file_store)
    rec = await files.upload_file(filename="notes.txt", data=b"hello")

    uploads = []
    deletes = []

    class FakeVectorStoreFiles:
        async def upload_and_poll(self, *, vector_store_id, file):
            uploads.append((vector_store_id, file))

    class FakeVectorStores:
        files = FakeVectorStoreFiles()

        async def create(self, *, name):
            assert name.startswith("cua-session-")
            return SimpleNamespace(id="vs_test")

        async def delete(self, *, vector_store_id):
            deletes.append(vector_store_id)

    client = SimpleNamespace(vector_stores=FakeVectorStores())

    vector_store_id = await files.prepare_openai_file_search(client, [rec.file_id])
    await files.cleanup_openai_vector_store(client, vector_store_id)

    assert vector_store_id == "vs_test"
    assert uploads == [
        ("vs_test", ("notes.txt", b"hello", "text/plain")),
    ]
    assert deletes == ["vs_test"]


@pytest.mark.asyncio
async def test_anthropic_files_api_and_inline_text_are_distinct(monkeypatch, tmp_path):
    import backend.files as files

    file_store = FileStore(tmp_path)
    monkeypatch.setattr(files, "store", file_store)
    txt = await files.upload_file(filename="source.txt", data=b"document text")
    md = await files.upload_file(filename="guide.md", data=b"# Guide\n\nUse this.")

    uploaded = []

    class FakeFiles:
        async def upload(self, *, file):
            filename, fh, mime_type = file
            uploaded.append((filename, fh.read(), mime_type))
            return SimpleNamespace(id="file_uploaded")

    client = SimpleNamespace(beta=SimpleNamespace(files=FakeFiles()))
    document_blocks, inline_pairs = await files.prepare_anthropic_documents(
        client,
        [txt.file_id, md.file_id],
        file_cache={},
        inline_text_cache={},
    )

    assert uploaded == [("source.txt", b"document text", "text/plain")]
    assert document_blocks == [{
        "type": "document",
        "source": {"type": "file", "file_id": "file_uploaded"},
        "title": "source.txt",
    }]
    assert inline_pairs == [("guide.md", "# Guide\n\nUse this.")]
