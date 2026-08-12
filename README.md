# Computer Use Workbench

A local, single-user workbench for provider-native Computer Use agents. v2 adds a typed `/api/v2` contract, deterministic route fallback, SQLite audit history, binary frame streaming, declarative workflows, and a five-tab React dashboard.

> Computer Use can execute destructive actions. Run the sandbox with test accounts and non-sensitive data. This project is not a multi-tenant service and does not make model actions safe by itself.

## Supported execution routes

The workbench exposes exactly three direct provider-native routes: GPT-5.6 Luna through OpenAI Responses, Claude Sonnet 5 through Anthropic Messages, and Gemini 3.6 Flash through Google Interactions. All three accept API keys; Gemini also supports browser OAuth through the v2 Provider Manager.

The dated model and deprecation evidence is in [the July 23 research audit](docs/research-audit-2026-07-23.md).

## Quick start

On Windows 11, double-click `START.bat`. It installs missing Docker Desktop,
Node.js LTS, and [uv](https://docs.astral.sh/uv/) through winget, creates safe
local sandbox credentials, installs locked project dependencies, starts the
stack, and opens the dashboard. Normal Windows installer or UAC prompts may
appear; if Docker requests a restart, restart and double-click the file again.

```powershell
.\START.bat
```

For manual or non-Windows setup, install Docker, Node.js 22+, and uv, then use
the commands in [USAGE.md](USAGE.md). Provider API keys or Google OAuth can be
configured in the v2 Provider Manager. Provider credentials remain process-local
and expire after at most eight hours.

Open `http://127.0.0.1:3000` for development. A non-loopback deployment must set both `CUA_ALLOW_PUBLIC_BIND=1` and `CUA_API_TOKEN`. For a production-style single-process build, see [Deployment](docs/deployment.md).

For the full operator guide — every dashboard tab, provider/credential setup, prompt-writing tips, and scripting via REST — see [USAGE.md](USAGE.md).

## Commands

| Command | Purpose |
|---|---|
| `START.bat` | Install missing Windows dependencies and launch the app |
| `uv sync --frozen` | Install the exact Python environment |
| `uv run python dev.py` | Start backend, frontend, and sandbox |
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
- React consumes camelCase contracts and the `CUAF` binary-frame protocol, and exposes providers, live sessions, audit export, workflows, and analytics.

See [TECHNICAL.md](TECHNICAL.md), [Migration](docs/migration-v2.md), [Rollback](docs/rollback-v2.md), and [Security](SECURITY.md).

New to this codebase? Open the [interactive Zero to Hero handbook](docs/zero-to-hero-study-handbook.html) for guided GitHub-user, technical, and business tracks. Its [Markdown source](docs/zero-to-hero-study-handbook.md) remains available for plain-text reading and PDF generation.

## Verification status

Release publication is gated by `.github/workflows/ci.yml`: Ruff, formatting, mypy, Python 3.12–3.14 tests, evals, frontend lint/typecheck/tests/build, dependency audits, sandbox image build, and a blocking high/critical image scan. Live provider smoke tests are conditional and must be recorded in the release verification matrix; missing credentials are disclosed, never treated as a pass.

Licensed under MIT.
