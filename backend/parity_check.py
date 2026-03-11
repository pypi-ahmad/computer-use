"""Parity check utility to ensure alignment between ActionType, engine_capabilities.json, and prompt action lists."""

import logging
from typing import Set

from backend.models import ActionType
from backend.engine_capabilities import EngineCapabilities

logger = logging.getLogger(__name__)


def validate_tool_parity() -> bool:
    """
    Validate that:
    1. All engine actions in engine_capabilities.json exist in ActionType.
    2. Prompt action lists align with the engine capability schema.
    """

    success = True
    action_type_values: Set[str] = {a.value for a in ActionType}

    # 1. Engine capability schema vs ActionType
    try:
        caps = EngineCapabilities()
        for engine_name in caps.engine_names:
            engine_actions = caps.get_engine_actions(engine_name)
            unknown = engine_actions - action_type_values
            if unknown:
                logger.warning(
                    "SCHEMA WARNING: engine_capabilities.json '%s' has actions "
                    "not in ActionType enum: %s", engine_name, sorted(unknown),
                )
                success = False
        logger.info(
            "SCHEMA PARITY: Checked %d engines in engine_capabilities.json",
            len(caps.engine_names),
        )
    except Exception as e:
        logger.warning("Engine capability schema parity check failed: %s", e)
        success = False

    # 2. Prompt action drift detection
    try:
        from backend.agent.prompts import validate_prompt_actions
        prompt_warnings = validate_prompt_actions()
        if prompt_warnings:
            for w in prompt_warnings:
                logger.warning("PROMPT DRIFT: %s", w)
        else:
            logger.info("PROMPT PARITY: All prompt action lists align with schema.")
    except Exception as e:
        logger.warning("Prompt parity check failed: %s", e)

    return success


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    validate_tool_parity()
