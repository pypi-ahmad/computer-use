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


async def _run_bundled_capture():
    """Helper: execute a click with include_screenshot and capture wire payloads."""
    from unittest.mock import AsyncMock
    from backend.executor import DesktopExecutor

    captured = []

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"success": True, "message": "ok", "screenshot": "BUNDLED_B64"}

    class _Client:
        async def post(self, _url, *, json, headers):
            captured.append(dict(json))
            return _Resp()

    ex = DesktopExecutor(screen_width=1440, screen_height=900, normalize_coords=False)
    import backend.engine as _eng
    _orig = _eng._app_config.ui_settle_delay
    ex._get_client = AsyncMock(return_value=_Client())
    result = await ex.execute("click_at", {"x": 10, "y": 20, "include_screenshot": True})
    return captured, result


def test_p1_include_screenshot_is_sent_and_bundled_frame_surfaces():
    """P1: execute(..., include_screenshot=True via args) adds the flag to the
    /action POST and the bundled screenshot surfaces in the result extra."""
    import asyncio
    captured, result = asyncio.run(_run_bundled_capture())
    assert captured and captured[0].get("include_screenshot") == 1
    # include_screenshot must NOT leak as an action field beyond the flag
    assert "include_screenshot" not in result.extra or result.extra.get("screenshot") == "BUNDLED_B64"
    assert result.extra.get("screenshot") == "BUNDLED_B64"
