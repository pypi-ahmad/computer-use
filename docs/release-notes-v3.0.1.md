# Computer Use Workbench v3.0.1

Released 2026-08-13.

## Highlights

- Makes the Windows local launcher wait for backend health before opening the dashboard.
- Binds the Vite dev server to `127.0.0.1` so the address matches the URL the launcher opens.
- Starts Vite through Node on Windows instead of the interactive `npm.cmd` wrapper.
- Rebuilds esbuild during Windows setup so the frontend build tool is ready after `npm ci`.

This is a patch on v3.0.0. Supported Computer Use routes, credential handling, and API contracts are unchanged.

Review `CHANGELOG.md` and `USAGE.md` before upgrading an existing deployment.
