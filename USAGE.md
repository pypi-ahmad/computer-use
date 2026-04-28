# USAGE

Operator guide for `computer-use`. This document is written for someone who
will run the app every day, hand it real tasks, and need to reason about what
went wrong when something fails. It covers installation, daily operation,
each piece of the workbench UI, prompt shape, file uploads, the API
surface for scripted runs, configuration knobs, troubleshooting, and
recovery procedures. Everything is grounded in the current code on this
branch — no behavior is described that isn't actually implemented.

The app runs provider-native Computer Use against a Docker desktop. Web
Search is implemented as a separate provider-native planning pass; the
Computer Use loop itself only ever sees the computer tool. Reference-file
retrieval is an optional request-time addition that uses each provider's
documented retrieval contract (vector store for OpenAI, Files API for
Anthropic, rejected for Gemini).

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Installation](#installation)
3. [First Run](#first-run)
4. [Daily Operation](#daily-operation)
5. [The Workbench UI](#the-workbench-ui)
6. [Provider, Model, and Reasoning Effort](#provider-model-and-reasoning-effort)
7. [Web Search Toggle](#web-search-toggle)
8. [Reference Files](#reference-files)
9. [Writing Effective Tasks](#writing-effective-tasks)
10. [Running, Watching, and Stopping](#running-watching-and-stopping)
11. [Safety Confirmations](#safety-confirmations)
12. [Sessions, History, and Export](#sessions-history-and-export)
13. [API Keys and Resolution Order](#api-keys-and-resolution-order)
14. [Configuration Reference](#configuration-reference)
15. [Scripting via REST and WebSocket](#scripting-via-rest-and-websocket)
16. [Troubleshooting](#troubleshooting)
17. [Tests and Verification](#tests-and-verification)
18. [Uninstall and Clean Reset](#uninstall-and-clean-reset)

## Prerequisites

| Requirement | Version | Why |
|---|---|---|
| Docker Desktop or Docker Engine | 24+ | Runs the `cua-environment` sandbox. |
| Python | 3.11+ | Backend uses 3.11 typing features and asyncio task groups. |
| Node.js | 20+ | Vite 6 dev server requires Node 20. |
| Provider API key | OpenAI, Anthropic, or Google AI | At least one is required. The UI lets you switch per-session. |

Check versions:

```powershell
docker --version
python --version
node --version
```

A working `docker compose` (the v2 plugin, not the legacy `docker-compose`
binary) is required. The setup scripts call `docker compose` directly.

The app is a single-user localhost workbench. There is no built-in
authentication. Do not expose the backend (port 8100) or noVNC (port 6080)
to a network you don't trust without first reading
[Configuration Reference](#configuration-reference) and the security
sections of `TECHNICAL.md`.

## Installation

Two paths are supported: a one-command bootstrap and a manual install.

### One-command bootstrap

```powershell
git clone https://github.com/pypi-ahmad/computer-use.git
cd computer-use
cp .env.example .env
# add OPENAI_API_KEY, ANTHROPIC_API_KEY, or GEMINI_API_KEY/GOOGLE_API_KEY
python dev.py --bootstrap
```

`dev.py --bootstrap` performs:

1. Creates a Python virtualenv at `.venv` if missing.
2. Activates the venv and runs `pip install -r requirements.txt`.
3. Runs `npm install` inside `frontend/`.
4. Builds the Docker image `cua-ubuntu:latest` and starts the
   `cua-environment` container via `docker compose up -d`.
5. Waits up to `CUA_CONTAINER_READY_TIMEOUT` seconds (default 30) for the
   in-container agent service on port 9222 to report healthy.

Bootstrap is idempotent: re-running it on an already-installed checkout is
safe. It skips steps that are already satisfied and reports which ones it
ran.

### Manual install

If you want to control each step (or the bootstrap script fails on your
platform), the equivalent manual steps are:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1   # Linux/macOS: source .venv/bin/activate

pip install -r requirements.txt

cd frontend
npm install
cd ..

docker compose up -d
```

After the container is running, start the backend and the frontend in
separate terminals:

```powershell
# Terminal 1 — backend
.\.venv\Scripts\Activate.ps1
python -m backend.main
```

```powershell
# Terminal 2 — frontend
cd frontend
npm run dev
```

### Environment file

Copy the example and edit it:

```powershell
cp .env.example .env
```

The file is heavily commented. The settings you usually need are:

```dotenv
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza...
# GEMINI_API_KEY= (also accepted as an alias for GOOGLE_API_KEY)

OPENAI_REASONING_EFFORT=low
SCREEN_WIDTH=1440
SCREEN_HEIGHT=900
MAX_STEPS=50
```

If you intend to bind the backend to a non-loopback address, also set
`CUA_ALLOW_PUBLIC_BIND=1` and `CUA_WS_TOKEN=<secret>`. Without both, the
process refuses to start when `HOST != 127.0.0.1`.

## First Run

After installation, the first session is a useful smoke test. Open
`http://localhost:3000` and run:

> Open the file manager. Stop when the file manager window is visible.

This task is purely local — no web search needed, no files attached. It
exercises the screenshot capture path, action dispatch, and the WebSocket
event stream end to end. If the agent opens the file manager and the
"Environment Ready" badge stays green throughout, the install is working.

If the run fails before the first screenshot, see
[Troubleshooting](#troubleshooting).

## Daily Operation

Once installed, daily use is one command:

```powershell
python dev.py
```

`dev.py` (no flags) does three things in this order:

1. **Port cleanup.** Kills any process listening on 8100 (backend), 3000
   (frontend), 6080 (noVNC), or 9222 (agent service). This recovers from
   crashed previous runs without manual `kill` commands.
2. **Sandbox check.** Confirms the `cua-environment` container is
   running and healthy. If not, it starts it via `docker compose up -d`
   and waits for the agent service to come up.
3. **Process launch.** Spawns the FastAPI backend and the Vite dev
   server as subprocesses, forwarding their stdout and stderr to the
   current terminal.

The tool keeps running until you press `Ctrl+C`. On exit, it stops the
launched subprocesses but leaves the Docker container running so the next
launch is fast. To stop the container too:

```powershell
docker compose down
```

The launcher accepts a few flags:

| Flag | Effect |
|---|---|
| `--bootstrap` | Run the full one-command setup before launching. |
| `--no-frontend` | Start only the backend. Useful when running the production frontend build behind a proxy. |
| `--no-backend` | Start only the frontend. Useful when developing the UI against a remote backend. |
| `--rebuild` | Force `docker compose up -d --build` before launch. |

### Open the workbench

Navigate to:

```text
http://localhost:3000
```

The Vite dev server proxies `/api`, `/ws`, and `/vnc/*` to the backend on
port 8100, so you do not need to deal with CORS during normal use.

## The Workbench UI

The single-page workbench has four primary regions:

1. **Control Panel** (left side): provider, model, API key, task box,
   advanced settings drawer, file uploads, Web Search toggle, Start /
   Stop buttons.
2. **Live Screen** (top right): the current screenshot, with a switch
   between deduplicated screenshots and the noVNC iframe view.
3. **Timeline** (right column): a step-by-step list of the model's actions
   with reasoning. Each entry shows the action name, coordinates, and
   the post-action screenshot.
4. **Logs Panel** (bottom): structured log output streamed over the
   WebSocket. Filterable by level. Has copy and download buttons.

Two secondary regions appear contextually:

- **Safety Modal**: opens when a provider raises a `require_confirmation`
  event. Blocks the run until you click Confirm or Deny. See
  [Safety Confirmations](#safety-confirmations).
- **History Drawer**: a scrollable list of the last 50 sessions stored
  in `localStorage`. Click an entry to load its task back into the
  Control Panel.

The header carries the **Environment Status** badge:

| Badge | Meaning |
|---|---|
| Environment Ready (green) | Container is running, agent service responding, at least one provider key resolved. |
| Starting (amber) | Container is up but the agent service has not yet reported healthy. |
| Container Down (red) | `cua-environment` is not running. Click the badge to start it. |
| Build Required (red) | Docker image is missing. Click the badge to run `docker compose up -d --build`. |

## Provider, Model, and Reasoning Effort

The Provider dropdown lists the three supported providers: OpenAI,
Anthropic, and Google (Gemini). Selection sets the relevant `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, or `GOOGLE_API_KEY` env-var hint and filters the Model
dropdown to the matching entries from `allowed_models.json`.

Currently shipped Computer-Use-capable models:

| Provider | Model ID | Display Name | Notes |
|---|---|---|---|
| OpenAI | `gpt-5.5` | GPT-5.5 | Default OpenAI CU model. |
| OpenAI | `gpt-5.4` | GPT-5.4 | Original CU release. |
| Anthropic | `claude-opus-4-7` | Claude Opus 4.7 | `computer_20251124` tool, beta endpoint required. |
| Anthropic | `claude-sonnet-4-6` | Claude Sonnet 4.6 | `computer_20251124` tool, beta endpoint required. |
| Google | `gemini-3-flash-preview` | Gemini 3 Flash Preview | Only Gemini CU SKU exposed by this app. |

The list is the runtime allowlist; the backend will reject any model not
in `backend/models/allowed_models.json`.

### OpenAI reasoning effort

For OpenAI models, the Advanced Settings drawer exposes a `reasoning_effort`
selector. The valid values are `minimal`, `low`, `medium`, `high`, and
`xhigh`. Defaults are model-specific:

- `gpt-5.4` defaults to `none`.
- `gpt-5.5` defaults to `medium`.

Higher effort improves multi-step reliability but costs more tokens and
takes longer per turn. `low` is a good baseline for desktop tasks; bump to
`medium` or `high` for ambiguous or long-horizon work.

### `max_steps`

The Advanced Settings drawer also exposes a `max_steps` slider. The
backend hard-caps this at 200. Most desktop tasks finish in fewer than 30
steps; raise the cap only when you have a specific reason to.

## Web Search Toggle

Web Search is a single boolean per session. When **off**, the model's
advertised tool list is computer-only; nothing else is exposed. When
**on**, the run is split into two phases:

1. **Planning phase.** The provider's documented search tool runs first
   without the computer tool. The prompt asks the model to return a
   compact execution brief with five fixed sections (interpreted task,
   environment assumptions, step-by-step execution brief, verification
   condition, pitfalls).
2. **Execution phase.** The original task is merged with the planner
   brief. The Computer Use loop then runs with the computer tool only
   (and `file_search` if files are attached). The model cannot call web
   search during this phase.

| Provider | Planning tool | Execution tool |
|---|---|---|
| OpenAI | `web_search` (Responses API) | `computer` |
| Anthropic | `web_search_20250305` | `computer_20251124` |
| Google | `google_search` grounding | `computer_use` |

Turning Web Search on costs one extra provider request (the planning
call) and adds a few seconds of latency. Use it when the task involves:

- Current public web facts (releases, prices, names that change).
- An app or workflow you want the model to be confident about before
  acting (the brief makes the steps explicit).
- A URL or service whose location is non-obvious.

Do **not** turn it on for purely local desktop work. Saying "open the file
manager" does not need a web search to interpret.

## Reference Files

The Files panel of the Control Panel accepts up to 10 files per session,
1 GB per file, with these extensions:

| Extension | Provider behavior |
|---|---|
| `.pdf` | OpenAI: vector-store `file_search`. Anthropic: `document` content blocks. Gemini: rejected. |
| `.txt` | OpenAI: vector-store. Anthropic: `document` blocks. Gemini: rejected. |
| `.md` | OpenAI: vector-store. Anthropic: extracted to inline text. Gemini: rejected. |
| `.docx` | OpenAI: vector-store. Anthropic: extracted to inline text. Gemini: rejected. |

The upload flow:

1. Drag a file onto the upload zone (or click to browse).
2. The frontend POSTs to `/api/files/upload`. The backend validates the
   extension, cross-checks magic bytes, and persists the file to the
   process-scoped temp store. It returns an opaque local file id.
3. The id appears as a chip in the Files panel. The chip shows the file
   name and size.
4. When you press Start, the local file ids are sent to the backend in
   the `attached_files` field. The backend prepares the provider-specific
   retrieval shape (vector store, document blocks, or rejects).

Removing a file chip deletes the upload from the local store. Files that
are not removed are GC'd after 6 hours by the backend's idle sweeper.

### File preparation per provider

- **OpenAI** creates a per-run `vector_store` and uploads each file with
  `purpose="user_data"`. The Computer Use run advertises both the
  computer tool and a `file_search` tool whose `vector_store_ids` points
  at this store. After the session ends, the store and its file objects
  are deleted in a best-effort cleanup pass.
- **Anthropic** uploads PDFs and TXTs through the Files API and emits
  `document` content blocks for them. Markdown and DOCX cannot be sent as
  document blocks under the Computer Use beta, so they are extracted to
  plain text and inlined as `text` content blocks.
- **Gemini** raises a `400` with a structured error before the provider
  call. Gemini File Search is not part of this app's Computer Use path.

The frontend disables the file upload zone when the selected provider is
Gemini.

## Writing Effective Tasks

A good task prompt is concrete, constrained, and verifiable. The
Computer Use prompt guide in `docs/computer-use-prompt-guide.md` has
worked-out examples; this section gives the operating principles.

### Always include

- **Outcome.** What does success look like? "The file manager window is
  visible" not "explore the desktop".
- **Starting point.** Where should the agent begin? "Open the browser
  and go to <url>" instead of "research <topic>".
- **Allowed evidence.** Which sources or apps may the agent use? Cuts
  meandering.
- **Constraints.** Things the agent must not do: "do not sign in", "do
  not submit forms", "do not download anything".
- **Stop condition.** A precise observable state.
- **Final answer format.** Tell the model whether you want a sentence,
  a bullet list, or a JSON object.

### Avoid

- Hard-coded pixel coordinates. They break across providers (Gemini uses
  a normalized 0–999 grid) and across screen sizes.
- Passive verbs ("the page should be opened"). Use imperative.
- Tasks that require credentials the agent does not have. The agent
  cannot guess passwords or 2FA codes.

### Examples

**Local-only:**

```text
Open the calculator app. Type "2 + 2" and press Enter.
Stop when the display shows "4".
Tell me the displayed result.
```

**Web research without files:**

```text
Open the browser and go to the official OpenAI docs.
Find the Computer Use guide.
Do not sign in or change any settings.
Stop when the guide page is visible.
Tell me the page title and the first section heading.
```

**With files:**

```text
Use the attached product notes as the source of truth.
Open the browser and compare the visible pricing page against the attached notes.
Do not submit forms or start purchases.
Stop after you identify any mismatch.
Return a short list of differences.
```

**Multi-step with verification:**

```text
Open VS Code.
Create a new file called notes.txt on the desktop.
Type "hello world" into it and save.
Stop when you can see notes.txt as an open tab in VS Code.
Confirm you saved the file by quoting the file path.
```

## Running, Watching, and Stopping

After filling the Control Panel, click **Start**. The UI:

1. Disables the Start button and clears the Timeline and Logs.
2. Sends `POST /api/agent/start`.
3. Subscribes to the WebSocket and forwards `screenshot_subscribe` for
   the new session.
4. Renders incoming `step`, `log`, `screenshot`, and `safety_prompt`
   events.

The Timeline shows each step's action (click, type, scroll, etc.), its
target coordinates, the model's reasoning text if any, and the screenshot
captured immediately after the action. Hover any entry to expand the full
reasoning.

The Logs panel shows structured log lines. A level dropdown filters to
INFO / WARN / ERROR / DEBUG.

### Stopping

Press **Stop** at any time. The frontend issues
`POST /api/agent/stop/{session_id}`, which:

1. Cancels the in-flight provider task.
2. Closes the executor's HTTP client.
3. Marks the session row as `stopped`.
4. Emits `agent_finished` with `status="stopped"`.

Stop is **immediate** and **safe**: the agent service refuses any further
actions for the dead session, and any retry happening in the engine
client is cancelled with `asyncio.CancelledError`.

### Stuck-agent detection

If the model issues three consecutive identical actions (same name, same
coordinates, same text payload), the backend stops the run automatically
without waiting for the step limit. The reason ("stuck-agent detector")
appears in the Logs and `agent_finished` payload.

Identical here means after coordinate normalization. A click that misses
by a single pixel does not count as identical.

### Step limit

When the run reaches `max_steps` without completing, the engine returns
the latest assistant text and emits `agent_finished` with
`status="max_steps_reached"`. Re-run with a higher cap or a more focused
task.

## Safety Confirmations

When a provider model invokes a `require_confirmation`-style action
(typically: form submission, payment, irreversible file deletion), the
backend pauses the run and the UI opens a modal:

- **Prompt.** The provider-supplied human-readable description.
- **Buttons.** Confirm or Deny.

The provider call awaits your decision via a backend event. The watchdog
auto-denies after 60 seconds, after which the run is marked as failed
and the Logs show `safety_timeout`.

You can stop the run instead of answering. Stop takes priority over the
pending safety prompt.

The safety pipeline is provider-side: the UI cannot fabricate a safety
prompt, and the backend never decides without operator input. See the
"Safety Confirmation Pipeline" section in `TECHNICAL.md` for the full
state machine.

## Sessions, History, and Export

The frontend persists the last 50 sessions to `localStorage` under
`cua_session_history_v1`. Each entry includes:

- Task text
- Provider and model
- Step count
- Final status
- Final answer (truncated)
- Start and end timestamps

Click a row in the History Drawer to load its task and settings back
into the Control Panel. This is for quick re-runs of similar tasks; the
session itself is **not** restored on the backend (the in-memory
session row is gone after the process restarts or after the agent
finishes).

### Exporting a session

While viewing a finished session, the Export menu offers:

- **HTML** — a self-contained file with the timeline, logs, and embedded
  screenshots. Good for sharing.
- **JSON** — the raw event stream including step records and provider
  metadata. Good for programmatic post-processing.
- **TXT** — a flat human-readable transcript.

If `CUA_TRACE_DIR` is set in the backend env, the server also writes a
JSON trace file per session under that directory. The trace file matches
the JSON export shape but is captured server-side without depending on
the browser staying open.

## API Keys and Resolution Order

The backend resolves API keys in this priority order:

1. **UI input.** Whatever you typed in the API key box of the Control
   Panel for the current session.
2. **Request body.** If a scripted client passes `api_key` in the
   `POST /api/agent/start` body.
3. **`.env` file.** `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and
   `GOOGLE_API_KEY` (or `GEMINI_API_KEY`).
4. **System environment.** The corresponding env var in the calling
   shell.

If none of the above is set for the selected provider, the request
returns `400` with a `MISSING_API_KEY` structured error.

The frontend never persists the API key to localStorage. Reloading the
page clears it from the form.

### Validating a key

The Control Panel has a "Validate" button that calls
`POST /api/keys/validate`. The backend makes a minimal documented
validation call against the provider:

| Provider | Validation call |
|---|---|
| OpenAI | `client.models.list()` |
| Anthropic | `client.beta.messages.create()` with a 1-token budget |
| Google | `client.models.list()` via `google-genai` |

The backend caches successful validations for 5 minutes per key hash so
repeat validations are cheap.

## Configuration Reference

All configuration is via environment variables. The complete list lives
in `.env.example`; this section calls out the ones operators usually
need to know.

### Networking

| Variable | Default | Notes |
|---|---|---|
| `HOST` | `127.0.0.1` | Backend bind. Setting to anything else requires `CUA_ALLOW_PUBLIC_BIND=1` and `CUA_WS_TOKEN`. |
| `PORT` | `8100` | Backend port. |
| `CUA_ALLOW_PUBLIC_BIND` | unset | Explicit opt-in for non-loopback `HOST`. The process refuses to start without this when `HOST != 127.0.0.1`. |
| `CUA_WS_TOKEN` | unset | Shared secret for `/ws` and `/vnc/*`. Required for non-loopback bind. |
| `CUA_ALLOWED_HOSTS` | derived from CORS | Extra Host headers to allow. |
| `CORS_ORIGINS` | localhost:3000/5173 | Comma-separated allowlist. |

### Sandbox and screen

| Variable | Default | Notes |
|---|---|---|
| `CONTAINER_NAME` | `cua-environment` | Docker container name. |
| `SCREEN_WIDTH` / `SCREEN_HEIGHT` | `1440` / `900` | Virtual display geometry. Must match the Dockerfile if you change it. |
| `AGENT_SERVICE_HOST` / `AGENT_SERVICE_PORT` | `127.0.0.1` / `9222` | Where the in-container action service listens. |
| `AGENT_SERVICE_TOKEN` | unset | Optional bearer token enforced by the agent service. |
| `CUA_CONTAINER_READY_TIMEOUT` | `30.0` | Seconds to wait for the agent service after `docker compose up`. |
| `CUA_ENABLE_LEGACY_ACTIONS` | `0` | Re-enable shell/clipboard/window-management actions inside the container. **Do not** enable this when binding non-loopback. |

### Agent runtime

| Variable | Default | Notes |
|---|---|---|
| `MAX_STEPS` | `50` | Default `max_steps` slider value. Hard cap is 200. |
| `STEP_TIMEOUT` | `30.0` | Seconds before a single action is considered hung. |
| `OPENAI_REASONING_EFFORT` | model-specific | Default reasoning effort if not specified per session. |
| `OPENAI_BASE_URL` | OpenAI default | Override for regional or proxy deployments. |
| `CUA_CLAUDE_MAX_TOKENS` | `32768` | Per-turn `max_tokens` budget for Claude. |
| `CUA_ANTHROPIC_WEB_SEARCH_ENABLED` | `0` | Skip the org-level web-search probe. Set when you've confirmed the key has access. |

### Streaming

| Variable | Default | Notes |
|---|---|---|
| `CUA_WS_SCREENSHOT_INTERVAL` | `1.5` | Screenshot publish interval in seconds. |
| `CUA_WS_SCREENSHOT_SUSPEND_WHEN_IDLE` | `1` | Pause screenshot capture when no subscriber is connected. |

### Limits

| Variable | Default | Notes |
|---|---|---|
| `CUA_MAX_BODY_BYTES` | `262144` | Reject HTTP bodies over this size. |

### Logging and tracing

| Variable | Default | Notes |
|---|---|---|
| `LOG_FORMAT` | `console` | Set to `json` for one JSON line per log record. |
| `LOG_LEVEL` | `INFO` | Standard Python log level. |
| `DEBUG` | `0` | Set to `1` to switch the backend to debug verbosity. |
| `CUA_TRACE_DIR` | `~/.computer-use/traces/` | Directory for per-session JSON trace files. |
| `CUA_UPLOAD_DIR` | system temp | Override the file upload store directory. |

A change to any of these variables takes effect on the next backend
start. The frontend has no env-var configuration; it reads runtime data
from the backend's `/api/*` endpoints.

## Scripting via REST and WebSocket

The full HTTP API is documented in `README.md` and exhaustively in
`TECHNICAL.md`. This section is the operator-friendly walkthrough of
common scripting patterns.

### Quick start a session from a script

```bash
curl -X POST http://localhost:8100/api/agent/start \
  -H "Content-Type: application/json" \
  -d '{
    "task": "Open the calculator and compute 2 + 2.",
    "provider": "openai",
    "model": "gpt-5.5",
    "max_steps": 30,
    "use_builtin_search": false,
    "attached_files": [],
    "engine": "computer_use",
    "execution_target": "docker"
  }'
```

The response contains a `session_id`. Use it to poll status:

```bash
curl http://localhost:8100/api/agent/status/<session_id>
```

To stop:

```bash
curl -X POST http://localhost:8100/api/agent/stop/<session_id>
```

### Stream events with `wscat`

```bash
wscat -c ws://localhost:8100/ws
{"event":"screenshot_subscribe","session_id":"<session_id>"}
```

The connection then receives `step`, `log`, `screenshot`, and
`agent_finished` events as JSON.

If `CUA_WS_TOKEN` is set, append the token to the URL:

```bash
wscat -c "ws://localhost:8100/ws?token=$CUA_WS_TOKEN"
```

### Upload a file from a script

```bash
curl -X POST http://localhost:8100/api/files/upload \
  -F file=@./notes.pdf
```

The response includes `file_id`. Pass that id in the `attached_files`
list of the next `/api/agent/start` request.

### Submitting a safety confirmation

If the model raises a safety prompt, your script must answer within the
60-second window:

```bash
curl -X POST http://localhost:8100/api/safety/confirm \
  -H "Content-Type: application/json" \
  -d '{"session_id": "<session_id>", "confirmed": true}'
```

### Forbidden fields and rate limiting

Schemas use `extra="forbid"`. Any unknown field returns 422 with a
structured error. The rate limiter is per-IP sliding window:

- 10 agent starts per minute
- 3 concurrent sessions
- 20 key validations per minute

Hitting a limit returns 429 with a `Retry-After` header.

## Troubleshooting

### Container does not start

```powershell
docker compose ps
docker logs cua-environment
docker compose down
docker compose up -d
```

If `docker compose up -d` fails on the build step, run with `--build` and
inspect the output for missing system packages:

```powershell
docker compose up -d --build
```

### Container starts but the badge stays "Starting"

The backend is waiting for the agent service inside the container.
Check:

```powershell
curl http://127.0.0.1:9222/health
```

If this fails, the agent service did not boot. Look at:

```powershell
docker logs cua-environment | Select-String -Pattern "agent" -SimpleMatch
```

The most common causes are:

- A stale `:99` X server lock from a previous container. `docker
  compose down && docker compose up -d` clears it.
- A custom `SCREEN_WIDTH`/`SCREEN_HEIGHT` that does not match the
  Dockerfile geometry. Reset to `1440x900`.

### Backend will not start

`HOST != 127.0.0.1` without `CUA_ALLOW_PUBLIC_BIND=1` and `CUA_WS_TOKEN`
makes the process exit with a clear error. Read the `.env` again. If
you genuinely want non-loopback, set both variables.

A missing dependency manifests as `ModuleNotFoundError`. Reinstall:

```powershell
pip install -r requirements.txt
```

### Frontend will not start

```powershell
cd frontend
npm install
npm run dev
```

If `npm install` fails on a corporate network, point npm at your proxy
and retry. The dev server requires Node 20+.

### "MISSING_API_KEY" when starting a session

The backend could not find a key for the selected provider. Either type
one into the API key box, fill `.env`, or set the env var in the shell
that launched the backend.

### "Web Search is not enabled for this organization" (Anthropic)

The org-level probe found that your Anthropic API key is not entitled
to use `web_search_20250305`. Two options:

- Disable Web Search for the session.
- Enable it on the Anthropic console for your org. After confirming, set
  `CUA_ANTHROPIC_WEB_SEARCH_ENABLED=1` and restart the backend to skip
  the probe on subsequent starts.

### Files attached but Gemini selected

Gemini File Search is not part of this app's Computer Use path. Switch
to OpenAI or Anthropic for runs that need file retrieval, or remove the
files.

### Run hangs at "starting"

Check the Logs for an error. The most common causes:

- Provider rate limit (429). The retry decorator backs off, so wait a
  minute or switch keys.
- Provider auth (401). Re-validate the key.
- Container restart in progress. Wait for the badge to go green.

### "Stuck-agent detector" tripped early

The model is repeating an identical action. Either:

- The task is ambiguous and the model is stuck on a single screen.
  Tighten the prompt.
- A UI element does not exist where the model thinks it does. Try a
  different starting URL or app.
- `max_steps` is set very low and the model has not had a chance to
  recover.

### Screenshot stream stops updating

Click the **Live Screen** view to refresh the WebSocket subscription. If
that does not help, refresh the browser tab; the session continues
server-side.

### Port already in use

`dev.py` clears default ports automatically. If you bypassed it, free
the port manually:

```powershell
Get-NetTCPConnection -LocalPort 8100 | Select-Object OwningProcess
Stop-Process -Id <pid>
```

### Full reset

If multiple things are off, the cheapest reset is:

```powershell
docker compose down --remove-orphans
python dev.py --bootstrap
```

This rebuilds the image, restarts the container, reinstalls Python and
Node dependencies, and boots the app.

## Tests and Verification

The project ships an extensive test suite. After significant config
changes (or before opening a PR), run:

```powershell
python -m pytest -p no:cacheprovider tests evals --tb=short
```

For focused checks:

```powershell
# Provider run() contract
python -m pytest tests/test_provider_run_contract.py --tb=short

# File store and per-provider preparation
python -m pytest tests/test_files.py --tb=short

# Schema, rate-limit, and host-allowlist validation
python -m pytest tests/test_server_validation.py --tb=short

# Engine client unit tests
python -m pytest tests/engine/test_openai.py tests/engine/test_claude.py tests/engine/test_gemini.py --tb=short

# Action dispatch
python -m pytest tests/test_executor_split.py --tb=short
```

Live SDK integration tests are gated behind the `integration` marker:

```powershell
python -m pytest -m integration --tb=short
```

These require a real provider key and outbound network access. They are
excluded from the default run.

The agent service inside the container has its own contract tests in
`tests/docker/test_agent_service.py`. They run against a stub HTTP layer
and do not require Docker.

### Frontend build sanity check

```powershell
cd frontend
npm run build
```

This emits a static bundle to `frontend/dist/`. Failures here are
usually caused by a Node version below 20 or a missing dependency.

## Uninstall and Clean Reset

To remove the running services without deleting data:

```powershell
docker compose down
```

The `cua-environment` image stays on disk. To remove the image too:

```powershell
docker compose down --rmi all
```

To remove the Docker volumes (none are mounted by default, but if you
added some, this is how you wipe them):

```powershell
docker compose down -v
```

To remove uploaded files cached on the host:

```powershell
Remove-Item -Recurse -Force "$env:TEMP/cua-uploads"
```

To reset the Python virtualenv:

```powershell
Remove-Item -Recurse -Force .venv
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

To reset the frontend cache:

```powershell
cd frontend
Remove-Item -Recurse -Force node_modules, dist
npm install
```

To clear browser-side history and theme:

- Open the History Drawer and click "Clear all" (the action removes
  `cua_session_history_v1` from `localStorage`).
- Or use the browser's site-data clear for `localhost:3000`.

After any of these, `python dev.py --bootstrap` reboots from a clean
state.

---

For a deeper look at the runtime contracts and module boundaries, read
`TECHNICAL.md`. For prompt patterns and anti-patterns, read
`docs/computer-use-prompt-guide.md`. For changelog and release notes,
read `CHANGELOG.md`.

## Appendix A — Operating Patterns

This appendix collects patterns that show up repeatedly in real
sessions. None of them are required reading for first-time use, but
they make daily operation noticeably smoother.

### Use the noVNC view to debug, then switch back to screenshots

The Live Screen panel toggles between two render modes:

- **Screenshot stream.** The backend captures one frame at the
  configured `CUA_WS_SCREENSHOT_INTERVAL` (default 1.5 seconds), the
  publisher deduplicates by content hash, and the WebSocket pushes the
  resulting base64 PNG. The view shows what the model itself sees on
  every turn — the same frames the provider receives.
- **noVNC iframe.** The frontend embeds the in-container noVNC client
  on port 6080. This is a continuous render of the actual XFCE
  framebuffer at native frame rate.

Use the noVNC view when:

- A drag operation is failing and you want to see whether the cursor
  moved at all.
- You suspect a modal or transient menu appeared between screenshots
  and was missed.
- A keyboard input does not seem to register.

Switch back to the screenshot view for normal operation. The screenshot
view matches the model's perception, which is what you want when you're
debugging the model's reasoning rather than the desktop itself.

### Pin reasoning effort per task class

Bias toward lower reasoning effort and raise it only for tasks that
need it. A rough decision table:

| Task class | OpenAI reasoning effort | Notes |
|---|---|---|
| Open / close a single app | `minimal` or `low` | Pure UI navigation. |
| Single-app multi-step (file edit, save) | `low` | Default for most desktop work. |
| Cross-app workflow (browser → editor → save) | `medium` | The model needs to keep more state. |
| Research-and-act with web search planning | `medium` or `high` | Brief is non-trivial; execution must follow it. |
| Diagnose a failure or recover from a stuck state | `high` | The model needs more deliberation per turn. |

Whatever you pick, watch the per-turn latency. If a task that should
take 30 seconds is taking minutes per step, the effort level is too
high for the work.

### Prefer URLs to navigation prompts

When a task needs the browser, give the URL directly. "Open the
browser and go to https://example.com" is shorter and more reliable
than "Open the browser, search for example, and click the first result".
The latter is a much longer chain that can fail at any step; the former
is one navigation.

### Constrain the workspace

Most desktop tasks misbehave because the model has too much room to
explore. Cut the search space:

- Tell the model the exact app to use ("Open VS Code" not "open a code
  editor").
- Tell it which files or windows are off limits ("do not modify any
  other open file").
- Tell it whether to close apps when done ("leave VS Code open" or
  "close all windows").

The provider documentation calls these "guardrails". They are the
single largest lever you have.

### Capture the final answer

Always end the prompt with an explicit final-answer instruction:

```text
Stop when the file manager is visible.
Tell me the title bar text and the first three folder names you see.
```

This forces the model to produce a structured final response rather
than just declaring success. The text appears in `agent_finished` and
in the session export.

### Re-run with a tighter prompt instead of a longer step budget

If a run hits `max_steps`, the temptation is to raise the cap. Try the
opposite first: tighten the prompt. A tighter prompt reaches the goal
faster more reliably than a looser prompt with more budget.

### Use the History Drawer for prompt iteration

The History Drawer makes it easy to iterate on a single task across
runs. Run, observe, click the entry, edit the task in the Control
Panel, run again. The previous task is preserved in history; you do
not lose your previous wording.

## Appendix B — Provider-Specific Behavior

Each provider has small idiosyncrasies operators benefit from knowing.

### OpenAI

- **Replay model.** Every Computer Use turn replays the full
  conversation history. This is intentional for ZDR compatibility; it
  also means OpenAI bills for the full history every turn. Token usage
  on long sessions is not linear.
- **Screenshot resize.** The Responses API caps `detail: "original"`
  images at 10,240,000 pixels and a 6000 px long edge. Screenshots
  beyond this are downscaled before upload, and pixel coordinates the
  model returns are remapped back to real screen space.
- **Web search sources.** When the planning pass uses `web_search`, the
  session export includes the source URLs the model consulted under
  `completion_payload.sources`.
- **Reasoning effort defaults.** `gpt-5.4` defaults to `none`, `gpt-5.5`
  defaults to `medium`. The Advanced Settings drawer override applies
  only to the current session.

### Anthropic

- **Beta endpoint.** All Anthropic Computer Use traffic goes through
  `client.beta.messages.create()` with the `computer-use-2025-11-24`
  beta header. If your API key is on a strictly stable channel without
  this beta enabled, the run fails with a `403`.
- **Web search probe.** The first session per API key per 24 hours that
  enables Web Search runs a tiny probe call to confirm the org has
  access to `web_search_20250305`. Cached for 24 hours after success.
- **Document blocks.** PDFs and TXTs are uploaded as Anthropic Files
  and inlined as `document` content blocks. Markdown and DOCX are
  inlined as plain text. There is no provider-side vector store.
- **`max_tokens`.** Set per turn from `CUA_CLAUDE_MAX_TOKENS` (default
  32,768). If a turn truncates because of `max_tokens`, you see an
  explicit error rather than a silent cut-off.

### Google Gemini

- **Coordinate grid.** Gemini emits coordinates on a 0–999 normalized
  grid. The executor denormalizes to real pixels using the configured
  `SCREEN_WIDTH` / `SCREEN_HEIGHT`. If you change those env vars,
  restart the backend so the executor picks up the new geometry.
- **History pruning.** Gemini sessions prune to a sliding window of 10
  turns (configurable via the engine code). Pruning drops entire turns
  to preserve the `toolCall` / `toolResponse` / `thoughtSignature`
  invariants Gemini documents; field-level rewrites would break replay.
- **Files rejected.** Uploads with provider Gemini fail at start.
  Switch provider or remove files.
- **Grounding metadata.** When Web Search is on, the planning pass'
  Google Search grounding payload is normalized and attached to the
  final `agent_finished` event for inspection.

## Appendix C — Resource Profile

Approximate steady-state resource usage for one running session:

| Component | CPU | Memory |
|---|---|---|
| FastAPI backend | <5 % single-core | ~150–250 MB |
| Vite dev server | <2 % single-core | ~120–200 MB |
| Docker container | 1–2 cores burst | up to 4 GB cap (`docker-compose.yml`) |
| Browser tab (workbench) | 1 core during render | 200–400 MB |

Disk usage:

- Docker image: ~3.5 GB after first build (Ubuntu base + browsers +
  LibreOffice + VS Code).
- Per-session uploads: capped at 1 GB per file × 10 files = 10 GB
  worst case. The GC sweeper deletes uploads after 6 hours.
- Trace files (if `CUA_TRACE_DIR` is set): ~100 KB to ~5 MB per
  session depending on screenshot count and size.
- Session history (browser): bounded to 50 entries, stored as JSON in
  `localStorage`.

If you regularly run long sessions or attach many files, consider
pointing `CUA_UPLOAD_DIR` at a fast local disk with sufficient space.

## Appendix D — Privacy Notes

The app keeps everything local by default:

- API keys are never sent to anyone except the chosen provider's
  endpoint. The frontend does not persist them.
- Screenshots stay on the host except when sent to the provider as
  part of the Computer Use loop.
- Uploaded files are sent to the provider only when a session is
  started with `attached_files`. They are also sent to the provider's
  Files API or vector-store API; you should treat them with the same
  privacy posture as any other content you send to that provider.
- Logs and traces stay on the host. Set `CUA_TRACE_DIR` to a directory
  you can audit and rotate.
- The session history in `localStorage` includes the task text, model,
  step count, and a truncated final answer. Clear it from the History
  Drawer when you want to remove that data.

The provider call pipelines are the only outbound traffic the backend
makes during normal operation. The agent service inside the container
does not call out to the network on its own; the model's
browser/desktop actions inside the sandbox can of course initiate
arbitrary outbound traffic from inside the container.

## Appendix E — Quick Reference

### One-line bootstrap

```powershell
python dev.py --bootstrap
```

### Daily run

```powershell
python dev.py
```

### Stop everything

```powershell
docker compose down
```

### Open the workbench

```text
http://localhost:3000
```

### Open the live desktop directly (no UI)

```text
http://localhost:6080/vnc.html?autoconnect=true&resize=scale
```

### Tail backend logs

If you launched via `dev.py`, the backend logs are in the same
terminal. Otherwise:

```powershell
docker logs -f cua-environment           # sandbox logs
```

### Run all tests

```powershell
python -m pytest -p no:cacheprovider tests evals --tb=short
```

### Rebuild the Docker image

```powershell
docker compose up -d --build
```

### Reset a stuck environment

```powershell
docker compose down --remove-orphans
python dev.py --bootstrap
```
