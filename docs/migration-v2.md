# Migrating from v1 to v2

v2 is an intentional API and WebSocket break. There is no compatibility shim.

1. Back up v1 traces and any uploaded artifacts. They are not imported into SQLite automatically.
2. Replace `requirements.txt` installation with `uv sync --frozen`.
3. Replace hard-coded provider/model dropdowns with `GET /api/v2/models` and `GET /api/v2/provider-routes`.
4. Create an ephemeral credential session or configure environment credentials; do not send inline `api_key` in session requests.
5. Replace v1 start/status endpoints with `/api/v2/sessions` and cursor-paginated action/event/metric endpoints.
6. Update WebSocket clients to parse JSON control events and binary `CUAF` preview frames.
7. Select an explicit primary route and ordered fallbacks. The server will not choose dynamically by price or latency.
8. Remove deprecated model IDs listed in the research audit.

Before deleting v1 state, run the v2 contract tests, create and delete a disposable session, verify safety confirmation, and export an audit trail.
