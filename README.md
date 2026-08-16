# Computer Use Workbench

A local app that lets an AI model operate a **real Linux desktop** for
you — click, type, open apps, and use a browser — while you watch.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/pypi-ahmad/computer-use/actions/workflows/ci.yml/badge.svg)](https://github.com/pypi-ahmad/computer-use/actions/workflows/ci.yml)
[![Python 3.12–3.14](https://img.shields.io/badge/python-3.12%20%7C%203.13%20%7C%203.14-3776AB)](pyproject.toml)
[![Latest release](https://img.shields.io/github/v/release/pypi-ahmad/computer-use)](https://github.com/pypi-ahmad/computer-use/releases/latest)

You write a task in plain English. A model from **Google**, **OpenAI**,
or **Anthropic** looks at screenshots of an isolated Docker desktop
and issues official Computer Use actions (`click`, `type`, `hotkey`,
…). Those actions run inside Ubuntu/XFCE, not on your host. You see
the desktop live in the browser (noVNC).

This is **not** a hosted product. Clone it, run it on **your machine**,
use **your API keys**. **MIT** ([LICENSE](LICENSE)). Contributions
(tests, bugs, ideas, PRs) are welcome. **Do not send money.** **You
own every file and payload you put in the app** (PDFs, other uploads,
sandbox files, keys). [DATA.md](DATA.md) · [OPEN_SOURCE.md](OPEN_SOURCE.md)
· [SUPPORT.md](SUPPORT.md).

Latest tag **v3.1.1**. This file matches the current tree, including
Unreleased work in `CHANGELOG.md`.

> Computer Use can execute destructive actions. Use test accounts and
> non-sensitive data. This project is not a multi-tenant service and
> does not make model actions safe by itself.

## What this project is

Three processes on one workstation:

| Piece | Role |
|---|---|
| React dashboard (`127.0.0.1:8505` in dev) | Six tabs. Live **Mission control** sits in the left CONTROL sidebar; the main pane is the XFCE screen. |
| FastAPI backend (`127.0.0.1:8100`) | Sessions, safety prompts, SQLite audit, cost estimates, noVNC proxy. |
| Docker sandbox `cua-environment` | Xvfb + XFCE + x11vnc (`-nopw`) + `docker/agent_service.py`. Loopback ports 5900 / 6080 / 9222. |

**A run, in order**

1. The Live viewport connects as soon as the sandbox is healthy. You
   do not start a run to see XFCE.
2. You pick a model (default `gemini-3.7-flash` on `gemini-direct`,
   fallback `gemini-3.5-flash-lite@gemini-direct`) and click
   **Start run**. That is `POST /api/v2/sessions` with `maxSteps: 50`.
3. Optional **Provider web search** (`useBuiltinSearch`) advertises
   `mcp_fetch` on the Computer Use loop. The model calls that tool;
   the host runs official Fetch MCP (`uvx mcp-server-fetch`). No
   OpenAI / Anthropic / Gemini `web_search`.
4. The selected vendor SDK returns desktop actions. The backend maps
   them onto the sandbox over `AGENT_SERVICE_TOKEN`.
5. Risky steps can pause for Approve/Deny (60 s then auto-deny).
6. After the run, Audit trail, Session cost (`EXECUTION` tokens ×
   list rates), and Analytics read SQLite on your disk.

**What it is not:** a cloud agent, a multi-user SaaS, or a guarantee
that the model will stay inside the task. Isolation is a disposable
container, not a review of every click.

## Index

**This README**

1. [What this project is](#what-this-project-is)
2. [Open source](#open-source)
3. [Features](#features)
4. [Demo](#demo)
5. [Tech stack](#tech-stack)
6. [Project structure](#project-structure)
7. [Installation and setup](#installation-and-setup)
8. [Environment variables](#environment-variables)
9. [Usage](#usage)
10. [Examples](#examples)
11. [How it works](#how-it-works)
12. [Configuration options](#configuration-options)
13. [Documentation](#documentation)
14. [Community](#community)
15. [License](#license)
16. [Acknowledgements](#acknowledgements)

**All other documents**

| Start here | Community | Technical | History |
|---|---|---|---|
| [USAGE.md](USAGE.md) | [OPEN_SOURCE.md](OPEN_SOURCE.md) | [TECHNICAL.md](TECHNICAL.md) | [CHANGELOG.md](CHANGELOG.md) |
| [TESTING.md](TESTING.md) | [SUPPORT.md](SUPPORT.md) | [docs/codebase/ARCHITECTURE.md](docs/codebase/ARCHITECTURE.md) | [docs/migration-v2.md](docs/migration-v2.md) |
| [DATA.md](DATA.md) | [CONTRIBUTING.md](CONTRIBUTING.md) | [docs/codebase/STACK.md](docs/codebase/STACK.md) | [docs/rollback-v2.md](docs/rollback-v2.md) |
| [docs/deployment.md](docs/deployment.md) | [SECURITY.md](SECURITY.md) | [docs/codebase/STRUCTURE.md](docs/codebase/STRUCTURE.md) | [docs/gemini-successor-evaluation.md](docs/gemini-successor-evaluation.md) |
| [docs/computer-use-prompt-guide.md](docs/computer-use-prompt-guide.md) | [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | [docs/codebase/CONVENTIONS.md](docs/codebase/CONVENTIONS.md) | [docs/research-audit-2026-07-23.md](docs/research-audit-2026-07-23.md) |
| [docs/business-guide.md](docs/business-guide.md) | [LICENSE](LICENSE) | [docs/codebase/INTEGRATIONS.md](docs/codebase/INTEGRATIONS.md) | [docs/release-notes-v3.1.1.md](docs/release-notes-v3.1.1.md) |
| [docs/zero-to-hero-study-handbook.md](docs/zero-to-hero-study-handbook.md) | [AGENTS.md](AGENTS.md) | [docs/codebase/CONCERNS.md](docs/codebase/CONCERNS.md) | [docs/release-notes-v3.1.0.md](docs/release-notes-v3.1.0.md) |
| [docs/zero-to-hero-study-handbook.html](docs/zero-to-hero-study-handbook.html) | [Bug template](.github/ISSUE_TEMPLATE/bug.yml) | [docs/codebase/TESTING.md](docs/codebase/TESTING.md) | [docs/release-notes-v3.0.3.md](docs/release-notes-v3.0.3.md) |
| [docs/zero-to-hero-study-handbook.pdf](docs/zero-to-hero-study-handbook.pdf) | [Idea template](.github/ISSUE_TEMPLATE/feature.yml) | [docker/SECURITY_NOTES.md](docker/SECURITY_NOTES.md) | [docs/release-notes-v3.0.2.md](docs/release-notes-v3.0.2.md) |
| | [PR template](.github/PULL_REQUEST_TEMPLATE.md) | [evals/README.md](evals/README.md) | [docs/release-notes-v3.0.1.md](docs/release-notes-v3.0.1.md) |
| | | [.env.example](.env.example) | [docs/release-notes-v3.0.0.md](docs/release-notes-v3.0.0.md) |
| | | | [docs/release-notes-v2.0.0.md](docs/release-notes-v2.0.0.md) |

Descriptions: [Documentation](#documentation).

## Open source

This repository is **MIT-licensed** ([LICENSE](LICENSE)). Clones,
forks, local use, and contributions are **always welcome**.

- Run it **on your own machine**. There is no hosted service.
- Use **your own provider API keys** (`GOOGLE_API_KEY` then
  `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`) or a
  Providers-tab credential session. `GOOGLE_API_KEY` already set in the
  user/process environment is snapshotted before `.env` loads
  (`backend/infra/config.py` `_USER_ENV`) and wins over a `.env`
  assignment. Keys stay process-local and are never written to SQLite.
- **You are solely responsible** for every file and payload you put
  into the app: PDFs, `.txt`, `.md`, `.docx` uploads, sandbox desktop
  files, browser sessions, screenshots, and task text. See
  [DATA.md](DATA.md).
- Community help is what this project needs: testers, bug reports,
  improvement ideas, features, docs, and PRs. See
  [SUPPORT.md](SUPPORT.md) and [CONTRIBUTING.md](CONTRIBUTING.md).
- **No financial help.** Do not send donations, sponsorship, bounties,
  or paid-support offers. Time and reports only.

[Latest release](https://github.com/pypi-ahmad/computer-use/releases/latest)
· [MIT License](LICENSE)
· Python 3.12–3.14
· [CI](https://github.com/pypi-ahmad/computer-use/actions/workflows/ci.yml)

## Features

- **Three direct Computer Use routes** (catalog:
  `backend/models/computer_use_models.v2.json`)
  - `gemini-direct` — Gemini 3.7 Flash (Live default model
    `gemini-3.7-flash`, `preferredRoute` `gemini-direct`) or Gemini 3.5
    Flash-Lite (Live default fallback
    `gemini-3.5-flash-lite@gemini-direct`)
  - `anthropic-direct` — Claude Sonnet 5 via Anthropic Messages
    (`computer_20251124`)
  - `openai-direct` — GPT-5.6 Luna or GPT-5.6 Terra via the OpenAI
    Responses API
- **Six-tab React dashboard** — Live session, Audit trail, Session
  cost, Workflow library, Providers, Analytics
  (`frontend/src/App.tsx`). On Live, Mission control is portaled into
  the CONTROL sidebar; the main pane is the noVNC viewport.
- **Live sandbox viewport** — noVNC iframe of XFCE as soon as the
  container is ready (`/vnc/vnc.html?path=vnc/websockify`, no VNC
  password). You do not start a run to see the screen.
- **Web-search planning** — Live toggle `useBuiltinSearch` runs
  `backend/providers/planner.py` + `backend/infra/mcp_fetch.py`
  (`uvx mcp-server-fetch`). It does **not** attach provider
  `web_search` / Google Search to the Computer Use loop.
- **Session event socket** — `/api/v2/ws/desktop` while idle, then
  `/api/v2/ws/{session_id}` after **Start run**. Carries pipeline,
  safety, and terminal events. CUAF preview frames are decoded but
  **not** shown; the picture is noVNC.
- **Session cost** — list-rate USD from recorded `EXECUTION` tokens
  (`frontend/src/pricing.ts`)
- **Typed `/api/v2` contract** — sessions, safety decisions,
  workflows, analytics, export, retention, diagnostics, shutdown
- **Deterministic route fallback** — primary route plus one optional
  `model@route` pair (Live select)
- **Safety policies** — `provider_default`, `confirm_mutating`,
  `read_only`; Approve/Deny on
  `POST /api/v2/sessions/{id}/safety-decisions`
- **Process-local credentials** — API keys and Google OAuth tokens
  stay in memory and expire within eight hours; they are not written
  to SQLite
- **SQLite WAL audit store** — sessions, actions, events, metrics,
  workflow versions (`data/computer-use-v2.sqlite3`)
- **Audit frame store** — screenshots under `data/audit-frames` with
  7-day or 1 GiB eviction
- **Declarative workflows** — named step lists; **Use in live
  session** calls `POST /api/v2/workflows/{id}/compile`
- **Windows one-file launcher** — `run.cmd` installs missing host
  tools, then starts the stack

Live catalog: `backend/models/allowed_models.json` and
`backend/models/computer_use_models.v2.json`. The July 2026 research
note is historical:
[docs/research-audit-2026-07-23.md](docs/research-audit-2026-07-23.md).

## Demo

After `run.cmd` (or `dev.py --open-browser`), open
[http://127.0.0.1:8505](http://127.0.0.1:8505). The Live session
viewport embeds the sandbox desktop.

![Sandbox XFCE desktop](assets/screenshot.png)

*Isolated XFCE desktop in the Live noVNC viewport. The screenshot is
from an earlier UI revision; the current operator surface is the
six-tab dashboard (Mission control in the left CONTROL sidebar).*

A local smoke task (no web search, no attachments):

> Open the file manager. Stop when the file manager window is visible.

If the file manager appears and the session badge reaches `COMPLETED`,
the install works. Full steps: [TESTING.md](TESTING.md).

## Tech stack

| Layer | What this repo uses |
|---|---|
| Backend | Python 3.12–3.14, FastAPI 0.141.1, Uvicorn, Pydantic 2, httpx, Pillow |
| Providers | `openai` 2.30, `anthropic` 0.88, `google-genai` 2.7, `google-auth` |
| Frontend | React 19, React Router 7, Vite 6, TypeScript 5.7, Lucide |
| Sandbox | Docker Compose, `cua-ubuntu:latest` (Ubuntu 24.04), Xvfb, XFCE, x11vnc (`-nopw`), noVNC, `docker/agent_service.py` |
| Persistence | SQLite WAL + on-disk audit frames |
| Tooling | uv (`uv.lock`), Ruff, mypy, pytest 9, Vitest 3, GitHub Actions |

Package name in `pyproject.toml`: `computer-use-workbench` **3.1.1**.

## Project structure

```text
computer-use/
├── backend/                  # FastAPI app
│   ├── main.py               # Uvicorn entry; public-bind guardrail
│   ├── server/               # HTTP/WS, noVNC proxy, app lifespan
│   ├── v2/                   # /api/v2 REST, credentials, SQLite, frames
│   ├── engine/               # OpenAI / Anthropic / Gemini Computer Use clients
│   ├── providers/            # Run adapters + MCP-fetch planner
│   ├── executor.py           # Desktop actions → agent_service
│   ├── loop.py               # Session / step orchestration
│   ├── infra/                # Config, Docker, logging, mcp_fetch.py
│   └── models/               # allowed_models.json, v2 catalog, schemas
├── frontend/                 # React + Vite dashboard
│   └── src/
│       ├── App.tsx           # Six tabs; Live form in CONTROL sidebar
│       ├── pricing.ts        # Session-cost list rates
│       └── api.ts            # /api/v2 client; desktopViewerSrc()
├── docker/                   # Sandbox image, entrypoint, agent_service
├── docker-compose.yml        # cua-environment (5900, 6080, 9222)
├── tests/                    # Offline pytest (excludes integration by default)
├── evals/                    # Offline HTTP/runtime evals
├── scripts/                  # Handbook site, release zip, changelog watchdog
├── docs/                     # Operator, architecture, and release notes
├── assets/screenshot.png     # Demo screenshot
├── run.cmd                   # Windows setup-if-needed + launch
├── setup.bat / setup.sh      # Bootstrap
├── START.bat                 # Always run setup, then launch
├── dev.py / dev.bat / dev.sh # Restart sandbox, backend, Vite
└── .env.example              # Environment template
```

## Installation and setup

### Requirements

- Docker Desktop (engine running)
- Node.js 22+
- [uv](https://docs.astral.sh/uv/) and Python 3.12 (3.13 and 3.14 are
  also tested in CI)
- Windows 11 for `run.cmd` / `START.bat`; Linux/macOS use `setup.sh`
  and `dev.sh`

### Windows (recommended)

```powershell
git clone https://github.com/pypi-ahmad/computer-use.git
cd computer-use
.\run.cmd
```

`run.cmd` installs missing host tools (uv, Python 3.12, Node.js LTS,
Docker Desktop) via winget, copies `.env.example` to `.env` if needed,
fills empty `AGENT_SERVICE_TOKEN` (and unused `VNC_PASSWORD`), runs
`uv sync --frozen`, installs frontend deps when Vite is missing,
builds `cua-ubuntu:latest` only if the image is absent, then starts
`dev.py --open-browser`. `dev.py` runs
`docker compose up -d --wait --wait-timeout 90` (healthy after
`9222/health` **and** `6080/vnc.html`), waits for `GET /api/health`,
starts Vite on `127.0.0.1:8505`, and opens that URL once Vite
responds. x11vnc starts with `-nopw`; `VNC_PASSWORD` is not used for
noVNC.

`START.bat` always runs `setup.bat --bootstrap-only` first, then
launches.

If Docker asks for a reboot or WSL setup, finish that and run
`run.cmd` again.

### Manual (Windows, Linux, macOS)

```powershell
git clone https://github.com/pypi-ahmad/computer-use.git
cd computer-use
Copy-Item .env.example .env   # then set AGENT_SERVICE_TOKEN
uv sync --frozen
npm --prefix frontend ci
docker compose build          # first time, or after Dockerfile changes
uv run python dev.py --open-browser
```

On Linux/macOS:

```bash
cp .env.example .env
# set AGENT_SERVICE_TOKEN
bash setup.sh                 # optional bootstrap
bash dev.sh
```

Daily start after setup: `uv run python dev.py --open-browser`.
Backend only: `uv run python -m backend.main` (Uvicorn on `HOST`/`PORT`,
default `127.0.0.1:8100`).

### URLs

| URL | What listens |
|---|---|
| http://127.0.0.1:8505 | Vite dashboard (dev). Proxies `/api`, `/api/v2/ws`, and `/vnc` to the backend. |
| http://127.0.0.1:8100 | FastAPI. Production UI is `frontend/dist` when that build exists. |
| http://127.0.0.1:9222/health | In-container agent service (loopback only) |
| http://127.0.0.1:6080 | noVNC inside the container (prefer the dashboard `/vnc` proxy) |

The backend snapshots user/process `GOOGLE_API_KEY` (then
`GEMINI_API_KEY`) **before** loading repository-root `.env` (next to
`docker-compose.yml`, not `backend/.env`) with
`load_dotenv(..., override=False)`. That user-env key wins over a
`.env` assignment (`backend/infra/config.py`).

A non-loopback `HOST` requires both `CUA_ALLOW_PUBLIC_BIND=1` and
`CUA_API_TOKEN`. Otherwise `backend/main.py` exits with code 2.
Production SPA at `:8100` also serves `/`, `/audit`, `/cost`,
`/workflows`, `/providers`, `/analytics` (`_SPA_ROUTES`).

## Environment variables

Copy `.env.example` to `.env`. Never commit the populated file.

### Required for the sandbox

| Variable | Default | Role |
|---|---|---|
| `AGENT_SERVICE_TOKEN` | generated by `run.cmd` if empty | Shared secret between the backend and `docker/agent_service.py` |

If the token in `.env` does not match the container, screenshots return
`401` and the viewport stays blank. Restart the backend after fixing
it. Confirm with
`docker exec cua-environment printenv AGENT_SERVICE_TOKEN`.

### Provider credentials (at least one route)

| Variable | Role |
|---|---|
| `OPENAI_API_KEY` | `openai-direct` |
| `ANTHROPIC_API_KEY` | `anthropic-direct` |
| `GOOGLE_API_KEY` or `GEMINI_API_KEY` | `gemini-direct`. User/process `GOOGLE_API_KEY` is captured before `.env` and wins. `GEMINI_API_KEY` is the alias. |

You can also create an ephemeral credential session in the Providers
tab instead of putting keys in `.env`. Google OAuth needs
`GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` or
`GOOGLE_OAUTH_CLIENT_SECRET_FILE` (optional `GOOGLE_CLOUD_PROJECT`,
`CUA_GOOGLE_OAUTH_REDIRECT_URI`).

### Networking and workbench auth

| Variable | Default | Role |
|---|---|---|
| `HOST` | `127.0.0.1` | Backend bind |
| `PORT` | `8100` | Backend port |
| `CUA_ALLOW_PUBLIC_BIND` | unset | Required for non-loopback `HOST` |
| `CUA_API_TOKEN` | unset | Optional shared secret for `/api/*` (except the Google OAuth callback), `/ws`, `/api/v2/ws/*`, and `/vnc/websockify`. HTTP: `X-CUA-Token` or `?token=`. Default-open on loopback when unset. |
| `CUA_WS_TOKEN` | unset | Deprecated fallback for `CUA_API_TOKEN` |
| `CORS_ORIGINS` | 8505 / 8100 / 5173 / 3000 on localhost and 127.0.0.1 | Comma-separated Origin allowlist |

### Sandbox and agent

| Variable | Default | Role |
|---|---|---|
| `CONTAINER_NAME` | `cua-environment` | Docker container name |
| `AGENT_SERVICE_HOST` / `AGENT_SERVICE_PORT` | `127.0.0.1` / `9222` | Action service |
| `SCREEN_WIDTH` / `SCREEN_HEIGHT` | `1440` / `900` | Virtual display (restart backend after change) |
| `MAX_STEPS` | `50` | Default step budget for v1/v2 APIs (hard cap 200). Live **Start run** always sends `maxSteps: 50`. |
| `STEP_TIMEOUT` | `30.0` | Seconds before one action is treated as hung |
| `VNC_PASSWORD` | unused | Ignored. x11vnc starts with `-nopw`; Compose does not pass this in. |
| `CUA_ENABLE_LEGACY_ACTIONS` | `0` | Re-enables shell/clipboard/window-management actions in the sandbox. Do not enable off loopback. |
| `CUA_ALLOWED_NAV_HOSTS` | unset | Optional comma-separated host allowlist for navigation |
| `CUA_MCP_FETCH_CMD` | `uvx mcp-server-fetch` | Fetch MCP used when Live **Provider web search planning** is on |

### Persistence and logging

| Variable | Default | Role |
|---|---|---|
| `CUA_V2_DB_PATH` | `data/computer-use-v2.sqlite3` | SQLite WAL store |
| `CUA_V2_FRAME_PATH` | `data/audit-frames` | Audit screenshots |
| `CUA_FRONTEND_DIST` | `frontend/dist` | Optional path to the production UI bundle |
| `LOG_FORMAT` | `console` | Set `json` for one JSON object per line |
| `LOG_LEVEL` | `INFO` | Python log level |
| `DEBUG` | `0` | Debug verbosity (`1` / `true` / `yes`) |
| `CUA_RELOAD` | unset | Uvicorn `--reload` (explicit; `DEBUG` does not turn this on) |

Frontend Vite (dev server only): `VITE_API_PORT` (default `8100`),
`VITE_WS_TOKEN`, `VITE_PORT` (default `8505`).

The full template is [.env.example](.env.example). Operator notes:
[USAGE.md](USAGE.md#configuration-reference). What is stored where:
[DATA.md](DATA.md).

## Usage

### Dashboard

1. Start the stack (`run.cmd` or `dev.py --open-browser`).
2. Open `http://127.0.0.1:8505`.
3. **Providers** — create a credential session (API key or Google
   OAuth) if you did not set provider keys in the environment or
   `.env`.
4. **Live session** — Mission control is in the left CONTROL sidebar.
   Defaults: model `gemini-3.7-flash`, route `gemini-direct`, fallback
   `gemini-3.5-flash-lite@gemini-direct`. Optional reasoning (when the
   catalog lists efforts), safety policy, **Provider web search
   planning (MCP fetch)** (`uvx mcp-server-fetch`), optional reference
   files on non-Gemini models, then **Start run** (`maxSteps: 50`).
   The main pane is the noVNC viewport (`GET /api/v2/desktop`, no VNC
   password).
5. Approve or deny amber **Approval required** banners when the policy
   and provider ask (`POST /api/v2/sessions/{id}/safety-decisions`).
   Unanswered prompts auto-deny after 60 seconds.
6. **Stop run** patches the session to `STOPPING`. Sidebar **Stop
   app** posts `POST /api/v2/system/shutdown` (active sessions, Docker
   sandbox, then SIGINT on the backend). `Ctrl+C` in the launcher
   stops Vite, the backend, and `docker compose down`.

| Path | Tab | Role |
|---|---|---|
| `/` | Live session | Mission control (sidebar), noVNC viewport, pipeline stages |
| `/audit` | Audit trail | SQLite action journal, events, ZIP export |
| `/cost` | Session cost | Current-session `EXECUTION` tokens × list rates in `frontend/src/pricing.ts` |
| `/workflows` | Workflow library | Named step lists; compile into a Live task |
| `/providers` | Providers | Route readiness and ephemeral credentials |
| `/analytics` | Analytics | `sampleCount`, input/output tokens, `totalDurationMs`, diagnostics, retention prune |

### Session cost rates

The **Session cost** tab estimates USD as
`tokens / 1,000,000 × list rate`. Batch, cache, and Terra long-context
doubling are **not** applied. Totals appear after the session writes an
`EXECUTION` metric. Not a provider invoice.

| Model | Input / 1M | Output / 1M |
|---|---:|---:|
| Sonnet 5 (`claude-sonnet-5`) | $2.00 | $10.00 |
| Gemini Flash 3.7 (`gemini-3.7-flash`) | $0.75 | $3.75 |
| Gemini 3.5 Flash Lite (`gemini-3.5-flash-lite`) | $0.30 | $2.50 |
| GPT 5.6 Luna (`gpt-5.6-luna`) | $0.20 | $1.20 |
| GPT 5.6 Terra (`gpt-5.6-terra`) | $2.00 | $12.00 |

### Commands

| Command | Purpose |
|---|---|
| `run.cmd` | Windows: setup if needed, then launch |
| `START.bat` | Always bootstrap via `setup.bat`, then launch |
| `uv run python dev.py --open-browser` | Sandbox + backend + Vite; open `http://127.0.0.1:8505` once Vite responds |
| `uv run python -m backend.main` | Backend only (Uvicorn on `HOST`/`PORT`) |
| `uv run pytest` | Offline backend tests (`not integration`) |
| `uv run pytest -o addopts='' evals/` | Offline evals |
| `uv run ruff check .` / `uv run mypy` | Lint / type-check Python |
| `npm --prefix frontend run test:run` | Frontend unit tests |
| `npm --prefix frontend run build` | Production bundle at `frontend/dist` |
| `uv run python scripts/build_handbook_site.py` | Rebuild `docs/zero-to-hero-study-handbook.html` |
| `uv run python scripts/build_release.py` | Release zip and checksums |

CI-equivalent checks: [TESTING.md](TESTING.md). Operator detail:
[USAGE.md](USAGE.md).

## Examples

### Local smoke task

```text
Open the file manager. Stop when the file manager window is visible.
```

### Start a session over HTTP

Dev UI calls `/api/v2`. Shape from `frontend/src/api.ts`:

```http
POST /api/v2/sessions
Content-Type: application/json

{
  "task": "Open the file manager. Stop when the file manager window is visible.",
  "model": "gemini-3.7-flash",
  "primaryRoute": "gemini-direct",
  "fallbackRoutes": [{ "model": "gemini-3.5-flash-lite", "route": "gemini-direct" }],
  "maxSteps": 50,
  "safetyPolicy": "provider_default",
  "useBuiltinSearch": false,
  "attachedFiles": [],
  "retainAuditFrames": true
}
```

If `CUA_API_TOKEN` is set, send `X-CUA-Token` (or `?token=`).

### Production-style single process

```powershell
npm --prefix frontend run build
docker compose up -d --wait --wait-timeout 90
uv run python -m backend.main
```

Then open [http://127.0.0.1:8100](http://127.0.0.1:8100). See
[docs/deployment.md](docs/deployment.md).

## How it works

```text
Browser (127.0.0.1:8505)
    │  REST /api/v2/*
    │  WS   /api/v2/ws/desktop  or  /api/v2/ws/{session_id}
    │  HTTP+WS /vnc/*  (noVNC assets + websockify)
    ▼
FastAPI (127.0.0.1:8100)     backend/server, backend/v2
    │  orchestrates AgentLoop, safety, SQLite, CUAF frames
    ▼
Provider SDK                 backend/engine, backend/providers
    OpenAI Responses  |  Anthropic Messages  |  Gemini Interactions
    │  Computer Use actions
    ▼
DesktopExecutor              backend/executor.py
    │  HTTP + AGENT_SERVICE_TOKEN
    ▼
cua-environment              docker/agent_service.py
    Xvfb :99 1440×900 → XFCE → x11vnc :5900 → websockify :6080
```

1. `dev.py` runs `docker compose up -d --wait --wait-timeout 90` so
   agent `/health` and noVNC `vnc.html` are up before Vite starts.
2. The Live tab loads `/api/v2/desktop` (viewer URL with
   `path=vnc/websockify`, no `password=`) and `waitForNovnc()` holds
   the iframe until `/vnc/vnc.html` returns 200.
   `desktopViewerSrc()` strips leftover `password`/`token` query
   params.
3. If **Provider web search planning** is on,
   `maybe_plan_with_web_search()` fetches up to 3 public URLs via MCP
   fetch, then the Computer Use loop runs computer-only with that
   brief.
4. Starting a session stores the run in SQLite, calls the selected
   engine, and maps official desktop actions (`click`, `type`,
   `hotkey`, …) onto the sandbox. Google sessions use user-env
   `GOOGLE_API_KEY` via `resolve_api_key("google")`.
5. Fallback routes run only if the primary route fails
   (`max_attempts=1` per route; circuit opens after 3 failures for
   30 s). Live defaults fallback to Gemini 3.5 Flash-Lite.
6. Preview frames on the session WebSocket use the CUAF binary
   protocol (`frontend/src/protocol.ts`). The interactive desktop is
   noVNC, not those preview frames.

Architecture notes: [TECHNICAL.md](TECHNICAL.md),
[docs/codebase/ARCHITECTURE.md](docs/codebase/ARCHITECTURE.md).

## Configuration options

Safety policies on Live session / `POST /api/v2/sessions`:

| `safetyPolicy` | Behavior |
|---|---|
| `provider_default` | Provider’s own confirmation rules |
| `confirm_mutating` | Extra operator confirm for mutating actions |
| `read_only` | Reject mutating actions |

Optional `useBuiltinSearch` runs an MCP-fetch planning pass
(`backend/providers/planner.py`, `uvx mcp-server-fetch` unless
`CUA_MCP_FETCH_CMD` is set) before the computer-only loop. It does
not attach OpenAI `web_search`, Anthropic `web_search_20260209`, or
Gemini `google_search` to Computer Use turns. Private/localhost URLs
are skipped. Host needs `uvx` on `PATH`.

Gemini **File Search** cannot be combined with Computer Use; attaching
files with a Gemini model fails at session start. The Live tab hides
the file input when the selected family is `GEMINI`.

Non-Gemini routes accept `.md`, `.txt`, `.pdf`, `.docx` uploads as
reference files. You own those files and any data they contain.

## Documentation

### Operator

| Document | Contents |
|---|---|
| [USAGE.md](USAGE.md) | Tabs, credentials, REST, troubleshooting |
| [TESTING.md](TESTING.md) | Manual smoke test and CI-equivalent commands |
| [DATA.md](DATA.md) | Local storage and operator responsibility |
| [docs/deployment.md](docs/deployment.md) | Local and single-process production start |
| [docs/computer-use-prompt-guide.md](docs/computer-use-prompt-guide.md) | How to write bounded Computer Use tasks |
| [docs/business-guide.md](docs/business-guide.md) | Pilot, risk, and go/no-go for an org evaluation |
| [docs/zero-to-hero-study-handbook.md](docs/zero-to-hero-study-handbook.md) | Study handbook (markdown source) |
| [docs/zero-to-hero-study-handbook.html](docs/zero-to-hero-study-handbook.html) | Study handbook (standalone HTML) |
| [docs/zero-to-hero-study-handbook.pdf](docs/zero-to-hero-study-handbook.pdf) | Study handbook (PDF; may lag the HTML build) |

### Project and community

| Document | Contents |
|---|---|
| [OPEN_SOURCE.md](OPEN_SOURCE.md) | Clone, local use, own keys; no hosted service |
| [SUPPORT.md](SUPPORT.md) | How to get help; what this repo will not debug |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Branch, test, and PR rules |
| [SECURITY.md](SECURITY.md) | Security model and vulnerability reports |
| [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | Contributor Covenant 3.0 |
| [LICENSE](LICENSE) | MIT license text |
| [CHANGELOG.md](CHANGELOG.md) | Released and Unreleased changes |
| [AGENTS.md](AGENTS.md) | Terse-response rules for coding agents |
| [Bug template](.github/ISSUE_TEMPLATE/bug.yml) | GitHub bug report form |
| [Idea template](.github/ISSUE_TEMPLATE/feature.yml) | GitHub feature / improvement form |
| [PR template](.github/PULL_REQUEST_TEMPLATE.md) | Pull-request checklist |

### Technical

| Document | Contents |
|---|---|
| [TECHNICAL.md](TECHNICAL.md) | Runtime contracts, routes, models |
| [docs/codebase/ARCHITECTURE.md](docs/codebase/ARCHITECTURE.md) | Modular monolith and sandbox split |
| [docs/codebase/STACK.md](docs/codebase/STACK.md) | Languages, frameworks, and tools |
| [docs/codebase/STRUCTURE.md](docs/codebase/STRUCTURE.md) | Top-level repository map |
| [docs/codebase/CONVENTIONS.md](docs/codebase/CONVENTIONS.md) | Naming and code conventions |
| [docs/codebase/INTEGRATIONS.md](docs/codebase/INTEGRATIONS.md) | Providers, tokens, and external APIs |
| [docs/codebase/CONCERNS.md](docs/codebase/CONCERNS.md) | Prioritized risks |
| [docs/codebase/TESTING.md](docs/codebase/TESTING.md) | Test stack and how to run it |
| [docker/SECURITY_NOTES.md](docker/SECURITY_NOTES.md) | Sandbox / agent-service attack surface |
| [evals/README.md](evals/README.md) | Offline deterministic evals |
| [.env.example](.env.example) | Environment template (copy to `.env`; never commit `.env`) |

### History and migration

| Document | Contents |
|---|---|
| [docs/migration-v2.md](docs/migration-v2.md) | v1 → v2 contract and ops migration |
| [docs/rollback-v2.md](docs/rollback-v2.md) | v2 rollback triggers and checks |
| [docs/gemini-successor-evaluation.md](docs/gemini-successor-evaluation.md) | Checklist when a Gemini CU model is replaced |
| [docs/research-audit-2026-07-23.md](docs/research-audit-2026-07-23.md) | Historical July 2026 research note |
| [docs/release-notes-v3.1.1.md](docs/release-notes-v3.1.1.md) | v3.1.1 |
| [docs/release-notes-v3.1.0.md](docs/release-notes-v3.1.0.md) | v3.1.0 |
| [docs/release-notes-v3.0.3.md](docs/release-notes-v3.0.3.md) | v3.0.3 |
| [docs/release-notes-v3.0.2.md](docs/release-notes-v3.0.2.md) | v3.0.2 |
| [docs/release-notes-v3.0.1.md](docs/release-notes-v3.0.1.md) | v3.0.1 |
| [docs/release-notes-v3.0.0.md](docs/release-notes-v3.0.0.md) | v3.0.0 |
| [docs/release-notes-v2.0.0.md](docs/release-notes-v2.0.0.md) | v2.0.0 |

## Community

This project is open source. **Community support is welcome and
needed:**

- Test a local install ([TESTING.md](TESTING.md))
- Report bugs
- Suggest improvements and features
- Fix docs
- Send a pull request

First-time and issue-only help counts.

**We do not want or accept any financial help.** No donations,
sponsorship, bounties, paid support, or consulting. Do not offer money.

| Need | Where |
|---|---|
| How to test | [TESTING.md](TESTING.md) |
| Bug report | [Bug template](https://github.com/pypi-ahmad/computer-use/issues/new?template=bug.yml) |
| Feature or improvement | [Idea template](https://github.com/pypi-ahmad/computer-use/issues/new?template=feature.yml) |
| All issues | [GitHub Issues](https://github.com/pypi-ahmad/computer-use/issues/new/choose) |
| How we work together | [SUPPORT.md](SUPPORT.md) |
| Send a patch | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Security | [SECURITY.md](SECURITY.md) — do not file public issues with tokens or exploits |
| Conduct | [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) |

There is no automated end-to-end UI plus live-provider suite. CI
(`.github/workflows/ci.yml`) runs Ruff, format, mypy, `pip-audit`,
pytest on Python 3.12–3.14 with `--cov-fail-under=60`, evals, frontend
lint/typecheck/tests/build, `npm audit --audit-level=high`, sandbox
image build, and a blocking HIGH/CRITICAL Trivy scan
(`ignore-unfixed: true`). Live provider tests are opt-in
(`pytest -m integration`); missing credentials are disclosed, never
treated as a pass.

## License

[MIT](LICENSE). Copyright (c) 2026 Ahmad.

You may clone, use, modify, and contribute — contributions are always
welcome. Run the workbench on your own machine with your own API keys.
The software is provided as-is. **All data you use in the app —
including PDFs and other files — is your responsibility only.** See
[DATA.md](DATA.md).

## Acknowledgements

Computer Use protocols and model APIs belong to OpenAI, Anthropic, and
Google. This workbench is an independent local operator surface, not
an official product of those vendors.

<p align="center">Made with ❤️ by Ahmad Mujtaba</p>
