# Computer Use Workbench

A local, single-user workbench for provider-native Computer Use agents.

The latest tag is **v3.1.1**. This README describes the current tree
(including Unreleased work in `CHANGELOG.md`). You run a FastAPI backend, a
React dashboard, and an isolated Ubuntu/XFCE Docker sandbox on your own
machine. A model from OpenAI, Anthropic, or Google drives that desktop
through each vendor’s Computer Use protocol.

> Computer Use can execute destructive actions. Use test accounts and non-sensitive data. This project is not a multi-tenant service and does not make model actions safe by itself.

## Open source

This repository is **MIT-licensed** ([LICENSE](LICENSE)). Clones, forks,
local use, and contributions are **always welcome**.

- Run it **on your own machine**. There is no hosted service.
- Use **your own provider API keys** (`GOOGLE_API_KEY` then
  `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`) or a
  Providers-tab credential session. A process-level `GOOGLE_API_KEY` is
  not overwritten by `.env` (`load_dotenv(..., override=False)`). Keys
  stay process-local and are never written to SQLite.
- **You are solely responsible** for every file and payload you put into
  the app: PDFs, `.txt`, `.md`, `.docx` uploads, sandbox desktop files,
  browser sessions, screenshots, and task text. See [DATA.md](DATA.md).
- Contributions are always welcome: tests, bug reports, ideas, docs, and
  PRs. See [SUPPORT.md](SUPPORT.md) and [CONTRIBUTING.md](CONTRIBUTING.md).
  **No donations or paid support** — time and reports only.

