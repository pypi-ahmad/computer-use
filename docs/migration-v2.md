# Migrating from v1 to v2

v2 is an intentional API and WebSocket break. There is no compatibility shim.

1. Back up v1 traces and any uploaded artifacts. They are not imported into SQLite automatically.
2. Replace `requirements.txt` installation with `uv sync --frozen`.
3. Replace hard-coded provider/model dropdowns with `GET /api/v2/models` and `GET /api/v2/provider-routes`.
4. Create an ephemeral API-key credential session, configure environment
   credentials, or start the Google OAuth credential flow; do not send inline
   `api_key` in session requests.
5. Replace v1 start/status endpoints with `/api/v2/sessions`,
   `/api/v2/sessions/{id}/safety-decisions`, and cursor-paginated
   action/event/metric endpoints.
6. Update WebSocket clients to parse JSON control events and binary `CUAF`
   preview frames at `/api/v2/ws/{session_id}`. Supply `CUA_API_TOKEN` when
   workbench authentication is enabled.
7. Select an explicit primary route and ordered fallbacks. The server will not choose dynamically by price or latency.
8. Replace all model selections with the three supported logical IDs:
   `gpt-5.6-luna`, `claude-sonnet-5`, and `gemini-3.6-flash`.

Before deleting v1 state, run the v2 contract tests, create and delete a
disposable session, verify approve/deny safety confirmation, export an audit
trail, and preview/prune retained audit frames.
