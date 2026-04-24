"""Engine Capability Registry — structured, machine-readable engine metadata.

Loads ``engine_capabilities.json`` and exposes a typed Python API for:

* Action validation (reject unsupported actions before they hit the container)
* Capability filtering (list what an engine can do)
* Engine-specific schema injection (feed the model only valid actions)

Usage::

    from backend.engine_capabilities import EngineCapabilities

    caps = EngineCapabilities()
    caps.validate_action("computer_use", "click")       # → True
    caps.validate_action("computer_use", "evaluate_js")  # → True
    caps.get_engine_actions("computer_use")               # → frozenset(...)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional, Set

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_SCHEMA_FILENAME = "engine_capabilities.json"


def get_default_schema_path() -> Path:
    """Return the package-relative capability schema path."""
    return Path(__file__).resolve().parent / _SCHEMA_FILENAME


_DEFAULT_SCHEMA_PATH = get_default_schema_path()


# ── Dataclass-like typed containers ───────────────────────────────────────────

class EngineSchema:
    """Typed representation of a single engine's capability block."""

    __slots__ = ("name", "display_name", "description", "allowed_actions")

    def __init__(self, name: str, raw: Dict[str, Any]) -> None:
        """Parse a single engine's JSON block into typed fields."""
        self.name: str = name
        self.display_name: str = raw.get("display_name", name)
        self.description: str = raw.get("description", "")
        raw_actions = raw.get("allowed_actions", [])
        self.allowed_actions: FrozenSet[str] = frozenset(
            raw_actions if isinstance(raw_actions, list) else []
        )

    def __repr__(self) -> str:  # pragma: no cover
        return f"EngineSchema(name={self.name!r}, actions={len(self.allowed_actions)})"


# ── Main capability class ────────────────────────────────────────────────────

class EngineCapabilities:
    """Machine-readable engine capability registry.

    Parameters:
        schema_path: Path to ``engine_capabilities.json``.  Defaults to the
            file next to this module.

    Raises:
        FileNotFoundError: If the schema file does not exist.
        json.JSONDecodeError: If the schema file is malformed JSON.

    Example::

        caps = EngineCapabilities()
        assert caps.validate_action("computer_use", "click")
    """

    def __init__(self, schema_path: str | Path | None = None) -> None:
        path = Path(schema_path) if schema_path else get_default_schema_path()
        if not path.exists():
            raise FileNotFoundError(f"Engine capability schema not found: {path}")

        with open(path, "r", encoding="utf-8") as fh:
            raw: Dict[str, Any] = json.load(fh)

        self._version: str = raw.get("version", "unknown")

        # Parse each engine block into EngineSchema objects.
        self._engines: Dict[str, EngineSchema] = {}
        for name, block in raw.get("engines", {}).items():
            self._engines[name] = EngineSchema(name, block)

        # Materialise a global action → engines reverse index.
        self._action_index: Dict[str, Set[str]] = {}
        for eng_name, eng in self._engines.items():
            for action in eng.allowed_actions:
                self._action_index.setdefault(action, set()).add(eng_name)

        logger.debug(
            "EngineCapabilities loaded: version=%s, engines=%d, total_actions=%d",
            self._version,
            len(self._engines),
            len(self._action_index),
        )

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def version(self) -> str:
        """Schema version string."""
        return self._version

    @property
    def engine_names(self) -> FrozenSet[str]:
        """All registered engine names."""
        return frozenset(self._engines.keys())

    def get_engine(self, engine_name: str) -> Optional[EngineSchema]:
        """Return the full ``EngineSchema`` for *engine_name*, or ``None``."""
        return self._engines.get(engine_name)

    def get_engine_actions(self, engine_name: str) -> FrozenSet[str]:
        """Return the set of allowed actions for *engine_name*.

        Returns an empty frozenset if the engine is unknown.
        """
        eng = self._engines.get(engine_name)
        if eng is None:
            return frozenset()
        return eng.allowed_actions

    def validate_action(self, engine_name: str, action: str) -> bool:
        """Return ``True`` if *action* is valid for *engine_name*.

        This is the primary guard used to reject unsupported actions before
        they reach the container agent service.
        """
        eng = self._engines.get(engine_name)
        if eng is None:
            return False
        return action in eng.allowed_actions

    def validate_action_detailed(
        self, engine_name: str, action: str
    ) -> tuple[bool, str]:
        """Validate and return a ``(ok, message)`` tuple.

        On failure the message explains *why* and may suggest alternative
        engines that support the action.
        """
        eng = self._engines.get(engine_name)
        if eng is None:
            return False, f"Unknown engine: {engine_name!r}"

        if action in eng.allowed_actions:
            return True, ""

        # Build a helpful hint: which engines *do* support this action?
        alternatives = sorted(self._action_index.get(action, set()))
        if alternatives:
            alt_str = ", ".join(alternatives)
            return (
                False,
                f"Action {action!r} is not supported by {engine_name}. "
                f"Supported by: {alt_str}",
            )
        return (
            False,
            f"Action {action!r} is not supported by any registered engine.",
        )
