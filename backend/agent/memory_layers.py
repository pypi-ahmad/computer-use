from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from backend.models.schemas import load_allowed_models_json

logger = logging.getLogger(__name__)

_MAX_MEMORY_TOKENS = 500
_DEFAULT_STORE_LIMIT = 10_000
_SUMMARY_KIND = "evidence_summary"
_MAX_MEMORY_ITEMS = 5

REUSABLE_UI_PATTERNS_NAMESPACE = ("default", "reusable_ui_patterns")
OPERATOR_PREFERENCES_NAMESPACE = ("default", "operator_preferences")
PRIOR_WORKFLOWS_NAMESPACE = ("default", "prior_successful_workflows")

EVIDENCE_SUMMARY_SYSTEM_PROMPT = """\
You compress older evidence for a desktop computer-use agent.

Return strict JSON only with these keys:
- summary: short factual recap of the evicted evidence
- key_points: list of the most reusable facts or UI observations
- source_urls: list of canonical source URLs if present

Do not invent facts. Preserve only information that could still help later planning,
execution, or recovery.
"""

REUSABLE_WORKFLOW_SYSTEM_PROMPT = """\
You decide whether a completed desktop workflow is reusable as long-term memory.

Return strict JSON only with these keys:
- reusable: boolean
- workflow_summary: short reusable summary when reusable is true, else empty string
- ui_patterns: list of reusable UI patterns or navigation cues
- operator_preferences: object of durable preferences worth remembering
- rationale: brief justification

Only mark reusable true when the completed workflow contains stable UI patterns,
repeatable navigation steps, or durable operator preferences that are likely to help later runs.
"""


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _namespace_root(operator_id: str | None = None) -> str:
    candidate = str(operator_id or os.getenv("CUA_OPERATOR_ID") or "default").strip()
    return candidate or "default"


def reusable_ui_patterns_namespace(operator_id: str | None = None) -> tuple[str, ...]:
    return (_namespace_root(operator_id), REUSABLE_UI_PATTERNS_NAMESPACE[-1])


def operator_preferences_namespace(operator_id: str | None = None) -> tuple[str, ...]:
    return (_namespace_root(operator_id), OPERATOR_PREFERENCES_NAMESPACE[-1])


def prior_workflows_namespace(operator_id: str | None = None) -> tuple[str, ...]:
    return (_namespace_root(operator_id), PRIOR_WORKFLOWS_NAMESPACE[-1])


def _tokenize(text: str) -> set[str]:
    return {
        token
        for token in re.split(r"[^a-z0-9]+", text.lower())
        if len(token) >= 4
    }


