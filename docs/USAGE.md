# CUA Usage Guide

A comprehensive guide to setting up, running, and using all features of the CUA workbench.

---

## Table of Contents

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
  - [Viewing the Live Desktop](#viewing-the-live-desktop)
- [Features In Depth](#features-in-depth)
  - [Multi-Provider AI Support](#multi-provider-ai-support)
  - [Docker Sandbox](#docker-sandbox)
  - [Real-Time Streaming](#real-time-streaming)
  - [Step Timeline](#step-timeline)
  - [Session History](#session-history)
  - [Export (JSON, HTML, Logs)](#export-json-html-logs)
  - [Cost Estimation](#cost-estimation)
  - [Context Pruning](#context-pruning)
  - [Safety Confirmation Flow](#safety-confirmation-flow)
  - [OpenAI Reasoning Effort](#openai-reasoning-effort)
  - [API Key Management](#api-key-management)
  - [Key Validation](#key-validation)
  - [noVNC Desktop Access](#novnc-desktop-access)
  - [Dark / Light Theme](#dark--light-theme)
  - [Toast Notifications](#toast-notifications)
  - [Error Boundary](#error-boundary)
- [Supported Models](#supported-models)
- [Supported Actions](#supported-actions)
- [Configuration Reference](#configuration-reference)
- [API Endpoints](#api-endpoints)
- [WebSocket Events](#websocket-events)
- [Keyboard Shortcuts](#keyboard-shortcuts)
- [Troubleshooting](#troubleshooting)

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

Both scripts will:
1. Verify prerequisites (Docker, Python, Node.js)
2. Build the Docker image (Ubuntu 24.04 desktop environment)
3. Create a Python virtual environment and install backend dependencies
4. Install frontend npm packages

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

Start two processes (the Docker container auto-starts from the UI when needed):

| Terminal | Command | Serves |
|---|---|---|
| ① Backend | `python -m backend.main` | FastAPI on `http://127.0.0.1:8000` |
| ② Frontend | `cd frontend && npm run dev` | Vite on `http://127.0.0.1:3000` |

Open **http://127.0.0.1:3000** in your browser.

> **Tip:** The Docker container starts automatically when you launch an agent task, so there is no need to run `docker compose up` manually.

> **Port conflict?** Set `PORT=8001` before starting the backend, and `VITE_API_PORT=8001` for the frontend so the dev proxy routes to the correct backend.

> **Windows:** Prefer `127.0.0.1` over `localhost` to avoid IPv6 binding issues with Docker.

---

## Using the Workbench

### First Run

On your first visit, a **welcome overlay** explains the three-step flow:

1. Choose your AI provider and enter an API key
2. Describe a task for the agent to perform
3. Watch the agent work in real time on the live desktop

The overlay is dismissed once and remembered in `localStorage`. It will not appear again unless you clear browser data.

### Starting the Environment

The Docker container (a full Ubuntu desktop with XFCE, Chrome, LibreOffice, and more) can be started two ways:

- **Automatically** — clicking **Start Agent** will start the container if it is not already running
- **Manually** — click the **Start Environment** button in the header

The header displays real-time status: `Environment Ready` (green) or `Environment Offline` (red). A loading indicator appears during startup and shutdown. If a container operation fails, an actionable error message is shown inline.

### Selecting a Provider and Model

1. **Provider** — choose from Google Gemini, Anthropic Claude, or OpenAI
2. **Model** — the dropdown auto-populates when a provider is selected, sourced from `GET /api/models`

Models are validated server-side against `backend/allowed_models.json`. If no models appear, check that the backend is running and reachable.

### Configuring API Keys

Three sources are available, in priority order:

| Priority | Source | How to Set |
|---|---|---|
| 1 (highest) | **Manual input** | Type or paste directly in the UI (`type="password"`, never persisted) |
| 2 | **`.env` file** | Add `GOOGLE_API_KEY=...`, `ANTHROPIC_API_KEY=...`, or `OPENAI_API_KEY=...` in the project root `.env` |
| 3 | **System environment** | Export the same variable names in your shell |

The API key source toggle shows availability with checkmarks (✓) and masked previews (e.g., `AIza...4xQk`). You can switch between sources at any time when the agent is not running.

> **Security:** API keys entered in the UI are sent to the backend per-request and are never written to `localStorage` or any persistent storage.

### Running an Agent Task

1. **Describe the task** in the textarea (max 10,000 characters). Example task chips appear when the field is empty — click one to populate it.
2. Optionally expand **Advanced Settings** to adjust max steps (1–200, default 50) or OpenAI reasoning effort.
3. Click **Start Agent** (or press **Ctrl+Enter**).

The agent will:
- Auto-start the Docker container if needed
- Take a screenshot → send to the LLM → receive an action → execute it → repeat
- Stop when the model returns `done`, an error occurs, or the step limit is reached

### Monitoring Execution

While the agent runs, the workbench provides:

| Element | Location | Description |
|---|---|---|
| **Live desktop** | Center pane | Interactive noVNC iframe or screenshot stream |
| **Progress bar** | Below the desktop | Visual indicator of steps used vs. maximum |
| **Step timeline** | Right panel (top) | Expandable items with action type, icon, target, coordinates, reasoning, raw JSON |
| **Log panel** | Right panel (bottom) | Scrollable real-time logs with severity badges (info / error / warning / debug) |
| **Step counter** | Header | `Steps: N/M` with tabular-nums formatting |
| **Cost estimate** | Header | Approximate session cost (hover for caveat tooltip) |
| **Agent Running pill** | Header | Blue status badge visible while the agent is active |

Both the timeline and log panel auto-scroll to the latest entry.

### Safety Confirmations

When the AI model flags an action that requires explicit human approval:

1. A **modal dialog** appears with the action explanation
2. A **60-second countdown** timer is displayed
3. You can click **Approve** to proceed or **Deny** to block the action
4. If no response is given within 60 seconds, the action is **automatically denied**

The agent pauses until you respond. After approval or denial, execution resumes.

### Stopping a Session

- Click **Stop** to halt the agent immediately
- The container remains running for manual inspection
- A **completion banner** appears showing the outcome (completed / failed / stopped) and step count
- The session is recorded in **session history**

---

## Features In Depth

### Multi-Provider AI Support

| Provider | Protocol | Coordinates | Key Env Var |
|---|---|---|---|
| **Google Gemini** | `function_call` | Normalized 0–999 grid → denormalized to pixels | `GOOGLE_API_KEY` |
| **Anthropic Claude** | `tool_use` with `computer_20251124` | Real pixel values with pre-resize scaling | `ANTHROPIC_API_KEY` |
| **OpenAI** | Responses API `computer` tool | Real pixel values matching the screenshot | `OPENAI_API_KEY` |

Each provider's native Computer Use API is used directly — no prompt-only workarounds or regex parsing.

### Docker Sandbox

All agent actions execute inside an isolated Docker container:

- **Ubuntu 24.04** with XFCE4 desktop environment
- **Resource limits:** 4 GB RAM, 2 CPUs
- **Security:** `no-new-privileges`, localhost-only port bindings
- **Pre-installed:** Google Chrome, LibreOffice, VLC, Node.js 20, Python 3, terminal emulators, file manager
- **Virtual display:** Xvfb at configurable resolution (default 1440×900)

Your host machine is never exposed to the agent.

### Real-Time Streaming

The backend broadcasts events over a persistent WebSocket connection at `/ws`:

| Event | Payload | Description |
|---|---|---|
| `screenshot` / `screenshot_stream` | base64 PNG | Live desktop captures |
| `step` | Structured step record | Action details with timestamps |
| `log` | Log entry | Backend log messages with level |
| `agent_finished` | Session result | Completion notification with status and step count |

The frontend auto-reconnects after 2 seconds on disconnect and sends heartbeat pings every 15 seconds.

### Step Timeline

Each agent step is rendered as an expandable timeline item with:

- **SVG icon** matching the action type (lucide-react: mouse, keyboard, scroll, navigate, etc.)
- **Action name** and **target** (truncated with tooltip)
- **Timestamp** in locale format
- **Expand** to see: reasoning text, exact coordinates, error details, and raw JSON payload
- **Keyboard accessible** — `Tab` to focus, `Enter`/`Space` to toggle, `focus-visible` outline

### Session History

The last 50 sessions are stored in `localStorage` (`cua_session_history_v1`):

- Task (first 100 chars), model, provider, step count, status, timestamp
- Toggle between the live timeline and history using the clock icon in the panel header
- Clear all history with one click
- No API keys or sensitive data are stored

### Export (JSON, HTML, Logs)

Three export formats are available from the log panel header:

| Format | Contents | Icon |
|---|---|---|
| **JSON** | Task, model, provider, all steps (action, error, timestamp), all logs, export timestamp | `FileJson` |
| **HTML** | Self-contained styled report with timeline and log table. All content is HTML-escaped via a centralized `esc()` function. | `FileText` |
| **Logs (.txt)** | Timestamped log lines: `[HH:MM:SS] [LEVEL] message` | `Download` |

### Cost Estimation

An approximate cost is displayed in the header during and after sessions:

- Based on centralized per-model pricing in `frontend/src/utils/pricing.js`
- Uses rough averages of ~3,500 input tokens and ~800 output tokens per step
- Clearly labeled as approximate — hover to see the caveat tooltip
- Returns `null` for models without pricing data

### Context Pruning

To prevent unbounded token growth in long sessions, the engine automatically replaces old screenshots with text placeholders after **3 turns**. This keeps the conversation context within model limits while preserving recent visual context for accurate action planning.

### Safety Confirmation Flow

When the CU engine encounters a `require_confirmation` safety decision:

1. Engine emits safety callback → `AgentLoop` broadcasts a `safety_confirmation` WebSocket event
2. Frontend shows a modal with countdown timer
3. User clicks Approve or Deny → `POST /api/agent/safety-confirm`
4. Backend signals the waiting `asyncio.Event` → engine resumes or skips
5. **Timeout:** 60 seconds → auto-deny

### OpenAI Reasoning Effort

When using OpenAI models, control the depth of chain-of-thought reasoning:

| Level | Description |
|---|---|
| `none` | No extended reasoning |
| `low` | Minimal reasoning (default) |
| `medium` | Moderate reasoning |
| `high` | Thorough reasoning |
| `xhigh` | Maximum reasoning effort |

Set via the UI dropdown (under Advanced Settings) or the `OPENAI_REASONING_EFFORT` environment variable.

### API Key Management

- Keys entered in the UI are sent to the backend per-request only — never stored
- `.env` and system env keys are loaded at backend startup
- `GET /api/keys/status` returns availability and masked previews per provider
- The UI auto-selects the best available source on provider change

### Key Validation

Before starting a session, you can validate an API key via the check button next to the key input:

- Frontend calls `POST /api/keys/validate` with provider and key
- Backend performs provider-specific format validation (prefix, length, character set)
- Result shown inline: green checkmark for valid, red message for invalid

### noVNC Desktop Access

An interactive noVNC viewer is embedded in the center pane:

- Full keyboard and mouse interaction with the container desktop
- All traffic proxied through the backend (`/vnc/websockify`) — the browser never connects directly to Docker
- Falls back to WebSocket screenshot stream if noVNC is unavailable
- Standalone access available at `http://127.0.0.1:6080`

### Dark / Light Theme

- Toggle via the Sun/Moon button in the header
- Persisted in `localStorage` (`cua_theme`)
- Applied via `data-theme="light"` attribute on `<html>`, overriding CSS custom properties
- Default is dark

### Toast Notifications

Non-blocking toast messages appear in the top-right corner:

- **Success** (green): agent started, task complete
- **Error** (red): task failed
- **Info** (blue): agent stopped
- Auto-dismiss after 4 seconds
- Implemented via `useToasts()` hook in `ToastContainer.jsx`

### Error Boundary

A React error boundary wraps the entire application. If an unhandled exception occurs in the component tree, a recovery UI is shown with a **Reload Page** button instead of a blank screen.

---

## Supported Models

| Provider | Model ID | Computer Use | Notes |
|---|---|---|---|
| Google | `gemini-3-flash-preview` | ✅ Native | Fast, lightweight |
| Google | `gemini-3.1-pro-preview` | ⚠️ Unconfirmed | Stronger reasoning |
| Anthropic | `claude-sonnet-4-6` | ✅ Native | Beta endpoint |
| Anthropic | `claude-opus-4-6` | ✅ Native | Beta endpoint |
| OpenAI | `gpt-5.4` | ✅ Native | Responses API |

To add models: edit `backend/allowed_models.json` and restart the backend. The UI auto-refreshes via `GET /api/models`.

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

Set as environment variables or in a `.env` file in the project root:

| Variable | Default | Description |
|---|---|---|
| `GOOGLE_API_KEY` | — | Google Gemini API key |
| `ANTHROPIC_API_KEY` | — | Anthropic Claude API key |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `OPENAI_REASONING_EFFORT` | `low` | Reasoning: `none` / `low` / `medium` / `high` / `xhigh` |
| `GEMINI_MODEL` | `gemini-3-flash-preview` | Default Gemini model |
| `CONTAINER_NAME` | `cua-environment` | Docker container name |
| `AGENT_SERVICE_HOST` | `127.0.0.1` | Agent service host inside the container |
| `AGENT_SERVICE_PORT` | `9222` | Agent service port |
| `SCREEN_WIDTH` | `1440` | Virtual display width (px) |
| `SCREEN_HEIGHT` | `900` | Virtual display height (px) |
| `MAX_STEPS` | `50` | Default max steps per session |
| `STEP_TIMEOUT` | `30.0` | Per-step timeout (seconds) |
| `HOST` | `0.0.0.0` | Backend bind address |
| `PORT` | `8000` | Backend port |
| `DEBUG` | `false` | Enable debug logging + Uvicorn auto-reload |
| `CORS_ORIGINS` | `localhost:3000,localhost:5173` | Comma-separated allowed CORS origins |
| `VNC_PASSWORD` | *(unset)* | Optional VNC authentication password |
| `VITE_API_PORT` | `8000` | Frontend proxy target port (Vite dev server only) |

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Liveness check |
| `GET` | `/api/models` | List allowed models |
| `GET` | `/api/engines` | List available engines |
| `GET` | `/api/keys/status` | API key status per provider (masked) |
| `POST` | `/api/keys/validate` | Pre-flight key validation |
| `GET` | `/api/screenshot` | Current desktop screenshot (base64) |
| `GET` | `/api/container/status` | Container and agent service health |
| `POST` | `/api/container/start` | Build (if needed) and start the container |
| `POST` | `/api/container/stop` | Stop agents and remove the container |
| `POST` | `/api/container/build` | Trigger Docker image rebuild |
| `GET` | `/api/agent-service/health` | Agent service health check |
| `POST` | `/api/agent-service/mode` | Confirm desktop mode |
| `POST` | `/api/agent/start` | Start an agent session |
| `POST` | `/api/agent/stop/{session_id}` | Stop a running session |
| `GET` | `/api/agent/status/{session_id}` | Session status and last action |
| `GET` | `/api/agent/history/{session_id}` | Full step history (without screenshots) |
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
| `model` | `string` | No | Must be in allowlist (defaults to provider default) |
| `mode` | `string` | Yes | `"desktop"` only |
| `api_key` | `string` | No | Empty string → resolved from env |
| `max_steps` | `int` | No | 1–200 (default 50) |
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

Returns `{ "valid": true, "message": "Key format looks correct" }` or `{ "valid": false, "message": "..." }`.

---

## WebSocket Events

Connect to `ws://127.0.0.1:8000/ws` (or proxied via Vite at `ws://127.0.0.1:3000/ws`).

### Server → Client

| Event | Payload | Description |
|---|---|---|
| `screenshot` | `{ screenshot: <base64> }` | Screenshot from agent step |
| `screenshot_stream` | `{ screenshot: <base64> }` | Periodic desktop capture |
| `step` | `{ step: StepRecord }` | Step completion (action, timestamp, error) |
| `log` | `{ log: LogEntry }` | Backend log message (may include `safety_confirmation` data) |
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
- Check that ports `5900`, `6080`, and `9222` are not in use: `netstat -ano | findstr :5900`
- Rebuild the image: `docker compose build`
- On Windows, use `127.0.0.1` instead of `localhost`

### Agent not responding

- Verify your API key is valid for the selected provider (use the ✓ validation button)
- Check the log panel for error messages
- Ensure the agent service is healthy (green "Environment Ready" pill in the header)
- Wait 10–20 seconds after container start for XFCE + agent service to fully boot

### Backend port conflict

If port 8000 is in use:
```bash
PORT=8001 python -m backend.main
# And for the frontend:
VITE_API_PORT=8001 npm run dev
```

### Screenshots not updating

- Check the WebSocket connection status (header shows "Connected" or "Reconnecting…")
- The frontend auto-reconnects after 2 seconds — if it persists, refresh the page
- Check browser DevTools → Network → WS tab for connection issues

### Safety confirmation timeout

- Confirmations time out after 60 seconds and default to deny
- If you missed a confirmation, stop the session and restart the task

### Model not listed

- Only models in `backend/allowed_models.json` appear in the dropdown
- Edit the file, restart the backend, and the UI will refresh automatically

### Rate limit errors (429)

- Agent starts are limited to **10 per minute** with a maximum of **3 concurrent sessions**
- Wait and try again if you hit the limit

### Session state lost after restart

- All session state is **in-memory only** — restarting the backend clears active sessions
- Session history (task/model/status) persists in the browser's localStorage
- The Docker container persists independently

### Cost estimate shows nothing

- Cost data is only available for models listed in `frontend/src/utils/pricing.js`
- Unknown models show no estimate — the feature degrades gracefully
