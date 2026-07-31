# Technical Architecture — v2.0.0

## Runtime boundary

The application targets a trusted, single-user workstation. FastAPI runs as one process because the credential vault, active task handles, WebSocket clients, and circuit breaker are process-local. SQLite uses WAL for durable domain records. The Docker sandbox is the only component allowed to execute OS input.

## Request path

1. `POST /api/v2/sessions` validates a logical model and an explicit primary route.
2. The coordinator constructs the primary plus ordered fallbacks. It never performs cost- or latency-based dynamic routing.
3. A transport adapter validates provider output and converts it to canonical actions.
4. Confirmed actions are journalled individually. A provider-neutral checkpoint contains only goal, confirmed progress, frame reference, and safety state; vendor reasoning is never translated.
5. The sandbox executes idempotent action IDs. Ambiguous execution is not replayed automatically.

Direct OpenAI, Anthropic, and Google transports execute in v2.0.0. Azure, Bedrock, and Vertex routes are visible for configuration and compatibility inspection but report `isExecutable: false` until a verified transport bridge is shipped.

## Frames and WebSockets

One frame broker coalesces screenshot demand. The canonical full frame remains the model input; capability-gated ROI images supplement it. Browser previews are compressed WebP/JPEG and sent as binary `CUAF` frames with version, codec, sequence, dimensions, and timestamp. Control, lifecycle, safety, routing, metric, and log events remain JSON. Slow clients keep only the latest pending frame.

## Persistence and retention

`CUA_V2_DB_PATH` defaults to `data/computer-use-v2.sqlite3`. Audit image bytes stay outside SQLite and are referenced by hash and metadata. Default eviction is seven days or one GiB. Sessions may opt out, and deleting a terminal session purges retained frames.

## Credentials

Direct vendor keys may come from environment variables or an ephemeral credential session. Credential-session responses contain readiness metadata only; secrets are never returned or persisted. AWS uses its default credential chain, Google uses ADC, and Azure uses Entra `DefaultAzureCredential` with API-key fallback when their execution bridges become available.

## Production frontend

After `frontend/dist/index.html` exists, FastAPI mounts the bundle last. API and WebSocket routes therefore retain precedence, client-side dashboard routes fall back to `index.html`, and a missing bundle is non-fatal during development. Override the location with `CUA_FRONTEND_DIST`.

## Public contracts

The clean v2 surface lives under `/api/v2`; v1 request/response compatibility is not promised. JSON uses camelCase and upper-snake enums. Errors contain `code`, `message`, `details`, `isRetryable`, and `requestId`. Lists use cursor pagination. See the live OpenAPI document at `/docs`.

## Quality gates

The lockfile is authoritative. CI installs with `uv sync --frozen` and `npm ci`, then blocks on static analysis, tests, evals, builds, dependency audits, and image scanning. Live SDK tests are opt-in because CI must not receive production provider credentials.
