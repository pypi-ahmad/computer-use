# TECHNICAL

Architecture notes for contributors.

The repo is intentionally small in concept:

**Computer Use + optional Web Search + optional provider file retrieval.**

The runtime is a provider SDK loop plus a shared desktop executor.

## Runtime Shape

```text
React UI
  -> FastAPI server
    -> AgentLoop
      -> ComputerUseEngine
        -> provider SDK call with documented tools
        -> DesktopExecutor
          -> docker/agent_service.py
            -> Xvfb/XFCE desktop
```

The provider sees screenshots and returns tool calls. The executor performs
those calls inside the Docker desktop and returns the next screenshot.

## Core Modules

| File | Responsibility |
|---|---|
| `backend/server.py` | HTTP API, WebSocket events, key/model validation, session lifecycle |
| `backend/loop.py` | Turns a UI request into one provider Computer Use run |
| `backend/engine/__init__.py` | Provider/model validation and `ComputerUseEngine` facade |
| `backend/providers/openai.py` | OpenAI public `run(...)` wrapper |
| `backend/providers/anthropic.py` | Anthropic public `run(...)` wrapper |
| `backend/providers/gemini.py` | Gemini public `run(...)` wrapper |
| `backend/engine/openai.py` | OpenAI Responses API Computer Use client |
| `backend/engine/claude.py` | Anthropic Messages Computer Use client |
| `backend/engine/gemini.py` | Gemini GenerateContent Computer Use client |
| `backend/executor.py` | Screenshot capture, action dispatch, coordinate normalization |
| `backend/files.py` | Provider file upload and retrieval preparation |
| `backend/prompts.py` | Provider-specific prompt text |
| `backend/safety.py` | Safety approval registry |
| `docker/agent_service.py` | In-container desktop action service |

## Provider Run Contract

Each provider wrapper exposes:

```python
run(task, *, tools, files, on_event, on_safety, executor)
```

The wrapper decides how to instantiate or drive its provider client. The
provider client owns the exact SDK request shape.

Shared options:

```python
tools.web_search: bool
files: list[str]
executor: ActionExecutor
on_event(event): callback for UI/session events
on_safety(prompt): callback for provider safety confirmation
```

`tools.web_search` is the only public search option. If it is false, the
provider receives only Computer Use plus any eligible file context. If it is
true, the provider receives its official Web Search tool in the same run.

## Tool Matrix

| Condition | OpenAI | Anthropic | Gemini |
|---|---|---|---|
| Base run | `computer` | computer tool | `computer_use` |
| Web Search on | add `web_search` | add `web_search_20250305` | add `google_search` |
| Files uploaded | add `file_search` | add Files API/document context | reject |

Gemini file uploads are rejected because Gemini File Search is not documented
as part of this app's Computer Use path.

Rejected request-shape examples:

- Gemini plus `attached_files`
- OpenAI Web Search with minimal reasoning effort
- any request field outside the public schema

## Executor Contract

`backend/executor.py` owns:

- `ActionExecutor`
- `DesktopExecutor`
- `CUActionResult`
- `SafetyDecision`
- screenshot capture helpers
- action dispatch helpers
- Gemini 0-999 coordinate denormalization

Provider code should not talk to Docker or noVNC directly. It should call the
executor.

## File Retrieval Contract

`backend/files.py` owns provider differences:

- OpenAI: create/upload/use vector store for `file_search`
- Anthropic: upload through Files API and build document/text context
- Gemini: reject files for Computer Use sessions

File ids passed through the UI are local opaque ids until `backend/files.py`
prepares them for a provider.

The backend accepts `.pdf`, `.txt`, `.md`, and `.docx`. Provider-side limits may
be stricter than the local upload cap and are surfaced as provider errors at
session start.

## Session Lifecycle

1. `POST /api/agent/start`
2. server validates provider/model/tools/files
3. Docker sandbox is started or confirmed healthy
4. `AgentLoop` builds `ComputerUseEngine`
5. provider loop runs until final answer, stop request, error, or turn limit
6. server broadcasts session events over `/ws`

Safety prompts pause the active provider loop through `backend/safety.py` and
resume when the operator confirms or denies.

## API Surface

Primary endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /api/models` | Return the Computer Use capable model allowlist |
| `GET /api/container/status` | Report sandbox/container health |
| `POST /api/files/upload` | Store a reference file and return a local file id |
| `POST /api/agent/start` | Start one Computer Use run |
| `POST /api/agent/stop/{session_id}` | Stop a running session |
| `GET /api/agent/status/{session_id}` | Poll current session state |
| `POST /api/safety/confirm` | Confirm or deny a provider safety prompt |
| `WS /ws` | Stream logs, steps, status, and safety prompts |

## Sandbox Boundary

The Docker container runs:

- Xvfb
- XFCE4
- noVNC/websockify
- browser and desktop apps
- `docker/agent_service.py`

The backend talks to the sandbox action service on loopback port `9222`.
Actions do not execute on the host desktop.

## Tests

Full suite:

```powershell
python -m pytest -p no:cacheprovider tests evals --tb=short
```

Focused architecture tests:

```powershell
python -m pytest tests/test_provider_run_contract.py tests/test_files.py tests/test_server_validation.py --tb=short
```

Provider hot spots:

```powershell
python -m pytest tests/engine/test_openai.py tests/engine/test_claude.py tests/engine/test_gemini.py --tb=short
```
