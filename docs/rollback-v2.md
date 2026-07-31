# v2 Rollback Runbook

## Triggers

Rollback on data-integrity failure, unsafe action replay, credential exposure, inability to stop a session, error rate above twice baseline, or p95 latency more than 50% above baseline.

## Procedure

1. Stop accepting new sessions and request stop for active sessions.
2. Preserve the v2 SQLite database, WAL/SHM files, frame directory, and structured logs for diagnosis.
3. Stop FastAPI and the sandbox with `docker compose down` (do not add `-v`).
4. Check out or redeploy the previously verified tag and restore its matching lockfiles and frontend bundle.
5. Start the previous sandbox and backend, then verify health, readiness, screenshot capture, safety confirmation, and one disposable direct-provider session.
6. Keep the v2 database archived. v1 must not open or modify it; no automatic down-migration is provided.

Rollback is a code deployment rollback, not a destructive data migration.
