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


def test_every_client_emitted_action_has_executor_handler():
    """Q4: parity guard — every literal action name the Claude/OpenAI adapters
    pass to ``executor.execute("...")`` must have a matching ``_act_*`` handler,
    so adding a client action without the executor side (or renaming one) fails
    loudly instead of returning 'Unimplemented desktop action' at runtime.
    (Gemini dispatches dynamically via ``fc.name`` and is covered by the
    agent_service action-gate tests instead.)
    """
    import inspect
    import re

    from backend.executor import DesktopExecutor
    import backend.engine.claude as claude_mod
    import backend.engine.openai as openai_mod

    exec_actions = {n[len("_act_"):] for n in dir(DesktopExecutor) if n.startswith("_act_")}
    pat = re.compile(r"""executor\.execute\(\s*["']([a-zA-Z_]+)["']""")
    emitted: set[str] = set()
    for mod in (claude_mod, openai_mod):
        emitted |= set(pat.findall(inspect.getsource(mod)))

    assert emitted, "expected to find executor.execute('...') literals to check"
    missing = emitted - exec_actions
    assert not missing, f"client(s) emit actions with no DesktopExecutor handler: {sorted(missing)}"
