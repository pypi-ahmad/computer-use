from __future__ import annotations

from typing import Any

from backend.agent.memory_layers import build_evidence_brief, build_memory_context_brief
from backend.agent.prompts import get_system_prompt


def _prompt_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _prompt_active_subgoal(active_plan: Any, subgoals: Any) -> str | None:
    if isinstance(active_plan, dict):
        explicit = str(active_plan.get("active_subgoal") or "").strip()
        if explicit:
            return explicit
    if not isinstance(subgoals, list):
        return None
    pending: str | None = None
    for item in subgoals:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("objective") or "").strip()
        if not title:
            continue
        status = str(item.get("status") or "pending").lower()
        if status == "active":
            return title
        if pending is None and status == "pending":
            pending = title
    return pending


def build_executor_system_prompt(
    *,
    provider: str,
    model: str | None = None,
    active_plan: Any = None,
    subgoals: Any = None,
    completion_criteria: Any = None,
    verification_status: str | None = None,
    unmet_completion_criteria: Any = None,
    recovery_context: Any = None,
    evidence: Any = None,
    memory_context: Any = None,
) -> str:
    prompt = get_system_prompt("computer_use", provider=provider, model=model)
    active_subgoal = _prompt_active_subgoal(active_plan, subgoals)
    if not active_subgoal:
        return prompt

    plan_summary = ""
    if isinstance(active_plan, dict):
        plan_summary = str(active_plan.get("summary") or "").strip()

    remaining_subgoals: list[str] = []
    if isinstance(subgoals, list):
        for item in subgoals:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("objective") or "").strip()
            if not title or title == active_subgoal:
                continue
            status = str(item.get("status") or "pending").lower()
            remaining_subgoals.append(f"{title} ({status})")

    context_lines = [
        "",
        "ACTIVE EXECUTION CONTEXT:",
        f"- Active subgoal: {active_subgoal}",
        "- Execute this subgoal faithfully. Do not re-plan or expand the scope.",
    ]
    if plan_summary:
        context_lines.append(f"- Plan summary: {plan_summary}")
    if remaining_subgoals:
        context_lines.append(f"- Remaining subgoals: {', '.join(remaining_subgoals)}")
    criteria = _prompt_str_list(completion_criteria)
    if criteria:
        context_lines.append(f"- Completion criteria: {'; '.join(criteria)}")
    unmet = _prompt_str_list(unmet_completion_criteria)
    if str(verification_status or "").strip().lower() == "needs_more_work" and unmet:
        context_lines.append(f"- Verifier says more work is required. Unmet criteria: {'; '.join(unmet)}")
    if isinstance(recovery_context, dict):
        classification = str(recovery_context.get("classification") or "").strip()
        retry_reason = str(recovery_context.get("retry_reason") or "").strip()
        error = str(recovery_context.get("error") or recovery_context.get("error_classification") or "").strip()
        if classification:
            summary = f"- Recovery context: previous failure classified as {classification}"
            if retry_reason:
                summary += f" during {retry_reason}"
            if error:
                summary += f" ({error})"
            context_lines.append(summary)
    evidence_brief = build_evidence_brief(evidence, limit=2)
    if evidence_brief:
        context_lines.append(f"- Working memory:\n{evidence_brief}")
    memory_brief = build_memory_context_brief(memory_context, limit=2)
    if memory_brief:
        context_lines.append(f"- Long-term memory:\n{memory_brief}")
    return prompt + "\n" + "\n".join(context_lines)