[Latest release](https://github.com/pypi-ahmad/computer-use/releases/latest)
· [MIT License](LICENSE)
· Python 3.12–3.14
· [CI](https://github.com/pypi-ahmad/computer-use/actions/workflows/ci.yml)

## Features

- **Three direct Computer Use routes** (catalog:
  `backend/models/computer_use_models.v2.json`)
  - `gemini-direct` — Gemini 3.7 Flash (dashboard `preferredRoute` and
    first catalog entry) or Gemini 3.5 Flash-Lite via Google Interactions
  - `anthropic-direct` — Claude Sonnet 5 via Anthropic Messages (`computer_20251124`)
  - `openai-direct` — GPT-5.6 Luna (default OpenAI model on this route) or
    GPT-5.6 Terra via the OpenAI Responses API
- **Six-tab React dashboard** — Live session, Audit trail, Session cost,
  Workflow library, Providers, Analytics (`frontend/src/App.tsx`)
- **Live sandbox viewport** — noVNC iframe of XFCE as soon as the
  container is ready (`/vnc/vnc.html?path=vnc/websockify`); you do not
  start a run to see the screen
- **Session event socket** — `/api/v2/ws/desktop` while idle, then
  `/api/v2/ws/{session_id}` after **Start run**. Carries pipeline,
  safety, and terminal events. CUAF preview frames are decoded but
  **not** shown; the picture is noVNC
- **Session cost** — list-rate USD from recorded `EXECUTION` tokens
  (`frontend/src/pricing.ts`)
- **Typed `/api/v2` contract** — sessions, safety decisions, workflows,
  analytics, export, retention, diagnostics, shutdown
- **Deterministic route fallback** — primary route plus one optional
  `model@route` pair (Live select)
- **Safety policies** — `provider_default`, `confirm_mutating`, `read_only`;
  Approve/Deny on `POST /api/v2/sessions/{id}/safety-decisions`
- **Process-local credentials** — API keys and Google OAuth tokens stay
  in memory and expire within eight hours; they are not written to SQLite
- **SQLite WAL audit store** — sessions, actions, events, metrics,
  workflow versions (`data/computer-use-v2.sqlite3`)
- **Audit frame store** — screenshots under `data/audit-frames` with
  7-day or 1 GiB eviction
- **Declarative workflows** — named step lists; **Use in live session**
  calls `POST /api/v2/workflows/{id}/compile`
- **Windows one-file launcher** — `run.cmd` installs missing host tools,
  then starts the stack

Live catalog: `backend/models/allowed_models.json` and
`backend/models/computer_use_models.v2.json`. The July 2026 research
note is historical: [docs/research-audit-2026-07-23.md](docs/research-audit-2026-07-23.md).

## Demo

After `run.cmd` (or `dev.py --open-browser`), open [http://127.0.0.1:8505](http://127.0.0.1:8505). The Live session viewport embeds the sandbox desktop.

![Sandbox XFCE desktop](assets/screenshot.png)

*Isolated XFCE desktop in the Live noVNC viewport. The screenshot is from an earlier UI revision; the current operator surface is the six-tab dashboard below.*

A local smoke task (no web search, no attachments):

> Open the file manager. Stop when the file manager window is visible.

If the file manager appears and the session badge reaches `COMPLETED`, the install works. Full steps: [TESTING.md](TESTING.md).

## Tech stack

| Layer | What this repo uses |
|---|---|
| Backend | Python 3.12–3.14, FastAPI, Uvicorn, Pydantic, httpx, Pillow |
| Providers | `openai`, `anthropic`, `google-genai`, `google-auth` |
| Frontend | React 19, React Router 7, Vite 6, TypeScript, Lucide |
| Sandbox | Docker Compose, `cua-ubuntu:latest`, Xvfb, XFCE, x11vnc, noVNC, `docker/agent_service.py` |
| Persistence | SQLite WAL + on-disk audit frames |
| Tooling | uv (`uv.lock`), Ruff, mypy, pytest, Vitest, GitHub Actions |

Package name in `pyproject.toml`: `computer-use-workbench`.

## Project structure

```text
computer-use/
├── backend/                  # FastAPI app
│   ├── main.py               # Uvicorn entry; public-bind guardrail
│   ├── server/               # HTTP/WS, noVNC proxy, app lifespan
│   ├── v2/                   # /api/v2 REST, credentials, SQLite, frames
│   ├── engine/               # OpenAI / Anthropic / Gemini Computer Use clients
│   ├── providers/            # Provider-neutral run adapters
│   ├── executor.py           # Desktop actions → agent_service
│   ├── loop.py               # Session / step orchestration
│   ├── infra/                # Config, Docker lifecycle, logging
│   └── models/               # allowed_models.json, v2 catalog, schemas
├── frontend/                 # React + Vite dashboard
│   └── src/App.tsx           # Six tabs; pricing.ts holds list rates
├── docker/                   # Sandbox image, entrypoint, agent_service
├── docker-compose.yml        # cua-environment (5900, 6080, 9222)
├── tests/                    # Offline pytest (excludes integration by default)
├── evals/                    # Offline HTTP/runtime evals
├── scripts/                  # Handbook site, release zip, changelog watchdog
├── docs/                     # Operator, architecture, and release notes
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
- [uv](https://docs.astral.sh/uv/) and Python 3.12 (3.13 and 3.14 are also tested in CI)
- Windows 11 for `run.cmd` / `START.bat`; Linux/macOS use `setup.sh` and `dev.sh`

### Windows (recommended)

```powershell
.\run.cmd
```

`run.cmd` installs missing host tools (uv, Python 3.12, Node.js LTS, Docker Desktop) via winget, copies `.env.example` to `.env` if needed, fills empty `AGENT_SERVICE_TOKEN` and `VNC_PASSWORD`, runs `uv sync --frozen`, installs frontend deps when Vite is missing, builds `cua-ubuntu:latest` only if the image is absent, then starts `dev.py --open-browser`. `dev.py` runs `docker compose up -d --wait --wait-timeout 90` (healthy after `9222/health` **and** `6080/vnc.html`), waits for `GET /api/health`, starts Vite on `127.0.0.1:8505`, and opens that URL once Vite responds.

`START.bat` always runs `setup.bat --bootstrap-only` first, then launches.

If Docker asks for a reboot or WSL setup, finish that and run `run.cmd` again.

### Manual (Windows, Linux, macOS)

```powershell
git clone https://github.com/pypi-ahmad/computer-use.git
cd computer-use
Copy-Item .env.example .env   # then set AGENT_SERVICE_TOKEN and VNC_PASSWORD
uv sync --frozen
npm --prefix frontend ci
docker compose build          # first time, or after Dockerfile changes
uv run python dev.py --open-browser
```

On Linux/macOS:

```bash
cp .env.example .env
# set AGENT_SERVICE_TOKEN and VNC_PASSWORD
bash setup.sh                 # optional bootstrap
bash dev.sh
```

### URLs

| URL | What listens |
|---|---|
| http://127.0.0.1:8505 | Vite dashboard (dev). Proxies `/api`, `/api/v2/ws`, and `/vnc` to the backend. |
| http://127.0.0.1:8100 | FastAPI. Production UI is `frontend/dist` when that build exists. |
| http://127.0.0.1:9222/health | In-container agent service (loopback only) |
| http://127.0.0.1:6080 | noVNC inside the container (prefer the dashboard `/vnc` proxy) |

The backend loads **repository-root** `.env` (next to `docker-compose.yml`), not `backend/.env`, with `load_dotenv(..., override=False)`.

A non-loopback `HOST` requires both `CUA_ALLOW_PUBLIC_BIND=1` and `CUA_API_TOKEN`. Otherwise `backend/main.py` exits with code 2. Production SPA at `:8100` also serves `/`, `/audit`, `/cost`, `/workflows`, `/providers`, `/analytics` (`_SPA_ROUTES`).

## Environment variables

Copy `.env.example` to `.env`. Never commit the populated file.

### Required for the sandbox

| Variable | Default | Role |
|---|---|---|
| `AGENT_SERVICE_TOKEN` | generated by `run.cmd` if empty | Shared secret between the backend and `docker/agent_service.py` |
| `VNC_PASSWORD` | generated by `run.cmd` if empty | x11vnc password; also passed into the noVNC viewer URL |

If the token in `.env` does not match the container, screenshots return `401` and the viewport stays blank. Restart the backend after fixing it. Confirm with `docker exec cua-environment printenv AGENT_SERVICE_TOKEN`.

### Provider credentials (at least one route)

| Variable | Role |
|---|---|
| `OPENAI_API_KEY` | `openai-direct` |
| `ANTHROPIC_API_KEY` | `anthropic-direct` |
| `GOOGLE_API_KEY` or `GEMINI_API_KEY` | `gemini-direct`. Prefer process-level `GOOGLE_API_KEY`; `.env` does not override it. `GEMINI_API_KEY` is the alias. |

You can also create an ephemeral credential session in the Providers tab instead of putting keys in `.env`. Google OAuth needs `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` or `GOOGLE_OAUTH_CLIENT_SECRET_FILE` (optional `GOOGLE_CLOUD_PROJECT`, `CUA_GOOGLE_OAUTH_REDIRECT_URI`).

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
| `CUA_ENABLE_LEGACY_ACTIONS` | `0` | Re-enables shell/clipboard/window-management actions in the sandbox. Do not enable off loopback. |
| `CUA_ALLOWED_NAV_HOSTS` | unset | Optional comma-separated host allowlist for navigation |

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

Frontend Vite (dev server only): `VITE_API_PORT` (default `8100`), `VITE_WS_TOKEN`, `VITE_PORT` (default `8505`).

The full template is [.env.example](.env.example). Operator notes: [USAGE.md](USAGE.md#configuration-reference). What is stored where: [DATA.md](DATA.md).

## Usage

### Dashboard

1. Start the stack (`run.cmd` or `dev.py --open-browser`).
2. Open `http://127.0.0.1:8505`.
3. **Providers** — create a credential session (API key or Google OAuth) if you did not set provider keys in `.env`.
4. **Live session** — choose model and primary route (defaults:
   `gemini-3.7-flash` / `gemini-direct`), optional fallback
   (`model@route`), reasoning (when the catalog lists efforts), safety
   policy, optional provider web-search planning, optional reference
   files on non-Gemini models, then **Start run** (`maxSteps: 50`).
5. Approve or deny amber **Approval required** banners when the policy
   and provider ask (`POST /api/v2/sessions/{id}/safety-decisions`).
   Unanswered prompts auto-deny after 60 seconds.
6. **Stop run** patches the session to `STOPPING`. Sidebar **Stop app**
   posts `POST /api/v2/system/shutdown` (active sessions, Docker
   sandbox, then SIGINT on the backend). `Ctrl+C` in the launcher stops
   Vite, the backend, and `docker compose down`.

Tabs:

| Path | Tab | Role |
|---|---|---|
| `/` | Live session | Task, routing, noVNC viewport, pipeline stages |
| `/audit` | Audit trail | SQLite action journal, events, ZIP export |
| `/cost` | Session cost | `EXECUTION` tokens × list rates in `frontend/src/pricing.ts` |
| `/workflows` | Workflow library | Named step lists; compile into a Live task |
| `/providers` | Providers | Route readiness and ephemeral credentials |
| `/analytics` | Analytics | `sampleCount`, input/output tokens, `totalDurationMs`, diagnostics, retention prune |

### Example: start a session over HTTP

Dev UI calls `/api/v2`. Example shape from `frontend/src/api.ts`:

```http
POST /api/v2/sessions
Content-Type: application/json

{
  "task": "Open the file manager. Stop when the file manager window is visible.",
  "model": "gemini-3.7-flash",
  "primaryRoute": "gemini-direct",
  "fallbackRoutes": [],
  "maxSteps": 50,
  "safetyPolicy": "provider_default",
  "useBuiltinSearch": false,
  "attachedFiles": [],
  "retainAuditFrames": true
}
```

If `CUA_API_TOKEN` is set, send `X-CUA-Token` (or `?token=`).

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

CI-equivalent checks: [TESTING.md](TESTING.md). Operator detail: [USAGE.md](USAGE.md).

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

1. `dev.py` runs `docker compose up -d --wait --wait-timeout 90` so agent `/health` and noVNC `vnc.html` are up before Vite starts.
2. The Live tab loads `/api/v2/desktop` (viewer URL with `path=vnc/websockify` and `VNC_PASSWORD`) and `waitForNovnc()` holds the iframe until `/vnc/vnc.html` returns 200.
3. Starting a session stores the run in SQLite, calls the selected engine, and maps official desktop actions (`click`, `type`, `hotkey`, …) onto the sandbox.
4. Fallback routes run only if the primary route fails and you configured them (`max_attempts=1` per route; circuit opens after 3 failures for 30 s).
5. Preview frames on the session WebSocket use the CUAF binary protocol (`frontend/src/protocol.ts`). The interactive desktop is noVNC, not those preview frames.

Architecture notes: [TECHNICAL.md](TECHNICAL.md), [docs/codebase/ARCHITECTURE.md](docs/codebase/ARCHITECTURE.md).

## Configuration options

Safety policies on Live session / `POST /api/v2/sessions`:

| `safetyPolicy` | Behavior |
|---|---|
| `provider_default` | Provider’s own confirmation rules |
| `confirm_mutating` | Extra operator confirm for mutating actions |
| `read_only` | Reject mutating actions |

Optional `useBuiltinSearch` runs a provider-native planning/search pass
before the computer-only loop (OpenAI `web_search`, Anthropic
`web_search_20260209` for `claude-sonnet-5`, Gemini `google_search`).
Gemini **File Search** cannot be combined with Computer Use; attaching
files with a Gemini model fails at session start. The Live tab hides the
file input when the selected family is `GEMINI`.

Non-Gemini routes accept `.md`, `.txt`, `.pdf`, `.docx` uploads as reference files.

Production-style single-process serving: build the frontend, then run the backend so it can serve `frontend/dist`. See [docs/deployment.md](docs/deployment.md).

## Documentation

| Document | Contents |
|---|---|
| [USAGE.md](USAGE.md) | Operator guide: tabs, credentials, REST, troubleshooting |
| [TESTING.md](TESTING.md) | Manual smoke test and CI-equivalent commands |
| [DATA.md](DATA.md) | What is stored locally and operator responsibility |
| [SUPPORT.md](SUPPORT.md) | How to get help; what this repo will not debug |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Branch, test, and PR rules |
| [SECURITY.md](SECURITY.md) | Workbench security model and vulnerability reports |
| [CHANGELOG.md](CHANGELOG.md) | Released and Unreleased changes |
| [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | Contributor Covenant 3.0 |
| [TECHNICAL.md](TECHNICAL.md) | Runtime contracts |
| [docs/migration-v2.md](docs/migration-v2.md) / [docs/rollback-v2.md](docs/rollback-v2.md) | v2 migration and rollback |
| [docs/release-notes-v3.1.1.md](docs/release-notes-v3.1.1.md) | v3.1.1 release notes |
| [docs/zero-to-hero-study-handbook.html](docs/zero-to-hero-study-handbook.html) | Guided handbook |

## Community

Contributions are always welcome. Run the app locally, file what you
find, suggest improvements, or send a PR. First-time and issue-only
help counts. This project does **not** take donations, sponsorship, or
paid support.

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

There is no automated end-to-end UI plus live-provider suite. CI (`.github/workflows/ci.yml`) runs Ruff, format, mypy, pytest on Python 3.12–3.14, evals, frontend lint/typecheck/tests/build, `pip-audit`, `npm audit`, sandbox image build, and a blocking HIGH/CRITICAL image scan. Live provider tests are opt-in; missing credentials are disclosed, never treated as a pass.

## License

[MIT](LICENSE). Copyright (c) 2026 Ahmad.

You may clone, use, modify, and contribute — contributions are always
welcome. Run the workbench on your
own machine with your own API keys. The software is provided as-is.
**All data you use in the app — including PDFs and other files — is
your responsibility only.** See [DATA.md](DATA.md).

<p align="center">Made with ❤️ by Ahmad Mujtaba</p>
