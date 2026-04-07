# How to Use CUA — Computer Using Agent

A practical guide to setting up, running, and using all features of the CUA workbench.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Running the App](#running-the-app)
- [Using the Workbench](#using-the-workbench)
  - [Starting the Container](#starting-the-container)
  - [Configuring a Provider and Model](#configuring-a-provider-and-model)
  - [Running an Agent Task](#running-an-agent-task)
  - [Monitoring Execution](#monitoring-execution)
  - [Safety Confirmations](#safety-confirmations)
  - [Stopping a Session](#stopping-a-session)
  - [Viewing the Live Desktop](#viewing-the-live-desktop)
- [Features](#features)
  - [Multi-Provider AI Support](#multi-provider-ai-support)
  - [Docker Sandbox](#docker-sandbox)
  - [Real-Time Streaming](#real-time-streaming)
  - [Step Timeline and Logs](#step-timeline-and-logs)
  - [Context Pruning](#context-pruning)
  - [Safety Confirmation Flow](#safety-confirmation-flow)
  - [OpenAI Reasoning Effort](#openai-reasoning-effort)
  - [API Key Management](#api-key-management)
  - [noVNC Desktop Access](#novnc-desktop-access)
  - [Log Download](#log-download)
- [Supported Models](#supported-models)
- [Supported Actions](#supported-actions)
- [Configuration Reference](#configuration-reference)
- [API Endpoints](#api-endpoints)
- [WebSocket Events](#websocket-events)
- [Troubleshooting](#troubleshooting)

---

## Prerequisites

| Requirement     | Minimum Version |
|-----------------|-----------------|
| **Docker**      | With BuildKit enabled |
| **Python**      | 3.13+           |
| **Node.js**     | 18+             |
| **OS**          | Windows, macOS, or Linux |

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

1. **Build the Docker image:**

   ```bash
   docker compose build
   ```

2. **Install backend dependencies:**

   ```bash
   python -m venv .venv
   # Windows
   .venv\Scripts\activate
   # Linux/macOS
   source .venv/bin/activate

   pip install -r requirements.txt
   ```

3. **Install frontend dependencies:**

   ```bash
   cd frontend
   npm install
   ```

---

## Running the App

Start three processes in separate terminals:

| Terminal | Command | Purpose |
|----------|---------|---------|
| ① Backend | `python -m backend.main` | FastAPI server on port `8000` |
| ② Frontend | `cd frontend && npm run dev` | Vite dev server on port `3000` |
| ③ Container *(optional)* | `docker compose up -d` | Can also be started from the UI |

Open **http://127.0.0.1:3000** in your browser.

> **Note:** The Docker container can be started directly from the web UI — you don't have to run `docker compose up` manually.

---

## Using the Workbench

### Starting the Container

1. Open the web UI at `http://127.0.0.1:3000`.
2. Click the **Start Container** button in the control panel.
3. Wait for the status indicator to show the container and agent service are healthy.

The container runs a full Ubuntu 24.04 desktop with XFCE4, Google Chrome, LibreOffice, VLC, and other common applications pre-installed.

### Configuring a Provider and Model

1. **Select a provider** from the dropdown: Google (Gemini), Anthropic (Claude), or OpenAI (GPT-5.4).
2. **Select a model** from the model list that appears for your chosen provider.
3. **Enter an API key** if one isn't already configured via environment variables. Keys entered in the UI take priority over `.env` or system environment variables.

### Running an Agent Task

1. Type a natural-language task description in the task input field.
   - Example: *"Open Chrome and search for the weather in San Francisco"*
   - Example: *"Open LibreOffice Calc and create a budget spreadsheet"*
   - Maximum length: 10,000 characters.
2. Optionally adjust **Max Steps** (1–200, default 50).
3. If using OpenAI, optionally set the **Reasoning Effort** level.
4. Click **Start** to begin the agent session.

The agent will take a screenshot, send it to the LLM, receive an action, execute it, and repeat until the task is done.

### Monitoring Execution

While the agent runs, you can observe:

- **Live screenshots** updating in real time via WebSocket.
- **Step-by-step timeline** showing each action the agent took, expandable for details.
- **Progress bar** indicating how many steps have been used out of the maximum.
- **Log panel** with real-time log entries.

### Safety Confirmations

Some actions trigger a **safety confirmation prompt** in the UI. When this happens:

- The agent pauses and waits for your approval.
- You have **60 seconds** to approve or deny.
- If you don't respond, the action is **denied by default**.
- Click **Approve** to let the agent proceed or **Deny** to block the action.

### Stopping a Session

Click the **Stop** button at any time to halt the agent. The session will transition to a completed state and the container remains running for inspection.

### Viewing the Live Desktop

The workbench includes an embedded **noVNC** viewer that shows the container's desktop in real time. You can watch the agent interact with applications as it works. The VNC stream is proxied through the backend — your browser never connects directly to the Docker container.

---

## Features

### Multi-Provider AI Support

CUA supports three major AI providers with native Computer Use protocols:

| Provider | Protocol | How It Works |
|----------|----------|--------------|
| **Google Gemini** | `function_call` | Normalized 0–999 coordinate grid, denormalized to actual pixels |
| **Anthropic Claude** | `tool_use` with `computer_20251124` | Real pixel coordinates with pre-resize scaling |
| **OpenAI GPT-5.4** | Responses API `computer` tool | Real pixel coordinates, built-in CU support |

Each provider uses its own native API for structured action output — no prompt-only hacks or regex parsing.

### Docker Sandbox

All agent actions execute inside an isolated Docker container:

- **Ubuntu 24.04** with XFCE4 desktop environment
- **Resource limits:** 4 GB RAM, 2 CPUs
- **Security:** `no-new-privileges` flag, localhost-only port bindings
- **Pre-installed software:** Google Chrome, LibreOffice, VLC, Node.js 20, Python 3
- **Virtual display:** Xvfb at configurable resolution (default 1440×900)

Your real machine is never exposed to the agent.

### Real-Time Streaming

The backend broadcasts events over a WebSocket connection at `/ws`:

- **`screenshot`** / **`screenshot_stream`** — Live desktop screenshots as base64 images
- **`step`** — Structured step records with action details
- **`log`** — Backend log entries
- **`agent_finished`** — Session completion notification

The frontend auto-reconnects after 2 seconds on disconnect.

### Step Timeline and Logs

The Workbench page provides:

- **Expandable step timeline** — Click any step to see what action was taken, coordinates, and timing.
- **Progress bar** — Visual indicator of step count vs. maximum.
- **Log panel** — Scrollable, real-time log output from the backend.
- **Log download** — Export the full session log for offline analysis.

### Context Pruning

To prevent unbounded token growth in long sessions, CUA automatically replaces old screenshots with text placeholders after **3 turns**. This keeps the conversation context within model limits while preserving recent visual context.

### Safety Confirmation Flow

When the AI model flags an action with `require_confirmation`, the backend:

1. Pauses execution
2. Broadcasts a confirmation request to the UI
3. Waits up to 60 seconds for user input
4. Defaults to **deny** if no response is received

This prevents the agent from performing potentially dangerous or unintended actions without your explicit approval.

### OpenAI Reasoning Effort

When using OpenAI models, you can control how much "thinking" the model does before responding:

| Level | Description |
|-------|-------------|
| `none` | No extended reasoning |
| `low` | Minimal reasoning (default) |
| `medium` | Moderate reasoning |
| `high` | Thorough reasoning |
| `xhigh` | Maximum reasoning effort |

Set via the UI dropdown or the `OPENAI_REASONING_EFFORT` environment variable.

### API Key Management

Keys are resolved in priority order:

1. **UI input** — Keys entered directly in the web interface (highest priority)
2. **`.env` file** — Keys defined in a `.env` file in the project root
3. **System environment** — Keys set as OS-level environment variables

The `/api/keys/status` endpoint shows which providers have keys configured and displays a masked preview (e.g., `AIza...4xQk`).

### noVNC Desktop Access

An interactive noVNC viewer is embedded in the workbench, allowing you to:

- Watch the agent work in real time
- Optionally interact with the desktop manually (for debugging)
- All traffic proxied through the backend for security

### Log Download

After a session completes (or while running), you can download the full step history and logs for offline review and debugging.

---

## Supported Models

| Provider   | Model ID                   | Computer Use |
|------------|---------------------------|--------------|
| Google     | `gemini-3-flash-preview`   | ✅ Native    |
| Google     | `gemini-3.1-pro-preview`   | ⚠️ Unconfirmed |
| Anthropic  | `claude-sonnet-4-6`        | ✅ Native    |
| Anthropic  | `claude-opus-4-6`          | ✅ Native    |
| OpenAI     | `gpt-5.4`                  | ✅ Native    |

To add or remove models, edit `backend/allowed_models.json` and restart the backend.

---

## Supported Actions

The agent can perform 15 high-level actions, plus additional low-level primitives:

### High-Level Actions

| Category   | Actions |
|------------|---------|
| Navigation | `open_web_browser`, `navigate`, `go_back`, `go_forward`, `search` |
| Mouse      | `click_at`, `hover_at`, `drag_and_drop` |
| Keyboard   | `type_text_at`, `key_combination` |
| Scroll     | `scroll_document`, `scroll_at` |
| Wait       | `wait_5_seconds` |
| Terminal   | `done`, `error` |

### Low-Level Primitives

`double_click`, `right_click`, `middle_click`, `triple_click`, `move`, `type_at_cursor`, `left_mouse_down`, `left_mouse_up`, `hold_key`

Action names are normalized automatically — for example, `press` → `key`, `leftclick` → `click`.

---

## Configuration Reference

Set these as environment variables or in a `.env` file in the project root:

| Variable | Default | Description |
|----------|---------|-------------|
| `GOOGLE_API_KEY` | — | Google Gemini API key |
| `ANTHROPIC_API_KEY` | — | Anthropic Claude API key |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `OPENAI_REASONING_EFFORT` | `low` | Reasoning level: `none`/`low`/`medium`/`high`/`xhigh` |
| `GEMINI_MODEL` | `gemini-3-flash-preview` | Default Gemini model |
| `CONTAINER_NAME` | `cua-environment` | Docker container name |
| `AGENT_SERVICE_HOST` | `127.0.0.1` | Agent service host inside the container |
| `AGENT_SERVICE_PORT` | `9222` | Agent service port |
| `SCREEN_WIDTH` | `1440` | Virtual display width in pixels |
| `SCREEN_HEIGHT` | `900` | Virtual display height in pixels |
| `MAX_STEPS` | `50` | Default max agent steps per session |
| `STEP_TIMEOUT` | `30.0` | Per-step timeout in seconds |
| `DEBUG` | `false` | Enable debug logging and Uvicorn auto-reload |
| `VNC_PASSWORD` | *(unset)* | Optional VNC authentication password |

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Liveness check |
| `GET` | `/api/models` | List allowed models |
| `GET` | `/api/engines` | List available engines |
| `GET` | `/api/keys/status` | API key status per provider (masked) |
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

### Start Agent Request Body

```json
{
  "task": "Open Chrome and search for AI news",
  "provider": "google",
  "model": "gemini-3-flash-preview",
  "mode": "desktop",
  "api_key": "optional-override",
  "max_steps": 50,
  "engine": "computer_use",
  "execution_target": "docker",
  "reasoning_effort": "low"
}
```

---

## WebSocket Events

Connect to `ws://127.0.0.1:8000/ws` for real-time updates.

### Server → Client

| Event | Description |
|-------|-------------|
| `screenshot` | Full desktop screenshot (base64) |
| `screenshot_stream` | Streaming screenshot update |
| `step` | Structured step record with action details |
| `log` | Backend log entry |
| `agent_finished` | Session completed or errored |
| `pong` | Response to client ping |

### Client → Server

Send `{ "type": "ping" }` every 15 seconds to keep the connection alive.

---

## Troubleshooting

### Container won't start

- Ensure Docker Desktop is running and BuildKit is enabled.
- Check that ports `5900`, `6080`, and `9222` are not in use by other processes.
- Try rebuilding the image: click **Build** in the UI or run `docker compose build`.

### Agent not responding

- Verify your API key is valid for the selected provider.
- Check the log panel for error messages.
- Ensure the agent service is healthy (green status indicator in the UI).

### Screenshots not updating

- Check the WebSocket connection status in the browser's developer tools (Network → WS tab).
- The frontend auto-reconnects after 2 seconds — if it persists, refresh the page.

### Safety confirmation timeout

- Confirmations time out after 60 seconds and default to deny.
- If you missed a confirmation, stop the session and restart the task.

### Model not listed

- Only models in `backend/allowed_models.json` appear in the dropdown.
- Edit the file and restart the backend to add new models.

### Rate limit errors

- Agent starts are limited to **10 per minute** with a maximum of **3 concurrent sessions**.
- Wait and try again if you hit the limit.

### Session state lost after restart

- All session state is **in-memory only**. Restarting the backend clears all sessions.
- The Docker container persists independently — only agent session data is lost.
