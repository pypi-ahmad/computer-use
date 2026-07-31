# Computer Use Workbench

A local, single-user workbench for provider-native Computer Use agents. v2 adds a typed `/api/v2` contract, deterministic route fallback, SQLite audit history, binary frame streaming, declarative workflows, and a five-tab React dashboard.

> Computer Use can execute destructive actions. Run the sandbox with test accounts and non-sensitive data. This project is not a multi-tenant service and does not make model actions safe by itself.

## Supported execution routes

OpenAI, Anthropic, and Google direct routes are executable. Azure OpenAI, AWS Bedrock, Vertex Gemini, and Vertex Claude are catalogued and contract-validated, but their execution bridges are intentionally reported as unavailable in v2.0.0. OpenRouter is omitted because its documented generic tool routing is not a vendor-native Computer Use protocol.

The dated model and deprecation evidence is in [the July 23 research audit](docs/research-audit-2026-07-23.md).

## Quick start

Requirements: Docker Desktop, Node.js 22, and [uv](https://docs.astral.sh/uv/).

```powershell
Copy-Item .env.example .env
uv sync --frozen
Set-Location frontend; npm ci; Set-Location ..
.\dev.bat
```

Set at least one of `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `GOOGLE_API_KEY` in the process environment or `.env`. Generate `AGENT_SERVICE_TOKEN` and `VNC_PASSWORD` before starting Compose. Secrets entered in the v2 Provider Manager remain in memory and expire after at most eight hours.

Open `http://127.0.0.1:3000` for development. For a production-style single-process build, see [Deployment](docs/deployment.md).

For the full operator guide — every dashboard tab, provider/credential setup, prompt-writing tips, and scripting via REST — see [USAGE.md](USAGE.md).

## Commands

| Command | Purpose |
|---|---|
| `uv sync --frozen` | Install the exact Python environment |
| `uv run python dev.py` | Start backend, frontend, and sandbox |
| `uv run pytest` | Run offline backend tests |
| `uv run pytest -o addopts='' evals/` | Run offline evals |
| `uv run ruff check .` | Lint Python |
| `uv run mypy` | Type-check Python |
| `npm --prefix frontend run typecheck` | Type-check React |
| `npm --prefix frontend run test:run` | Run frontend tests |
| `npm --prefix frontend run build` | Build the production UI |
| `uv run python scripts/build_release.py` | Build release archive and checksums |

## Architecture

- FastAPI owns REST, WebSocket events, orchestration, and production static serving.
- SQLite WAL persists sessions, actions, events, metrics, workflow versions, and checkpoints.
- A bounded filesystem store retains audit frames for seven days or one GiB by default.
- The sandbox is an isolated Ubuntu/XFCE container exposing authenticated screenshot and input endpoints.
- React consumes generated-style camelCase contracts and the `CUAF` binary-frame protocol.

See [TECHNICAL.md](TECHNICAL.md), [Migration](docs/migration-v2.md), [Rollback](docs/rollback-v2.md), and [Security](SECURITY.md).

New to this codebase? [Zero to Hero Study Handbook](docs/zero-to-hero-study-handbook.md) is a from-scratch tutorial covering the theory (agentic loops, coordinate spaces, sandboxing, safety confirmation) and the implementation (v1 and v2 architecture, module-by-module map, execution-flow traces).

## Verification status

Release publication is gated by `.github/workflows/ci.yml`: Ruff, formatting, mypy, Python 3.12–3.14 tests, evals, frontend lint/typecheck/tests/build, dependency audits, sandbox image build, and a blocking high/critical image scan. Live provider smoke tests are conditional and must be recorded in the release verification matrix; missing credentials are disclosed, never treated as a pass.

Licensed under MIT.
