"""Grounding helpers shared by provider adapters and the grounding subgraph."""

from __future__ import annotations

import re
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


def _normalize_source_pairs(pairs: list[tuple[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    normalized: list[dict[str, str]] = []
    for title, url in pairs:
        clean_url = str(url or "").strip()
        if not clean_url:
            continue
        clean_title = str(title or clean_url).strip() or clean_url
        key = (clean_title, clean_url)
        if key in seen:
            continue
        seen.add(key)
        normalized.append({"title": clean_title, "url": clean_url})
    return normalized


def _split_grounding_facts(text: str) -> list[str]:
    stripped = str(text or "").strip()
    if not stripped:
        return []
    facts: list[str] = []
    for line in stripped.splitlines():
        candidate = line.strip().lstrip("-* ").strip()
        if candidate:
            facts.append(candidate)
    if not facts:
        for chunk in re.split(r"(?<=[.!?])\s+", stripped):
            candidate = chunk.strip()
            if candidate:
                facts.append(candidate)
    deduped: list[str] = []
    seen: set[str] = set()
    for fact in facts:
        if fact in seen:
            continue
        seen.add(fact)
        deduped.append(fact)
        if len(deduped) >= 5:
            break
    return deduped


def _grounding_confidence(text: str, sources: list[dict[str, str]]) -> str:
    lowered = str(text or "").lower()
    if not sources:
        return "low"
    if any(marker in lowered for marker in (
        "not sure",
        "unclear",
        "could not",
        "unable to",
        "did not find",
        "unknown",
        "maybe",
    )):
        return "low"
    if len(sources) >= 2 and len(str(text or "").strip()) >= 40:
        return "high"
    return "medium"


def _extract_openai_grounding_result(response: Any) -> dict[str, Any]:
    output_items = list(getattr(response, "output", []) or [])
    plain_items = [
        item if isinstance(item, dict) else _to_plain_dict(item)
        for item in output_items
    ]
    text = str(getattr(response, "output_text", "") or "").strip()
    source_pairs: list[tuple[str, str]] = []
    for item in plain_items:
        if item.get("type") == "message":
            for part in item.get("content", []) or []:
                if not isinstance(part, dict):
                    continue
                if not text and part.get("type") in {"output_text", "text"} and part.get("text"):
                    text = str(part.get("text") or "").strip()
                for ann in part.get("annotations", []) or []:
                    if not isinstance(ann, dict) or ann.get("type") != "url_citation":
                        continue
                    url = str(ann.get("url") or "").strip()
                    if url:
                        source_pairs.append((str(ann.get("title") or url), url))
        elif item.get("type") == "web_search_call":
            action = item.get("action") or {}
            if not isinstance(action, dict):
                action = _to_plain_dict(action)
            for src in action.get("sources", []) or []:
                if not isinstance(src, dict):
                    continue
                url = str(src.get("url") or "").strip()
                if url:
                    source_pairs.append((str(src.get("title") or url), url))
    return {
        "text": text,
        "sources": _normalize_source_pairs(source_pairs),
        "raw_response": {"output": plain_items},
    }


def _extract_claude_grounding_result(response: Any) -> dict[str, Any]:
    content = list(getattr(response, "content", []) or [])
    plain_blocks = [
        block if isinstance(block, dict) else _to_plain_dict(block)
        for block in content
    ]
    text_parts: list[str] = []
    source_pairs: list[tuple[str, str]] = []
    for block in plain_blocks:
        block_text = str(block.get("text") or "").strip()
        if block_text:
            text_parts.append(block_text)
        for citation in block.get("citations", []) or []:
            if not isinstance(citation, dict):
                continue
            url = str(citation.get("url") or "").strip()
            if url:
                source_pairs.append((str(citation.get("title") or url), url))
        if block.get("type") == "web_search_tool_result":
            for result in block.get("content", []) or []:
                if not isinstance(result, dict):
                    continue
                url = str(result.get("url") or "").strip()
                if url:
                    source_pairs.append((str(result.get("title") or url), url))
    return {
        "text": " ".join(text_parts).strip(),
        "sources": _normalize_source_pairs(source_pairs),
        "raw_response": {
            "content": plain_blocks,
            "stop_reason": getattr(response, "stop_reason", None),
        },
    }


def _extract_gemini_grounding_result(response: Any) -> dict[str, Any]:
    candidates = getattr(response, "candidates", None) or []
    candidate = candidates[0] if candidates else None
    candidate_plain = _to_plain_dict(candidate) if candidate is not None else {}
    content = candidate_plain.get("content") or {}
    if not isinstance(content, dict):
        content = _to_plain_dict(content)
    text_parts: list[str] = []
    for part in content.get("parts", []) or []:
        if not isinstance(part, dict):
            continue
        part_text = str(part.get("text") or "").strip()
        if part_text:
            text_parts.append(part_text)

    grounding_payload = _extract_gemini_grounding_payload(response)
    source_pairs: list[tuple[str, str]] = []
    if grounding_payload:
        for chunk in grounding_payload.get("groundingChunks", []) or []:
            web = chunk.get("web") or {}
            if not isinstance(web, dict):
                continue
            url = str(web.get("uri") or "").strip()
            if url:
                source_pairs.append((str(web.get("title") or url), url))

    return {
        "text": " ".join(text_parts).strip(),
        "sources": _normalize_source_pairs(source_pairs),
        "raw_response": {
            "candidate": candidate_plain,
            "grounding_payload": grounding_payload,
        },
    }


def _build_grounding_evidence_entry(
    *,
    provider: str,
    subgoal: str,
    plan_summary: str,
    query: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    text = str(result.get("text") or "").strip()
    sources = [
        source
        for source in result.get("sources") or []
        if isinstance(source, dict) and str(source.get("url") or "").strip()
    ]
    return {
        "kind": "grounding",
        "provider": str(provider or "").strip(),
        "subgoal": str(subgoal or "").strip(),
        "plan_summary": str(plan_summary or "").strip(),
        "query": str(query or "").strip(),
        "summary": text,
        "facts": _split_grounding_facts(text),
        "sources": sources,
        "confidence": _grounding_confidence(text, sources),
        "raw_response": _to_plain_dict(result.get("raw_response")),
    }