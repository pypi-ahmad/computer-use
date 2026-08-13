"""Public model exports for tests and runtime imports."""

from __future__ import annotations

from typing import Any

__all__ = [
    "ActionType",
    "AgentAction",
    "AgentSession",
    "LogEntry",
    "SessionStatus",
    "StartTaskRequest",
    "StepRecord",
    "StructuredError",
    "TaskStatusResponse",
    "load_allowed_models_json",
]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from backend.models import schemas

    return getattr(schemas, name)