def _copy_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _extract_json_object(text: str) -> dict[str, Any]:
    candidate = str(text or "").strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.lower().startswith("json"):
            candidate = candidate[4:]
        candidate = candidate.strip()
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end < start:
            raise
        payload = json.loads(candidate[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("Memory helper output must be a JSON object")
    return payload


def _derived_evidence_id(entry: dict[str, Any]) -> str:
    payload = copy.deepcopy(entry)
    payload.pop("evidence_id", None)
    digest = hashlib.sha256(_json_dumps(payload).encode("utf-8")).hexdigest()[:16]
    return f"evidence-{digest}"


def normalize_evidence_entries(value: Any, *, assign_ids: bool = False) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            entry = copy.deepcopy(item)
        else:
            entry = {"kind": "note", "value": str(item)}
        evidence_id = str(entry.get("evidence_id") or "").strip()
        if assign_ids and not evidence_id:
            entry["evidence_id"] = _derived_evidence_id(entry)
        normalized.append(entry)
    return normalized


def planner_pinned_evidence_ids(active_plan: Any) -> set[str]:
    if not isinstance(active_plan, dict):
        return set()
    return {
        value
        for value in _copy_str_list(active_plan.get("pinned_evidence_ids"))
        if value
    }


def latest_evidence_summary(evidence: Any) -> dict[str, Any] | None:
    for item in reversed(normalize_evidence_entries(evidence)):
        if item.get("kind") == _SUMMARY_KIND:
            return item
    return None


def build_evidence_brief(evidence: Any, *, limit: int = 3) -> str:
    entries = normalize_evidence_entries(evidence)
    if not entries:
        return ""
    lines: list[str] = []
    summary = latest_evidence_summary(entries)
    if isinstance(summary, dict):
        summary_text = str(summary.get("summary") or "").strip()
        if summary_text:
            lines.append(f"Compressed earlier evidence: {summary_text}")
    for item in entries[-max(int(limit), 1):]:
        kind = str(item.get("kind") or "note").strip() or "note"
        text = str(
            item.get("summary")
            or item.get("value")
            or item.get("notes")
            or item.get("query")
            or item.get("subgoal")
            or ""
        ).strip()
        if text:
            lines.append(f"Recent {kind}: {text}")
    return "\n".join(lines)


def build_memory_context_brief(memory_context: Any, *, limit: int = 2) -> str:
    if not isinstance(memory_context, dict):
        return ""
    lines: list[str] = []
    preferences = memory_context.get("operator_preferences")
    if isinstance(preferences, list):
        for item in preferences[:1]:
            if not isinstance(item, dict):
                continue
            preferred_model = str(item.get("preferred_model") or "").strip()
            risk_level = str(item.get("risk_level") or "").strip()
            notes = _copy_str_list(item.get("notes"))
            parts = [part for part in [preferred_model, risk_level] if part]
            if notes:
                parts.extend(notes[:2])
            if parts:
                lines.append(f"Operator preferences: {'; '.join(parts)}")
    workflows = memory_context.get("prior_workflows")
    if isinstance(workflows, list):
        for item in workflows[:max(int(limit), 1)]:
            if not isinstance(item, dict):
                continue
            summary = str(item.get("workflow_summary") or item.get("summary") or "").strip()
            if summary:
                lines.append(f"Prior workflow: {summary}")
    patterns = memory_context.get("ui_patterns")
    if isinstance(patterns, list):
        pattern_text: list[str] = []
        for item in patterns[:max(int(limit), 1)]:
            if isinstance(item, dict):
                text = str(item.get("pattern") or item.get("summary") or "").strip()
            else:
                text = str(item or "").strip()
            if text:
                pattern_text.append(text)
        if pattern_text:
            lines.append(f"Reusable UI patterns: {'; '.join(pattern_text)}")
    return "\n".join(lines)


def _item_value(item: Any) -> dict[str, Any] | None:
    value = copy.deepcopy(getattr(item, "value", None))
    return value if isinstance(value, dict) else None


def _memory_score(goal: str, value: dict[str, Any], *, boost_field: str | None = None) -> tuple[int, int]:
    goal_tokens = _tokenize(goal)
    text = _json_dumps(value)
    value_tokens = _tokenize(text)
    overlap = len(goal_tokens & value_tokens)
    boost = 0
    if boost_field:
        candidate = str(value.get(boost_field) or "").strip().lower()
        if candidate and candidate in goal.lower():
            boost = 2
    return overlap + boost, len(text)


async def _search_memory_namespace(store: BaseStore, namespace: tuple[str, ...]) -> list[Any]:
    try:
        return await store.asearch(namespace, limit=_DEFAULT_STORE_LIMIT)
    except Exception:
        logger.debug("Store search failed for namespace %s", namespace, exc_info=True)
        return []


async def read_long_term_memory(store: BaseStore | None, state: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    empty = {
        "ui_patterns": [],
        "operator_preferences": [],
        "prior_workflows": [],
    }
    if store is None:
        return empty
    goal = str(state.get("goal") or state.get("task") or "").strip()
    ui_items, pref_items, workflow_items = await asyncio.gather(
        _search_memory_namespace(store, reusable_ui_patterns_namespace()),
        _search_memory_namespace(store, operator_preferences_namespace()),
        _search_memory_namespace(store, prior_workflows_namespace()),
    )

    def _select(items: list[Any], *, boost_field: str | None = None) -> list[dict[str, Any]]:
        scored: list[tuple[tuple[int, int], int, dict[str, Any]]] = []
        for idx, item in enumerate(items):
            value = _item_value(item)
            if value is None:
                continue
            scored.append((_memory_score(goal, value, boost_field=boost_field), idx, value))
        scored.sort(key=lambda item: (item[0][0], item[0][1], item[1]), reverse=True)
        return [copy.deepcopy(item[2]) for item in scored[:_MAX_MEMORY_ITEMS]]

    return {
        "ui_patterns": _select(ui_items, boost_field="pattern"),
        "operator_preferences": _select(pref_items),
        "prior_workflows": _select(workflow_items, boost_field="workflow_summary"),
    }


def _workflow_payload(state: dict[str, Any]) -> dict[str, Any]:
    active_plan = copy.deepcopy(state.get("active_plan") or {}) if isinstance(state.get("active_plan"), dict) else {}
    return {
        "goal": str(state.get("goal") or state.get("task") or ""),
        "provider": str(state.get("provider") or ""),
        "model": str(state.get("model") or ""),
        "risk_level": str(state.get("risk_level") or "low"),
        "verification_status": str(state.get("verification_status") or ""),
        "completion_criteria": _copy_str_list(state.get("completion_criteria")),
        "active_plan": active_plan,
        "evidence_brief": build_evidence_brief(state.get("evidence"), limit=3),
        "memory_context": copy.deepcopy(state.get("memory_context") or {}),
    }


async def assess_reusable_workflow(state: dict[str, Any]) -> dict[str, Any]:
    payload = _workflow_payload(state)
    try:
        response = await request_memory_json(
            state,
            system_prompt=REUSABLE_WORKFLOW_SYSTEM_PROMPT,
            user_prompt=(
                "Decide whether this completed workflow should be written to long-term memory. Return strict JSON only.\n\n"
                f"Context:\n{json.dumps(payload, indent=2, ensure_ascii=True)}"
            ),
        )
        return {
            "reusable": bool(response.get("reusable")),
            "workflow_summary": str(response.get("workflow_summary") or "").strip(),
            "ui_patterns": _copy_str_list(response.get("ui_patterns")),
            "operator_preferences": copy.deepcopy(response.get("operator_preferences") or {}),
            "rationale": str(response.get("rationale") or "").strip(),
        }
    except Exception:
        logger.debug("Reusable workflow assessment fell back to non-reusable", exc_info=True)
        return {
            "reusable": False,
            "workflow_summary": "",
            "ui_patterns": [],
            "operator_preferences": {},
            "rationale": "Memory assessment unavailable.",
        }


async def write_long_term_memory(store: BaseStore | None, state: dict[str, Any]) -> dict[str, Any]:
    result = {
        "reusable": False,
        "workflow_summary": "",
        "ui_patterns": [],
        "operator_preferences": {},
        "rationale": "",
        "writes": 0,
    }
    if store is None or str(state.get("verification_status") or "").strip().lower() != "complete":
        return result

    assessment = await assess_reusable_workflow(state)
    result.update(assessment)
    if not assessment.get("reusable"):
        return result

    workflow_value = {
        "goal": str(state.get("goal") or state.get("task") or ""),
        "workflow_summary": str(assessment.get("workflow_summary") or "").strip(),
        "steps": _copy_str_list(((state.get("active_plan") or {}) if isinstance(state.get("active_plan"), dict) else {}).get("steps")),
        "completion_criteria": _copy_str_list(state.get("completion_criteria")),
        "provider": str(state.get("provider") or ""),
        "model": str(state.get("model") or ""),
        "risk_level": str(state.get("risk_level") or "low"),
        "evidence_brief": build_evidence_brief(state.get("evidence"), limit=3),
        "rationale": str(assessment.get("rationale") or "").strip(),
    }
    await store.aput(prior_workflows_namespace(), f"workflow-{uuid4()}", workflow_value, index=False)
    writes = 1

    ui_patterns = _copy_str_list(assessment.get("ui_patterns"))
    for pattern in ui_patterns[:_MAX_MEMORY_ITEMS]:
        await store.aput(
            reusable_ui_patterns_namespace(),
            f"ui-{uuid4()}",
            {
                "pattern": pattern,
                "goal": workflow_value["goal"],
                "workflow_summary": workflow_value["workflow_summary"],
            },
            index=False,
        )
        writes += 1

    operator_preferences = assessment.get("operator_preferences")
    if isinstance(operator_preferences, dict) and operator_preferences:
        await store.aput(
            operator_preferences_namespace(),
            "latest",
            {
                **copy.deepcopy(operator_preferences),
                "preferred_provider": str(state.get("provider") or ""),
                "preferred_model": str(state.get("model") or ""),
                "risk_level": str(state.get("risk_level") or "low"),
            },
            index=False,
        )
        writes += 1

    result["writes"] = writes
    return result


def _resolve_memory_target(state: dict[str, Any]) -> tuple[str, str, str] | None:
    model = str(state.get("planner_model") or state.get("model") or "").strip()
    if not model:
        return None
    provider = str(state.get("provider") or "").strip().lower()
    try:
        for item in load_allowed_models_json():
            if item.get("model_id") == model:
                provider = str(item.get("provider") or provider or "openai").strip().lower()
                break
    except Exception:
        logger.debug("Could not resolve memory helper provider", exc_info=True)
    api_key = str(state.get("api_key") or "").strip()
    if not api_key:
        env_map = {
            "anthropic": ("ANTHROPIC_API_KEY",),
            "google": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
            "openai": ("OPENAI_API_KEY",),
        }
        for env_name in env_map.get(provider or "openai", ("OPENAI_API_KEY",)):
            api_key = str(os.getenv(env_name) or "").strip()
            if api_key:
                break
    if not provider or not api_key:
        return None
    return provider, model, api_key


async def _call_openai_memory_json(model: str, api_key: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
    from openai import AsyncOpenAI

    kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = os.getenv("OPENAI_BASE_URL")
    if base_url:
        kwargs["base_url"] = base_url
    client = AsyncOpenAI(**kwargs)
    response = await client.responses.create(
        model=model,
        instructions=system_prompt,
        input=user_prompt,
    )
    return _extract_json_object(str(getattr(response, "output_text", "") or ""))


async def _call_anthropic_memory_json(model: str, api_key: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=api_key)
    response = await client.messages.create(
        model=model,
        max_tokens=_MAX_MEMORY_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    parts: list[str] = []
    for block in response.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
    return _extract_json_object(" ".join(parts))


async def _call_gemini_memory_json(model: str, api_key: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
    from google import genai
    from google.genai import types as genai_types

    client = genai.Client(api_key=api_key)
    response = await client.aio.models.generate_content(
        model=model,
        contents=user_prompt,
        config=genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
        ),
    )
    return _extract_json_object(str(getattr(response, "text", "") or ""))


async def request_memory_json(state: dict[str, Any], *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
    target = _resolve_memory_target(state)
    if target is None:
        raise ValueError("No memory helper target is configured")
    provider, model, api_key = target
    if provider == "anthropic":
        return await _call_anthropic_memory_json(model, api_key, system_prompt, user_prompt)
    if provider == "google":
        return await _call_gemini_memory_json(model, api_key, system_prompt, user_prompt)
    return await _call_openai_memory_json(model, api_key, system_prompt, user_prompt)


def _fallback_summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    urls: list[str] = []
    details: list[str] = []
    covered_ids: list[str] = []
    for item in entries:
        covered_ids.append(str(item.get("evidence_id") or ""))
        text = str(
            item.get("summary")
            or item.get("value")
            or item.get("notes")
            or item.get("query")
            or item.get("subgoal")
            or ""
        ).strip()
        if text:
            details.append(text)
        for source in item.get("sources") or []:
            if isinstance(source, dict):
                url = str(source.get("url") or source.get("link") or "").strip()
                if url:
                    urls.append(url)
    summary = "; ".join(details[:4]) or f"Compressed {len(entries)} older evidence item(s)."
    return {
        "summary": summary,
        "key_points": details[:5],
        "source_urls": urls[:8],
        "covered_evidence_ids": [value for value in covered_ids if value],
    }


async def summarize_evidence_entries(state: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = normalize_evidence_entries(entries, assign_ids=True)
    payload = {
        "goal": str(state.get("goal") or state.get("task") or ""),
        "active_subgoal": str(((state.get("active_plan") or {}) if isinstance(state.get("active_plan"), dict) else {}).get("active_subgoal") or ""),
        "entries": [
            {
                "evidence_id": str(item.get("evidence_id") or ""),
                "kind": str(item.get("kind") or "note"),
                "summary": str(
                    item.get("summary")
                    or item.get("value")
                    or item.get("notes")
                    or item.get("query")
                    or item.get("subgoal")
                    or ""
                ),
                "sources": [
                    {
                        "title": str(source.get("title") or ""),
                        "url": str(source.get("url") or source.get("link") or ""),
                    }
                    for source in (item.get("sources") or [])
                    if isinstance(source, dict)
                ][:5],
                "covered_evidence_ids": _copy_str_list(item.get("covered_evidence_ids")),
            }
            for item in normalized
        ],
    }
    try:
        response = await request_memory_json(
            state,
            system_prompt=EVIDENCE_SUMMARY_SYSTEM_PROMPT,
            user_prompt=(
                "Compress these older evidence entries into one reusable summary. Return strict JSON only.\n\n"
                f"Context:\n{json.dumps(payload, indent=2, ensure_ascii=True)}"
            ),
        )
        summary = str(response.get("summary") or "").strip()
        if not summary:
            raise ValueError("Evidence summary response did not include summary")
        return {
            "kind": _SUMMARY_KIND,
            "evidence_id": f"evidence-{uuid4()}",
            "summary": summary,
            "key_points": _copy_str_list(response.get("key_points")),
            "source_urls": _copy_str_list(response.get("source_urls")),
            "covered_evidence_ids": sorted(
                {
                    value
                    for item in normalized
                    for value in [str(item.get("evidence_id") or "")]
                    if value
                }
                | {
                    value
                    for item in normalized
                    for value in _copy_str_list(item.get("covered_evidence_ids"))
                }
            ),
        }
    except Exception:
        logger.debug("Falling back to deterministic evidence summary", exc_info=True)
        fallback = _fallback_summary(normalized)
        return {
            "kind": _SUMMARY_KIND,
            "evidence_id": f"evidence-{uuid4()}",
            **fallback,
        }


async def prune_evidence_entries(
    state: dict[str, Any],
    evidence: Any,
    *,
    evidence_limit: int,
) -> list[dict[str, Any]]:
    normalized = normalize_evidence_entries(evidence, assign_ids=True)
    limit = max(int(evidence_limit), 1)
    pinned_ids = planner_pinned_evidence_ids(state.get("active_plan"))

    summaries = [item for item in normalized if item.get("kind") == _SUMMARY_KIND]
    concrete_entries = [item for item in normalized if item.get("kind") != _SUMMARY_KIND]
    keep_recent_ids = {str(item.get("evidence_id") or "") for item in concrete_entries[-limit:]}
    keep_ids = {value for value in keep_recent_ids | pinned_ids if value}

    retained: list[dict[str, Any]] = []
    evicted: list[dict[str, Any]] = []
    for item in concrete_entries:
        if str(item.get("evidence_id") or "") in keep_ids:
            retained.append(item)
        else:
            evicted.append(item)

    if not evicted and len(summaries) <= 1:
        return summaries + retained

    summary_payload = summaries + evicted
    if not summary_payload:
        return retained
    compacted = await summarize_evidence_entries(state, summary_payload)
    return [compacted, *retained]


class JsonFileStore(BaseStore):
    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._delegate = InMemoryStore()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Could not read graph memory store at %s", self._path, exc_info=True)
            return
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            namespace = item.get("namespace")
            key = str(item.get("key") or "").strip()
            value = item.get("value")
            if not isinstance(namespace, list | tuple) or not key or not isinstance(value, dict):
                continue
            self._delegate.put(tuple(str(part) for part in namespace), key, value, index=False)

    def _snapshot(self) -> dict[str, Any]:
        items = self._delegate.search((), limit=_DEFAULT_STORE_LIMIT)
        return {
            "items": [
                {
                    "namespace": list(item.namespace),
                    "key": item.key,
                    "value": copy.deepcopy(item.value),
                }
                for item in items
            ]
        }

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp_path.write_text(_json_dumps(self._snapshot()), encoding="utf-8")
        tmp_path.replace(self._path)

    def batch(self, ops: list[Any]) -> list[Any]:
        results = self._delegate.batch(ops)
        self._flush()
        return results

    async def abatch(self, ops: list[Any]) -> list[Any]:
        results = await self._delegate.abatch(ops)
        await asyncio.to_thread(self._flush)
        return results


_EPHEMERAL_MEMORY_STORE_MODES = {
    "dev",
    "development",
    "disabled",
    "ephemeral",
    "in-memory",
    "inmemory",
    "memory",
    "none",
    "off",
    "test",
    "volatile",
}
_PERSISTENT_MEMORY_STORE_MODES = {
    "",
    "default",
    "file",
    "json",
    "persist",
    "persistent",
    "prod",
    "production",
}


def build_graph_store(checkpoint_path: str | Path) -> BaseStore:
    mode = str(
        os.getenv("CUA_MEMORY_STORE_MODE")
        or os.getenv("CUA_GRAPH_STORE_MODE")
        or "persistent"
    ).strip().lower()
    if mode in _EPHEMERAL_MEMORY_STORE_MODES:
        return InMemoryStore()
    if mode not in _PERSISTENT_MEMORY_STORE_MODES:
        logger.warning("Unknown graph memory store mode %r; using persistent JsonFileStore", mode)

    configured_path = os.getenv("CUA_MEMORY_STORE_PATH") or os.getenv("CUA_GRAPH_STORE_PATH")
    if configured_path:
        return JsonFileStore(Path(configured_path).expanduser())
    checkpoint_file = Path(checkpoint_path)
    return JsonFileStore(checkpoint_file.with_name("langgraph-store.json"))
