# USAGE

Operator guide for `computer-use` — written so both a non-technical
reader (what is this, why would I use it) and a technical operator
(exact commands, exact env vars, exact API shapes) can get what they
need from the same document. Everything here is grounded in the current
code on this branch; no behavior is described that isn't actually
implemented, and any feature gap is called out explicitly rather than
glossed over.

## What This App Does (read this first if you're new)

In plain language: this app lets an AI model **operate a real desktop
computer for you** — open apps, click buttons, type text, fill in forms,
browse the web — the same way a person would, by looking at the screen
and deciding what to do next, one action at a time. You give it a task
in plain English ("open the file manager and confirm it's visible"),
it looks at a screenshot, decides on one action, and repeats until it
either finishes, gets stuck, or hits a step limit you control.

The "desktop" it operates is not your real computer — it's an isolated,
disposable virtual desktop running inside Docker, specifically so that
whatever the model does stays contained. You watch it work in real time
through a browser dashboard.

**Who this is for:** anyone who wants to prototype or evaluate
AI-driven desktop automation locally — no cloud account, no
multi-user setup, single operator on a single machine.

**What it is not:** a production, multi-tenant, internet-facing service.
There is no user/account system. An optional shared `CUA_API_TOKEN` protects
sensitive REST operations, WebSockets, and noVNC, but it does not turn the
workbench into a multi-user service. Keep it on `127.0.0.1` unless you've read
[Network hardening](#networking) and deliberately opted in to exposing it.

**Two ways to use it**, both fully working today:

1. **The dashboard** (`http://127.0.0.1:8505`) — a five-tab web UI. This
   is what most people mean by "using the app." It talks to the newer,
   typed `/api/v2` backend surface. Use `127.0.0.1`, not `localhost`: the
   Vite dev server binds IPv4 loopback only.
2. **Direct API calls** (curl, scripts, `wscat`) — the original REST +
   WebSocket surface, still fully implemented, with a couple of features
   (Web Search, file attachments) that the dashboard doesn't expose yet.
   See [Feature Availability](#feature-availability-dashboard-vs-rest-api).

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Installation](#installation)
3. [First Run](#first-run)
4. [Daily Operation](#daily-operation)
5. [The Dashboard — Five Tabs In Depth](#the-dashboard--five-tabs-in-depth)
6. [Provider, Model, and Routing](#provider-model-and-routing)
7. [Provider Login (API Keys and Google OAuth)](#provider-login-api-keys-and-google-oauth)
8. [Safety Confirmations](#safety-confirmations)
9. [Writing Effective Tasks](#writing-effective-tasks)
10. [Feature Availability: Dashboard vs. REST API](#feature-availability-dashboard-vs-rest-api)
11. [Scripting via REST and WebSocket](#scripting-via-rest-and-websocket)
12. [Configuration Reference](#configuration-reference)
13. [Troubleshooting](#troubleshooting)
14. [Tests and Verification](#tests-and-verification)
15. [Uninstall and Clean Reset](#uninstall-and-clean-reset)

## Prerequisites

| Requirement | Version | Why |
|---|---|---|
| Docker Desktop or Docker Engine | 24+ | Runs the `cua-environment` sandbox (the virtual desktop). |
| Python | 3.12–3.14 | Backend runtime, managed via `uv`. |
| Node.js | 22+ | Vite 6 dashboard build/dev server. |
| Provider sign-in | OpenAI, Anthropic, or Google | Use an API key, or Google OAuth, for the model that decides what to click. |
| [`uv`](https://docs.astral.sh/uv/) | latest | Python package/venv manager this project uses instead of raw `pip`. |

The app is a single-user localhost workbench. It has an optional shared-secret
gate, not identity, roles, or tenant isolation. Do not expose the backend (port
`8100`) or noVNC (port `6080`) to a network you don't trust without first
reading [Networking](#networking) below and the security sections of
`TECHNICAL.md`.

> **Workbench token transport.** When `CUA_API_TOKEN` is set, the frontend uses
> `X-CUA-Token` for HTTP requests and a URL query parameter (`?token=...`) for
> `/ws`, `/api/v2/ws/*`, and the noVNC proxy. Query strings can appear in proxy
> logs or browser history. For non-loopback exposure, use TLS, restrict network
> access, prevent query logging, and place an independently authenticated
> reverse proxy in front of the workbench.

## Installation

Clone the repository, then use the platform launcher.

```powershell
git clone https://github.com/pypi-ahmad/computer-use.git
cd computer-use
.\run.cmd
```

On Windows 11, `run.cmd` is the one-file first-run installer and daily
launcher. It uses exact winget packages to install missing Docker Desktop,
Node.js LTS, and uv; creates `.env` when absent; generates the required local
sandbox secrets without printing or replacing existing values; installs
locked Python/frontend dependencies only when they are missing; rebuilds
esbuild after a fresh `npm ci`; builds the Docker image only when
`cua-ubuntu:latest` is absent; starts `dev.py --open-browser`; waits for
`GET /api/health`; and then opens `http://127.0.0.1:8505`. `START.bat` still
exists and always runs `setup.bat --bootstrap-only` first (including a
cached `docker compose build`). On Windows, Vite is started through Node
(`node .../vite/bin/vite.js`) rather than `npm.cmd`, and it listens on
`127.0.0.1`. Normal installer/UAC prompts may appear. If Docker Desktop
requires a restart or initial WSL setup, complete it and run `run.cmd` again.

Provider credentials are not generated. Enter an OpenAI, Anthropic, or Google
API key in the Provider Manager, or configure Google OAuth.

Linux/macOS and manual Windows setup remain available:

```powershell
Copy-Item .env.example .env
# Set AGENT_SERVICE_TOKEN and VNC_PASSWORD, then:
setup.bat             # Windows manual bootstrap
```

```bash
bash setup.sh          # Linux/macOS
```

`setup.bat --bootstrap-only` prepares dependencies without launching.
`setup.bat --clean` performs an explicitly destructive, from-scratch Docker
rebuild. Normal setup uses Docker's build cache and skips `npm ci` when the
installed frontend matches `package-lock.json`. After a fresh `npm ci` it
runs `npm rebuild esbuild --foreground-scripts` so the Vite binary is ready.

For a manual day-to-day start after setup:

```powershell
.\dev.bat             # Windows — does not install missing system tools
bash dev.sh           # Linux/macOS
```

`dev.py` (invoked by either wrapper) does four things every time you run
it: frees ports `8100`/`8505` if a previous crashed run left them held;
makes sure the `cua-environment` container is running and healthy
(starting it via `docker compose up -d` if not); launches the FastAPI
backend and waits for `GET /api/health`; then starts the Vite dev server
on `127.0.0.1` (through Node on Windows, through `npm run dev` elsewhere)
and streams both logs to your terminal. With `--open-browser`, it opens
`http://127.0.0.1:8505` only after that health check succeeds. `Ctrl+C` or
the sidebar **Stop app** button stops active sessions, the backend and
frontend processes, and the Docker sandbox. The browser tab remains open
so it can show the final stopped state.

### Environment file

The settings you'll actually touch:

```dotenv
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza...
# GEMINI_API_KEY=            # accepted as an alias for GOOGLE_API_KEY

AGENT_SERVICE_TOKEN=...      # generate a unique random value
VNC_PASSWORD=...             # generate a unique random value
SCREEN_WIDTH=1440
SCREEN_HEIGHT=900
MAX_STEPS=50
```

`AGENT_SERVICE_TOKEN` and `VNC_PASSWORD` are required sandbox secrets —
generate real random values, don't leave them blank or copy an example.
The backend loads this file from the **repository root** (next to
`docker-compose.yml`), not from `backend/.env`. If the token in `.env`
does not match the value Compose passed into the container, screenshots
return `401` and the viewport stays blank.
If you intend to bind the backend to a non-loopback address, also set
`CUA_ALLOW_PUBLIC_BIND=1` and `CUA_API_TOKEN=<secret>`; without both, the
process refuses to start when `HOST != 127.0.0.1`.

## First Run

`run.cmd` (or `START.bat`) opens `http://127.0.0.1:8505` after backend
health succeeds. If the browser does not open, go there manually. The
**Live session** viewport should show the sandbox XFCE desktop within a
few seconds — you do not need to start a run first. The header reads
**Stream linked** once `/api/v2/ws/desktop` is connected. If the
viewport stays on **Connecting to sandbox** or never shows a desktop,
see [Troubleshooting](#troubleshooting).

Then type a local smoke-test task:

> Open the file manager. Stop when the file manager window is visible.

This is purely local — no web search, no files — so it's a clean smoke
test of screenshot capture, action dispatch, and the live stream end to
end. If the viewport shows the file manager opening and the session
status badge reaches `COMPLETED`, the install is working.

## Daily Operation

```powershell
.\run.cmd        # Windows: setup if needed, launch, and open the UI
```

```bash
bash dev.sh           # Linux/macOS
```

then use `http://127.0.0.1:8505`. Vite listens on IPv4 loopback and
proxies `/api` and `/api/v2/ws` to the backend, so you don't deal with
CORS during normal use.

## The Dashboard — Five Tabs In Depth

The left sidebar has five workspaces. Each is a separate page; switching
tabs doesn't lose the state of the others.

### 1. Live session

This is the one you'll use most. It's split into two halves:

- **Left (Mission control):** a task text box, a Computer Use model
  dropdown, and a primary route dropdown (which provider/transport
  actually executes the model — see
  [Provider, Model, and Routing](#provider-model-and-routing)). Below
  that, a **Start run** button, or **Stop run** once one is active.
- **Right (Viewport):** the live sandbox desktop. The dashboard opens
  `/api/v2/ws/desktop` as soon as the page loads, so you see XFCE before
  any run. After **Start run**, the client switches to
  `/api/v2/ws/{session_id}` and the same viewport shows what the model
  is seeing. Above it, a five-stage pipeline indicator (capture → encode
  → infer → validate → act) highlights whichever stage the current turn
  is in. The header reads **Stream linked** when the WebSocket is up,
  or **Stream idle** while it is connecting.

If the model raises a safety-sensitive action, an amber "Approval
required" banner appears above the viewport with the provider's
explanation text (see [Safety Confirmations](#safety-confirmations) for
what this can and can't do right now).

A small status badge next to the viewport tracks the session through its
lifecycle: `PENDING` → `RUNNING` → `COMPLETED` / `STOPPED` / `ERROR`.

### 2. Audit trail

Every session you've ever run (this process's lifetime — see the
[Configuration](#configuration-reference) note on `CUA_V2_DB_PATH`) is
listed in a dropdown at the top of this tab. Pick one to see:

- **Confirmed action journal** — every action the model actually took,
  in order, with its type and raw payload. This is a durable record
  written to disk as the session ran, not something reconstructed
  afterward.
- **Recent events** — the last few lifecycle events for that session
  (e.g. `SESSION_STARTED`, `ROUTE_ATTEMPTED`, `ROUTE_SUCCEEDED`,
  `SESSION_FAILED`), each with a timestamp.

This is the tab to use when you want to know, after the fact, exactly
what an agent did and in what order — useful for compliance review or
for debugging a run that didn't go as expected.

### 3. Workflow library

A **workflow** here is a saved, reusable, named sequence of plain-English
instructions (e.g. "Weekly access review": *Open the admin portal → Export
active accounts → Save the report*) — not a low-code visual builder, just
an ordered list of steps you write once and reuse. Existing workflows are
shown as cards with their name and version number; the form on the right
creates a new one. Each edit to a workflow's steps creates a new version
rather than overwriting the old one, so you can always see what a
previous run actually used.

*(Compiling a workflow — substituting variables like `${account_name}`
into its steps and handing the result to a Live session run — exists as
a backend endpoint, `POST /api/v2/workflows/{id}/compile`, but is not yet
wired into the dashboard UI. Today it's reachable via
[direct API calls](#scripting-via-rest-and-websocket).)*

### 4. Providers

This is where you tell the app which AI account to use, without ever
typing a permanent secret. Pick OpenAI, Anthropic, or Google and create an
ephemeral API-key session. Google also offers **Google OAuth** when the OAuth
client environment variables are configured. See
[Provider Login](#provider-login-api-keys-and-google-oauth) for exactly what
happens to that key and how long it lasts.

The same tab lists the three direct **routes** (a provider + transport +
authentication-mode combination) with a status badge showing whether each is
configured and its current circuit state (`CLOSED` = healthy, `OPEN` =
temporarily skipped after repeated failures — see
[Provider, Model, and Routing](#provider-model-and-routing)).

### 5. Analytics

Four running totals across every session this process has executed:
session count, action count, number of latency samples recorded, and
average latency per recorded stage. Below the tiles, the same numbers
appear as a raw JSON object — useful if you're eyeballing whether a
particular provider/route combination is meaningfully slower than
another over time.

## Provider, Model, and Routing

**Provider** = which company's AI you're using (OpenAI, Anthropic, or Google).
**Model** = the provider-native Computer Use model. This release exposes only
GPT-5.6 Luna, GPT-5.6 Terra, Claude Sonnet 5, Gemini 3.7 Flash, and Gemini 3.5 Flash-Lite. **Route** = the direct
technical path used to reach that provider: `openai-direct`,
`anthropic-direct`, or `gemini-direct`. Cloud-intermediary and older
preview-model routes are not catalogued or selectable.

When you start a Live session, you pick a **primary route**. If you
don't pick one, the app defaults to the first route the model exposes.
There is no automatic "pick the cheapest/fastest" behavior — routing is
always the operator's explicit choice, which is a deliberate design
decision so you always know exactly which vendor is handling your task
and your data.

If a route starts failing repeatedly, its **circuit** opens (visible on
the Providers tab as `OPEN` instead of `CLOSED`) and the app stops trying
it for a short cooldown window, so a single flaky route can't make every
subsequent run slow or hang. It recovers to `CLOSED` on its own once the
cooldown passes and a call succeeds again.

## Provider Login (API Keys and Google OAuth)

Instead of typing your API key into every request, the Providers tab
lets you create a **credential session**: paste your key once, and the
app hands you back an opaque, short-lived reference (never the key
itself) that Live session runs can use going forward.

What actually happens to the key you paste:

- It's held **only in the backend process's memory** — never written to
  disk, never included in any log line, never stored in the audit
  database.
- It automatically expires **8 hours after creation at the absolute
  latest** (shorter if you configure a smaller TTL via the API), whether
  you're actively using it or not.
- If a session's credential has expired, gone missing, or simply wasn't
  supplied, the app falls back to whatever provider API key is set in
  your `.env`/system environment for that provider — so a stale or
  expired credential session never silently uses the *wrong* key,
  it only ever falls back to your own already-configured one.
- Deleting the credential session (the trash icon next to "Credential
  session active") removes it from memory immediately, before its TTL
  would otherwise expire.

If you'd rather not use the Providers tab at all, simply configure your
key(s) in `.env` — every route will resolve credentials from there
automatically with no credential session needed.

Google also supports browser OAuth. Set `GOOGLE_OAUTH_CLIENT_ID` and either
`GOOGLE_OAUTH_CLIENT_SECRET` or `GOOGLE_OAUTH_CLIENT_SECRET_FILE`, restart the
workbench, choose Google in the Providers tab, and start OAuth login. The
browser returns to the configured `CUA_GOOGLE_OAUTH_REDIRECT_URI` after Google
authorization. OAuth state and PKCE protect the callback; access and refresh
credentials stay in the same process-local vault as API-key sessions and are
lost when the backend stops. `GOOGLE_CLOUD_PROJECT` is optional and supplies a
quota project when your Google account requires one.

## Safety Confirmations

Some actions a model can propose — submitting a form, an irreversible
delete, a payment — are risky enough that the backend pauses the run
and asks a human before executing them.

**Honest current state:** the Live session tab *displays* this pause (the
amber "Approval required" banner with the provider's explanation), so you
always know when a run is blocked and why — but the dashboard does not
yet have a button to answer it. Today, resolving a paused confirmation
and the older provider-scripting confirmation flow both go through the
[REST API directly](#scripting-via-rest-and-websocket) (`POST
/api/agent/safety-confirm` for the v1 surface). A confirmation that's
never answered auto-denies after a timeout, and the run continues from
there marked as failed for that step — it does not hang forever.

## Writing Effective Tasks

A good task prompt is concrete, constrained, and verifiable. The
Computer Use prompt guide in `docs/computer-use-prompt-guide.md` has
worked-out examples; this section gives the operating principles.

### Always include

- **Outcome.** What does success look like? "The file manager window is
  visible" not "explore the desktop."
- **Starting point.** Where should the agent begin? "Open the browser
  and go to `<url>`" instead of "research `<topic>`."
- **Constraints.** Things the agent must not do: "do not sign in," "do
  not submit forms," "do not download anything."
- **Stop condition.** A precise, observable state.
- **Final answer format.** Tell the model whether you want a sentence,
  a bullet list, or a specific fact quoted back.

### Avoid

- Hard-coded pixel coordinates in your instructions — they mean
  different things across providers (Gemini's internal grid is
  normalized 0–999) and across screen sizes.
- Passive verbs ("the page should be opened"). Use imperative.
- Tasks that need credentials the agent doesn't have — it cannot guess
  passwords or 2FA codes.

### Examples

**Local-only:**

```text
Open the calculator app. Type "2 + 2" and press Enter.
Stop when the display shows "4". Tell me the displayed result.
```

**Web research (via the v1 REST surface, which supports web search —
see the feature-availability note below):**

```text
Open the browser and go to the official OpenAI docs.
Find the Computer Use guide. Do not sign in or change any settings.
Stop when the guide page is visible.
Tell me the page title and the first section heading.
```

**Multi-step with verification:**

```text
Open VS Code. Create a new file called notes.txt on the desktop.
Type "hello world" into it and save.
Stop when you can see notes.txt as an open tab in VS Code.
Confirm you saved the file by quoting the file path.
```

## Feature Availability: Dashboard vs. REST API

The five-tab dashboard and the original REST/WebSocket surface aren't
feature-identical yet. Use this table to decide which surface a task
needs:

| Feature | Dashboard (`/api/v2`) | Direct REST API (v1, `/api/agent/*`) |
|---|---|---|
| Start/stop a Computer Use run | ✅ Live session tab | ✅ `POST /api/agent/start` / `/stop` |
| Choose provider/model/route explicitly | ✅ Live session tab | ✅ request body fields |
| Ordered fallback routes | ✅ (`fallbackRoutes`, currently API-only for customizing beyond the default) | — (no fallback concept in v1) |
| Persistent, queryable session history | ✅ Audit trail tab (SQLite-backed) | — (in-memory only; lost on restart) |
| Credential vault (paste-once API keys) | ✅ Providers tab | — (pass `api_key` per request, or rely on `.env`) |
| **Web Search planning pass** | ❌ not yet wired into the UI or `SessionInput` contract | ✅ `use_builtin_search: true` |
| **File attachments** (`.pdf`/`.txt`/`.md`/`.docx`) | ❌ not yet wired into the UI or `SessionInput` contract | ✅ `POST /api/files/upload` + `attached_files` |
| **Answering a safety confirmation** | ❌ display-only (see above) | ✅ `POST /api/agent/safety-confirm` |
| Reusable named workflows | ✅ Workflow library tab | — (no workflow concept in v1) |
| Aggregate latency/usage analytics | ✅ Analytics tab | — |

If your task needs Web Search, file attachments, or answering a safety
prompt programmatically, use the REST API directly for now — see the
next section.

## Scripting via REST and WebSocket

The full HTTP API is documented exhaustively in `TECHNICAL.md` and via
the live OpenAPI document at `/docs`. This section is the
operator-friendly walkthrough of common patterns for both surfaces.

### v1 — quick-start a session with Web Search and files

```bash
curl -X POST http://localhost:8100/api/agent/start \
  -H "Content-Type: application/json" \
  -d '{
    "task": "Open the calculator and compute 2 + 2.",
    "provider": "openai",
    "model": "gpt-5.6-luna",
    "max_steps": 30,
    "use_builtin_search": false,
    "attached_files": [],
    "engine": "computer_use",
    "execution_target": "docker"
  }'
```

The response contains a `session_id`. Poll status, or stop it:

```bash
curl http://localhost:8100/api/agent/status/<session_id>
curl -X POST http://localhost:8100/api/agent/stop/<session_id>
```

Upload a file first if you need `attached_files`:

```bash
curl -X POST http://localhost:8100/api/files/upload -F file=@./notes.pdf
```

Stream events over `/ws` (append `?token=$CUA_API_TOKEN` if that's set):

```bash
wscat -c ws://localhost:8100/ws
```

Answer a safety confirmation (must respond within the timeout window,
using the `nonce` the `safety_confirmation` event carried):

```bash
curl -X POST http://localhost:8100/api/agent/safety-confirm \
  -H "Content-Type: application/json" \
  -d '{"session_id": "<session_id>", "confirm": true, "nonce": "<nonce>"}'
```

### v2 — the dashboard's own API

```bash
# List available models and routes
curl http://localhost:8100/api/v2/models
curl http://localhost:8100/api/v2/provider-routes

# Create a credential session (returns an opaque id, never the key)
curl -X POST http://localhost:8100/api/v2/credential-sessions \
  -H "Content-Type: application/json" \
  -d '{"credentials": {"OPENAI": "sk-..."}, "ttlSeconds": 3600}'

# Start a session using that credential session
curl -X POST http://localhost:8100/api/v2/sessions \
  -H "Content-Type: application/json" \
  -d '{
    "task": "Open the calculator and compute 2 + 2.",
    "model": "gpt-5.6-luna",
    "primaryRoute": "openai-direct",
    "fallbackRoutes": [],
    "credentialSessionId": "<id from above>",
    "maxSteps": 30,
    "retainAuditFrames": true
  }'

# List/query, stop
curl http://localhost:8100/api/v2/sessions
curl -X PATCH http://localhost:8100/api/v2/sessions/<id> \
  -H "Content-Type: application/json" -d '{"status": "STOPPING"}'
```

Both `/api/v2/sessions` and `/api/v2/credential-sessions` (and every
other mutating `/api/v2/*` route) enforce the same origin/token gate as
the v1 endpoints — see [Networking](#networking).

### Forbidden fields and rate limiting

Both surfaces use `extra="forbid"` schemas — an unknown field returns a
structured `422`. The v1 rate limiter is a per-IP sliding window: 10
agent starts per minute, 3 concurrent sessions, 20 key validations per
minute. Hitting a limit returns `429`.

## Configuration Reference

All configuration is via environment variables; the complete list lives
in `.env.example`. This section calls out the ones operators usually
need.

### Networking

| Variable | Default | Notes |
|---|---|---|
| `HOST` | `127.0.0.1` | Backend bind. Anything else requires `CUA_ALLOW_PUBLIC_BIND=1` and `CUA_API_TOKEN`. |
| `PORT` | `8100` | Backend port. |
| `CUA_ALLOW_PUBLIC_BIND` | unset | Explicit opt-in for non-loopback `HOST`. |
| `CUA_API_TOKEN` | unset | Shared secret gating sensitive/mutating REST operations, `/ws`, `/api/v2/ws/*`, and `/vnc/*`. `CUA_WS_TOKEN` is a deprecated fallback. |
| `CUA_ALLOWED_HOSTS` | derived from CORS | Extra Host headers to allow. |
| `CORS_ORIGINS` | `127.0.0.1`/`localhost` on `8505`, `8100`, `5173`, and `3000` | Comma-separated allowlist. Dev UI is `http://127.0.0.1:8505`; the production bundle served by FastAPI is `http://127.0.0.1:8100`. Both origins must be allowed or the live stream gets `403`. |

### Sandbox and screen

| Variable | Default | Notes |
|---|---|---|
| `CONTAINER_NAME` | `cua-environment` | Docker container name. |
| `SCREEN_WIDTH` / `SCREEN_HEIGHT` | `1440` / `900` | Virtual display geometry — restart the backend if you change these. |
| `AGENT_SERVICE_HOST` / `AGENT_SERVICE_PORT` | `127.0.0.1` / `9222` | Where the in-container action service listens. |
| `AGENT_SERVICE_TOKEN` | required | Bearer token enforced by the in-container agent service. |
| `CUA_ENABLE_LEGACY_ACTIONS` | `0` | Re-enables shell/clipboard/window-management actions inside the sandbox. Do not enable when binding non-loopback. |

### Agent runtime

| Variable | Default | Notes |
|---|---|---|
| `MAX_STEPS` | `50` | Default step budget. Hard cap is 200. |
| `STEP_TIMEOUT` | `30.0` | Seconds before a single action is considered hung. |

### v2 persistence and streaming

| Variable | Default | Notes |
|---|---|---|
| `CUA_V2_DB_PATH` | `data/computer-use-v2.sqlite3` | SQLite WAL database backing the Audit trail and Analytics tabs. |
| `CUA_V2_FRAME_PATH` | `data/audit-frames` | On-disk retention root for audit screenshot frames (7-day / 1 GiB default eviction). |

### Logging

| Variable | Default | Notes |
|---|---|---|
| `LOG_FORMAT` | `console` | Set to `json` for one JSON line per log record. |
| `LOG_LEVEL` | `INFO` | Standard Python log level. |
| `DEBUG` | `0` | Set to `1` for debug verbosity. |

A change to any of these takes effect on the next backend start. The
frontend has no build-time config beyond `VITE_WS_TOKEN` and
`VITE_API_PORT` (both optional, read at dev-server start).

## Troubleshooting

### Container does not start

```powershell
docker compose ps
docker logs cua-environment
docker compose down
docker compose up -d --build
```

### Container starts but the sandbox never becomes ready

```powershell
curl http://127.0.0.1:9222/health
```

If this fails, the in-container agent service didn't boot. Check
`docker logs cua-environment` for startup errors. Common causes: a stale
X server lock from a previous container (`docker compose down && docker
compose up -d` clears it), or a custom `SCREEN_WIDTH`/`SCREEN_HEIGHT`
that doesn't match the Dockerfile's expectations — reset to `1440x900`.

### Backend will not start

`HOST != 127.0.0.1` without both `CUA_ALLOW_PUBLIC_BIND=1` and
`CUA_API_TOKEN` set makes the process exit with a clear error — this is
intentional, not a bug. A missing dependency shows as
`ModuleNotFoundError`; reinstall with `uv sync --frozen`.

### Frontend will not start

On Windows, use `run.cmd` or `dev.py`. Those wait for backend health and
start Vite through Node. Do not use `npm run dev` as the daily launcher;
`npm.cmd` can leave an interactive wrapper that never releases the process.

### Viewport says "Connecting to sandbox" or never shows a desktop

The Live tab streams `/api/v2/ws/desktop` without starting a run. Check,
in order:

1. The sandbox is up: `docker ps --filter name=cua-environment` should
   show `healthy`. If not, `docker compose up -d` and wait ~30 seconds.
2. The agent service answers: `curl http://127.0.0.1:9222/health`.
3. The backend loaded the root `.env`. A log line
   `Agent service rejected request (token mismatch)` or repeated
   `401 Unauthorized` on `/screenshot` means restart the backend after
   confirming `AGENT_SERVICE_TOKEN` in the repo-root `.env` matches
   `docker exec cua-environment printenv AGENT_SERVICE_TOKEN`.
4. You are using `http://127.0.0.1:8505` (dev) or
   `http://127.0.0.1:8100` (production bundle). A WebSocket `403` means
   the page origin is not on the CORS allowlist.

If `node_modules` is missing or esbuild is broken after `npm ci`:

```powershell
cd frontend
npm ci
npm rebuild esbuild --foreground-scripts
```

Requires Node 22+. If `npm ci` fails on a corporate network, point npm
at your proxy and retry.

### A route shows as configured but every session on it fails

Check the Providers tab's circuit-state badge — if it shows `OPEN`, the
route is being temporarily skipped after repeated failures and will
recover automatically after its cooldown window.

### Files attached or Web Search requested but nothing happens

These two features aren't wired into the dashboard's session contract
yet — see [Feature Availability](#feature-availability-dashboard-vs-rest-api)
and use the v1 REST API directly for now.

### Port already in use

`dev.py` clears default ports automatically. If you bypassed it:

```powershell
Get-NetTCPConnection -LocalPort 8100 | Select-Object OwningProcess
Stop-Process -Id <pid>
```

### Full reset

```powershell
docker compose down --remove-orphans
setup.bat --clean
```

## Tests and Verification

```powershell
uv run pytest -p no:warnings --tb=short
uv run pytest -p no:warnings --tb=short -o addopts='' evals/
```

Focused checks:

```powershell
uv run pytest tests/test_v2_platform.py --tb=short          # v2 platform contract
uv run pytest tests/test_provider_run_contract.py --tb=short
uv run pytest tests/test_server.py --tb=short
uv run pytest tests/engine/test_openai.py tests/engine/test_claude.py tests/engine/test_gemini.py --tb=short
uv run pytest tests/docker/test_agent_service.py --tb=short  # in-container service; no Docker needed
```

Live SDK integration tests are gated behind the `integration` marker and
excluded from the default run (they need a real provider key and
outbound network access):

```powershell
uv run pytest -m integration --tb=short
```

Frontend:

```powershell
cd frontend
npm run lint
npm run typecheck
npm run test:run
npm run build          # emits frontend/dist/ — also the production bundle FastAPI serves
```

## Uninstall and Clean Reset

```powershell
docker compose down             # stop services, keep the image and data
docker compose down --rmi all   # also remove the built image
docker compose down -v          # also remove any Docker volumes you've added
```

Reset the Python environment:

```powershell
Remove-Item -Recurse -Force .venv
uv sync --frozen
```

Reset the frontend:

```powershell
cd frontend
Remove-Item -Recurse -Force node_modules, dist
npm ci
```

Clear v2's persisted history: stop the backend, then delete
`CUA_V2_DB_PATH` (default `data/computer-use-v2.sqlite3`) along with its
`-wal`/`-shm` files, and `CUA_V2_FRAME_PATH` (default
`data/audit-frames`) if you
also want to drop retained audit screenshots.

---

For a deeper look at the runtime contracts and module boundaries, read
`TECHNICAL.md` or the [Zero to Hero Study Handbook](docs/zero-to-hero-study-handbook.md).
For prompt patterns, read `docs/computer-use-prompt-guide.md`. For
changelog and release notes, read `CHANGELOG.md` and
`docs/release-notes-v3.1.0.md`.

## Appendix A — Operating Patterns

Patterns that show up repeatedly in real sessions.

### Pin reasoning effort per task class (v1 REST; not yet in the dashboard)

| Task class | Suggested effort | Notes |
|---|---|---|
| Open/close a single app | `minimal` or `low` | Pure UI navigation. |
| Single-app multi-step (edit, save) | `low` | Default for most desktop work. |
| Cross-app workflow | `medium` | The model needs to keep more state. |
| Research-and-act with web search | `medium` or `high` | The planning brief is non-trivial; execution must follow it. |
| Diagnosing a failure / recovering a stuck state | `high` | More deliberation per turn helps. |

Watch per-turn latency regardless of setting — if a 30-second task is
taking minutes per step, the effort level is too high for the work.

### Prefer URLs to navigation prompts

"Open the browser and go to `https://example.com`" is one navigation and
far more reliable than "open the browser, search for example, click the
first result," which is a longer chain that can fail at any link.

### Constrain the workspace

Most desktop tasks misbehave because the model has too much room to
explore. Name the exact app ("Open VS Code" not "open a code editor"),
say what's off-limits ("do not modify any other open file"), and say
whether to close things when done.

### Capture the final answer explicitly

```text
Stop when the file manager is visible.
Tell me the title bar text and the first three folder names you see.
```

This forces a structured final response rather than a vague "done."

### Re-run with a tighter prompt instead of a longer step budget

If a run hits `max_steps`, tighten the prompt before raising the cap — a
tighter prompt reaches the goal faster and more reliably than a looser
one with more budget.

## Appendix B — Provider-Specific Behavior

### OpenAI

- **Replay model.** Every Computer Use turn replays the full
  conversation history (intentional, for ZDR compatibility) — billing
  and latency both scale with session length, not linearly with turn
  count.
- **Screenshot resize.** Images beyond 10,240,000 pixels or a 6000px
  long edge are downscaled before upload; returned pixel coordinates are
  remapped back to real screen space automatically.
- **Reasoning effort defaults.** `gpt-5.6-luna` and `gpt-5.6-terra` default to `medium`.
- **Navigation hosts.** Set `CUA_ALLOWED_NAV_HOSTS` (comma-separated) to
  restrict `navigate` / `open_url` to those hostnames.

### Anthropic

- **Model.** Claude Sonnet 5 uses `computer_20251124`, adaptive thinking,
  and the `computer-use-2025-11-24` beta.
- **Streamed turns.** Turns stream via the beta Messages API with the
  `computer-use-2025-11-24` header to avoid the SDK's HTTP-timeout guard
  at the configured `max_tokens` budget.
- **Web search probe.** The first session per API key per 24 hours that
  enables Web Search runs a small probe call confirming the org has
  access; cached for 24 hours after success.
- **Document handling.** PDFs/TXTs upload via the Files API as document
  blocks; Markdown/DOCX are extracted and inlined as plain text — there
  is no Anthropic-side vector store equivalent.

### Google Gemini

- **Coordinate grid.** Gemini emits coordinates on a 0–999 normalized
  grid; the executor denormalizes to real pixels using the configured
  `SCREEN_WIDTH`/`SCREEN_HEIGHT`. Restart the backend after changing
  those so the executor picks up the new geometry.
- **Interactions state.** Gemini 3.7 Flash and Gemini 3.5 Flash-Lite continue turns with
  `previous_interaction_id`; action results include the current screenshot.
- **Prompt-injection detection.** Computer Use requests enable Google's
  built-in prompt-injection detection and preserve its confirmation handshake.
- **OAuth.** Configure `GOOGLE_OAUTH_CLIENT_ID`,
  `GOOGLE_OAUTH_CLIENT_SECRET`, and optionally `GOOGLE_CLOUD_PROJECT`; tokens
  remain only in the process-local credential vault.
- **Files rejected for Computer Use.** Gemini File Search cannot be
  combined with the Computer Use tool in this app — attaching files with
  Gemini selected fails at session start.

## Appendix C — Resource Profile

Approximate steady-state usage for one running session:

| Component | CPU | Memory |
|---|---|---|
| FastAPI backend | <5% single-core | ~150–250 MB |
| Vite dev server | <2% single-core | ~120–200 MB |
| Docker sandbox | 1–2 cores burst | up to the cap set in `docker-compose.yml` |
| Browser tab (dashboard) | ~1 core during render | 200–400 MB |

Disk: the sandbox image is several GB after first build (Ubuntu base +
browsers + LibreOffice + VS Code + GIMP/Inkscape). v2 audit frames are
bounded to 7 days or 1 GiB by default under `CUA_V2_FRAME_PATH`; the SQLite
database at `CUA_V2_DB_PATH` grows with session/action/event history and
has no automatic eviction — prune it manually if it grows large.

## Appendix D — Privacy Notes

The app keeps everything local by default:

- API keys are sent only to the chosen provider's own endpoint. Neither
  the v1 in-request key nor a v2 credential-session key is ever written
  to disk, logged, or included in the audit database.
- Screenshots stay on the host except when sent to the provider as part
  of the Computer Use loop itself; v2 audit frames retained under
  `CUA_V2_FRAME_PATH` are also local-only.
- Uploaded files (v1 REST path) are sent to the provider's Files
  API/vector-store only when a session actually attaches them — treat
  them with the same privacy posture as anything else you send that
  provider.
- Logs stay on the host. Set `LOG_FORMAT=json` if you want to pipe them
  into a log-aggregation tool you control.

The provider call itself is the only outbound traffic the backend makes
during normal operation. The in-container agent service does not call
out to the network on its own — but the model's own browser/desktop
actions inside the sandbox can of course initiate arbitrary outbound
traffic from inside that container, since that's the whole point of
letting it operate a browser.
