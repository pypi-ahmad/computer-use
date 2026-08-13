# Computer Use Workbench v3.0.2

Released 2026-08-13.

## Highlights

- Clears the Python quality, frontend lint, and sandbox image-scan gates that blocked v3.0.1 publication.
- Keeps the Windows launcher fixes from v3.0.1: backend health before opening the dashboard, Vite on `127.0.0.1`, Node-spawned Vite, and esbuild rebuild during setup.
- The sandbox image installs only a venv for `agent_service`. It no longer copies the host FastAPI/Pillow stack.

Supported Computer Use routes, credential handling, and API contracts are unchanged from v3.0.1.

Review `CHANGELOG.md` and `USAGE.md` before upgrading an existing deployment.
