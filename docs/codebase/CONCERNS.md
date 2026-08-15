# Codebase Concerns

## 1) Top Risks (Prioritized)

| Severity | Concern | Impact | Suggested action |
|---|---|---|---|
| high outside localhost | shared workbench token in URLs for WebSocket/noVNC | token may enter browser history or proxy logs | retain loopback default and use TLS/proxy controls for external access |
| medium | process-local runtime state | one worker only; restarts discard credentials and active runs | retain the local-workbench boundary unless product scope changes |
| medium | shared desktop sandbox | concurrent sessions can contend for one desktop | keep concurrency limits and test multi-session behavior before expanding scope |
| medium | SQLite history has no automatic database retention | long-lived installations can grow indefinitely | export/backup regularly; add database-retention policy only with a product requirement |
| medium | large transport modules | provider/API changes can regress server or engine behavior | make focused, contract-tested changes rather than broad refactors |

## 2) Resolved Concerns

- The v2 dashboard can now approve or deny nonce-bound safety prompts through
  `/api/v2/sessions/{id}/safety-decisions`.
- `CUA_API_TOKEN` now gates REST, WebSocket, and noVNC access when configured;
  `CUA_WS_TOKEN` remains only as a deprecated compatibility fallback.
- Confirmed v2 actions are persisted during execution, avoiding the former
  post-run-only journal gap.
- Audit-frame retention has preview/prune controls; SQLite record retention
  remains a separate operational concern.

## 3) Security and Privacy Watchpoints

- Model actions are untrusted. Keep the Docker socket and host filesystem out
  of the sandbox and retain loopback binding, dropped capabilities, non-root
  execution, and action allowlists.
- Provider screenshots and attached documents leave the host when sent to the
  selected provider; “local” describes orchestration and storage defaults, not
  model inference.
- Session cost (`/cost`) multiplies recorded token totals by list rates in
  `frontend/src/pricing.ts`. It is an estimate, not a provider invoice.
  Batch, cache, and long-context multipliers are not applied because those
  meters are not stored.
- API-key and Google OAuth credential sessions are process-local and
  non-recoverable by design. Do not add them to SQLite, logs, action payloads,
  or checkpoints.
- Google OAuth is intentionally limited to the configured redirect URI and a
  short-lived, PKCE-bound authorization state.

## 4) Technical Debt and Change Discipline

- `requirements.txt` duplicates direct runtime dependencies from
  `pyproject.toml`; keep the lockfile/`uv` workflow authoritative.
- The v2 platform deliberately bridges the legacy `AgentLoop`; changes to the
  shared runtime affect both API generations.
- `backend/server/__init__.py` and provider engines remain high-churn
  boundaries. Update behavior tests and public documentation in the same
  change.

## 5) Evidence

- `backend/main.py` and `backend/server/__init__.py` - public-bind and
  workbench-token enforcement.
- `backend/v2/api.py`, `credentials.py`, `orchestrator.py`, and `retention.py`
  - v2 authentication, safety, persistence, and frame retention.
- `frontend/src/App.tsx`, `frontend/src/api.ts`, and
  `frontend/src/pricing.ts` - provider, safety, workbench-token, and
  cost-estimate user flows.
- `TECHNICAL.md` and `docs/deployment.md` - supported deployment boundary.
