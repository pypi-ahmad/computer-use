# TECHNICAL

Deep architecture reference for contributors. This document is the source of
truth for module boundaries, runtime contracts, threading rules, and provider
behavior. It is intentionally exhaustive: a senior engineer should be able to
read this once and understand the full execution model without diving into
`server.py` first.

The repo is intentionally small in concept:

**Computer Use + optional Web Search + optional provider file retrieval.**

The runtime is a provider SDK loop plus a shared desktop executor. Web Search
is implemented as a separate provider-native planning phase so the Computer
Use loop itself only ever sees the computer tool.

## Table of Contents

1. [Design Principles](#design-principles)
2. [Runtime Topology](#runtime-topology)
3. [Module Map](#module-map)
4. [Request Lifecycle](#request-lifecycle)
5. [Provider Run Contract](#provider-run-contract)
6. [Two-Phase Web Search Planner](#two-phase-web-search-planner)
7. [Engine Layer Internals](#engine-layer-internals)
8. [Desktop Executor and Sandbox Boundary](#desktop-executor-and-sandbox-boundary)
9. [File Retrieval Contract](#file-retrieval-contract)
10. [WebSocket Fan-Out and Streaming](#websocket-fan-out-and-streaming)
11. [Safety Confirmation Pipeline](#safety-confirmation-pipeline)
12. [Configuration and Environment](#configuration-and-environment)
13. [Security Model](#security-model)
14. [Observability and Tracing](#observability-and-tracing)
15. [Test Architecture](#test-architecture)
16. [Frontend Architecture](#frontend-architecture)
17. [Extension Points](#extension-points)
18. [Operational Notes](#operational-notes)

## Design Principles

These principles are the load-bearing constraints that explain every
architectural choice in the codebase.

1. **Provider-native only.** Tools advertised to a model must be ones the
   provider documents. The codebase never invents tool contracts, never
   wraps tool calls in custom JSON schemas, and never adds prompt-engineered
   "tools" that are actually free-form text. If the provider does not
   document a tool, this app does not expose it.
2. **One way to do each thing.** A single provider client per provider, a
   single executor per execution target, a single allowed-models source of
   truth, a single rate limiter. Configuration knobs are kept to the
   minimum the documented behavior actually needs.
3. **Strict isolation.** The agent runs only inside the Docker sandbox. The
   backend has no code path that runs `xdotool` or `scrot` on the host.
4. **Stateless replay where possible.** Conversation history is replayed on
   every provider request. No `previous_response_id`, no server-side thread
   state, no provider memory. This keeps the loop ZDR-safe for OpenAI and
   minimizes blast radius on retries.
5. **Fail closed.** The agent service rejects any action whose name is not
   on the live allowlist. Pydantic models declare `extra="forbid"`. Origin
   and Host header gates default to deny.
6. **Operator stays in the loop.** Provider safety prompts surface as
   modals; sessions can be stopped at any time; stuck-agent detection
   short-circuits without waiting for the turn limit.

## Runtime Topology

```text
React UI  (Vite dev server, port 3000)
  -> FastAPI server  (port 8100)
    -> AgentLoop                       (one per session)
      -> Provider wrapper run()        (backend/providers/<provider>.py)
        -> [optional] Planner pass     (backend/providers/planner.py)
        -> CU client run_loop          (backend/engine/<provider>.py)
          -> DesktopExecutor           (backend/executor.py)
            -> docker/agent_service.py (loopback :9222)
              -> Xvfb / XFCE desktop
```

Two long-lived processes plus a Docker container:

- **FastAPI / uvicorn** (`backend/main.py`) — single process, single worker.
  Holds the in-memory session table, rate limiter, file store, and the
  WebSocket subscriber registry.
- **Vite dev server** (`frontend/`) — proxies `/api`, `/ws`, and `/vnc/*` to
  the FastAPI process. Production builds emit static assets that any reverse
  proxy can serve.
- **`cua-environment` container** — Ubuntu 24.04 with Xvfb + XFCE4 + the
  agent service on port 9222, plus noVNC/websockify on 6080.

The model never has direct socket access to the host. All actions go through
the agent service, which enforces an allowlist before invoking `xdotool`,
`scrot`, or `xclip`.

## Module Map

### Backend top level (`backend/`)

| File | Responsibility |
|---|---|
| `main.py` | uvicorn entry point. Reads `Config`, builds the FastAPI app, binds to `HOST:PORT`. |
| `server.py` | All HTTP routes, the WebSocket endpoint, security middleware (Host allowlist, Origin gate, body size cap, security headers), per-IP rate limiter, session registry, container lifecycle proxies. |
| `loop.py` | `AgentLoop` — turns a UI request into one provider Computer Use run. Owns step recording, stuck-agent detection, safety relay, and the `on_step` / `on_log` / `on_screenshot` callbacks. |
| `executor.py` | `ActionExecutor` protocol, `DesktopExecutor` HTTP client, `CUActionResult`, `SafetyDecision`, key allowlist, Gemini coordinate denormalization, shared agent-service connection pool. |
| `files.py` | Provider-aware preparation of uploaded files: OpenAI vector store creation, Anthropic Files API uploads, Gemini rejection. |
| `prompts.py` | Provider-specific system prompt strings. |
| `safety.py` | Async `Event` + decision registry that lets the provider loop block until the operator confirms or denies a safety prompt. |

### Engine layer (`backend/engine/`)

| File | Responsibility |
|---|---|
| `__init__.py` | Public facade `ComputerUseEngine`, model/provider validation, registry lookup helpers, `default_openai_reasoning_effort_for_model`, `_lookup_claude_cu_config`, retry utilities, transient-error classification, screenshot coordinate helpers, shared SDK output normalization. |
| `openai.py` | `OpenAICUClient` — OpenAI Responses API computer tool. Stateless replay, screenshot resize for `detail: "original"` ceiling, ZDR-safe replay, `web_search_call.action.sources` retrieval, `file_search` integration, output-text harvesting. |
| `claude.py` | `ClaudeCUClient` — Anthropic beta Messages API. Tool version + beta flag from `allowed_models.json`, web-search org-level enablement probe with 24h cache, Files API document blocks, `max_tokens` budgeting, structured stop-reason handling. |
| `gemini.py` | `GeminiCUClient` — Google `google-genai` SDK. Atomic history pruning, normalized 0–999 coordinate grid, `Tool(computer_use=ComputerUse(...))` documented tool type, grounding metadata normalization. |

### Provider layer (`backend/providers/`)

| File | Responsibility |
|---|---|
| `_common.py` | Shared types: `ProviderTools`, `ProviderEvent`, `EventCallback`, `SafetyCallback`. Shared helpers: `normalize_tools`, `emit_event`, `maybe_plan_with_web_search`, `stream_client_run_loop`. |
| `planner.py` | Provider-native Web Search planning pass. `create_web_execution_brief` runs OpenAI `web_search`, Anthropic `web_search_20250305`, or Gemini `google_search` without the computer tool, then `build_planned_computer_use_task` merges the brief into the executing task. |
| `openai.py` | OpenAI `run(task, *, tools, files, on_event, on_safety, executor, **options)`. Builds a CU client if needed, runs the optional planner, streams the CU loop. |
| `anthropic.py` | Anthropic equivalent of the OpenAI wrapper. |
| `gemini.py` | Gemini equivalent. Rejects `files` non-empty before any provider call. |
| `__init__.py` | Re-exports the three `run` functions plus `ProviderTools`. |

### Models and validation (`backend/models/`)

| File | Responsibility |
|---|---|
| `allowed_models.json` | Canonical list of CU-capable models with provider, display name, and Anthropic-specific `cu_tool_version` / `cu_betas`. The single source of truth at runtime. |
| `engine_capabilities.json` | Action vocabulary metadata for the `ComputerUseEngine` facade. |
| `schemas.py` | Pydantic v2 models with `extra="forbid"`: `StartTaskRequest`, `AgentSession`, `StepRecord`, `AgentAction`, `LogEntry`, `TaskStatusResponse`, `StructuredError`. Also `load_allowed_models_json` and `load_engine_capabilities_json`. |
| `registry.py` | Helper functions for filtering and grouping models. |
| `validation.py` | Cross-checks that every advertised tool is documented for its provider. Used in tests and at startup. |

### Infrastructure (`backend/infra/`)

| File | Responsibility |
|---|---|
| `config.py` | `Config` dataclass + `config` singleton. Reads env vars, clamps numeric values to safe ranges, normalizes paths, derives the CORS / Host allowlists. |
| `docker.py` | Container lifecycle: `start_container`, `stop_container`, `is_container_running`, `wait_for_agent_service`, `build_image`. |
| `storage.py` | `FileStore` — process-scoped on-disk store for uploads with TTL GC, magic-byte validation, extension allowlist, and per-file size cap. |
| `observability.py` | Session-scoped logging context, JSON formatter, optional trace recorder writing one JSON file per session under `CUA_TRACE_DIR`. |

### Sandbox (`docker/`)

| File | Responsibility |
|---|---|
| `Dockerfile` | Ubuntu 24.04 + Xvfb + XFCE4 + browsers + LibreOffice/GIMP/Inkscape/VS Code + agent service + noVNC/websockify. |
| `agent_service.py` | FastAPI app inside the container. Endpoints: `GET /health`, `GET /screenshot`, `POST /action`. Enforces the action-name allowlist, validates xdotool key tokens, falls back to `scrot` for screenshots. |
| `entrypoint.sh` | Boots Xvfb, XFCE, noVNC, then the agent service. |
| `SECURITY_NOTES.md` | Lists allowed action names and which legacy ones require `CUA_ENABLE_LEGACY_ACTIONS=1`. |

## Request Lifecycle

A session goes through these stages, with the responsible module called out
at each step.

### 1. HTTP entry — `POST /api/agent/start`

Handled by `backend/server.py`. The request body is parsed against
`StartTaskRequest`, which uses `extra="forbid"`; unknown fields cause a 422
before the provider is touched.

Server-side validation, in order:

1. Body size is enforced before parsing by `BodyLimitMiddleware`
   (`CUA_MAX_BODY_BYTES`, default 256 KiB).
2. Origin / Host header gates run for every state-changing route.
3. Per-IP rate limiter checks the sliding window: max 10 starts per minute,
   max 3 concurrent sessions, max 20 key validations per minute.
4. The provider-and-model pair is validated against
   `allowed_models.json`. The map is the single source of truth; nothing
   downstream is allowed to "know" model SKUs out-of-band.
5. The chosen API key is resolved from the request, falling back to env
   variables. If absent, a `StructuredError` with code `MISSING_API_KEY` is
   returned.
6. If `attached_files` is non-empty, every file id is verified against the
   `FileStore` and re-checked for provider compatibility (Gemini rejects).

If validation passes, the server creates an `AgentSession` row, starts a
container if the sandbox is not already healthy, and dispatches the work to
`AgentLoop` on the running asyncio loop.

### 2. AgentLoop — bridge to the provider

`AgentLoop.run` lives in `backend/loop.py`. It owns the live session and
exposes the only async function the provider layer needs to know about. It:

- Builds a `ComputerUseEngine` if the request still routes through the
  facade. Pure provider runs use the wrapper functions directly.
- Subscribes its callbacks (`on_step`, `on_log`, `on_screenshot`) to the
  provider event stream. These callbacks are fire-and-forget; exceptions are
  swallowed so a misbehaving subscriber cannot kill the run.
- Hashes each turn's action via `blake2b(action_name + coordinates + text)`.
  Three consecutive identical fingerprints trigger a stop request and
  cancel the in-flight provider task immediately.
- Watches a `safety_event` for safety prompts. When a prompt arrives, it
  broadcasts a WebSocket message and awaits an answer from
  `backend/safety.py` for up to 60 seconds (then auto-denies).

### 3. Provider wrapper — `backend/providers/<provider>.py`

The wrapper builds (or accepts) a CU client, normalizes the tools dataclass,
runs the optional planner pass, then yields events from
`stream_client_run_loop`. It also owns the lifecycle of any executor it
created (closes connections via `aclose`).

### 4. CU client `run_loop`

Each engine client implements `run_loop(goal, executor, turn_limit,
on_safety, on_turn, on_log)`. The body is provider-specific: OpenAI uses a
manual replay loop against the Responses API, Anthropic walks a tool-use /
tool-result message loop, Gemini iterates `GenerateContent` calls.

### 5. Streaming back to the UI

`stream_client_run_loop` translates the per-turn callbacks into
`ProviderEvent` objects (`turn`, `log`, `final`, `error`) and yields them as
a Python async generator. The wrapper bubbles those up to the agent loop,
which fans them out to the WebSocket subscribers via the broadcaster in
`server.py`.

### 6. Termination

Termination paths in priority order:

1. Operator-issued stop (`POST /api/agent/stop/{session_id}`).
2. Provider returns a final response with no more tool calls.
3. Stuck-agent detector trips.
4. Step limit is reached.
5. Provider raises an exception → translated into a `StructuredError` and a
   final `agent_finished` WS event with `status="error"`.

In every case, the executor's HTTP client is closed (when owned by the
wrapper) and the session row is updated with the final state.

## Provider Run Contract

Each provider wrapper exposes a single async generator with this signature:

```python
async def run(
    task: str,
    *,
    tools: ProviderTools | Mapping[str, Any] | None = None,
    files: Sequence[str] | None = None,
    on_event: EventCallback | None = None,
    on_safety: SafetyCallback | None = None,
    executor: Any | None = None,
    **options: Any,
):
    """Yield ProviderEvent items: log, turn, final."""
```

`ProviderTools` is the only place the public tool surface is declared:

```python
@dataclass(frozen=True)
class ProviderTools:
    web_search: bool = False
```

There is no `file_search` flag because file retrieval is implied by passing
file ids: if `files` is non-empty and the provider supports retrieval, the
file path is enabled; otherwise the request is rejected. This keeps the
public contract a single boolean plus a list, which matches what the UI
actually exposes.

`ProviderEvent.type` is one of:

| Event | Payload |
|---|---|
| `log` | `{"level": str, "message": str}` |
| `turn` | A `StepRecord`-shaped dict with action, coordinates, reasoning, screenshot, and timing. |
| `final` | `{"text": str, "completion_payload": dict}` — the final assistant text and any provider-side metadata (Gemini grounding, OpenAI sources, etc.). |
| `error` | An exception object. The wrapper re-raises it after emitting; subscribers should treat this as the terminal event. |

The wrapper, not the engine client, owns:

- Building or accepting a `DesktopExecutor`.
- Running the planner pass when `tools.web_search` is true.
- Closing the executor on exit if it built one (`close_executor=True`).
- Restoring the client's `_use_builtin_search` flag if the planner forced it
  off for the executing phase.

Engine clients still expose a low-level `run_loop` for tests and the
`ComputerUseEngine` facade. The wrapper is the only public entry point that
matches the documented `run(...)` shape.

## Two-Phase Web Search Planner

When `use_builtin_search` is true, the run is split into two distinct
provider calls instead of advertising web search and computer tools to the
same model at the same time. This is implemented in
`backend/providers/planner.py` and orchestrated by
`maybe_plan_with_web_search` in `backend/providers/_common.py`.

### Why two phases

Mixing the computer tool and a web search tool in the same call has two
failure modes that show up in practice:

1. The model spends turns searching when the task is purely local desktop
   work ("open VS Code", "create a folder on the desktop"), wasting wall
   clock and API budget.
2. Provider-specific minimum-effort modes (for example, OpenAI GPT-5 with
   `reasoning.effort="minimal"`) interact badly with simultaneous search +
   computer tools.

The two-phase plan removes both classes of failure: web search is used only
for interpretation and external facts; the computer phase has no search tool
to call.

### Planner prompt shape

`_PLANNER_PROMPT` in `planner.py` requires the model to return:

```
- Interpreted task
- Environment assumptions
- Step-by-step execution brief
- Verification condition
- Pitfalls
```

The model is explicitly told it must not perform desktop actions in this
phase and must use the search tool only when it helps interpret the task,
the application name, OS behavior, or current public web facts.

### Per-provider planning calls

| Provider | Planner tool | SDK shape |
|---|---|---|
| OpenAI | `[{"type": "web_search"}]` | `client.responses.create(...)` with `tools=[{"type":"web_search"}]`, `parallel_tool_calls=False`, `include=["web_search_call.action.sources"]`. For `gpt-5*` models the call sets `reasoning.effort="low"` so the planning pass stays bounded. |
| Anthropic | `web_search_20250305` (`max_uses=3`) | `client.beta.messages.create(...)` with `max_tokens=2048` and a system prompt instructing the model to produce a brief only. The org-level web-search probe is reused. |
| Gemini | `Tool(google_search=GoogleSearch())` | `client.aio.models.generate_content(...)` with no computer tool. |

Each planning helper extracts the brief from the SDK response and returns a
plain string. If the SDK call fails or the brief is empty,
`maybe_plan_with_web_search` emits a warning log event and falls back to
running the CU loop without a brief.

### Brief injection

When the planner returns a non-empty brief, `build_planned_computer_use_task`
combines it with the original task:

```text
Complete the original user task using the computer tool only.

Original user task:
<task>

Execution brief from the provider-native planning/search phase:
<brief>

Do not use web search in this phase. Use screenshots and computer
actions to complete the task. Stop only when the verification
condition is true.
```

`stream_client_run_loop` is then invoked with `force_computer_only=True`,
which temporarily flips `client._use_builtin_search` to `False` while the CU
loop runs and restores the original value when the loop exits. The
advertised tool list during the desktop phase therefore contains only the
provider's computer tool plus, when files are attached, OpenAI `file_search`
or Anthropic Files API document blocks.

### Event ordering

`maybe_plan_with_web_search` emits all planner-phase log events through
`on_event` before returning, then yields the events again to the caller.
This means a UI subscribing to the same event stream sees the planning
phase's log output interleaved with the CU phase's events in source order,
not buffered until completion.

## Engine Layer Internals

### Tool Matrix (executing phase)

| Condition | OpenAI | Anthropic | Gemini |
|---|---|---|---|
| Base run, no files | `computer` | `computer_20251124` | `computer_use` |
| Web Search on (after planner) | `computer` only | `computer_20251124` only | `computer_use` only |
| Files uploaded | `computer` + `file_search` | `computer_20251124` + Files API document blocks | rejected |
| Web Search on + files | `computer` + `file_search` | `computer_20251124` + Files API document blocks | rejected |

Gemini file uploads are rejected because Gemini File Search is not documented
as part of this app's Computer Use path.

### OpenAI engine — `backend/engine/openai.py`

`OpenAICUClient` drives the Responses API computer tool with stateless
replay.

- **Stateless replay.** The client never sets `previous_response_id`. Every
  request includes the full conversation history. This is intentional for
  Zero Data Retention compatibility and makes retries trivial.
- **Tool shape.** The advertised `computer` tool is the short-form
  `{"type": "computer"}` for `gpt-5.4` and later. Older shapes are not
  emitted.
- **Screenshot resize.** OpenAI accepts `detail: "original"` only up to
  10,240,000 pixels and a 6000 px long edge. Before each request, the
  client downscales the latest screenshot if needed and remembers the scale
  factor so any pixel coordinates returned by the model are remapped back
  to real screen space.
- **`computer_call_output.detail`.** Every output block sets
  `detail: "original"` so the model is not forced to re-process a
  thumbnail.
- **`web_search_call.action.sources`.** The client requests the source
  metadata via `include=[...]` so URLs from the planner phase are visible
  in logs.
- **False-completion guard.** A first turn that returns assistant text
  without any computer tool call, on a task that clearly requires a UI
  action, is rejected via a heuristic and the model is reprompted.
- **`reasoning.effort`.** Defaults are taken from
  `default_openai_reasoning_effort_for_model`: `"none"` for `gpt-5.4`
  variants, `"medium"` otherwise.
- **`file_search`.** When files are attached, the wrapper passes a
  `vector_store_id` and the client appends a `file_search` tool. Vector
  store cleanup is owned by `backend/files.py`.

### Anthropic engine — `backend/engine/claude.py`

`ClaudeCUClient` uses `client.beta.messages.create()` (the beta endpoint is
required for Computer Use).

- **Tool version routing.** The advertised tool name (`computer_20251124`)
  and the beta header (`computer-use-2025-11-24`) are read from
  `allowed_models.json` via `_lookup_claude_cu_config`. There is no
  substring matching on the model name; rolling out a new tool version is a
  registry-only change.
- **Web-search org probe.** The first time a session attempts to use web
  search, the client makes a minimal probe call to confirm the org has
  access. Result is cached for 24 hours keyed by the SHA-256 of the API
  key. `CUA_ANTHROPIC_WEB_SEARCH_ENABLED=1` skips the probe.
- **Document blocks.** PDFs and TXT are uploaded to the Anthropic Files
  API and referenced as `document` content blocks. Markdown and DOCX are
  extracted to plain text and inlined; Anthropic document blocks do not
  accept these formats in the Computer Use beta.
- **`max_tokens`.** Per-turn budget defaults to `CUA_CLAUDE_MAX_TOKENS`
  (default 32,768).
- **Stop reasons.** `end_turn`, `stop_sequence`, `tool_use`, and
  `max_tokens` are handled distinctly. `max_tokens` raises a structured
  error rather than silently truncating.

### Gemini engine — `backend/engine/gemini.py`

`GeminiCUClient` uses the `google-genai` SDK with the documented
`Tool(computer_use=ComputerUse(...))` type.

- **Normalized coordinates.** Gemini emits coordinates on a 0–999 grid.
  `denormalize_x` and `denormalize_y` in `executor.py` convert to real
  pixels using the configured screen size.
- **History pruning is atomic.** When pruning to `max_history_turns`
  (default 10), entire turns are dropped. Field-level rewrites would break
  the `toolCall` / `toolResponse` / `thoughtSignature` invariants Gemini
  documents for tool-call replay.
- **Grounding metadata.** When the planner phase emits Google Search
  grounding, the metadata is normalized into a structured payload and
  attached to the final session record.
- **No web search in the CU phase.** The CU loop advertises only
  `computer_use`. Search runs only during the planner phase.

### `ComputerUseEngine` facade — `backend/engine/__init__.py`

The facade class is kept for the registry-driven validation path and shared
helpers. It:

- Validates the provider/model pair against `allowed_models.json`.
- Picks the right CU client and forwards `execute_task`.
- Owns shared coordinate helpers, key-token allowlist, retry decorators, and
  transient-error classification.

### Retry and transient errors

A small retry decorator wraps SDK calls with exponential backoff for
documented transient classes: HTTP 429, HTTP 5xx, connection resets, and
provider-specific overload errors. Non-transient errors (auth, schema
validation, content policy) bypass retry and propagate as-is.

## Desktop Executor and Sandbox Boundary

`backend/executor.py` is the only path between provider code and the Docker
container.

### Public surface

```python
class ActionExecutor(Protocol):
    async def capture_screenshot(self) -> bytes: ...
    async def execute(self, name: str, args: Mapping[str, Any]) -> CUActionResult: ...
    async def aclose(self) -> None: ...

class DesktopExecutor(ActionExecutor):
    def __init__(
        self,
        screen_width: int,
        screen_height: int,
        normalize_coords: bool,
        agent_service_url: str,
        container_name: str,
    ) -> None: ...
```

`CUActionResult` carries the action name, its arguments after coordinate
denormalization, the next screenshot, optional safety prompt, and any
provider-specific metadata.

`SafetyDecision` is a sentinel + reason pair returned by safety callbacks;
the executor surfaces it back to the engine client without interpreting it.

### Connection management

The executor uses a shared `httpx.AsyncClient` per session, configured with
loopback-only base URL (`http://127.0.0.1:9222`). `close_shared_executor_clients`
is called from process shutdown hooks so a hot reload does not leak
connections.

### Coordinate handling

- OpenAI and Anthropic emit real pixel coordinates. The executor passes
  them through after a sanity range check.
- Gemini emits values on a 0–999 grid. `normalize_coords=True` triggers
  `denormalize_x` / `denormalize_y` using the configured screen size.
- Coordinates are clamped to `[0, screen_width-1]` / `[0, screen_height-1]`
  before being sent to the agent service. This prevents off-screen clicks
  from blocking on `xdotool`.

### Action dispatch

`execute(name, args)` performs:

1. Look up the action name in the local allowlist (a dict of `{name:
   handler}`). Unknown names raise `ValueError` and never reach the
   container.
2. POST `/action` with `{"name": ..., "args": ...}` and an optional bearer
   token (`AGENT_SERVICE_TOKEN`).
3. Parse the response, capture the new screenshot in the same call when
   the agent service includes it, and wrap into `CUActionResult`.

### Sandbox-side enforcement (`docker/agent_service.py`)

The container's agent service is the second wall around action execution.
It enforces:

- Action-name allowlist (`SECURITY_NOTES.md` is the human-readable list).
- xdotool key-token allowlist — every `keysym` is checked against a
  hardcoded set before being passed to `xdotool key`. Tokens not on the
  list are dropped, which prevents keystroke injection via crafted action
  args.
- Optional bearer token (`AGENT_SERVICE_TOKEN`), required when the
  container is reachable from anywhere other than loopback.
- Legacy actions (shell execution, clipboard, window management) are
  disabled by default and only re-enabled when
  `CUA_ENABLE_LEGACY_ACTIONS=1` is set in the container env.

### Screenshot capture

`GET /screenshot?mode=desktop` returns a base64 PNG of the current
framebuffer. The agent service uses `scrot` for capture and falls back to a
direct `xwd` + `convert` pipeline if `scrot` is unavailable.

When the container's HTTP service is unreachable, the executor falls back to
`docker exec <container> scrot -o /tmp/cua-fallback.png` and reads the file
back over `docker cp`. This path is only used when the agent service is in a
brief startup window.

## File Retrieval Contract

`backend/files.py` is the only place provider differences for uploaded files
live.

### Upload path

`POST /api/files/upload` multipart endpoint:

1. Read the file into memory with a streaming size guard (1 GB cap by
   default).
2. Validate the extension against `{".pdf", ".txt", ".md", ".docx"}`.
3. Cross-check magic bytes against the declared MIME type. A `.pdf` whose
   first bytes are not `%PDF-` is rejected.
4. Persist the bytes to a process-scoped temp directory via `FileStore`.
5. Return `{file_id, filename, size_bytes, mime_type}`.

The `FileStore` runs an idle GC thread that sweeps entries older than 6
hours, so uploaded files do not survive a long-running process forever.

### Provider preparation

When a session starts and `attached_files` is non-empty, `backend/files.py`
calls one of:

- `prepare_openai_file_search(client, file_ids, ...)` —
  - Creates a `vector_store` via the OpenAI Responses API.
  - Uploads every file with `purpose="user_data"`.
  - Polls until each file is `processed`.
  - Returns the `vector_store_id` so the engine can attach a
    `file_search` tool.
  - Registers a cleanup hook that deletes the vector store and individual
    file objects on session end. Cleanup is best-effort: a failure
    surfaces as a warning log, never as a session error.
- `prepare_anthropic_documents(client, file_ids, ...)` —
  - PDFs and TXT are uploaded to the Anthropic Files API. The
    `file_id` plus `media_type` is returned so the engine emits
    `{"type": "document", "source": {"type": "file", "file_id": ...}}`
    blocks.
  - Markdown and DOCX cannot be sent as document blocks under the Computer
    Use beta. They are extracted to plain text and returned as inline
    `text` content blocks instead.
  - Re-upload across turns is avoided via a per-session cache keyed by
    file id.
- Gemini — raises `ValueError(GEMINI_CU_FILE_REJECTION)` before any provider
  call. The server catches this in `/api/agent/start` and returns a 400 with
  a structured error.

### File id semantics

- `file_id` strings returned by `/api/files/upload` are opaque local UUIDs.
  They are not provider ids.
- `attached_files` in the agent start request contains these local UUIDs.
- The provider id (OpenAI file id, Anthropic file id) is generated by the
  preparation helper at session start and lives only in memory for the
  duration of the run.

The backend accepts `.pdf`, `.txt`, `.md`, and `.docx`. Provider-side limits
may be stricter than the local upload cap and are surfaced as provider
errors at session start.

## WebSocket Fan-Out and Streaming

The WebSocket is a one-way broadcast channel from the backend to the
frontend, with three small inbound message types for screenshot
subscription and ping.

### Topology

- A single `WebSocketRegistry` in `backend/server.py` owns the set of
  connections and a per-session subscriber map.
- A single async publisher task per session reads events from the agent
  loop and pushes them into the registry; the registry fans out to
  subscribers.
- Screenshot events are deduplicated by content hash to avoid pushing
  identical frames repeatedly.
- When no subscriber is attached and
  `CUA_WS_SCREENSHOT_SUSPEND_WHEN_IDLE=1`, the publisher pauses
  screenshot capture entirely until a subscriber appears, freeing the
  agent service from unnecessary work.

### Inbound message types

| Event | Effect |
|---|---|
| `screenshot_subscribe` | Add this socket to the per-session subscriber list. |
| `screenshot_unsubscribe` | Remove this socket. |
| `ping` | Reply with `pong`. Used by the UI to detect dead connections. |

### Outbound event types

| Event | Source | Notes |
|---|---|---|
| `log` | `on_log` callback | Goes through the secret scrubber before broadcast. |
| `step` | `on_step` callback | Mirrors a `StepRecord` row including action, coordinates, and reasoning. |
| `screenshot` | screenshot publisher | Base64 PNG; deduplicated. |
| `agent_finished` | session terminator | Final state, total steps, final text, optional Gemini grounding payload. |
| `safety_prompt` | `backend/safety.py` | Carries the provider-supplied prompt text and the session id. |
| `pong` | ping handler | — |

### Token gate

When `CUA_WS_TOKEN` is set, every WebSocket connection must include
`?token=<value>`. Mismatched tokens are rejected with close code `4401`
before any message is read.

## Safety Confirmation Pipeline

Provider `require_confirmation` events are surfaced to the operator and
block the provider call until decided.

### Backend side — `backend/safety.py`

`SafetyRegistry` is an in-memory map of `session_id -> SafetyState`. Each
state carries:

- `event` — an `asyncio.Event` the provider call awaits.
- `prompt` — the human-readable text from the provider.
- `decision` — populated when the operator answers.
- `created_at` — used by the auto-deny watchdog (60 second timeout).

When the engine client encounters a confirmation tool call, it calls
`registry.request(session_id, prompt)` and awaits `event.wait()`. The
server fires a `safety_prompt` WebSocket event in parallel. When
`POST /api/safety/confirm` is called, the registry stores the decision and
sets the event. If no decision arrives within 60 seconds, an automatic
`deny` is set so the run cannot stall forever.

### Provider side — engine clients

Each engine client maps its provider's confirmation contract onto
`SafetyDecision`:

- OpenAI: `pending_safety_check` items in the response.
- Anthropic: `tool_use` blocks with the Computer Use safety subtype.
- Gemini: documented safety acknowledgement field on the function call.

The client never decides locally — it always relays through the registry.

## Configuration and Environment

`backend/infra/config.py` defines a `Config` dataclass and a `config`
singleton. It reads env vars (and optionally a `.env` file at process
start) and clamps numeric values to safe ranges.

### Numeric clamping rules

| Key | Range |
|---|---|
| `MAX_STEPS` | 1–200 |
| `SCREEN_WIDTH` / `SCREEN_HEIGHT` | 320–4096 |
| `CUA_WS_SCREENSHOT_INTERVAL` | 0.25–10 seconds |
| `CUA_CONTAINER_READY_TIMEOUT` | 1–600 seconds |
| `CUA_MAX_BODY_BYTES` | 1024–10,485,760 (1 KiB to 10 MiB) |
| `CUA_CLAUDE_MAX_TOKENS` | 256–200,000 |

### Path normalization

Path-shaped env vars (`CUA_TRACE_DIR`, `CUA_UPLOAD_DIR`) are normalized to
absolute paths and the directories are created on first use. Values that
fall outside the allowed roots (for example, attempts to point
`CUA_TRACE_DIR` at `/etc/`) are rejected at startup.

### CORS and Host allowlist derivation

`CORS_ORIGINS` is parsed once. Each entry is validated against a strict
`scheme://host[:port]` regex; values that fail are dropped with a warning.
The Host allowlist is derived from the validated CORS list plus
`CUA_ALLOWED_HOSTS` (additive). Loopback is always allowed.

### Reload safety

`CUA_RELOAD=1` enables uvicorn auto-reload, but the Docker container is
intentionally not reloaded with the backend — restarting the sandbox on
every code change would slow development to a crawl. The trade-off is that
changes to the Docker image require a manual `docker compose up -d
--build`.

## Security Model

The threat model assumes the operator runs a single instance on
`localhost`. The hardening below is layered so accidentally exposing the
process to a non-loopback address still requires multiple deliberate
configuration changes.

### Network surface

- All ports bind to `127.0.0.1` by default. `HOST=0.0.0.0` is supported but
  must be paired with `CUA_WS_TOKEN`, a reverse-proxy auth layer, and an
  explicit `CUA_ALLOWED_HOSTS`.
- The container's published ports (`6080`, `9222`) are bound to
  `127.0.0.1` in `docker-compose.yml`.

### Middleware stack

Every HTTP request flows through, in order:

1. `BodyLimitMiddleware` — rejects oversize bodies before reading.
2. `HostHeaderMiddleware` — DNS-rebinding gate using the derived
   allowlist.
3. `OriginCsrfMiddleware` — for state-changing routes, requires a matching
   `Origin` or a loopback peer.
4. `SecurityHeadersMiddleware` — adds `X-Content-Type-Options`,
   `X-Frame-Options: DENY`, a strict `Content-Security-Policy`,
   `Cross-Origin-Opener-Policy: same-origin`,
   `Cross-Origin-Embedder-Policy: require-corp`,
   and `Permissions-Policy: camera=(), microphone=(), geolocation=()`.
5. `RateLimitMiddleware` — sliding-window per-IP limiter.

### Schema strictness

All Pydantic models declare `extra="forbid"`. Unknown fields cause a 422
without a deserialization side effect. This is the single largest defense
against client-side typos that would otherwise silently change runtime
behavior.

### Secret hygiene

- API keys are never logged. The structured logger has a redactor that
  substitutes any token matching common provider key prefixes with
  `***REDACTED***`.
- The same redactor runs over WebSocket broadcasts so a model that echoes
  a key never reaches the UI.
- `CUA_WS_TOKEN` is loaded once and compared with `secrets.compare_digest`.

### Container hardening (`docker-compose.yml`)

- `cap_drop: ALL` and `security_opt: no-new-privileges:true`.
- Resource caps: 4 GB memory, 2 CPUs, 256 PIDs.
- `tmpfs` mounts for `/tmp` (512 MB) and `/var/run` (16 MB).
- No host-mounted volumes by default. Files reach the container only
  through the agent service.

## Observability and Tracing

### Logging

- `LOG_FORMAT=console` (default) emits human-readable lines suitable for
  `docker logs`.
- `LOG_FORMAT=json` emits one JSON object per line with
  `{timestamp, level, logger, message, session_id, request_id, ...}`.
- Session-scoped context (`session_id`) is attached via a contextvar, so
  log lines emitted from inside the provider loop carry the session
  automatically.

### Trace recorder

If `CUA_TRACE_DIR` is set, every session writes a single JSON file at
session end:

```json
{
  "session_id": "...",
  "started_at": "...",
  "ended_at": "...",
  "provider": "openai",
  "model": "gpt-5.5",
  "use_builtin_search": false,
  "attached_files": [],
  "steps": [{"step_number": 0, "action": "click", ...}],
  "final_text": "...",
  "errors": []
}
```

The recorder is a passive subscriber to the same event stream the
WebSocket fan-out uses; it never blocks the run.

### Health endpoints

- `GET /api/health` — process liveness only.
- `GET /api/ready` — multi-check readiness: Docker daemon reachable, at
  least one provider key present, container in a healthy state.
- `GET /api/agent-service/health` — proxy of the in-container
  `GET /health` endpoint, useful for diagnosing whether `xdotool` and
  `scrot` are responding.

## Test Architecture

### Layout

```text
tests/
├── conftest.py                          # Shared fixtures and patches
├── engine/
│   ├── test_openai.py                   # Stateless replay, screenshot resize, file_search
│   ├── test_claude.py                   # Tool version routing, web-search probe, document blocks
│   ├── test_gemini.py                   # Coordinate denormalization, history pruning
│   └── test_engine.py                   # Facade behavior, retries, transient error mapping
├── docker/
│   └── test_agent_service.py            # Action allowlist, key-token validation
├── integration/
│   └── test_gemini_live_sdk.py          # Live SDK transport (excluded by default)
├── test_server.py                       # HTTP endpoint integration tests
├── test_server_validation.py            # Schema, rate limit, host allowlist, body cap
├── test_provider_run_contract.py        # ProviderTools / run() public contract
├── test_files.py                        # FileStore + provider preparation helpers
├── test_executor_split.py               # DesktopExecutor dispatch and key allowlist
├── test_models.py                       # allowed_models.json schema and registry
├── test_infra.py                        # Config clamping and observability
├── test_audit_fixes.py                  # Sentinel tests preventing regression on fixed audits
├── test_fixes_wave_apr2026.py           # April 2026 fix wave coverage
├── test_fixes_wave_apr2026_followup.py  # Follow-up wave (gpt-5.4 tool migration, registry)
├── test_gap_coverage.py                 # Gap-coverage tests for previously untested branches
├── test_integration_hot_paths.py        # Hot-path integration without real SDKs
└── test_gemini_changelog_watchdog.py    # Watchdog for Gemini upgrade evaluation
```

### Markers

`pytest.ini` (via `pyproject.toml`) defines two markers:

- `integration` — live-SDK or live-Docker tests, excluded from the default
  run.
- `slow` — anything over a few seconds.

### Async mode

`asyncio_mode = "auto"` is set globally. Tests can declare async functions
without `@pytest.mark.asyncio`.

### Fixture conventions

- `client` — `TestClient` over the FastAPI app with the executor patched
  to a fake.
- `fake_executor` — an in-memory `ActionExecutor` that records calls and
  returns deterministic screenshots.
- `temp_file_store` — clean `FileStore` rooted at a `tmp_path`.

### Running

```powershell
python -m pytest -p no:cacheprovider tests evals --tb=short
```

Focused checks:

```powershell
python -m pytest tests/test_provider_run_contract.py --tb=short
python -m pytest tests/test_server_validation.py --tb=short
python -m pytest tests/engine/test_openai.py tests/engine/test_claude.py tests/engine/test_gemini.py --tb=short
python -m pytest -m integration --tb=short                # opt-in live tests
```

## Frontend Architecture

The frontend is intentionally thin. It is a session controller in front of
the FastAPI WebSocket and REST API.

### Stack

- React 19 + Vite 6, no SSR.
- `react-router-dom 7.13` for two routes (`/` and `*` -> `NotFound`).
- `lucide-react` for icons.
- No global state library. State lives in components and hooks.

### Component map

| File | Role |
|---|---|
| `src/main.jsx` | Mounts the router and the workbench page. |
| `src/api.js` | Typed REST client. Single fetch wrapper with body-cap pre-check. |
| `src/utils.js` | `escapeHtml`, `formatTime`, `estimateCost`, `getSessionHistory`, theme helpers. Cost estimator reads a static `MODEL_PRICING` table and is informational only. |
| `src/hooks/useWebSocket.js` | Thin wrapper around the WebSocket. Routes inbound events into a callback table. Reconnects with exponential backoff. |
| `src/hooks/useSessionController.js` | Session lifecycle: start, stop, completion, history persistence. Owns the 10-second poll fallback that catches missed WS events. |
| `src/pages/WorkbenchPage.jsx` | Main page. Holds form state and the container poll. |
| `src/pages/workbench/ControlPanelView.jsx` | Provider/model dropdowns, API key input, task box, advanced settings drawer. |
| `src/pages/workbench/Timeline.jsx` | Step-by-step timeline of `step` events. |
| `src/pages/workbench/LogsPanel.jsx` | Streaming logs with copy/download. |
| `src/pages/workbench/HistoryDrawer.jsx` | localStorage-backed session history (max 50 entries). |
| `src/pages/workbench/ExportMenu.jsx` | HTML / JSON / TXT exporters. |
| `src/components/SafetyModal.jsx` | Modal for safety prompts. |
| `src/components/ScreenView.jsx` | Live screenshot view; can switch to noVNC iframe. |

### Persistence

- `localStorage["cua_session_history_v1"]` — last 50 sessions.
- `localStorage["cua_settings_v1"]` — last-used provider, model,
  max_steps, reasoning_effort, use_builtin_search.
- `localStorage["cua_theme"]` — UI theme.

The frontend never persists the API key.

## Extension Points

Adding a new model on an existing provider is the lowest-effort change:

1. Append an entry to `backend/models/allowed_models.json` with the
   provider, model id, display name, and (for Anthropic) `cu_tool_version`
   and `cu_betas`.
2. Restart the backend. The frontend reads `/api/models` and picks up the
   new option automatically.

Adding a new provider is a larger but well-defined change:

1. Implement a new engine client under `backend/engine/<name>.py`
   exposing `run_loop(goal, executor, turn_limit, on_safety, on_turn,
   on_log)`.
2. Implement a wrapper under `backend/providers/<name>.py` matching the
   `run(...)` shape and using `maybe_plan_with_web_search` if the provider
   supports a search tool.
3. Add the provider id to the allowed-providers tuple in
   `backend/engine/__init__.py`.
4. Update `backend/files.py` to either prepare provider-specific file
   context or reject files for the provider.
5. Add tests under `tests/engine/test_<name>.py` covering the SDK request
   shape, replay rules, and any provider-specific quirks.
6. Update `allowed_models.json`, `engine_capabilities.json`, README, and
   this file.

Adding a new tool is intentionally hard. The product contract is "Computer
Use + Web Search + provider file retrieval"; new tools are out of scope by
design.

## Operational Notes

- **Single-process only.** The server holds in-memory state. Running with
  `uvicorn --workers N>1` will desynchronize the rate limiter, session
  registry, and WebSocket fan-out. Use a single worker behind a proxy.
- **Docker is required.** There is no host-execution mode. Tests use a
  patched executor; production runs use the sandbox container.
- **Hot reload is dev-only.** `CUA_RELOAD=1` triggers uvicorn auto-reload.
  The agent service has no hot-reload — it ships in the container image.
- **Provider quotas matter.** Web Search and File Search calls count
  against provider quotas separately from the Computer Use tokens. The
  planner phase is short by design but does spend a request.
- **Stuck-agent detection is conservative.** It triggers on three
  identical fingerprints, so legitimate retries (a click that misses by a
  pixel) are not flagged.
- **Coordinate systems differ by provider.** Tasks that hardcode pixel
  coordinates will misbehave on Gemini; rely on the model's screenshot
  understanding instead of literal coordinates in prompts.
- **Frontend dead files.** `frontend/src/components.jsx`,
  `frontend/src/pages/Workbench.jsx`,
  `frontend/src/pages/workbench/ControlPanel.jsx`, and
  `frontend/src/pages/workbench/panels.jsx` are pre-refactor archives that
  are never imported. They can be deleted in a single commit.
- **`ChromiumPlaywrightExecutor`** is a reference implementation in
  `backend/engine/playwright_executor.py` that is not wired to any active
  code path. Treat it as a future-Playwright-execution-target sketch, not
  a supported runtime.

