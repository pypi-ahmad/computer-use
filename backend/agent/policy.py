from __future__ import annotations

import copy
import json
import re
from typing import Any

_DESTRUCTIVE_RE = re.compile(r"\b(delete|remove|drop|format|erase|wipe|destroy|truncate)\b", re.IGNORECASE)
_FINANCIAL_RE = re.compile(r"\b(payment|pay|card|credit|debit|cvv|bank|billing|checkout|purchase|invoice|transfer|wire)\b", re.IGNORECASE)
_MESSAGE_RE = re.compile(r"\b(email|mail|chat|message|dm|post|tweet|slack|discord|teams|whatsapp|telegram|social|sms|send)\b", re.IGNORECASE)
_FILE_RE = re.compile(r"\b(file|folder|directory|path|document|download|upload)\b", re.IGNORECASE)
_ACTIONISH_RE = re.compile(r"\b(click|press|submit|send|enter|type|write|confirm|open|drag|drop)\b", re.IGNORECASE)
_PATH_RE = re.compile(r"([A-Za-z]:\\[^\s\"']+|/(?:[^\s\"']+/?)+)")
_SAFE_PATH_PREFIXES = (
    "/tmp/",
    "/workspace/",
    "/mnt/data/",
    "/home/oai/",
    "/home/sandbox/",
    "a:\\computer-use\\",
)


def _native_actions(batch: Any) -> list[dict[str, Any]]:
    if not isinstance(batch, dict):
        return []
    native_actions = batch.get("native_actions")
    if not isinstance(native_actions, list):
        return []
    out: list[dict[str, Any]] = []
    for item in native_actions:
        if isinstance(item, dict):
            out.append(copy.deepcopy(item))
    return out


def _text_blob(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True)
    except Exception:
        return str(value)


def _provider_action_descriptors(provider: str, batch: dict[str, Any]) -> list[dict[str, Any]]:
    descriptors: list[dict[str, Any]] = []
    model_text = str(batch.get("model_text") or "")
    for native in _native_actions(batch):
        if provider == "openai" and native.get("type") == "computer_call":
            actions = list(native.get("actions") or [])
            if not actions and isinstance(native.get("action"), dict):
                actions = [native["action"]]
            for action in actions:
                descriptors.append(
                    {
                        "name": str((action or {}).get("type") or "computer_action"),
                        "payload": copy.deepcopy(action),
                        "text": f"{model_text}\n{_text_blob(action)}",
                    }
                )
            continue
        if provider == "anthropic" and native.get("type") == "tool_use":
            payload = copy.deepcopy(native.get("input") or {})
            descriptors.append(
                {
                    "name": str(payload.get("action") or native.get("name") or "tool_use"),
                    "payload": payload,
                    "text": f"{model_text}\n{_text_blob(payload)}",
                }
            )
            continue
        if provider == "google":
            payload = copy.deepcopy(native.get("args") or {})
            descriptors.append(
                {
                    "name": str(native.get("name") or "function_call"),
                    "payload": payload,
                    "text": f"{model_text}\n{_text_blob(native)}",
                }
            )
            continue
        descriptors.append(
            {
                "name": str(native.get("type") or native.get("name") or "action"),
                "payload": copy.deepcopy(native),
                "text": f"{model_text}\n{_text_blob(native)}",
            }
        )
    return descriptors


def _outside_sandbox_path(text: str) -> bool:
    for match in _PATH_RE.findall(text):
        candidate = str(match or "").strip().replace("\\", "/").lower()
        if not candidate:
            continue
        if any(candidate.startswith(prefix.replace("\\", "/").lower()) for prefix in _SAFE_PATH_PREFIXES):
            continue
        return True
    return False


def classify_pending_action_batch(
    *,
    provider: str,
    batch: dict[str, Any] | None,
    risk_level: str,
    provider_capabilities: dict[str, Any] | None = None,
) -> dict[str, Any]:
    capabilities = provider_capabilities if isinstance(provider_capabilities, dict) else {}
    if capabilities and capabilities.get("verified") is True and not bool(capabilities.get("computer_use", False)):
        reason = "session capability probe did not verify computer_use support for the selected model/config"
        return {
            "overall_level": "high",
            "actions": [
                {
                    "name": str(item.get("type") or item.get("name") or "action"),
                    "level": "high",
                    "reasons": [reason],
                }
                for item in _native_actions(batch or {})
            ],
            "summary_reasons": [reason],
        }
    descriptors = _provider_action_descriptors(provider, batch or {})
    normalized_risk = str(risk_level or "low").lower()
    if normalized_risk not in {"low", "medium", "high"}:
        normalized_risk = "low"

    results: list[dict[str, Any]] = []
    overall = "low"
    summary_reasons: list[str] = []
    for descriptor in descriptors:
        text = descriptor["text"]
        name = str(descriptor["name"] or "")
        combined = f"{name}\n{text}"
        level = "low"
        reasons: list[str] = []
        if _DESTRUCTIVE_RE.search(combined):
            level = "high"
            reasons.append("destructive action")
        elif _FINANCIAL_RE.search(combined) and _ACTIONISH_RE.search(combined):
            level = "high"
            reasons.append("financial or payment UI")
        elif _MESSAGE_RE.search(combined) and _ACTIONISH_RE.search(combined):
            level = "high"
            reasons.append("external messaging action")
        elif _FILE_RE.search(combined) and (_DESTRUCTIVE_RE.search(combined) or _outside_sandbox_path(combined)):
            level = "high"
            reasons.append("file modification outside sandbox path")
        elif normalized_risk in {"medium", "high"}:
            level = "medium"
            reasons.append(f"session risk level is {normalized_risk}")

        if level == "high":
            overall = "high"
        elif level == "medium" and overall != "high":
            overall = "medium"
        results.append(
            {
                "name": name,
                "level": level,
                "reasons": reasons,
            }
        )
        summary_reasons.extend(reasons)

    if not descriptors and normalized_risk in {"medium", "high"}:
        overall = "medium"
        summary_reasons.append(f"session risk level is {normalized_risk}")

    return {
        "overall_level": overall,
        "actions": results,
        "summary_reasons": summary_reasons,
    }


def policy_explanation(classification: dict[str, Any]) -> str:
    overall = str(classification.get("overall_level") or "low")
    reasons = [str(item) for item in (classification.get("summary_reasons") or []) if str(item).strip()]
    if reasons:
        return f"Policy gate classified the pending action batch as {overall} risk: {'; '.join(reasons)}"
    return f"Policy gate classified the pending action batch as {overall} risk."