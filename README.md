# Computer Use Workbench

A local, single-user workbench for provider-native Computer Use agents. The current release is **v3.0.3**. The operator surface is the typed `/api/v2` contract, deterministic route fallback, SQLite audit history, binary frame streaming, declarative workflows, and a five-tab React dashboard.

> Computer Use can execute destructive actions. Run the sandbox with test accounts and non-sensitive data. This project is not a multi-tenant service and does not make model actions safe by itself.

## Supported execution routes

The workbench exposes exactly three direct provider-native routes: GPT-5.6 Luna or GPT-5.6 Terra through OpenAI Responses, Claude Sonnet 5 through Anthropic Messages, and Gemini 3.7 Flash or Gemini 3.5 Flash-Lite through Google Interactions. All three accept API keys; Gemini also supports browser OAuth through the v2 Provider Manager.

The dated model and deprecation evidence is in [the July 23 research audit](docs/research-audit-2026-07-23.md).

## Quick start

On Windows 11, double-click `run.cmd`. That single file installs anything
that is missing (Docker Desktop, Node.js LTS, [uv](https://docs.astral.sh/uv/),
Python 3.12, locked Python/frontend dependencies, and the sandbox image), then
starts the stack, waits for `GET /api/health`, and opens
`http://127.0.0.1:8505`. Already-present tools, `cua-ubuntu:latest`, and a
working Vite install are skipped. `START.bat` still exists and always runs
the full `setup.bat` bootstrap first. Vite listens on IPv4 loopback so that
address matches the URL the launcher opens. Normal Windows installer or UAC
prompts may appear; if Docker requests a restart, restart and run `run.cmd`
again.

```powershell
.\run.cmd
```

The Live session tab shows the sandbox desktop as soon as the container is
ready. You do not start a run to see the screen. The dashboard opens
`/api/v2/ws/desktop` immediately; a run switches to `/api/v2/ws/{session_id}`.
If an old tab still says "No session on the wire", hard-refresh
(`Ctrl+Shift+R`). Blank after that usually means `AGENT_SERVICE_TOKEN` in the
repo-root `.env` does not match the container — restart the backend after
fixing it. The production bundle at `http://127.0.0.1:8100` is a valid
WebSocket origin.

For manual or non-Windows setup, install Docker, Node.js 22+, and uv, then use
the commands in [USAGE.md](USAGE.md). Provider API keys or Google OAuth can be
configured in the v2 Provider Manager. Provider credentials remain process-local
and expire after at most eight hours.

Open `http://127.0.0.1:8505` for development. A non-loopback deployment must set both `CUA_ALLOW_PUBLIC_BIND=1` and `CUA_API_TOKEN`. For a production-style single-process build, see [Deployment](docs/deployment.md).

For the full operator guide — every dashboard tab, provider/credential setup, prompt-writing tips, and scripting via REST — see [USAGE.md](USAGE.md).

## Commands

| Command | Purpose |
|---|---|
| `run.cmd` | One-file Windows setup-if-needed, then launch the app |
| `START.bat` | Always bootstrap through `setup.bat`, then launch |
| `uv sync --frozen` | Install the exact Python environment |
| `uv run python dev.py --open-browser` | Start backend, frontend, and sandbox; open the dashboard after backend health succeeds |
| `uv run pytest` | Run offline backend tests |
| `uv run pytest -o addopts='' evals/` | Run offline evals |
| `uv run ruff check .` | Lint Python |
| `uv run mypy` | Type-check Python |
| `npm --prefix frontend run typecheck` | Type-check React |
| `npm --prefix frontend run test:run` | Run frontend tests |
| `npm --prefix frontend run build` | Build the production UI |
| `uv run python scripts/build_handbook_site.py` | Regenerate the standalone handbook website |
| `uv run python scripts/build_release.py` | Build release archive and checksums |

## Architecture

- FastAPI owns authenticated REST/WebSocket access, orchestration, and production static serving.
- Sessions use explicit primary and fallback routes, with provider-default, confirm-mutating, and read-only safety policies.
- SQLite WAL persists sessions, actions, events, metrics, and workflow versions; a bounded filesystem store retains audit frames for seven days or one GiB by default.
- The sandbox is an isolated Ubuntu/XFCE container exposing authenticated screenshot and input endpoints.
- React consumes camelCase contracts and the `CUAF` binary-frame protocol. The Live tab streams the sandbox desktop immediately on `/api/v2/ws/desktop`, then switches to `/api/v2/ws/{session_id}` for an active run.

See [TECHNICAL.md](TECHNICAL.md), [v3.0.3 release notes](docs/release-notes-v3.0.3.md), [Migration](docs/migration-v2.md), [Rollback](docs/rollback-v2.md), and [Security](SECURITY.md).

New to this codebase? Open the [interactive Zero to Hero handbook](docs/zero-to-hero-study-handbook.html) for guided GitHub-user, technical, and business tracks. Its [Markdown source](docs/zero-to-hero-study-handbook.md) remains available for plain-text reading and PDF generation.

## Verification status

Release publication is gated by `.github/workflows/ci.yml`: Ruff, formatting, mypy, Python 3.12–3.14 tests, evals, frontend lint/typecheck/tests/build, dependency audits, sandbox image build, and a blocking high/critical image scan. Live provider smoke tests are conditional and must be recorded in the release verification matrix; missing credentials are disclosed, never treated as a pass.

Licensed under MIT.
