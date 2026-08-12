# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added

- Exactly three provider-native Computer Use routes: GPT-5.6 Luna through
  OpenAI Responses, Claude Sonnet 5 through Anthropic Messages, and Gemini 3.6
  Flash through Google Interactions.
- Ephemeral API-key credential sessions for every provider and a state- and
  PKCE-bound Google OAuth flow. Credentials remain process-local and are never
  returned by the API.
- Optional `CUA_API_TOKEN` protection for sensitive REST operations,
  WebSockets, and noVNC, with `CUA_WS_TOKEN` retained as a deprecated fallback.
- Explicit safety policies and nonce-bound safety decisions, deterministic
  fallback routes, SQLite audit records, binary `CUAF` frame streaming, and
  bounded on-disk audit-frame retention.
- `START.bat`, which checks host prerequisites, installs missing project
  dependencies through `setup.bat`, starts the workbench, and opens the
  dashboard.
- A self-contained Zero to Hero handbook website with user, technical, and
  business learning tracks.
- A richer Ubuntu sandbox application set for desktop demos and evaluations.

### Changed

- The model catalog, UI, provider adapters, tests, and documentation now expose
  only the three supported direct routes. Removed cloud routes are no longer
  advertised as unavailable catalog entries.
- v1 REST and WebSocket endpoints remain available for compatibility and for
  features not yet exposed by the v2 dashboard; new typed contracts live under
  `/api/v2`.
- Provider-specific attachment validation now rejects incompatible route/file
  combinations before execution. Gemini Computer Use sessions reject reference
  files; OpenAI and Anthropic keep their documented provider-native flows.
- Anthropic computer-tool configuration is driven by model-registry metadata,
  and organization web-search readiness is probed and cached per API key.
- Gemini history pruning retains atomic tool-call/tool-response turns.
- Documentation now reflects the current launcher, credential, authentication,
  safety, retention, and provider behavior.

### Removed

- GPT-5.5 Pro, GPT-5.4 Nano, `computer-use-preview`, Gemini 2.5 Computer Use
  Preview, Gemini 3.x preview and Flash-Lite entries, retired Claude entries,
  OpenRouter, Azure OpenAI, Bedrock, and Vertex execution routes.
- Obsolete pricing, reasoning-default, and migration guidance tied to removed
  model identifiers.

### Security

- Non-loopback binding requires both explicit public-bind consent and a shared
  workbench token.
- Sensitive state-changing endpoints, interactive WebSockets, and the noVNC
  proxy share constant-time token validation.
- Provider secrets are redacted from responses and logs, kept out of SQLite,
  and removed when their process-local credential session expires.
- Ambiguous OS actions are not replayed automatically during provider failover.

## [2.0.0] - 2026-07-23

### Added

- Versioned Computer Use-only model catalog with transport-specific identifiers, coordinate spaces, limits, and lifecycle metadata.
- `/api/v2` sessions, credential sessions, provider readiness, workflows, analytics, cursor-paginated audit records, and structured errors.
- Deterministic primary/fallback routing, transient retry, per-route circuit breaking, and provider-neutral checkpoints.
- SQLite WAL session/action/event/metric persistence and bounded audit-frame retention.
- Coalesced frame capture, canonical full-frame inference input, ROI supplements, compressed previews, and binary `CUAF` WebSocket frames.
- Five-tab TypeScript dashboard for live execution, audit history, workflows, providers, and analytics.
- Locked uv project, Python 3.12-3.14 CI, strict frontend checks, dependency audits, image scanning, release packaging, deployment, migration, and rollback guides.

### Changed

- Production FastAPI serves the built React SPA when `frontend/dist` exists.
- Provider credentials use environment variables or non-persistent credential sessions with an eight-hour maximum lifetime.
- Provider selection is explicit and deterministic; there is no implicit price/latency router.
- Direct OpenAI, Anthropic, and Google routes execute. Azure OpenAI, Bedrock, Vertex Gemini, and Vertex Claude are visible but unavailable until verified execution bridges ship.

### Removed

- v1 request/WebSocket compatibility and inline `api_key` session payloads.
- OpenAI `computer-use-preview`, GPT-5.5 Pro, GPT-5.4 Nano, Gemini 2.5 Computer Use Preview, Gemini 3.5 Flash-Lite, and retired/non-CU Claude entries.
- OpenRouter integration; its documented API does not expose a vendor-native Computer Use protocol.

### Security

- Secrets are never persisted or returned, uncertain OS actions are not replayed during failover, and release CI blocks on high/critical dependency and image findings.
