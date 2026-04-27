"""Grounding helpers shared by provider adapters."""

from __future__ import annotations

from typing import Any


def _to_plain_dict(value: Any) -> dict[str, Any]:
    def _to_plain_value(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: _to_plain_value(val) for key, val in item.items()}
        if isinstance(item, list):
            return [_to_plain_value(val) for val in item]
        if isinstance(item, tuple):
            return [_to_plain_value(val) for val in item]
        if hasattr(item, "model_dump"):
            return _to_plain_value(item.model_dump())
        if hasattr(item, "dict"):
            return _to_plain_value(item.dict())
        if hasattr(item, "__dict__"):
            return {
                key: _to_plain_value(val)
                for key, val in vars(item).items()
                if not key.startswith("_")
            }
        return item

    plain = _to_plain_value(value)
    return plain if isinstance(plain, dict) else {}


def _extract_gemini_grounding_payload(response: Any) -> dict[str, Any] | None:
    """Normalize Gemini grounding metadata into the frontend payload shape."""
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return None

    candidate = _to_plain_dict(candidates[0])
    grounding = candidate.get("grounding_metadata") or candidate.get("groundingMetadata") or {}
    if not isinstance(grounding, dict):
        grounding = _to_plain_dict(grounding)
    if not grounding:
        return None

    search_entry = grounding.get("search_entry_point") or grounding.get("searchEntryPoint") or {}
    if not isinstance(search_entry, dict):
        search_entry = _to_plain_dict(search_entry)
    rendered_content = str(
        search_entry.get("renderedContent")
        or search_entry.get("rendered_content")
        or ""
    ).strip()

    normalized_chunks: list[dict[str, Any]] = []
    for raw_chunk in grounding.get("grounding_chunks") or grounding.get("groundingChunks") or []:
        chunk = raw_chunk if isinstance(raw_chunk, dict) else _to_plain_dict(raw_chunk)
        web = chunk.get("web") or {}
        if not isinstance(web, dict):
            web = _to_plain_dict(web)
        uri = str(web.get("uri") or "").strip()
        if not uri:
            continue
        title = str(web.get("title") or uri).strip() or uri
        normalized_chunks.append({"web": {"uri": uri, "title": title}})

    normalized_supports: list[dict[str, Any]] = []
    for raw_support in grounding.get("grounding_supports") or grounding.get("groundingSupports") or []:
        support = raw_support if isinstance(raw_support, dict) else _to_plain_dict(raw_support)
        segment = support.get("segment") or {}
        if not isinstance(segment, dict):
            segment = _to_plain_dict(segment)

        start_index = segment.get("startIndex")
        if start_index is None:
            start_index = segment.get("start_index")
        end_index = segment.get("endIndex")
        if end_index is None:
            end_index = segment.get("end_index")
        try:
            start_index = int(start_index) if start_index is not None else None
        except (TypeError, ValueError):
            start_index = None
        try:
            end_index = int(end_index) if end_index is not None else None
        except (TypeError, ValueError):
            end_index = None

        indices_raw = support.get("grounding_chunk_indices")
        if indices_raw is None:
            indices_raw = support.get("groundingChunkIndices")
        indices: list[int] = []
        for value in indices_raw or []:
            try:
                idx = int(value)
            except (TypeError, ValueError):
                continue
            if idx >= 0 and idx not in indices:
                indices.append(idx)

        segment_payload: dict[str, Any] = {}
        if start_index is not None:
            segment_payload["startIndex"] = start_index
        if end_index is not None:
            segment_payload["endIndex"] = end_index
        segment_text = str(segment.get("text") or "").strip()
        if segment_text:
            segment_payload["text"] = segment_text

        if segment_payload or indices:
            normalized_supports.append(
                {
                    "segment": segment_payload,
                    "groundingChunkIndices": indices,
                }
            )

    web_search_queries = [
        str(query).strip()
        for query in grounding.get("web_search_queries") or grounding.get("webSearchQueries") or []
        if str(query).strip()
    ]

    payload: dict[str, Any] = {}
    if rendered_content:
        payload["renderedContent"] = rendered_content
    if normalized_chunks:
        payload["groundingChunks"] = normalized_chunks
    if normalized_supports:
        payload["groundingSupports"] = normalized_supports
    if web_search_queries:
        payload["webSearchQueries"] = web_search_queries
    return payload or None
