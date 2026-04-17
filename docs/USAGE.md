# CUA Usage Guide

Comprehensive reference for setting up, running, and operating the CUA (Computer Using Agent) workbench — a local AI agent that controls a sandboxed Ubuntu desktop through native Computer Use APIs.

---

## Table of Contents

- [Who This Is For](#who-this-is-for)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Running Locally](#running-locally)
- [Using the Workbench](#using-the-workbench)
  - [First Run](#first-run)
  - [Starting the Environment](#starting-the-environment)
  - [Selecting a Provider and Model](#selecting-a-provider-and-model)
  - [Configuring API Keys](#configuring-api-keys)
  - [Running an Agent Task](#running-an-agent-task)
  - [Monitoring Execution](#monitoring-execution)
  - [Safety Confirmations](#safety-confirmations)
  - [Stopping a Session](#stopping-a-session)
- [Features](#features)
  - [Multi-Provider AI Support](#multi-provider-ai-support)
  - [Docker Sandbox](#docker-sandbox)
  - [Real-Time Streaming](#real-time-streaming)
  - [Step Timeline](#step-timeline)
  - [Session History](#session-history)
  - [Export (JSON, HTML, Logs)](#export-json-html-logs)
  - [Cost Estimation](#cost-estimation)
  - [Context Pruning](#context-pruning)
  - [Safety Confirmation Flow](#safety-confirmation-flow)
  - [OpenAI Reasoning Effort (Thinking Depth)](#openai-reasoning-effort-thinking-depth)
  - [API Key Management](#api-key-management)
  - [Key Validation](#key-validation)
  - [noVNC Desktop Access](#novnc-desktop-access)
  - [Dark / Light Theme](#dark--light-theme)
  - [Help Button](#help-button)
  - [Toast Notifications](#toast-notifications)
  - [Error Boundary](#error-boundary)
- [Supported Models](#supported-models)
- [Supported Actions](#supported-actions)
- [Configuration Reference](#configuration-reference)
- [API Endpoints](#api-endpoints)
- [WebSocket Events](#websocket-events)
- [Keyboard Shortcuts](#keyboard-shortcuts)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)

---

## Who This Is For

- Developers evaluating Computer Use capabilities across Google, Anthropic, and OpenAI
- Researchers benchmarking multi-step desktop automation tasks
- Teams building internal tooling on top of CU APIs and needing a local sandbox

No cloud infrastructure is required. Everything runs on your machine.

---

## Prerequisites

| Requirement | Minimum Version | Notes |
|---|---|---|
| **Docker** | 20.10+ with BuildKit | Docker Desktop recommended on Windows/macOS |
| **Python** | 3.13+ | Used by the FastAPI backend |
| **Node.js** | 18+ | Used by the Vite frontend dev server |
| **OS** | Windows, macOS, or Linux | Docker provides the sandboxed Linux desktop regardless of host OS |

Ensure Docker Desktop is running before proceeding.

---

## Installation

### Automated Setup (Recommended)

**Windows:**

```bat
setup.bat
```

**Linux / macOS:**

```bash
bash setup.sh
```

Both scripts perform the same steps:

1. Verify prerequisites (Docker CLI + daemon, Python, Node.js)
2. Build the Docker image via `docker compose build` (Ubuntu 24.04 desktop)
3. Create a Python virtual environment (`.venv/`) and install backend dependencies from `requirements.txt`
4. Run `npm install` inside the `frontend/` directory

Pass `--clean` to either script to tear down existing containers, images, and volumes before rebuilding.

### Manual Setup

```bash
# 1. Clone
git clone https://github.com/pypi-ahmad/computer-use.git
cd computer-use

# 2. Build Docker image
docker compose build

# 3. Python backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 4. Frontend
cd frontend && npm install && cd ..
```

---

## Running Locally

Start two processes. The Docker container starts automatically from the UI when you launch a task.

| Terminal | Command | Serves |
|---|---|---|
| ① Backend | `python -m backend.main` | FastAPI on `http://127.0.0.1:8000` |
| ② Frontend | `cd frontend && npm run dev` | Vite on `http://127.0.0.1:3000` |

Open **http://127.0.0.1:3000** in your browser.

> **Tip:** There is no need to run `docker compose up` manually — the backend starts and stops the container on demand.

> **Port conflict?** Set `PORT=8001` before starting the backend, and `VITE_API_PORT=8001` for the frontend so the Vite dev proxy routes to the correct backend.

> **Windows:** Prefer `127.0.0.1` over `localhost` to avoid IPv6 binding issues with Docker.

---

## Using the Workbench

### First Run

On your first visit, a **welcome overlay** explains the three-step flow:

1. Choose your AI provider and enter an API key
2. Describe a task for the agent to perform
3. Watch the agent work in real time on the live desktop

The overlay is dismissed once and stored in `localStorage` (`cua_welcomed`). It will not appear again unless you clear browser data.

### Starting the Environment

The Docker container (Ubuntu 24.04 with XFCE4, Chrome, LibreOffice, and development tools) can be started two ways:

- **Automatically** — clicking **Start Agent** starts the container if it is not already running
- **Manually** — click the **Start Environment** button in the header

The header displays real-time status: `Environment Ready` (green) when the container is running, or `Not Started` (grey) when it is stopped. Container status is polled every 5 seconds. A loading indicator appears during startup and shutdown. If an operation fails, an error message is shown inline.

### Selecting a Provider and Model

1. **Provider** — choose from Google Gemini, Anthropic Claude, or OpenAI
2. **Model** — the dropdown auto-populates from `GET /api/models` when a provider is selected

Only models with `supports_computer_use: true` in `backend/allowed_models.json` appear in the dropdown. The backend validates the selected model against the provider's allowlist before starting a session.

### Configuring API Keys

Three sources are available, resolved in priority order:

| Priority | UI Label | How to Set |
|---|---|---|
| 1 (highest) | **Manual** | Type or paste directly in the UI (`type="password"`, never persisted) |
| 2 | **Config File ✓** | Add `GOOGLE_API_KEY=...`, `ANTHROPIC_API_KEY=...`, or `OPENAI_API_KEY=...` in the project root `.env` |
| 3 | **Pre-configured ✓** | Export the same variable names in your shell before starting the backend |

The **Config File** and **Pre-configured** buttons are only shown when a key is actually found from that source. Each displays a checkmark and a masked preview (e.g., `AIza...4xQk`). You can switch between available sources at any time when the agent is not running.

> **Security:** API keys entered in the UI are sent to the backend per-request over localhost and are never written to `localStorage` or any persistent storage.

### Running an Agent Task

1. **Describe the task** in the textarea (max 10,000 characters). Example task chips appear when the field is empty — click one to populate it.
2. Optionally expand **Advanced Settings** to adjust the **Step Limit** (1–200, default 50) or, for OpenAI models, the **Thinking Depth** (reasoning effort).
3. Click **Start Agent** (or press **Ctrl+Enter**).

The agent will:
- Auto-start the Docker container if needed
- Take a screenshot → send to the LLM → receive an action → execute it → repeat
- Stop when the model returns `done`, an error occurs, or the step limit is reached

### Monitoring Execution

While the agent runs, the workbench provides:

| Element | Location | Description |
|---|---|---|
| **Live desktop** | Center pane | Interactive noVNC iframe with screenshot fallback |
| **Progress bar** | Below the desktop | Visual indicator of steps used vs. maximum |
| **Step timeline** | Right panel (top) | Expandable items showing action type, icon, target, coordinates, reasoning, raw JSON |
| **Log panel** | Right panel (bottom) | Scrollable real-time logs with severity badges (info / error / warning / debug) |
| **Step counter** | Header | `Steps: N/M` |
| **Cost estimate** | Header | Approximate session cost (hover for caveat tooltip) |
| **Agent Running pill** | Header | Blue status badge visible while the agent is active |

Both the timeline and log panel auto-scroll to the latest entry.

### Safety Confirmations

When the AI model flags an action that requires explicit approval:

1. A **modal dialog** appears with the action explanation
2. A **60-second countdown** timer is displayed
3. Click **Approve** to proceed or **Deny** to block the action
4. If no response is given within 60 seconds, the action is **automatically denied**

The agent pauses until you respond. After approval or denial, execution resumes.

### Stopping a Session

- Click **Stop** to halt the agent immediately
- The container remains running for manual inspection
- A **completion banner** appears showing the outcome (completed / failed / stopped), step count, elapsed time, and approximate cost (if pricing data is available for the selected model)
- The session is recorded in **session history**

---

## Features

### Multi-Provider AI Support

| Provider | Protocol | Coordinates | Key Env Var |
|---|---|---|---|
| **Google Gemini** | `function_call` | Normalized 0–999 grid → denormalized to screen pixels | `GOOGLE_API_KEY` |
| **Anthropic Claude** | `tool_use` with `computer_20251124` | Real pixel values with pre-resize scaling | `ANTHROPIC_API_KEY` |
| **OpenAI** | Responses API `computer` tool | Real pixel values matching the screenshot | `OPENAI_API_KEY` + optional `OPENAI_BASE_URL` |

Each provider's native Computer Use API is used directly — no prompt-only workarounds or regex parsing.

### Docker Sandbox

All agent actions execute inside an isolated Docker container:

- **Ubuntu 24.04** with XFCE4 desktop environment
- **Resource limits:** 4 GB RAM, 2 CPUs, 2 GB shared memory (`shm_size`)
- **Security:** `no-new-privileges`, `init: true`, localhost-only port bindings (`127.0.0.1`)
- **Pre-installed software:** Google Chrome, LibreOffice, VLC, Node.js 20, Python 3, terminal emulators, file manager
- **Virtual display:** Xvfb at configurable resolution (default 1440×900, 24-bit color)
- **Restart policy:** `unless-stopped`

Your host machine is never exposed to the agent.

### Real-Time Streaming

The backend broadcasts events over a persistent WebSocket connection at `/ws`:

| Event | Payload | Description |
|---|---|---|
| `screenshot` / `screenshot_stream` | base64 PNG | Live desktop captures (stream interval: 1.5 s) |
| `step` | Structured step record | Action details with timestamps |
| `log` | Log entry | Backend log messages with level |
| `agent_finished` | Session result | Completion notification with status and step count |

The frontend auto-reconnects after 2 seconds on disconnect and sends heartbeat pings every 15 seconds.

### Step Timeline

Each agent step is rendered as an expandable timeline item:

- **Icon** matching the action type (30+ mappings via lucide-react: mouse, keyboard, scroll, navigate, clipboard, etc.)
- **Action name** and **target** (truncated at 20 chars with tooltip for full text)
- **Typed text** preview for input actions (quoted, truncated)
- **Timestamp** formatted as `HH:MM:SS` (24-hour)
- **Expand** to see: reasoning text, exact coordinates, error details, and raw JSON payload
- **Keyboard accessible** — `Tab` to focus, `Enter`/`Space` to toggle

### Session History

The last 50 sessions are stored in `localStorage` (`cua_session_history_v1`):

- Task (first 100 chars), model, provider, step count, status, timestamp
- Toggle between the live timeline and history using the clock icon in the panel header
- Clear all history with one click
- No API keys or sensitive data are stored

### Export (JSON, HTML, Logs)

The log panel starts **collapsed** by default. Click the panel header to expand it.

Three export formats are available from the log panel header:

| Format | Contents | Filename Pattern |
|---|---|---|
| **JSON** | Task, model, provider, all steps (action, error, timestamp), all logs, export timestamp | `cua_session_<ISO-timestamp>.json` |
| **HTML** | Self-contained styled report with timeline and log table; all content is HTML-escaped | `cua_session_<ISO-timestamp>.html` |
| **Logs (.txt)** | Timestamped log lines: `[HH:MM:SS] [LEVEL] message` | `CUA_logs_<YYYYMMDD>_<HHMMSS>.txt` |
| **Copy** | Copy all log entries to the clipboard via the Copy button | — |

Export buttons are disabled when there is no data to export.

### Cost Estimation

An approximate cost is displayed in the header during and after sessions:

- Based on per-model pricing constants in `frontend/src/utils/pricing.js`
- Uses rough averages of ~3,500 input tokens and ~800 output tokens per step
- Clearly labeled as approximate — hover to see the caveat tooltip
- Returns `null` (no display) for models without a pricing entry

> **Note:** The pricing table may not include entries for all models in the allowlist. If your model is not in the pricing table, no cost estimate is shown. This is expected behavior.

### Context Pruning

To prevent unbounded token growth in long sessions, the engine automatically replaces old screenshots with text placeholders (e.g., `[screenshot omitted]`), keeping the most recent **3 turns** intact. This applies to both Gemini and Claude conversation histories. The pruning keeps the context within model limits while preserving recent visual context for accurate action planning.

### Safety Confirmation Flow

When the CU engine encounters a `require_confirmation` safety decision:

1. Engine emits safety callback → `AgentLoop` broadcasts a `safety_confirmation` event via the `log` WebSocket message
2. Frontend detects `log.data.type === 'safety_confirmation'` and shows a modal with countdown timer
3. User clicks Approve or Deny → frontend calls `POST /api/agent/safety-confirm`
4. Backend signals the waiting `asyncio.Event` → engine resumes or skips the action
5. **Timeout:** 60 seconds → automatic deny

### OpenAI Reasoning Effort (Thinking Depth)

When using OpenAI models, control the depth of chain-of-thought reasoning. The setting appears in **Advanced Settings** as **Thinking Depth** and is only visible when OpenAI is the selected provider:

| Value | UI Label | Description |
|---|---|---|
| `none` | None — fastest, minimal reasoning | No extended reasoning |
| `low` | Low — quick decisions | Minimal reasoning (default) |
| `medium` | Medium — balanced | Moderate reasoning |
| `high` | High — thorough reasoning | Thorough reasoning |
| `xhigh` | Extra High — deepest analysis | Maximum reasoning effort |

Can also be set via the `OPENAI_REASONING_EFFORT` environment variable. The parameter is only sent to the backend when the provider is OpenAI.

### API Key Management

- Keys entered in the UI are sent to the backend per-request only — never stored on disk or in the browser
- `.env` and system env keys are loaded at backend startup (`.env` does not override existing system env vars)
- `GET /api/keys/status` returns availability, source (`env` / `dotenv` / `none`), and masked previews per provider
- The UI auto-selects the best available source when the provider changes

### Key Validation

Before starting a session, you can validate an API key via the check button (✓) next to the key input field:

- Frontend calls `POST /api/keys/validate` with the provider name and key
- Backend makes a **lightweight HTTP request to the provider's API** to verify the key is functional:
  - **Google:** `GET https://generativelanguage.googleapis.com/v1beta/models?key=<key>`
  - **Anthropic:** `GET https://api.anthropic.com/v1/models` with `x-api-key` header
  - **OpenAI:** `GET https://api.openai.com/v1/models` with `Authorization: Bearer` header
- Result shown inline: green "Key is valid" on success, red error message on failure
- Validation has a 10-second timeout — if the request times out, a retry message is shown

> This is a **live API call**, not a format check. An internet connection is required for validation.

### noVNC Desktop Access

An interactive noVNC viewer is embedded in the center pane:

- Full keyboard and mouse interaction with the container desktop
- All traffic proxied through the backend (`/vnc/websockify` WebSocket, `/vnc/*` static files) — the browser never connects directly to Docker-mapped ports
- Falls back to a static base64 screenshot stream if the noVNC iframe fails to load
- A toggle button lets you switch between interactive (VNC) and screenshot views
- Direct noVNC access is also available at `http://127.0.0.1:6080` (bypasses the backend proxy)

### Dark / Light Theme

- Toggle via the Sun/Moon button in the header
- Persisted in `localStorage` (`cua_theme`)
- Applied via `data-theme` attribute on `<html>`, overriding CSS custom properties
- Default is dark

### Help Button

Click the **?** (HelpCircle) icon in the header to re-open the welcome overlay at any time. The overlay explains the three-step flow and can be dismissed again without losing session state.

### Toast Notifications

Non-blocking toast messages appear for key events:

- **Success** (green): agent started, task complete
- **Error** (red): task failed
- **Info** (blue): agent stopped
- Auto-dismiss after 4 seconds
- Rendered in an `aria-live="polite"` container for screen reader accessibility

### Error Boundary

A React error boundary wraps the entire application. If an unhandled exception occurs in the component tree, a recovery UI is shown with a **Reload Page** button instead of a blank screen.

---

## Supported Models

Only models with `supports_computer_use: true` in `backend/allowed_models.json` are available in the UI.

| Provider | Model ID | Display Name | Notes |
|---|---|---|---|
| Google | `gemini-3-flash-preview` | Gemini 3 Flash Preview | Fast, lightweight |
| Anthropic | `claude-opus-4-7` | Claude Opus 4.7 | Beta endpoint + `computer_20251124` tool; supports up to 2576px long edge |
| Anthropic | `claude-sonnet-4-6` | Claude Sonnet 4.6 | Requires beta endpoint + `computer_20251124` tool |
| Anthropic | `claude-opus-4-6` | Claude Opus 4.6 | Requires beta endpoint + `computer_20251124` tool |
| OpenAI | `gpt-5.4` | GPT-5.4 | Responses API built-in computer tool; ZDR-compatible |

> `gemini-3.1-pro-preview` is present in `allowed_models.json` with `supports_computer_use: false` and is excluded from the UI. It is reserved for future use if Google confirms CU support.

To add or remove models: edit `backend/allowed_models.json`, set `supports_computer_use` appropriately, and restart the backend. The frontend reads the list dynamically via `GET /api/models`.

---

## Supported Actions

### High-Level Actions (15)

| Category | Actions |
|---|---|
| **Navigation** | `open_web_browser`, `navigate`, `go_back`, `go_forward`, `search` |
| **Mouse** | `click_at`, `hover_at`, `drag_and_drop` |
| **Keyboard** | `type_text_at`, `key_combination` |
| **Scroll** | `scroll_document`, `scroll_at` |
| **Wait** | `wait_5_seconds` |
| **Terminal** | `done`, `error` |

### Low-Level Primitives

`double_click`, `right_click`, `middle_click`, `triple_click`, `move`, `type_at_cursor`, `left_mouse_down`, `left_mouse_up`, `hold_key`

Action names are normalized via `action_aliases.py` — e.g., `press` → `key`, `leftclick` → `click`.

---

## Configuration Reference

Set as environment variables or in a `.env` file in the project root. The `.env` file does not override existing system environment variables.

| Variable | Default | Description |
|---|---|---|
| `GOOGLE_API_KEY` | — | Google Gemini API key |
| `ANTHROPIC_API_KEY` | — | Anthropic Claude API key |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `OPENAI_BASE_URL` | — | Custom OpenAI API base URL (e.g., `https://us.api.openai.com/v1` for regional endpoints or ZDR orgs) |
| `OPENAI_REASONING_EFFORT` | `low` | Reasoning depth: `none` / `low` / `medium` / `high` / `xhigh` |
| `GEMINI_MODEL` | `gemini-3-flash-preview` | Default Gemini model |
| `CONTAINER_NAME` | `cua-environment` | Docker container name |
| `AGENT_SERVICE_HOST` | `127.0.0.1` | Agent service host inside the container |
| `AGENT_SERVICE_PORT` | `9222` | Agent service port |
| `SCREEN_WIDTH` | `1440` | Virtual display width (px) |
| `SCREEN_HEIGHT` | `900` | Virtual display height (px) |
| `MAX_STEPS` | `50` | Default max steps per session (UI cap: 200) |
| `STEP_TIMEOUT` | `30.0` | Per-step timeout (seconds) |
| `HOST` | `0.0.0.0` | Backend bind address |
| `PORT` | `8000` | Backend port |
| `DEBUG` | `false` | Enable verbose logging (`1`, `true`, or `yes`) |
| `CORS_ORIGINS` | *(see below)* | Comma-separated allowed CORS origins |
| `VNC_PASSWORD` | *(unset)* | Optional VNC authentication password (uncomment in `docker-compose.yml`) |
| `VITE_API_PORT` | `8000` | Frontend Vite dev server proxy target port |

**CORS defaults** (when `CORS_ORIGINS` is not set):
`http://localhost:5173`, `http://127.0.0.1:5173`, `http://localhost:3000`, `http://127.0.0.1:3000`

---

## API Endpoints

All endpoints are served by the FastAPI backend. Interactive docs are available at `/docs` (Swagger UI) and `/redoc`.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Liveness check |
| `GET` | `/api/models` | List allowed CU models (filtered by `supports_computer_use`) |
| `GET` | `/api/engines` | List available engines (`computer_use` only) |
| `GET` | `/api/keys/status` | API key availability per provider (masked) |
| `POST` | `/api/keys/validate` | Live key validation via provider API |
| `GET` | `/api/screenshot` | Current desktop screenshot (base64) |
| `GET` | `/api/container/status` | Container running state and agent service health |
| `POST` | `/api/container/start` | Build (if needed) and start the container |
| `POST` | `/api/container/stop` | Stop all agents and remove the container |
| `POST` | `/api/container/build` | Trigger Docker image rebuild |
| `GET` | `/api/agent-service/health` | Agent service health check |
| `POST` | `/api/agent-service/mode` | Confirm desktop mode (only `desktop` accepted) |
| `POST` | `/api/agent/start` | Start an agent session |
| `POST` | `/api/agent/stop/{session_id}` | Stop a running session |
| `GET` | `/api/agent/status/{session_id}` | Session status and last action |
| `GET` | `/api/agent/history/{session_id}` | Full step history (excludes screenshots) |
| `POST` | `/api/agent/safety-confirm` | Respond to a safety confirmation prompt |

### `POST /api/agent/start` — Request Body

```json
{
  "task": "Open Chrome and search for AI news",
  "provider": "google",
  "model": "gemini-3-flash-preview",
  "mode": "desktop",
  "api_key": "",
  "max_steps": 50,
  "engine": "computer_use",
  "execution_target": "docker",
  "reasoning_effort": null
}
```

| Field | Type | Required | Constraints |
|---|---|---|---|
| `task` | `string` | Yes | Non-empty, max 10,000 chars |
| `provider` | `string` | Yes | `"google"` / `"anthropic"` / `"openai"` |
| `model` | `string` | Yes | Must be in the CU allowlist for the given provider |
| `mode` | `string` | Yes | `"desktop"` only |
| `api_key` | `string` | No | Empty string → resolved from `.env` or system env |
| `max_steps` | `int` | No | 1–200 (default 50, hard cap 200) |
| `engine` | `string` | No | `"computer_use"` only |
| `execution_target` | `string` | No | `"docker"` only |
| `reasoning_effort` | `string` | No | OpenAI only: `"none"` / `"low"` / `"medium"` / `"high"` / `"xhigh"` |

### `POST /api/keys/validate` — Request Body

```json
{
  "provider": "google",
  "api_key": "AIza..."
}
```

Returns `{ "valid": true, "message": "Key is valid" }` or `{ "valid": false, "message": "Invalid API key" }`.

---

## WebSocket Events

Connect to `ws://127.0.0.1:8000/ws` (or proxied via Vite at `ws://127.0.0.1:3000/ws`).

### Server → Client

| Event | Payload | Description |
|---|---|---|
| `screenshot` | `{ screenshot: <base64> }` | Screenshot from an agent step |
| `screenshot_stream` | `{ screenshot: <base64> }` | Periodic desktop capture (every 1.5 s) |
| `step` | `{ step: StepRecord }` | Step completion (action, timestamp, error; excludes `screenshot_b64` and `raw_model_response`) |
| `log` | `{ log: LogEntry }` | Backend log message (may include `data.type: "safety_confirmation"` for safety prompts) |
| `agent_finished` | `{ session_id, status, steps }` | Agent loop terminated |
| `pong` | `{}` | Heartbeat response |

### Client → Server

Send `{ "type": "ping" }` every 15 seconds to maintain the connection.

---

## Keyboard Shortcuts

| Shortcut | Context | Action |
|---|---|---|
| `Ctrl+Enter` | Task textarea focused | Start agent |
| `Tab` | Timeline items | Navigate between steps |
| `Enter` / `Space` | Focused timeline item | Expand/collapse step details |
| `Escape` | Safety modal open | No effect (must explicitly approve or deny) |

---

## Troubleshooting

### Container won't start

- Ensure Docker Desktop is running with BuildKit enabled
- Check that ports `5900`, `6080`, `9222`, and `9223` are not in use: `netstat -ano | findstr :5900`
- Rebuild the image: `docker compose build`
- On Windows, use `127.0.0.1` instead of `localhost`

### Agent not responding

- Verify your API key is valid for the selected provider (use the ✓ validation button)
- Check the log panel for error messages
- Ensure the container is healthy (green "Environment Ready" pill in the header)
- Wait 10–20 seconds after container start for XFCE + agent service to fully boot

### Backend port conflict

If port 8000 is in use:
```bash
PORT=8001 python -m backend.main
# And for the frontend:
VITE_API_PORT=8001 npm run dev
```

### Screenshots not updating

- Check the WebSocket connection status — the header shows a `Reconnecting…` pill when the connection is lost; no pill means connected
- The frontend auto-reconnects after 2 seconds — if it persists, refresh the page
- Check browser DevTools → Network → WS tab for connection issues

### Safety confirmation timeout

- Confirmations time out after 60 seconds and default to deny
- If you missed a confirmation, stop the session and restart the task

### Model not listed

- Only models with `supports_computer_use: true` in `backend/allowed_models.json` appear in the dropdown
- After editing the file, restart the backend — the UI fetches the list dynamically

### Rate limit errors (429)

- Agent starts are limited to **10 per minute** with a maximum of **3 concurrent sessions**
- Wait and retry if you hit the limit

### Session state lost after restart

- All active session state is **in-memory only** — restarting the backend clears running sessions
- Session history (task, model, status) persists in the browser's `localStorage` (up to 50 entries)
- The Docker container persists independently of the backend

### Cost estimate shows nothing

- Cost data is only available for models with pricing entries in `frontend/src/utils/pricing.js`
- If your model is not in the pricing table, no estimate is shown — the feature degrades gracefully

---

## Limitations

- **Single host only.** The system is designed for local development — there is no authentication, multi-user support, or production deployment configuration.
- **In-memory sessions.** All active session state lives in the backend process. Restarting the backend loses running sessions (browser history persists).
- **Desktop mode only.** The `mode` parameter only accepts `"desktop"`. Browser-only mode is not supported.
- **Cost estimates are approximate.** Token counts are rough averages, and pricing entries may not cover all models in the allowlist.
- **No persistent storage.** Files created inside the Docker container are lost when the container is removed. Mount a volume if you need to preserve work.
