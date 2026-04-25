<!-- markdownlint-disable-file MD013 -->

# USAGE

Operator reference for `computer-use`. For the project pitch and quickstart context, see
[README.md](README.md). For architecture, provider internals, and extension points, see
[TECHNICAL.md](TECHNICAL.md). This guide covers installation, running a session, every
environment variable the backend reads, the sandbox's per-provider behaviour, and the
failure modes you are most likely to see.

## Table of contents

- [What this app is](#what-this-app-is)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Running a session](#running-a-session)
- [Model selection](#model-selection)
- [Configuration reference](#configuration-reference)
- [Sandbox behavior](#sandbox-behavior)
- [Workflows](#workflows)
- [Observability](#observability)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)
- [See also](#see-also)
- [Getting help](#getting-help)

## What this app is

`computer-use` is a local single-user operator workbench for running provider-native Computer
Use models against a controlled Ubuntu desktop running in Docker. The frontend provides a live
screen view, step timeline, log inspector, safety-prompt handling, and session export. The
backend orchestrates provider adapters (Anthropic, OpenAI, Google), manages the sandbox
container lifecycle, and streams model-visible screenshots to the UI.

It is built for researchers, adapter implementers, evaluators, and engineers who want to
inspect real CU sessions rather than treating the model as a black box. It is not a
multi-tenant SaaS, not a repository-aware coding agent, and not a browser-only automation
layer. Desktop actions run inside the sandbox container, not on the host.

## Prerequisites

- **Docker 24+** — the sandbox desktop always runs in Docker. Check with `docker --version`.
- **Python 3.11+** — required for the backend; matches CI. Check with `python --version`.
- **Node.js 20+** — required for the Vite frontend. Check with `node --version`.
- **At least one provider API key.** Obtain from:
  - Anthropic: <https://console.anthropic.com/settings/keys>
  - OpenAI: <https://platform.openai.com/api-keys>
  - Google AI: <https://aistudio.google.com/apikey>
- **Free loopback ports.** Defaults: `3000` (frontend), `8000` (backend), `6080` (noVNC),
  `5900` (VNC), `9222` (agent service).
- **Several GB of free disk space** for the sandbox image and browsers.

## Installation

### Recommended — setup script

`setup.sh` and `setup.bat` check prerequisites, build the sandbox image, create the Python
virtualenv, and install frontend dependencies.

```bash
git clone https://github.com/pypi-ahmad/computer-use.git
cd computer-use
cp .env.example .env
# add at least one API key to .env

bash setup.sh          # macOS / Linux
# .\setup.bat          # Windows PowerShell

docker compose up -d                                  # start the sandbox
source .venv/bin/activate && python -m backend.main   # backend
cd frontend && npm run dev                            # frontend (second terminal)

```

Open <http://localhost:3000>. The first image build takes several minutes; subsequent starts
are fast. Re-running `setup.sh` / `setup.bat` does a full rebuild and is meant for setup or
recovery, not daily use.

### Manual

```bash
git clone https://github.com/pypi-ahmad/computer-use.git
cd computer-use
cp .env.example .env

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd frontend && npm install && cd ..

docker compose up -d
python -m backend.main
# second terminal:
cd frontend && npm run dev

```

The sandbox always runs in Docker even when the backend runs on the host. If the container
exits immediately, inspect `docker logs cua-environment`. A common first-run cause is the VNC
guard in `docker/entrypoint.sh`: uncomment `VNC_PASSWORD=...` in `docker-compose.yml` or add
`CUA_ALLOW_NOPW=1` to the service environment.

## Running a session

A session is one agent run, end-to-end: model selection → task entry → container start → perceive-act loop → terminal state. The workbench walks you through it in that order.

1. **Open the workbench.** With `python -m backend.main` running and `npm run dev` serving the Vite frontend, point a browser at `http://127.0.0.1:3000`. The top strip shows the environment state (`Not Started` → `Starting` → `Environment Ready`) and the WebSocket connection indicator (`Reconnecting…` flips off once the `/ws` upgrade succeeds).

2. **Configure a provider.** Pick Google / Anthropic / OpenAI. The model dropdown populates from `GET /api/models`, which reads [backend/allowed_models.json](backend/allowed_models.json) and filters to entries with `supports_computer_use: true`. If your API key is in `.env` or a system env var, the workbench offers it as a "Config File ✓" / "Pre-configured ✓" button with a masked preview; otherwise enter it in the password field and it will be used per-request only (never persisted).

3. **Describe the task.** Write a literal description of what you want done, not a chain-of-thought. The system prompt already covers "act only on what was asked" — over-specifying tends to make the model narrate instead of act. For Opus 4.7 specifically, the prompt has been stripped of self-verification scaffolding per Anthropic's migration guide; keep your task prompt similarly direct.

4. **Start.** Click **Start Agent**. The backend calls `docker run` (or reuses the existing container), waits on the in-container agent service's `/health` endpoint with exponential-backoff jitter up to `CUA_CONTAINER_READY_TIMEOUT` seconds (default 30 s), and then hands control to the chosen provider's adapter. If readiness fails, you get HTTP 409 with the most recent `/health` error detail — not a cryptic `screenshot capture failed` mid-run.

5. **Watch and interrupt.** The `ScreenView` panel shows the live desktop via noVNC when the container is up; toggle to "Screenshot" to see what the model actually sees (they are identical at 1440×900 but can diverge on hi-res Opus 4.7). The Timeline panel streams one entry per model turn: text + one or more tool calls + a fresh screenshot. Logs stream over the same WebSocket on a separate channel.

6. **Handle approvals.** When the provider returns `require_confirmation` (Gemini), `pending_safety_checks` (OpenAI), or `stop_reason=refusal` (Anthropic), the workbench opens the `SafetyModal` with the model's explanation. Confirm or deny. A denial terminates the run cleanly with `Agent terminated: safety confirmation denied.`; a 60-second no-response auto-denies per the ToS posture. See [docker/SECURITY_NOTES.md](docker/SECURITY_NOTES.md) for the underlying contract.

7. **Stop.** **Stop** issues `POST /api/agent/stop/<sid>`. The server returns 2xx or 404 (`session already ended`) on positive confirmation; either clears local state. Any other outcome (network error, 5xx) keeps `sessionId` populated and surfaces a retry toast — silent stops would otherwise leave the backend spending tokens.

## Model selection

Costs and latencies below are approximate and change frequently; treat them as ordering hints, not absolute numbers. Verify on the provider's current pricing page before committing to a sustained workload.

| Model | Best for | Tradeoffs |
|---|---|---|
| **Claude Opus 4.7** | Long-horizon agentic tasks, vision-heavy work, spreadsheet/document editing | Highest unit cost. Adaptive thinking only (`{type: adaptive}`); legacy `enabled + budget_tokens` returns HTTP 400. Sampling params (`temperature`, `top_p`, `top_k`) rejected. 1:1 pixel coordinates up to 2576 px long-edge — set `CUA_OPUS47_HIRES=1` plus a larger `SCREEN_WIDTH`/`SCREEN_HEIGHT` to use the full ceiling. |
| **Claude Sonnet 4.6** | General CU tasks, web automation, default choice | Cheaper and faster than Opus 4.7; downscales internally to 1568 px / 1.15 MP so a 1440×900 default viewport is a no-op. Adaptive thinking recommended; legacy `enabled + budget_tokens` still accepted but deprecated. |
| **GPT-5.4** | Built-in `computer` tool on the Responses API, ZDR-safe | Uses stateless replay with `reasoning.encrypted_content` + `store=false` instead of `previous_response_id`. CU floor is `reasoning.effort="high"`. `detail: "original"` is hard-coded on every `computer_call_output` per the OpenAI guide. Prompts > 272k tokens hit the 2× input / 1.5× output overage multiplier — prune session history before it grows past that. |
| **Gemini 3 Flash Preview** | Browser-centric tasks, price-sensitive workflows, Google-reference parity | Built-in CU; no separate model id required. Normalized 0–999 coordinates denormalised to pixels by `DesktopExecutor._px`. `thinking_level=high` recommended. Usually the best Google default when you want lower cost and faster turns. |
| **Gemini 3.1 Pro Preview** | Google tasks that need stronger reasoning than Flash | Higher cost than Flash, but keeps the same built-in CU path and normalized-coordinate contract. Use it when Flash is not enough for long-horizon planning or denser page understanding. |

The model picker is driven directly from [backend/allowed_models.json](backend/allowed_models.json). If a model ID is not listed there, it is not selectable for new sessions.

## Configuration reference

Every environment variable the backend reads, grouped by concern. "Where read" names the module that owns the value so operators can audit behaviour at the source.

### API keys

| Variable | Required | Default | Purpose | Where read |
|---|---|---|---|---|
| `ANTHROPIC_API_KEY` | when using Claude | – | Anthropic Messages API key | `backend/config.py` |
| `OPENAI_API_KEY` | when using OpenAI | – | OpenAI Responses API key | `backend/config.py` |
| `GOOGLE_API_KEY` | when using Gemini | – | Google Generative AI API key (preferred) | `backend/config.py` |
| `GEMINI_API_KEY` | when using Gemini | – | Alias accepted as fallback when `GOOGLE_API_KEY` is unset | `backend/config.py` |

Keys resolve in priority order: UI input > `.env` > system env. Keys entered in the UI are sent per-request over loopback only and never written to disk or `localStorage`.

### Backend bind

| Variable | Required | Default | Purpose | Where read |
|---|---|---|---|---|
| `HOST` | no | `127.0.0.1` | FastAPI bind host | `backend/config.py`, `backend/main.py` |
| `PORT` | no | `8000` | FastAPI bind port | `backend/config.py`, `backend/main.py` |
| `DEBUG` | no | `false` | Verbose logging + full tracebacks | `backend/config.py` |
| `CUA_RELOAD` | no | `false` | uvicorn `--reload`. Off by default; turning it on in non-dev is a footgun | `backend/main.py` |
| `CUA_WS_TOKEN` | required for non-loopback bind | unset | Shared secret; clients pass `?token=<value>` on `/ws` and `/vnc/websockify` | `backend/server.py` |
| `CUA_ALLOWED_HOSTS` | no | loopback + configured CORS | Comma-separated `Host`-header allowlist | `backend/server.py` |
| `CUA_ALLOW_PUBLIC_BIND` | required for non-loopback bind | unset | Guardrail: refuses to start on non-loopback unless also set alongside `CUA_WS_TOKEN` | `backend/main.py` |
| `CUA_MAX_BODY_BYTES` | no | 1 MB | Max request body size | `backend/server.py` |
| `CUA_MAX_SESSION_BROADCAST_BACKLOG` | no | 100 | WebSocket backpressure threshold | `backend/server.py` |

### Sandbox (container env)

| Variable | Required | Default | Purpose | Where read |
|---|---|---|---|---|
| `SCREEN_WIDTH` / `SCREEN_HEIGHT` | no | `1440` / `900` | Xvfb display dimensions | `docker/entrypoint.sh`, `docker/agent_service.py` |
| `WIDTH` / `HEIGHT` | no | same | Anthropic-compatible aliases | `docker/Dockerfile` |
| `DISPLAY` | no | `:99` | X11 display number | `docker/entrypoint.sh` |
| `AGENT_SERVICE_HOST` / `AGENT_SERVICE_PORT` | no | `127.0.0.1` / `9222` | In-container HTTP API address | `backend/config.py`, `docker/agent_service.py` |
| `AGENT_SERVICE_TOKEN` | auto-generated | random per-session | Shared secret between host and container's agent service | `backend/docker_manager.py` |
| `AGENT_MODE` | no | `desktop` | Execution mode selector | `backend/config.py` |
| `CONTAINER_NAME` | no | `cua-environment` | Docker container name | `backend/config.py` |
| `CUA_WINDOW_X` / `Y` / `W` / `H` | no | – | Optional window normaliser geometry | `docker/agent_service.py` |
| `CUA_ENABLE_LEGACY_ACTIONS` | no | `0` | Re-enables removed actions (`run_command`, window mgmt, etc.). Off by default | `docker/agent_service.py` |

### Provider-specific

| Variable | Required | Default | Purpose | Where read |
|---|---|---|---|---|
| `OPENAI_REASONING_EFFORT` | no | `high` | `minimal` / `low` / `medium` / `high`; legacy `none` → `minimal`, `xhigh` → `high` | `backend/engine/openai.py` |
| `OPENAI_BASE_URL` | no | – | Override for regional endpoints (e.g. `https://us.api.openai.com/v1`) or Azure / proxy deployments | `backend/engine/openai.py` |
| `CUA_CLAUDE_CACHING` | no | unset | When `1`: add `cache_control: {"type":"ephemeral"}` to the `computer_20251124` tool block | `backend/engine/claude.py` |
| `CUA_CLAUDE_MAX_TOKENS` | no | `32768` | Per-turn `max_tokens` for Claude CU calls | `backend/engine/claude.py` |
| `CUA_OPUS47_HIRES` | no | unset | Opus 4.7 only: bypass the 3.75 MP pixel cap, enforce only the 2576-px long-edge | `backend/engine/claude.py` |
| `CUA_GEMINI_THINKING_LEVEL` | no | `high` | `minimal` / `low` / `medium` / `high` | `backend/engine/gemini.py` |
| `CUA_GEMINI_RELAX_SAFETY` | no | unset | When `1`: apply `BLOCK_ONLY_HIGH` thresholds; default is Google's own "Off" for Gemini 3 | `backend/engine/gemini.py` |
| `CUA_GEMINI_USE_PLAYWRIGHT` | no | unset | When `1` and `playwright` is installed: use Playwright-controlled Chromium for native-fidelity Gemini sessions. Off keeps the xdotool path | `backend/engine/gemini.py` |
| `GEMINI_MODEL` | no | `gemini-3-flash-preview` | Default model id when none is passed | `backend/config.py` |

### Observability + development

| Variable | Required | Default | Purpose | Where read |
|---|---|---|---|---|
| `CUA_TRACE_DIR` | no | `~/.computer-use/traces/` | On-disk trace JSON files | `backend/tracing.py` |
| `CUA_DEBUG_TB` | no | unset | When `1`: include full tracebacks in executor error logs | `backend/engine/__init__.py` |
| `CUA_TEST_MODE` | no | unset | Test-harness-only switches | `backend/server.py` |
| `CUA_SESSIONS_DB` | no | – | Override LangGraph SQLite checkpoint path | `backend/server.py` |
| `CUA_SESSIONS_DB_ALLOW_DIR` | no | – | Extra allowed parent dir for `CUA_SESSIONS_DB` | `backend/server.py` |
| `CUA_SESSIONS_MAX_THREADS` | no | 1000 | Cap on retained LangGraph threads | `backend/server.py` |

## Sandbox behavior

The Docker sandbox is the union of Anthropic's computer-use-demo package baseline, OpenAI's Option-1 browser-security guidance, and Google's Gemini CU coordinate contract. Packages coexist; nothing is replaced. Full rationale lives in [docker/SECURITY_NOTES.md](docker/SECURITY_NOTES.md).

- **Viewport.** 1440×900 by default — the exact Gemini docs recommendation, OpenAI's preferred downscale target, Anthropic-compatible. Override with `SCREEN_WIDTH` / `SCREEN_HEIGHT` (or the `WIDTH` / `HEIGHT` aliases). Set `CUA_OPUS47_HIRES=1` with a larger viewport for Opus 4.7's 2576-px ceiling.
- **Window manager.** XFCE4. `mutter` is installed alongside for Anthropic-reference parity but XFCE4 is the active WM.
- **Browsers.** Google Chrome (pre-profiled to suppress first-run UI), Chromium, Firefox-ESR all coexist. The Gemini adapter prefers Chromium to match Google's reference; the OpenAI adapter spawns Chrome with `--disable-extensions --disable-file-system --no-default-browser-check --user-data-dir=<profile>`; both use a minimal env (DISPLAY, HOME, PATH, LANG, XDG_RUNTIME_DIR only — no host-env inheritance that would leak API keys into a compromised renderer).
- **Action surface.** Only actions the engine emits are reachable. Everything else (`run_command`, window management, DOM stubs, `screenshot_region` POST) returns HTTP 404 on `POST /action` unless `CUA_ENABLE_LEGACY_ACTIONS=1`. `run_command` has an executable allowlist + blocked-pattern regex that fires on the full argv (catches `bash -c 'rm -rf /'`, not just argv[0]).
- **Session reset.** Each `docker run` starts fresh. The `--user-data-dir` profile is pre-created at build time (`/tmp/chrome-profile`) and reused; to fully reset browser state between sessions, stop and restart the container.

## Workflows

Three concrete task patterns to show what the system is actually good at.

### 1. Fill a web form and submit (GPT-5.4, browser path)

Task: *"Go to example.com/contact and fill the form with name 'Alice', email 'alice@example.com', message 'Testing CU'. Click Submit and confirm the success message is visible."*

What to expect: GPT-5.4 takes a screenshot, emits a batch of `computer_call.actions[]` (click → type → click → type → …) per turn rather than one action at a time, and confirms by reading the success message back in text. `detail: "original"` on every screenshot output means the model sees the full 1440×900 render; no low-fidelity passes. ZDR-safe reasoning replay keeps prior turns out of `previous_response_id` stash.

### 2. Edit a LibreOffice document (Claude Opus 4.7, full desktop)

Task: *"Open /home/agent/Documents/report.odt in LibreOffice, change the title to 'Q2 Report', insert a bulleted list of three items under the first heading, save, and close."*

What to expect: Opus 4.7 uses `zoom` to read fine print in chart legends when needed (requires `enable_zoom: true` in the tool definition, which the adapter sets automatically). Adaptive thinking; no budget knob to tune. If you run the viewport at 2560×1600 with `CUA_OPUS47_HIRES=1`, the tool definition advertises `display_width_px: 2576` after long-edge clamping and the model keeps 1:1 pixel coordinates — useful for dense spreadsheets.

### 3. Research on Google Shopping (Gemini 3 Flash, Chromium)

Task: *"Search Google Shopping for a USB-C GaN charger under $30 with at least 60W output. Open the first result, find the return policy, summarise it."*

What to expect: the Gemini adapter launches Chromium (Google's reference). Coordinates come back normalized 0–999; `DesktopExecutor._px` denormalises against the actual 1440×900 viewport. If the model emits a `safety_decision: require_confirmation` (rare for research tasks, common for financial confirms), the workbench's SafetyModal prompts you; the ToS-mandated `safety_acknowledgement: "true"` echo only fires after you confirm.

### 4. Multi-application workflow (Claude Sonnet 4.6)

Task: *"Open a terminal, create the directory /home/agent/export, open LibreOffice Calc, enter three rows of sample data, save the file to /home/agent/export/data.ods, then open the file manager and confirm it is there."*

What to expect: Sonnet 4.6 is the natural default for cross-application work because it balances cost and accuracy well. The session will touch a terminal emulator, an office application, and a GUI file manager. Watch the Timeline panel — tool batches often show a `zoom` action inside LibreOffice for reading cell content. Expected duration: 3–5 minutes depending on the number of correction turns needed.

### 5. Long-running data extraction (GPT-5.4)

Task: *"Go to [a paginated public data source], collect the name and value from the first 20 rows, and save them line-by-line to /home/agent/Documents/results.txt. Stop when done."*

What to expect: GPT-5.4 batches multiple actions per turn (scroll, read, type) which reduces total turn count for repetitive work. Raise `MAX_STEPS` before starting if the default 50 is too low for the data size. ZDR-safe replay keeps prior turns out of persistent storage — useful if the source data is sensitive. Expected duration: 8–15 minutes for 20 rows with inter-page navigation.

## Observability

Every session produces both a **LangGraph checkpoint** (per-node state, enables restart-resume on approval) and a **session trace** (ordered `TraceEvent` records with redacted payloads).

- **Live.** The workbench streams `log`, `step`, `screenshot_stream`, `safety_confirmation`, and `agent_finished` events over WebSocket. The Logs panel renders them in real time and supports copy / download / export.
- **After the run.** Traces land at `$CUA_TRACE_DIR/<session_id>.json` (default `~/.computer-use/traces/`). Inspect with:

  ```bash
  python -m backend.tracing list
  python -m backend.tracing dump <session_id>

  ```

  Screenshots in the persisted trace are redacted to `{"sha256": <hex>, "len": <int>}` — the bytes stay on disk only if you asked for them. Free-text fields pass through the same `scrub_secrets` regex that redacts logs.
- **Restart-resume.** If the backend is killed while a session is paused on `approval_interrupt`, the LangGraph SQLite checkpointer preserves `pending_approval` and the exact graph state. Posting to `/api/agent/safety-confirm` after restart resumes via `graph.ainvoke(Command(resume=decision), config)`. Test coverage: `tests/test_agent_graph_nodes.py::TestApprovalInterruptResume::test_pause_and_resume_across_fresh_runtime`.

## Troubleshooting

1. **Docker container won't start / "Sandbox is not ready".** The server returns HTTP 409 with the underlying health-probe error. Inspect with `docker logs cua-environment`; the most common cause is `x11vnc` crashing because XFCE4 hasn't come up yet — bump `CUA_CONTAINER_READY_TIMEOUT` past 30 s or check Xvfb logs.
2. **WebSocket disconnects mid-session.** The frontend auto-reconnects with a 2 s delay and a 15 s heartbeat; a permanent disconnect means the backend has died or a reverse proxy is closing idle upgrades. If you set `CUA_WS_TOKEN` and clients see `close code 4401`, the `?token=` query parameter is missing or wrong.
3. **"API key is required" despite having set one.** The UI resolution order is UI > `.env` > system env. A key in `.env` that the backend picked up earlier still needs the UI's "Config File ✓" toggle flipped on. Restart the backend if you added the key after first launch.
4. **`require_confirmation` / refusal.** Expected. The workbench opens the SafetyModal with the provider's explanation. Approve or deny. A 60 s auto-deny is deliberate — never auto-approve CU safety prompts; Gemini's ToS explicitly forbids it.
5. **Screenshot is blank.** `Xvfb` hasn't rendered anything yet, or XFCE4 panel hasn't started. Try clicking **Stop Environment** → **Start Environment**. If it persists, `docker exec cua-environment scrot /tmp/test.png` and inspect manually; the most common cause is a stale `/tmp/.X99-lock`.
6. **Session state bleeding.** Each run reuses the same `cua-environment` container. To reset browser profiles, session history, and `/tmp` state, use `Stop Environment` in the header (which calls `docker rm -f`). A new session will `docker run` fresh.
7. **`HTTP 400` from OpenAI after upgrading.** The Responses API enum for `reasoning.effort` is `{minimal, low, medium, high}`. Legacy values (`none`, `xhigh`) are mapped to canonical by `OpenAICUClient.__init__`, but a hand-rolled request skipping that path will 400. Use the env var or the request-body override; don't pass legacy values directly to the SDK.
8. **Claude `HTTP 400: temperature is not supported`.** Opus 4.7 rejects `temperature`, `top_p`, `top_k` at any non-default value. The adapter omits them; if you've patched custom sampling params in, remove them.
9. **Gemini `400 INVALID_ARGUMENT`.** The adapter's log line names the three most common causes: screenshot too large or corrupt; tool-version mismatch; context exceeded. See [backend/engine/gemini.py](backend/engine/gemini.py) (search for `INVALID_ARGUMENT`).
10. **Ghost sessions.** If the workbench shows "Agent Running" but nothing happens, the stop flow preserves `sessionId` on transient failures — click **Stop** again. The server's `/api/agent/stop/<sid>` idempotently returns 404 for already-ended sessions.
11. **Tests fail with `ModuleNotFoundError: uvicorn`.** Four tests in `tests/test_audit_fixes.py::TestPublicBindGuardrail` require uvicorn on the dev host. `pip install uvicorn`.

## FAQ

**Is this production-ready?**
No. It is a local research workbench. The REST surface is unauthenticated; the default bind is `127.0.0.1`. Do not expose it on a LAN without both `CUA_WS_TOKEN` and your own reverse-proxy auth.

**Can I run multiple concurrent sessions?**
Up to 3, hard-capped by `_MAX_CONCURRENT_SESSIONS` in `backend/server.py`. The 4th returns HTTP 429. Each session gets its own LangGraph checkpoint thread.

**Does it support custom models?**
Only model IDs in [backend/allowed_models.json](backend/allowed_models.json) are accepted. Add an entry with `supports_computer_use: true` and, for Anthropic, the correct `cu_tool_version` + `cu_betas`.

**Does it solve CAPTCHAs?**
No. The system prompt explicitly forbids it, and Anthropic / OpenAI / Google's prompt-injection classifiers trigger `require_confirmation` on CAPTCHA-like prompts. A human must confirm.

**How do I capture a session for a bug report?**
Export the trace: `python -m backend.tracing dump <session_id> > session.json`. Export the Timeline + Logs from the workbench (Export JSON/HTML buttons). Include the `agent_finished` event data, the model ID, the env vars in use, and the container status at the time of failure.

**Does it work without Docker?**
No. The sandbox is load-bearing for isolation. The `AGENT_SERVICE_TOKEN` handshake, the `no-new-privileges` flag, the dropped Linux capabilities, the Chrome profile hardening, and the browser subprocess's minimal env all assume a container boundary.

**Why is Gemini 3.1 Pro Preview excluded?**
Google has not enabled Computer Use on that model as of 2026-04-24 despite the Gemini 3 developer guide implying support. The official CU docs list only `gemini-3-flash-preview` and `gemini-2.5-computer-use-preview-10-2025`. See [CHANGELOG.md](CHANGELOG.md) for the forum repro link.

## See also

- [README.md](README.md) — project pitch and quickstart
- [TECHNICAL.md](TECHNICAL.md) — architecture and internals
- [CHANGELOG.md](CHANGELOG.md) — release history
- [docker/SECURITY_NOTES.md](docker/SECURITY_NOTES.md) — sandbox security posture

## Getting help

File bug reports and usage questions at <https://github.com/pypi-ahmad/computer-use/issues>.
Include: model ID, provider, session ID, whether on noVNC or Screenshot mode, a trace dump
from `python -m backend.tracing dump <session_id>`, and `docker logs cua-environment` if
the sandbox was involved.
