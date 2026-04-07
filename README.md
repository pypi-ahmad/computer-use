<div align="center">

# 🖥️ CUA — Computer Using Agent

**An open-source workbench for building, testing, and observing autonomous computer-using agents powered by native Computer Use protocols from Google Gemini, Anthropic Claude, and OpenAI.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.13+](https://img.shields.io/badge/Python-3.13+-3776AB.svg?logo=python&logoColor=white)](https://python.org)
[![React 19](https://img.shields.io/badge/React-19-61DAFB.svg?logo=react&logoColor=black)](https://react.dev)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Docker](https://img.shields.io/badge/Docker-Ubuntu_24.04-2496ED.svg?logo=docker&logoColor=white)](https://docker.com)
[![Tests](https://img.shields.io/badge/Tests-118_passing-brightgreen.svg)](#-testing)
[![Gemini](https://img.shields.io/badge/Gemini-CU_Native-4285F4.svg?logo=google&logoColor=white)](#-supported-models)
[![Claude](https://img.shields.io/badge/Claude-CU_Native-CC785C.svg?logo=anthropic&logoColor=white)](#-supported-models)
[![OpenAI](https://img.shields.io/badge/OpenAI-CU_Native-10A37F.svg?logo=openai&logoColor=white)](#-supported-models)

---

Run a full **Linux desktop inside Docker**, stream it live to a **React web UI**, and let a vision-language model drive desktop tasks autonomously using pixel-level **perceive → think → act** loops.

**Built for** AI/ML engineers, researchers, and developers who need a local, sandboxed environment to experiment with computer-using agents — without giving LLMs access to their real machines.

[Quickstart](#-quickstart) · [Architecture](#-architecture) · [API Reference](#-api--websocket-reference) · [Configuration](#-configuration) · [Contributing](#-contributing)

</div>

---

## Table of Contents

| | | |
|---|---|---|
| 1. [Overview](#-overview) | 7. [API & WebSocket Reference](#-api--websocket-reference) | 13. [Troubleshooting](#-troubleshooting) |
| 2. [Features](#-features) | 8. [Quickstart](#-quickstart) | 14. [Safety & Security](#-safety--security) |
| 3. [Architecture](#-architecture) | 9. [Installation](#-installation) | 15. [Roadmap & Known Limitations](#-roadmap--known-limitations) |
| 4. [How The Engine Works](#-how-the-engine-works) | 10. [Configuration](#-configuration) | 16. [Contributing](#-contributing) |
| 5. [Agent Loop & Lifecycle](#-agent-loop--lifecycle) | 11. [Usage](#-usage) | 17. [License](#-license) |
| 6. [Supported Models](#-supported-models) | 12. [Testing](#-testing) | |

---

## 🔭 Overview

CUA implements a closed-loop **perceive → think → act** cycle for autonomous computer control:

1. **Perceive** — capture a screenshot of a virtual Linux desktop running inside a Docker container
2. **Think** — send the screenshot and user task to a vision-language model (Gemini, Claude, or OpenAI)
3. **Act** — receive a structured action command via the model's native Computer Use tool protocol and execute it inside the sandbox

The cycle repeats until the task completes, an unrecoverable error occurs, or the configured step limit is reached.

The system uses **native Computer Use protocols exclusively** — Gemini `function_call`, Claude `tool_use`, and the OpenAI Responses API `computer_call` — for pixel-level interaction. No text parsing or regex extraction is required. All actions execute inside a resource-constrained Docker container through a **desktop-mode** runtime powered by `xdotool` + `scrot` for any X11 application.

A single-page React workbench provides real-time desktop streaming (WebSocket screenshots + interactive noVNC), a step-by-step action timeline, session management, log viewing, JSON/HTML export, approximate cost estimation, dark/light theming, and a first-run onboarding overlay.

---

## ✨ Features

| Category | Details |
|---|---|
| **Native CU Engine** | Gemini, Claude, and OpenAI native Computer Use tool protocols — structured, pixel-level desktop automation with no prompt hacks |
| **Desktop Runtime** | `xdotool` + `scrot` to control and observe any X11 application inside the sandbox |
| **Multi-Provider AI** | Google Gemini, Anthropic Claude, and OpenAI with a centralized model allowlist enforced at the API layer. OpenAI supports configurable reasoning effort (`none`/`low`/`medium`/`high`/`xhigh`) |
| **Docker Sandbox** | Ubuntu 24.04 container with resource limits (4 GB RAM, 2 CPUs), `no-new-privileges`, and localhost-only port bindings |
| **Real-Time Streaming** | Live screenshot stream via WebSocket + interactive noVNC desktop access proxied through the backend |
| **Cross-Platform Host** | Backend + frontend run on Windows, macOS, or Linux; Docker provides the sandboxed Linux desktop |
| **Safety Confirmation** | CU safety gates surface to the UI with a 60-second countdown — auto-deny on timeout |
| **API Key Validation** | Pre-flight key validation via `POST /api/keys/validate` — makes a lightweight live API call to the provider (not just format checks) |
| **Input Validation** | Rate limiting (10 starts/min), concurrent session cap (3), model allowlist enforcement, UUID session IDs, task length bounds (10 000 chars) |
| **Context Pruning** | Automatic pruning of old screenshots from conversation context to prevent unbounded token growth |
| **Session History** | Bounded localStorage history (50 sessions) with task, model, step count, and status |
| **Export** | One-click JSON and HTML session reports with safely escaped content |
| **Cost Estimation** | Approximate per-session cost display based on centralized model pricing data |
| **Theming** | Dark and light themes with persistent toggle via `data-theme` attribute |
| **Onboarding** | First-run welcome overlay with 3-step guide, dismissible and remembered via localStorage |
| **Accessibility** | Minimum 12 px font sizes, SVG icons via `lucide-react`, `aria-label` on all icon-only buttons, keyboard-navigable timeline, focus-visible outlines |
| **Hermetic Test Suite** | 118 unit tests using mocks/patches — no running container or network required |

---

## 🏗️ Architecture

The system is a **three-process architecture** spanning the host and a Docker container:

| Layer | Technology | Entry Point | Port |
|---|---|---|---|
| **Frontend** | React 19 / Vite 6 / React Router 7 | `frontend/src/main.jsx` | `3000` |
| **Backend** | Python 3.13 / FastAPI / Uvicorn | `backend/main.py` → `backend.server:app` | `8000` |
| **Container** | Ubuntu 24.04 / XFCE 4 / Xvfb / desktop automation tools | `docker/entrypoint.sh` → `docker/agent_service.py` | `9222` |

### High-Level Architecture

<p align="center">
  <img src="docs/assets/architecture.svg" alt="CUA High-Level Architecture" width="100%"/>
</p>

<details>
<summary>View connection details</summary>

| Path | Protocol | Description |
|---|---|---|
| Frontend → Backend | HTTP REST + WebSocket | `api.js` → `/api/*` endpoints; `useWebSocket.js` → `/ws` |
| Frontend → Container | noVNC (WebSocket) | `ScreenView.jsx` → `/vnc/websockify` proxy in `server.py` |
| Backend → Agent Service | HTTP | `loop.py` / `screenshot.py` → `:9222/action`, `:9222/screenshot` |
| Backend → LLM APIs | HTTPS | `google-genai` / `anthropic` / `openai` SDKs → cloud endpoints |
| Backend → Docker CLI | Subprocess | `docker_manager.py` → `docker build/run/rm/exec` |

</details>

### Agent Execution Flow

<p align="center">
  <img src="docs/assets/execution-flow.svg" alt="Agent Execution Flow — Perceive → Think → Act" width="100%"/>
</p>

### Component Relationship

<p align="center">
  <img src="docs/assets/components.svg" alt="Component Relationship Map" width="100%"/>
</p>

---

## ⚙️ How The Engine Works

The sole supported engine is **`computer_use`**, implementing the native Computer Use protocols from Gemini, Claude, and OpenAI. Engine capabilities are registered in `backend/engine_capabilities.json` (schema v3.0).

The model receives a **screenshot** (base64 PNG) and the **user's task**, then returns a structured action via Gemini `function_call`, Claude `tool_use`, or the OpenAI Responses API `computer_call`. The engine executes that action inside the Docker container and captures a new screenshot. This loop repeats until the model determines the task is **done** or encounters an **error**.

### Provider Comparison

| | Google Gemini | Anthropic Claude | OpenAI GPT-5.4 |
|---|---|---|---|
| **SDK** | `google-genai` | `anthropic` | `openai` |
| **Tool Protocol** | `types.Tool(computer_use=...)` | `computer_20251124` tool + beta endpoint | Responses API built-in `computer` tool |
| **Coordinates** | Normalized 0–999 grid, denormalized to pixels by engine | Real pixel values matching reported display size | Real pixel values matching the screenshot |
| **Screenshot Handling** | Sent as inline `Part` | Pre-resized per Anthropic limits (max 1568px long edge, 1.15M total pixels) | Returned as `computer_call_output` with `detail: "original"` |
| **Context Pruning** | Old screenshots replaced with text placeholders after 3 turns | Same pruning logic | Uses `previous_response_id` to continue the native loop |
| **System Prompt** | Detailed action instructions + coordinate semantics | Minimal — Anthropic auto-injects CU schema | OpenAI-specific computer-tool guidance |

### Supported Actions (15)

| Category | Actions |
|---|---|
| **Navigation** | `open_web_browser`, `navigate`, `go_back`, `go_forward`, `search` |
| **Mouse** | `click_at`, `hover_at`, `drag_and_drop` |
| **Keyboard** | `type_text_at`, `key_combination` |
| **Scroll** | `scroll_document`, `scroll_at` |
| **Wait** | `wait_5_seconds` |
| **Terminal** | `done`, `error` |

---

## 🔄 Agent Loop & Lifecycle

### Core Loop

`AgentLoop.run()` in `backend/agent/loop.py` delegates to `_run_computer_use_engine()`, which constructs a `ComputerUseEngine` and calls `execute_task()`. The engine runs its own internal perceive → act → screenshot loop for up to `max_steps` turns (default 50, hard cap 200).

1. **Perceive** — capture screenshot via agent service HTTP API (`/screenshot`) or `docker exec scrot` fallback
2. **Think** — send screenshot + task + conversation history to the LLM
3. **Act** — receive structured CU action, execute via the desktop executor
4. **Record** — emit `CUTurnRecord` to the loop, which maps it to a `StepRecord` and broadcasts via WebSocket
5. **Loop or terminate** — continue on success; stop on `done`/`error` from model, user stop request, or step limit

### Safety Confirmation Flow

When the CU engine encounters a `require_confirmation` safety decision (e.g., for sensitive actions), the flow pauses:

1. Engine emits safety callback → `AgentLoop._on_safety()` broadcasts a `safety_confirmation` WebSocket event
2. Frontend shows a confirmation dialog to the user
3. User clicks confirm/deny → `POST /api/agent/safety-confirm`
4. Backend signals the waiting `asyncio.Event` → engine resumes or skips the action
5. **Timeout:** if no response within 60 seconds, the action is **denied** by default

### Session State Machine

```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> Running : POST /api/agent/start
    Running --> Running : step executed (perceive → think → act)
    Running --> Completed : model returns "done"
    Running --> Error : model returns "error" / 3 consecutive failures
    Running --> Completed : max_steps reached / user stop
    Completed --> [*]
    Error --> [*]
```

### In-Memory State

All session state lives in memory — no persistent database. State is lost on backend restart.

```python
_active_loops: dict[str, AgentLoop]     # session_id → loop instance
_active_tasks: dict[str, asyncio.Task]  # session_id → running task
_ws_clients: list[WebSocket]            # connected WebSocket clients
_safety_events: dict[str, Event]        # session_id → safety confirmation events
```

---

## 🤖 Supported Models

Defined in `backend/allowed_models.json` — the single source of truth for both backend validation and frontend dropdowns.

| Provider | Model ID | Display Name | Runtime Mode | CU Support | Notes |
|---|---|---|---|---|---|
| Google | `gemini-3-flash-preview` | Gemini 3 Flash Preview | Desktop | ✅ Native | Fast, lightweight CU model |
| Google | `gemini-3.1-pro-preview` | Gemini 3.1 Pro Preview | Desktop | ❌ `supports_computer_use: false` | Present in allowlist but **excluded from UI** — reserved for future CU support |
| Anthropic | `claude-sonnet-4-6` | Claude Sonnet 4.6 | Desktop | ✅ Native | Beta endpoint + `computer_20251124` tool |
| Anthropic | `claude-opus-4-6` | Claude Opus 4.6 | Desktop | ✅ Native | Beta endpoint + `computer_20251124` tool |
| OpenAI | `gpt-5.4` | GPT-5.4 | Desktop | ✅ Native | Responses API built-in `computer` tool |

> Browser mode was removed from the backend and frontend runtime. All supported providers now run through the desktop harness only.

> **Adding models:** Edit `backend/allowed_models.json`, restart the backend. The UI auto-refreshes via `GET /api/models`.

---

## 📡 API & WebSocket Reference

### REST Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Liveness probe — returns `{ "status": "ok" }` |
| `GET` | `/api/models` | Canonical model allowlist for frontend dropdowns |
| `GET` | `/api/engines` | Available engines (currently only `computer_use`) |
| `GET` | `/api/keys/status` | API key availability per provider (masked preview) |
| `POST` | `/api/keys/validate` | Live API key validation (lightweight call to the provider) |
| `GET` | `/api/screenshot` | Current screenshot as base64 PNG |
| `GET` | `/api/container/status` | Docker container + agent service health |
| `POST` | `/api/container/start` | Build-if-needed and start the sandbox container |
| `POST` | `/api/container/stop` | Stop all agents then remove the container |
| `POST` | `/api/container/build` | Trigger Docker image build |
| `GET` | `/api/agent-service/health` | Check if the in-container agent service responds |
| `POST` | `/api/agent-service/mode` | Confirm desktop mode; reject browser mode |
| `POST` | `/api/agent/start` | **Start a new agent session** (see payload below) |
| `POST` | `/api/agent/stop/{session_id}` | Stop a running session |
| `GET` | `/api/agent/status/{session_id}` | Session status + last action |
| `GET` | `/api/agent/history/{session_id}` | Full step history (without screenshots) |
| `POST` | `/api/agent/safety-confirm` | Respond to CU safety confirmation prompt |
| `GET` | `/vnc/{path}` | noVNC static file proxy |
| `WS` | `/vnc/websockify` | noVNC WebSocket proxy to container |

### `POST /api/agent/start` — Request Body

| Field | Type | Default | Constraints |
|---|---|---|---|
| `task` | `string` | *(required)* | Non-empty, max 10,000 chars |
| `provider` | `string` | *(required)* | `"google"`, `"anthropic"`, or `"openai"` |
| `model` | `string` | `"gemini-3-flash-preview"` | Must be in allowlist |
| `mode` | `string` | *(required)* | `"desktop"` only |
| `api_key` | `string?` | `null` | Optional — resolved from env if empty |
| `max_steps` | `int` | `50` | 1–200 |
| `engine` | `string` | `"computer_use"` | Only `"computer_use"` accepted |
| `execution_target` | `string` | `"docker"` | Only `"docker"` accepted |
| `reasoning_effort` | `string?` | `null` | OpenAI only: `"none"`, `"low"`, `"medium"`, `"high"`, `"xhigh"`. Falls back to `OPENAI_REASONING_EFFORT` env var, then `"low"`. |

### WebSocket Events (`/ws`)

**Server → Client:**

| Event | Payload | Description |
|---|---|---|
| `screenshot` | `{ screenshot: <base64> }` | Screenshot from agent step |
| `screenshot_stream` | `{ screenshot: <base64> }` | Periodic desktop capture (1.5 s interval) |
| `log` | `{ log: LogEntry }` | Agent log message |
| `step` | `{ step: StepRecord }` | Step completion (action, timestamp, error) |
| `agent_finished` | `{ session_id, status, steps }` | Agent loop terminated |
| `pong` | `{}` | Heartbeat response |

**Client → Server:** `{ "type": "ping" }` every 15 seconds for keepalive.

---

## 🚀 Quickstart

### Prerequisites

| Requirement | Version |
|---|---|
| **Docker** | With BuildKit support |
| **Python** | 3.13+ |
| **Node.js** | 18+ (npm included) |

### 1. Clone

```bash
git clone https://github.com/pypi-ahmad/computer-use.git
cd computer-use
```

### 2. Automated Setup

**Windows:**
```bat
setup.bat
```

**Linux / macOS:**
```bash
bash setup.sh
```

Both scripts: verify prerequisites → build Docker image → create Python venv → install pip dependencies → install frontend npm packages. Pass `--clean` for a destructive rebuild (removes containers, images, and volumes first).

### 3. Configure API Key (at least one required)

```bash
# Option A: .env file in project root
echo "GOOGLE_API_KEY=your-key-here" >> .env
# or
echo "ANTHROPIC_API_KEY=your-key-here" >> .env
# or
echo "OPENAI_API_KEY=your-key-here" >> .env

# Option B: system environment variable
# Option C: paste directly in the UI at runtime
```

### 4. Run

| Terminal | Command |
|---|---|
| **Backend** | `python -m backend.main` (activate venv first) |
| **Frontend** | `cd frontend && npm run dev` |
| **Open** | [http://127.0.0.1:3000](http://127.0.0.1:3000) |

> **Container:** The Docker container starts automatically when you click **Start Agent** in the UI. You can also start it manually with `docker compose up -d` if you want the desktop available before launching a task.

> **Windows:** Prefer `127.0.0.1` over `localhost` to avoid IPv6 binding issues with Docker.

---

## 📦 Installation

### Manual Setup (Full Detail)

```bash
# 1. Clone
git clone https://github.com/pypi-ahmad/computer-use.git
cd computer-use

# 2. Build Docker image
docker compose build
# or: docker build -t cua-ubuntu:latest -f docker/Dockerfile .

# 3. Python backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 4. Frontend
cd frontend && npm install && cd ..
```

### Key Dependencies

**Backend** (`requirements.txt`):

| Package | Purpose |
|---|---|
| `fastapi` + `uvicorn` | HTTP API + WebSocket server |
| `google-genai` | Google Gemini API client |
| `anthropic` | Anthropic Claude API client |
| `openai` | OpenAI API client |
| `httpx` | Async HTTP client (agent service communication) |
| `Pillow` | Screenshot resizing for Claude coordinate scaling |
| `python-dotenv` | `.env` file loading |
| `pydantic` | Request/response validation |
| `websockets` | noVNC WebSocket proxy |
| `opencv-python-headless` | Image processing for screenshot handling |
| `numpy` | Array operations used alongside OpenCV |

**Frontend** (`package.json`):

| Package | Purpose |
|---|---|
| `react` 19 + `react-dom` | UI framework |
| `react-router-dom` 7 | Client-side routing (`/` → Workbench, `/workbench` → redirect, `*` → 404) |
| `lucide-react` | SVG icon library (replaces emoji icons) |
| `vite` 6 | Dev server with HMR + configurable API proxy |

### Platform Notes

- **Docker container** is always Linux (Ubuntu 24.04) regardless of host OS
- The Docker image is still large because it includes XFCE, Chrome, LibreOffice, and other desktop applications

---

## 📝 Configuration

### API Key Resolution

Keys are resolved in priority order — the first non-empty value wins:

| Priority | Source | Setup |
|---|---|---|
| 1 (highest) | **UI input** | Paste directly in the Workbench |
| 2 | **`.env` file** | `GOOGLE_API_KEY=...`, `ANTHROPIC_API_KEY=...`, or `OPENAI_API_KEY=...` in project root |
| 3 | **System env var** | Same variable names set in your shell |

### Environment Variables

**Backend** (set in `.env` or system environment — `.env` values do not override existing system environment variables):

| Variable | Default | Description |
|---|---|---|
| `GOOGLE_API_KEY` | — | Google Gemini API key |
| `ANTHROPIC_API_KEY` | — | Anthropic Claude API key |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `OPENAI_REASONING_EFFORT` | `low` | OpenAI reasoning effort: `none`, `low`, `medium`, `high`, `xhigh` |
| `GEMINI_MODEL` | `gemini-3-flash-preview` | Default model name |
| `CONTAINER_NAME` | `cua-environment` | Docker container name |
| `AGENT_SERVICE_HOST` | `127.0.0.1` | Agent service hostname |
| `AGENT_SERVICE_PORT` | `9222` | Agent service port |
| `AGENT_MODE` | `desktop` | Default and only supported runtime mode |
| `SCREEN_WIDTH` | `1440` | Virtual display width (pixels) |
| `SCREEN_HEIGHT` | `900` | Virtual display height (pixels) |
| `MAX_STEPS` | `50` | Default max steps per session |
| `STEP_TIMEOUT` | `30.0` | Per-step timeout (seconds) |
| `HOST` | `0.0.0.0` | Backend server bind address |
| `PORT` | `8000` | Backend server port |
| `DEBUG` | `false` | Enable debug logging + Uvicorn reload |
| `CORS_ORIGINS` | *(see below)* | Comma-separated allowed CORS origins |

**Frontend** (set in system environment before running `npm run dev`):

| Variable | Default | Description |
|---|---|---|
| `VITE_API_PORT` | `8000` | Backend port for the Vite dev proxy (must match `PORT`) |

**Container-side** (set in `docker-compose.yml`):

| Variable | Default | Description |
|---|---|---|
| `VNC_PASSWORD` | *(unset)* | Set to require VNC authentication |
| `DISPLAY` | `:99` | X11 display identifier |
| `SCREEN_DEPTH` | `24` | X11 color depth |

**CORS defaults** (when `CORS_ORIGINS` is not set):
`http://localhost:5173`, `http://127.0.0.1:5173`, `http://localhost:3000`, `http://127.0.0.1:3000`

---

## ▶️ Usage

### Workbench (`/`)

The single-page workbench is the canonical interface — a responsive three-pane layout:

| Pane | Content |
|---|---|
| **Left sidebar** | Provider/model selection, API key source toggle (manual / `.env` / system), key validation, collapsible advanced settings (max steps, reasoning effort), task textarea with character counter, example task chips, Start/Stop/Clear buttons |
| **Center** | Live desktop via interactive noVNC iframe (falls back to WebSocket screenshots), progress bar during agent execution |
| **Right panel** | Expandable step-by-step timeline with action icons, session history toggle, log panel with severity badges, JSON/HTML export, log download |

**Header** includes: environment start/stop controls with loading state, connection status pill, agent running indicator, approximate cost estimate, step counter, API docs link, and dark/light theme toggle.

**Routing:**

| Path | Behavior |
|---|---|
| `/` | Workbench (canonical) |
| `/workbench` | Redirects to `/` |
| `*` | 404 page with link back to `/` |

### Typical Workflow

1. **Select provider and model** — e.g., Google / `gemini-3-flash-preview`
2. **Configure API key** — use `.env`, system env, or paste in UI (with pre-flight validation)
3. **Describe the task** — *"Go to wikipedia.org and search for 'artificial intelligence'"*
4. **Click Start** — the container auto-starts if needed, the agent loop begins
5. **Observe** — watch the live desktop, step timeline, and logs in real time
6. **Result** — a completion banner shows the outcome; session is saved to history

### First Run

On first visit, a welcome overlay explains the 3-step flow (choose provider → describe task → watch). It is dismissed once and remembered via localStorage.

### Viewing the Desktop

| Method | Description |
|---|---|
| **noVNC** (embedded) | Full interactive desktop in the center pane — the default when the container is running |
| **noVNC** (standalone) | `http://127.0.0.1:6080` — direct noVNC access outside the app |
| **Screenshot stream** | Automatic base64 PNGs via WebSocket when noVNC is unavailable |

### Export & History

- **JSON export** — full session data (task, steps, logs, timestamps)
- **HTML export** — formatted, self-contained session report with safely escaped content
- **Log download** — timestamped `.txt` file
- **Session history** — last 50 sessions stored in localStorage, viewable from the timeline panel

### Stopping

```bash
# Stop the container
docker compose down

# Stop backend/frontend: Ctrl+C in their respective terminals
```

### 📖 Detailed Usage Guide

For in-depth operational documentation — including feature-by-feature breakdowns, every configuration option, full API/WebSocket references, keyboard shortcuts, troubleshooting, and known limitations — see the **[Usage Guide](docs/USAGE.md)**.

<details>
<summary>Usage Guide contents</summary>

| Section | Topics |
|---|---|
| [Who This Is For](docs/USAGE.md#who-this-is-for) | Target audience |
| [Prerequisites](docs/USAGE.md#prerequisites) | Docker, Python, Node.js requirements |
| [Installation](docs/USAGE.md#installation) | Automated and manual setup |
| [Running Locally](docs/USAGE.md#running-locally) | Starting the backend, frontend, and container |
| [Using the Workbench](docs/USAGE.md#using-the-workbench) | First run, environment, providers, API keys, tasks, monitoring, safety, stopping |
| [Features](docs/USAGE.md#features) | Multi-provider AI, Docker sandbox, streaming, timeline, history, export, cost estimation, context pruning, safety flow, reasoning effort, key management, noVNC, theming, toasts, error boundary |
| [Supported Models](docs/USAGE.md#supported-models) | Model allowlist and how to add models |
| [Supported Actions](docs/USAGE.md#supported-actions) | High-level actions and low-level primitives |
| [Configuration Reference](docs/USAGE.md#configuration-reference) | All environment variables with defaults |
| [API Endpoints](docs/USAGE.md#api-endpoints) | Full REST API reference with request/response schemas |
| [WebSocket Events](docs/USAGE.md#websocket-events) | Server→Client and Client→Server event contracts |
| [Keyboard Shortcuts](docs/USAGE.md#keyboard-shortcuts) | Workbench keyboard bindings |
| [Troubleshooting](docs/USAGE.md#troubleshooting) | Common issues and fixes |
| [Limitations](docs/USAGE.md#limitations) | Known constraints and caveats |

</details>

---

## 🧪 Testing

| | |
|---|---|
| **Framework** | pytest |
| **Tests** | 118 tests across 10 files |
| **Hermetic** | All tests use mocks/patches — no running container or network required |

### Running Tests

```bash
# Activate venv, then:
pytest tests/ -v               # All tests, verbose
pytest tests/ -v --tb=short    # Concise tracebacks
pytest tests/ -q               # Quick summary
```

### Test Coverage

| File | Scope |
|---|---|
| `test_computer_use_engine.py` | Coordinate denormalization, executor mocking, safety decisions, OpenAI runtime path + helpers |
| `test_claude_actions.py` | Claude action dispatch into the shared desktop executor interface |
| `test_coordinate_scaling.py` | Claude screenshot scaling & coordinate math |
| `test_context_pruning.py` | Conversation context pruning logic |
| `test_config.py` | Config singleton, `from_env()`, agent service URL, OpenAI key resolution |
| `test_models.py` | ActionType enum, Pydantic model validation, StructuredError |
| `test_model_policy.py` | `allowed_models.json` integrity, model endpoint, provider rejection |
| `test_prompts.py` | Prompt separation (Gemini vs Claude vs OpenAI), viewport injection, drift detection |
| `test_server_validation.py` | API input validation: engines, providers, models, desktop-only mode enforcement, rate limiting, safety |
| `test_docker_security.py` | Container security settings validation |

---

## 🐳 Docker Runtime

### Container: `cua-environment`

Built from `docker/Dockerfile` on **Ubuntu 24.04**. The entrypoint (`docker/entrypoint.sh`) starts services in sequence:

1. **D-Bus** — system + session bus for desktop communication
2. **Xvfb** — virtual X11 framebuffer at `:99`, resolution `1440×900×24`
3. **XFCE 4** — full desktop environment with window manager
4. **x11vnc** — VNC server on port 5900 (optional password via `VNC_PASSWORD`)
5. **noVNC + websockify** — browser-accessible VNC on port 6080
6. **Browser bootstrap** — sets Chrome as default browser, seeds profile to skip first-run dialogs
7. **Agent Service** — `agent_service.py` runs as PID 1 to receive signals cleanly

### Pre-installed Software

Google Chrome, xdotool, wmctrl, xclip, scrot, ffmpeg, Node.js 20, LibreOffice, VLC, gedit, file manager, terminal emulators

### Agent Service (`docker/agent_service.py`)

An HTTP server running inside the container handling desktop automation and screenshot capture:

| Endpoint | Method | Purpose |
|---|---|---|
| `/health` | GET | Liveness check with supported mode metadata |
| `/screenshot` | GET | Capture the desktop via scrot |
| `/action` | POST | Execute a single desktop action |
| `/mode` | POST | Confirm desktop mode; reject browser mode |

### Port Map

| Port | Service | Binding |
|---|---|---|
| `3000` | Frontend (Vite dev server) | Host |
| `5900` | VNC (x11vnc) | `127.0.0.1` |
| `6080` | noVNC (websockify) | `127.0.0.1` |
| `8000` | Backend API (FastAPI) | `0.0.0.0` |
| `9222` | Agent Service | `127.0.0.1` |

---

## 🔧 Troubleshooting

### Agent service unreachable / timeouts

- Use `127.0.0.1` (not `localhost`) in `AGENT_SERVICE_HOST` — especially on Windows
- Verify: `curl http://127.0.0.1:9222/health`
- Check container: `docker compose ps`
- Wait 10–20 seconds after start for XFCE + agent service to fully boot

### `POST /api/agent/start` returns 400

| Cause | Fix |
|---|---|
| Invalid model | Use a model from `GET /api/models` |
| Empty task | Provide a non-blank task description |
| Missing API key | Set key in UI, `.env`, or system env |
| Wrong engine / target | Only `engine=computer_use` and `execution_target=docker` are valid |

### `POST /api/agent/start` returns 429

| Cause | Fix |
|---|---|
| Rate limit | Max 10 starts per 60 seconds — wait and retry |
| Concurrent cap | Max 3 simultaneous sessions — stop one first |

### noVNC shows a black desktop

Normal during the first 5–15 seconds after container start while XFCE boots. Check: `docker compose logs -f cua-environment`

### Screenshot capture fails

- Agent service may not be ready yet — check `/health`
- The system falls back to `docker exec scrot` automatically if the HTTP capture fails

### Gemini actions miss their targets

Gemini uses normalized 0–999 coordinates, which the engine denormalizes using `SCREEN_WIDTH` × `SCREEN_HEIGHT`. Ensure these values match the container's Xvfb resolution (default: 1440×900).

### Container build takes a long time

Expected — the image installs XFCE, Chrome, LibreOffice, and many utilities. First build is still large; subsequent builds use layer cache.

---

## 🔐 Safety & Security

### Input Validation

| Protection | Details |
|---|---|
| Rate limiting | 10 agent starts per 60-second sliding window |
| Concurrent session cap | Max 3 active sessions |
| Max steps hard cap | 200 regardless of client input |
| Provider/model allowlists | Server-side validation against `allowed_models.json` |
| Session ID validation | UUID format enforced |
| Task length limit | 10,000 characters |
| API key masking | Keys truncated in all audit logs |

### Container Security

| Setting | Value |
|---|---|
| `security_opt` | `no-new-privileges:true` |
| `shm_size` | `2gb` |
| Memory limit | `4g` |
| CPU limit | `2` cores |
| Port binding | `127.0.0.1` only (not externally exposed) |
| Healthcheck | `curl -f http://localhost:9222/health` every 30s |
| Init process | `init: true` (proper signal handling) |
| Restart policy | `unless-stopped` |

### CU Safety Confirmation

Actions flagged with `require_confirmation` by the Gemini CU protocol are **not auto-approved**. They surface to the user via WebSocket, and the agent loop blocks until the user explicitly confirms or denies (60-second timeout defaults to **deny**).

### Security Boundaries

- **No authentication** on the backend API — designed for local development only
- **No TLS** between backend and agent service (localhost-only)
- **VNC unauthenticated** by default — set `VNC_PASSWORD` for password-protected access
- **WebSocket connections** have no auth; rely on CORS (`localhost:3000/5173` only)
- **Model output is untrusted** — all actions execute inside the sandboxed container, not on the host

---

## 📁 Project Structure

```
computer-use/
├── backend/
│   ├── main.py                    # Uvicorn entry point
│   ├── server.py                  # FastAPI routes, WebSocket, noVNC proxy, key validation
│   ├── config.py                  # Config dataclass, env loading, API key resolution
│   ├── models.py                  # ActionType enum, Pydantic request/response models
│   ├── engine.py                  # ComputerUseEngine, GeminiCUClient, ClaudeCUClient,
│   │                              #   OpenAICUClient, DesktopExecutor
│   ├── allowed_models.json        # Canonical model allowlist (5 models, 3 providers)
│   ├── engine_capabilities.json   # Engine capability schema (v3.0)
│   ├── engine_capabilities.py     # Schema loader for engine_capabilities.json
│   ├── certifier.py               # Runtime engine certification checks
│   ├── parity_check.py            # ActionType ↔ capabilities ↔ prompt drift audit
│   ├── action_aliases.py          # CU action alias resolution
│   ├── docker_manager.py          # Container lifecycle (build, start, stop, health)
│   └── agent/
│       ├── loop.py                # AgentLoop orchestrator (perceive → think → act)
│       ├── prompts.py             # System prompts for Gemini, Claude, and OpenAI CU
│       └── screenshot.py          # Screenshot capture via agent service + fallback
├── docker/
│   ├── Dockerfile                 # Ubuntu 24.04, XFCE 4, Chrome, desktop apps
│   ├── entrypoint.sh              # Service startup: Xvfb → XFCE → VNC → agent service
│   └── agent_service.py           # In-container HTTP server: action dispatch + screenshots
├── frontend/
│   ├── package.json               # React 19, Vite 6, React Router 7, lucide-react
│   ├── vite.config.js             # Dev server + configurable API/WS/VNC/docs proxy
│   ├── index.html                 # Meta tags, OG tags, SVG favicon
│   └── src/
│       ├── main.jsx               # Router: / → Workbench, /workbench → redirect, * → 404
│       ├── api.js                 # REST client (9 exports incl. validateKey)
│       ├── index.css              # Global styles, design tokens, light theme, component CSS
│       ├── hooks/
│       │   └── useWebSocket.js    # WS with auto-reconnect (2s), heartbeat (15s), safety detection
│       ├── components/
│       │   ├── ScreenView.jsx     # noVNC iframe + screenshot fallback
│       │   ├── SafetyModal.jsx    # 60s countdown, approve/deny, auto-deny on timeout
│       │   ├── CompletionBanner.jsx  # Success/error/stopped banner with lucide icons
│       │   ├── ToastContainer.jsx # Toast notification system (4s auto-dismiss)
│       │   ├── WelcomeOverlay.jsx # First-run 3-step onboarding guide
│       │   └── ErrorBoundary.jsx  # React error boundary with recovery UI
│       ├── pages/
│       │   ├── Workbench.jsx      # Canonical single-page: sidebar + screen + timeline + logs
│       │   ├── Workbench.css      # 3-pane layout, responsive breakpoints, theme toggle
│       │   └── NotFound.jsx       # 404 page with link to /
│       └── utils/
│           ├── formatTime.js      # Timestamp → locale time string
│           ├── pricing.js         # Centralized approximate model pricing + estimateCost()
│           ├── sessionHistory.js  # Bounded localStorage session history (50 cap)
│           └── theme.js           # Theme get/set/init with data-theme attribute
├── docs/
│   ├── USAGE.md                   # Detailed usage guide
│   └── assets/                    # SVG architecture and flow diagrams
│       ├── architecture.svg
│       ├── execution-flow.svg
│       └── components.svg
├── tests/
│   ├── test_computer_use_engine.py
│   ├── test_claude_actions.py
│   ├── test_coordinate_scaling.py
│   ├── test_context_pruning.py
│   ├── test_config.py
│   ├── test_models.py
│   ├── test_model_policy.py
│   ├── test_prompts.py
│   ├── test_server_validation.py
│   └── test_docker_security.py
├── docker-compose.yml             # Container orchestration (resource limits, ports)
├── requirements.txt               # Python dependencies
├── pyproject.toml                 # Project metadata
├── setup.bat                      # Windows one-command setup
├── setup.sh                       # Linux/macOS one-command setup
└── LICENSE                        # MIT License
```

---

## 🗺️ Roadmap & Known Limitations

### Current Limitations

- **No persistent storage** — session state lives in memory; lost on backend restart. localStorage-based session history captures task/model/status but not full replay data.
- **No authentication** — designed for local development, not production deployment
- **Single container** — one Docker container serves all sessions; no per-session isolation
- **Preview models** — Gemini, Claude, and OpenAI CU capabilities are in preview/beta and subject to change
- **No CI/CD pipeline** — tests run locally; no automated GitHub Actions workflow yet
- **Container size** — ~3–4 GB image due to full desktop environment
- **Coordinate precision** — Gemini's 0–999 normalization can cause slight pixel misalignment on non-standard resolutions
- **Cost estimates** — based on approximate per-token pricing; actual costs depend on token usage patterns

### Potential Future Work

- Persistent session storage (SQLite or Redis) with full replay
- Per-session container isolation
- Authentication and role-based access
- CI/CD with automated test runs
- Production deployment configuration (TLS, reverse proxy)
- Additional model providers as CU support expands
- Configurable action timeouts and retry policies

---

## 🤝 Contributing

Contributions are welcome. To get started:

1. **Fork** the repository and **clone** locally
2. Run `setup.sh` or `setup.bat` for a complete local environment
3. Create a **feature branch** from `main`
4. Make changes — follow existing code conventions:
   - Python: `"""triple-quote"""` docstrings, type hints, consistent formatting
   - JS/JSX: `/** JSDoc */` comments on exported functions and components, `lucide-react` for icons
5. **Write tests** for any new behavior; keep tests hermetic (mocks/patches, no live network or container required)
6. **Run the test suite**: `pytest tests/ -v --tb=short`
7. **Open a pull request** with a clear description of changes

### Development Commands

```bash
# Run all tests
pytest tests/ -v

# Run a specific test file
pytest tests/test_computer_use_engine.py -v

# Start backend in debug mode (auto-reload)
DEBUG=true python -m backend.main

# Frontend dev server (with API proxy to :8000)
cd frontend && npm run dev

# Tool parity check
python -m backend.parity_check
```

---

## 📄 License

This project is licensed under the **MIT License** — see [LICENSE](LICENSE) for details.

Copyright (c) 2026 Ahmad

---

<div align="center">

**[Back to Top](#️-cua--computer-using-agent)** · **[Quickstart](#-quickstart)** · **[Architecture](#-architecture)** · **[Configuration](#-configuration)** · **[Contributing](#-contributing)**

</div>
