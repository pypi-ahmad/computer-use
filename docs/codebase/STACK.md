# Technology Stack

## 1) Runtime Summary

| Area | Value | Evidence |
|---|---|---|
| Primary languages | Python; TypeScript/TSX | `pyproject.toml`; `frontend/tsconfig.json` |
| Runtime versions | Python 3.12-3.14; Node.js 22; Ubuntu 24.04 sandbox | `pyproject.toml:12`; `docs/deployment.md:15`; `docker/Dockerfile:1` |
| Package managers | uv/`uv.lock`; npm/`frontend/package-lock.json` | `.github/workflows/ci.yml:20-24,61-67` |
| Module/build systems | Hatchling wheel; Vite SPA; Docker/Compose | `pyproject.toml:1-3,27-28`; `frontend/package.json:7-12`; `docker-compose.yml` |

Bash and Windows batch wrappers support setup/development. The package is `computer-use-workbench` 3.0.3. On Windows, `run.cmd` is the one-file setup-if-needed launcher; `START.bat` always bootstraps through `setup.bat`. `dev.py` waits for backend health, binds Vite to `127.0.0.1`, and starts Vite through Node.

## 2) Production Frameworks and Dependencies

### Backend

- FastAPI 0.141.1 and Uvicorn 0.35.0 expose REST, WebSocket, OpenAPI, and the production frontend bundle.
- Pydantic 2.13.0 defines request, response, provider, and action contracts.
- Provider SDKs are OpenAI 2.30.0, Anthropic 0.88.0, Google Gen AI 2.7.0, and Google Auth 2.49.1 for OAuth credentials.
- SQLite from the Python standard library stores v2 sessions, actions, events, metrics, workflows, and checkpoints in WAL mode.
- HTTPX and `websockets` handle service/provider communication. Pillow, pypdf, and python-docx support frames and uploaded documents.

| Dependency | Version | Role in system | Evidence |
|---|---:|---|---|
| FastAPI | 0.141.1 | HTTP, WebSocket, OpenAPI, static bundle | `pyproject.toml:15` |
| Pydantic | 2.13.0 | Request/domain contracts | `pyproject.toml:23` |
| OpenAI | 2.30.0 | OpenAI provider SDK | `pyproject.toml:21` |
| Anthropic | 0.88.0 | Anthropic provider SDK | `pyproject.toml:16` |
| google-genai | 2.7.0 | Gemini Interactions provider SDK | `pyproject.toml:18` |
| google-auth | 2.49.1 | Google OAuth credential refresh | `pyproject.toml:17` |
| HTTPX | 0.28.1 | Async HTTP transport | `pyproject.toml:19` |
| Uvicorn | 0.35.0 | ASGI server | `pyproject.toml:28` |

### Frontend

- React 19, React DOM 19, and React Router 7 implement the five-tab single-page dashboard.
- Vite 6 builds and serves the application; TypeScript 5.7 and ESLint 10 provide static checks.
- Vitest 3, jsdom, and Testing Library provide frontend tests. Lucide React supplies icons.

### Sandbox and infrastructure

- The sandbox image starts from Ubuntu 24.04 and contains an XFCE/X11 desktop, browser and desktop applications, VNC/noVNC, and a Python HTTP action service.
- Docker Compose binds VNC (5900), noVNC (6080), and the action service (9222) to loopback. It drops Linux capabilities, sets `no-new-privileges`, applies PID/memory/CPU/file-descriptor limits, and uses temporary filesystems for `/tmp` and `/var/run`.
- GitHub Actions runs Python quality/tests, frontend validation, container build, Trivy image scanning, and release packaging.

## 3) Development Toolchain

| Tool | Purpose | Evidence |
|---|---|---|
| uv + Hatchling | Locked Python install and package build | `uv.lock`; `pyproject.toml:1-3` |
| Ruff 0.14.4 | Python lint/format/import order | `pyproject.toml:40,42-59` |
| mypy 1.18.2 | Python static typing | `pyproject.toml:39,61-73` |
| pytest 9.0.3 + pytest-cov | Python tests and coverage | `pyproject.toml:31-38` |
| Vite + TypeScript + ESLint | Frontend build and static checks | `frontend/package.json:7-12,26-34` |
| Vitest + Testing Library | Browser component/protocol tests | `frontend/package.json:13-25,35` |
| GitHub Actions + Trivy | CI, audits, container scan, releases | `.github/workflows/ci.yml`; `.github/workflows/release.yml` |

- `uv.lock` is authoritative for Python installs; normal and CI installs use `uv sync --frozen`.
- `pyproject.toml` pins direct Python dependencies and development tools exactly.
- `frontend/package-lock.json` is authoritative for frontend installs; use `npm ci`.
- `requirements.txt` duplicates direct runtime dependencies for compatibility, but migration and CI documentation direct contributors to `uv`.

## 4) Key Commands

```powershell
uv sync --frozen
npm --prefix frontend ci
uv run --frozen python -m backend.main
uv run --frozen python dev.py
docker compose up -d --build
uv run pytest -p no:warnings --tb=short
uv run ruff check .
uv run ruff format --check .
uv run mypy
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run test:run
npm --prefix frontend run build
```

## 5) Environment and Config

- Provider credentials: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and `GOOGLE_API_KEY`/`GEMINI_API_KEY`.
- Backend/state: `HOST`, `PORT`, `CUA_V2_DB_PATH`, `CUA_V2_FRAME_PATH`, and optional `CUA_FRONTEND_DIST`.
- Sandbox: `AGENT_SERVICE_TOKEN`, `VNC_PASSWORD`, `CONTAINER_NAME`, agent-service host/port, screen geometry, step count, and timeout.
- Google OAuth uses `GOOGLE_OAUTH_CLIENT_ID` plus `GOOGLE_OAUTH_CLIENT_SECRET` (or `GOOGLE_OAUTH_CLIENT_SECRET_FILE`), with optional project and redirect settings.
- External binding is opt-in through `CUA_ALLOW_PUBLIC_BIND` plus `CUA_API_TOKEN`; deployment documentation additionally requires an authenticated TLS reverse proxy.
- Logging uses `LOG_FORMAT`, `LOG_LEVEL`, and `DEBUG`. Hot reload is separately controlled by `CUA_RELOAD`.

## 6) Evidence

- `pyproject.toml:1-67` - package metadata, pinned dependencies, pytest, Ruff, and mypy configuration.
- `frontend/package.json:1-36` - frontend runtime, scripts, and dependency stack.
- `frontend/tsconfig.json:1-10` and `frontend/eslint.config.js:1-27` - strict TypeScript and lint configuration.
- `docker/Dockerfile:1-27,213,237-252` - Ubuntu base, non-root runtime, ports, health check, and entrypoint.
- `docker-compose.yml:1-73` - sandbox networking and resource/security constraints.
- `.github/workflows/ci.yml:1-91` and `.github/workflows/release.yml:1-45` - CI and release toolchain.
- `.env.example:1-45` and `docs/deployment.md:1-61` - supported configuration and deployment prerequisites.
