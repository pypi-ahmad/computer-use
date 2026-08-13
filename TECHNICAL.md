# Technical Architecture — v3.0.3

## Runtime boundary

The application targets a trusted, single-user workstation. FastAPI runs as
one process because the credential vault, active execution handles, WebSocket
subscribers, frame broker, and circuit breaker are process-local. SQLite uses
WAL for durable v2 records. The Docker sandbox is the only component allowed
to execute OS input.

On Windows, `START.bat` is the one-click entry point. It delegates prerequisite
and dependency setup to `setup.bat` (including `npm rebuild esbuild` after a
fresh `npm ci`), then starts `dev.py --open-browser`. `dev.py` starts the
backend first and waits for `GET /api/health` before launching Vite. On
Windows it starts Vite through `node .../vite/bin/vite.js` rather than
`npm.cmd`. Vite binds `127.0.0.1` on port `8505` by default. The dashboard
opener probes backend health, then opens `http://127.0.0.1:8505`. Shutdown is
forwarded to both children. The normal production path builds `frontend/dist`
and serves it from FastAPI instead.

## Model catalog and request path

The catalog exposes exactly three executable, provider-native routes:

| Model | Route | Transport |
|---|---|---|
| Gemini 3.6 Flash | `gemini-direct` | Google Interactions Computer Use |
| Claude Sonnet 5 | `anthropic-direct` | Anthropic Messages Computer Use |
| GPT-5.6 Luna | `openai-direct` | OpenAI Responses Computer Use |

1. `POST /api/v2/sessions` validates the selected model, compatible primary
   route, ordered explicit fallbacks, attached files, and runtime options.
2. The coordinator executes the primary route followed only by the supplied
   fallbacks; it never makes cost- or latency-based routing decisions.
3. Transport adapters validate provider output and convert it to canonical
   actions. Gemini continues with `previous_interaction_id`; Claude and OpenAI
   use their provider-native computer tools.
4. `provider_default`, `confirm_mutating`, and `read_only` safety policies
   govern execution. Pending decisions are delivered with a nonce and answered
   through `POST /api/v2/sessions/{id}/safety-decisions`.
5. Confirmed actions, events, and metrics are journalled. The sandbox executes
   idempotent action IDs; ambiguous execution is never replayed automatically.

Built-in search is opt-in. File attachments are validated for every selected
route before a session starts; Gemini Computer Use sessions reject attachments.

## Frames and WebSockets

One frame broker coalesces screenshot demand. The canonical full frame remains
the model input; browser previews are compressed WebP/JPEG and sent as binary
`CUAF` frames with version, codec, sequence, dimensions, and timestamp. The
v2 stream at `/api/v2/ws/{session_id}` sends JSON lifecycle, safety, routing,
metric, and log events alongside frames. Slow clients keep only the latest
pending frame.

## Persistence, audit, and retention

`CUA_V2_DB_PATH` defaults to `data/computer-use-v2.sqlite3`. Audit image bytes
are stored outside SQLite under `CUA_V2_FRAME_PATH`, which defaults to
`data/audit-frames`; they are referenced by hash and metadata. Retained frames
are bounded to seven days or one GiB by default. Sessions may opt out of frame
retention, and deleting a session purges its retained frames.

The v2 API exposes per-session actions, events, and metrics; aggregate
analytics and diagnostics; a ZIP session export with optional frames; and
retention preview/prune endpoints. Versioned workflows can be created,
compiled with variables, and used to prefill a live session.

## Credentials and workbench authentication

OpenAI, Anthropic, and Google support API keys from environment variables or
an ephemeral credential session. Credential-session responses contain only
readiness metadata; secrets are never returned or persisted. Sessions are
process-local and expire after at most eight hours.

Google additionally supports OAuth: the v2 API starts a state- and PKCE-bound
authorization flow, exchanges the callback code, and retains refreshable
credentials only in the process-local vault. Configure
`GOOGLE_OAUTH_CLIENT_ID` plus `GOOGLE_OAUTH_CLIENT_SECRET` (or
`GOOGLE_OAUTH_CLIENT_SECRET_FILE`); `GOOGLE_CLOUD_PROJECT` and
`CUA_GOOGLE_OAUTH_REDIRECT_URI` are optional.

When `CUA_API_TOKEN` is set, it protects sensitive and state-changing REST API
requests, both WebSocket surfaces, and noVNC. HTTP clients send
`X-CUA-Token`; browser WebSocket and noVNC clients use a `token` query
parameter. `CUA_WS_TOKEN` remains a deprecated fallback. The Google OAuth
callback is the sole mutating API authentication exception because it is bound
to short-lived OAuth state and PKCE verification. Read-only discovery and
health surfaces are not a substitute for a network access-control boundary.

## Production frontend

After `frontend/dist/index.html` exists, FastAPI mounts the bundle last so API
and WebSocket routes retain precedence. The dashboard routes (`/audit`,
`/workflows`, `/providers`, and `/analytics`) fall back to `index.html`; unknown
paths and API/VNC paths remain 404. A missing bundle is non-fatal during
development. Override the bundle location with `CUA_FRONTEND_DIST`.

## Public contracts

The typed v2 surface lives under `/api/v2`. Existing v1 REST and WebSocket
endpoints remain available for compatibility and for features that the v2
dashboard does not yet expose, but new integrations should target v2. JSON uses
camelCase and upper-snake event enums. Errors contain `code`, `message`,
`details`, `isRetryable`, and `requestId`; list endpoints use cursor
pagination. The model and provider-route endpoints expose the supported
catalog, readiness, auth mode, and circuit state. See the live OpenAPI document
at `/docs` and `USAGE.md` for operator examples.

## Quality gates

The lockfile is authoritative. CI installs with `uv sync --frozen` and
`npm ci`, then blocks on static analysis, tests, evals, builds, dependency
audits, and image scanning. Live SDK tests are opt-in because CI must not
receive production provider credentials. The exact contributor commands and
pull-request checklist live in `CONTRIBUTING.md`.
