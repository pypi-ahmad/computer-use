from __future__ import annotations


def test_engine_reexports_executor_contract():
    from backend import executor as executor_module
    from backend.engine import (
        ActionExecutor,
        CUActionResult,
        DesktopExecutor,
        SafetyDecision,
        close_shared_executor_clients,
        denormalize_x,
        denormalize_y,
    )

    assert DesktopExecutor is executor_module.DesktopExecutor
    assert ActionExecutor is executor_module.ActionExecutor
    assert CUActionResult is executor_module.CUActionResult
    assert SafetyDecision is executor_module.SafetyDecision
    assert close_shared_executor_clients is executor_module.close_shared_executor_clients
    assert denormalize_x is executor_module.denormalize_x
    assert denormalize_y is executor_module.denormalize_y


def test_desktop_executor_body_lives_outside_engine():
    from backend.engine import DesktopExecutor

    assert DesktopExecutor.__module__ == "backend.executor"
