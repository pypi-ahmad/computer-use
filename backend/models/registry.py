from __future__ import annotations
# === merged from backend/engine_capabilities.py ===
"""Engine Capability Registry — structured, machine-readable engine metadata.

Loads ``engine_capabilities.json`` and exposes a typed Python API for:

* Action validation (reject unsupported actions before they hit the container)
* Capability filtering (list what an engine can do)
* Engine-specific schema injection (feed the model only valid actions)

Usage::

    from backend.models.registry import EngineCapabilities

    caps = EngineCapabilities()
    caps.validate_action("computer_use", "click")       # → True
    caps.validate_action("computer_use", "evaluate_js")  # → True
    caps.get_engine_actions("computer_use")               # → frozenset(...)
"""


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

# === merged from backend/action_aliases.py ===
"""Action alias resolution for the computer_use engine.

Normalizes action aliases to canonical ActionType values.

Usage::

    from backend.models.registry import resolve_action

    resolved = resolve_action("press")  # → "key"
"""


# ── Alias map: variant name → canonical ActionType value ──────────────────────
# Only aliases whose targets are valid computer_use actions or ActionType members.

ACTION_ALIASES: dict[str, str] = {
    # Mouse
    "left_click": "click",
    "click_element": "click",
    "dblclick": "double_click",
    "rightclick": "right_click",
    "context_click": "right_click",
    "mouseover": "hover",
    "mouse_move": "hover",
    "mousemove": "hover",
    "drag_and_drop": "drag",
    "drag_drop": "drag",
    # Keyboard
    "press": "key",
    "press_key": "key",
    "keypress": "key",
    "send_keys": "key",
    "type_text": "type",
    "input_text": "type",
    "input": "type",
    "enter_text": "type",
    "write": "type",
    "fill_form": "fill",
    "set_value": "fill",
    # Navigation
    "navigate": "open_url",
    "goto": "open_url",
    "go_to": "open_url",
    "go": "open_url",
    "open": "open_url",
    "visit": "open_url",
    "browse": "open_url",
    "back": "go_back",
    "forward": "go_forward",
    # Scrolling
    "scroll_up": "scroll",
    "scroll_down": "scroll",
    # DOM / JS
    "extract_text": "get_text",
    "read_text": "get_text",
    "get_content": "get_text",
    "eval_js": "evaluate_js",
    "execute_js": "evaluate_js",
    "run_js": "evaluate_js",
    "javascript": "evaluate_js",
    # Control
    "sleep": "wait",
    "pause": "wait",
    "delay": "wait",
    # Terminal
    "complete": "done",
    "finish": "done",
    "finished": "done",
    "success": "done",
    "fail": "error",
    "abort": "error",
}



def resolve_action(action: str) -> str:
    """Resolve an action string to its canonical ActionType value.

    Returns the canonical action name, or the original string if no alias match.
    Inputs longer than :data:`_MAX_ACTION_LEN` are truncated so a model
    emitting a runaway payload can't pollute logs with a 10k-char key.
    """
    if not isinstance(action, str):
        return "error"
    if len(action) > _MAX_ACTION_LEN:
        action = action[:_MAX_ACTION_LEN]
    normalized = action.strip().lower()
    return ACTION_ALIASES.get(normalized, normalized)


# Action names are short symbolic tokens; anything longer than this is
# either broken input or an attempt to bloat logs.
_MAX_ACTION_LEN = 64

