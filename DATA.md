# Data responsibility

You operate this workbench. The project does not host your sessions,
keys, or sandbox desktop. You are responsible for what the agent sees
and does.

Computer Use can execute destructive actions. README requires test
accounts and non-sensitive data. This repo does not make model actions
safe by itself.

## What stays on your machine

| Data | Default location | Who writes it |
|---|---|---|
| Sessions, actions, events, metrics, workflow versions | `data/computer-use-v2.sqlite3` (`CUA_V2_DB_PATH`) | v2 SQLite WAL store |
| Audit screenshots | `data/audit-frames` (`CUA_V2_FRAME_PATH`) | filesystem store, 7-day or 1 GiB eviction |
| Sandbox secrets | repo-root `.env` | you, or `run.cmd` generating `AGENT_SERVICE_TOKEN` / `VNC_PASSWORD` |
| Provider API keys and Google OAuth tokens | process memory only | Providers tab credential session |

`.env` is gitignored. Never commit it.

SQLite history has no automatic eviction. USAGE.md: stop the backend,
then delete the database plus `-wal`/`-shm`, and delete
`data/audit-frames` if you also want the screenshots gone.

## What leaves your machine

- API keys are sent only to the chosen provider endpoint (OpenAI,
  Anthropic, or Google). They are not written to the audit database or
  log lines.
- Screenshots leave the host when the Computer Use loop sends them to
  that provider. Retained audit frames under `CUA_V2_FRAME_PATH` stay
  local.
- Uploaded reference files (non-Gemini routes) go to the provider
  Files API / vector store only when a session attaches them.
- Gemini File Search cannot be combined with Computer Use here;
  attaching files with a Gemini model fails at session start.

Treat anything the model can see in the XFCE sandbox (browser sessions,
files, VNC) as data you chose to expose to the provider.

## Network and bind

Default bind is `127.0.0.1`. Binding off loopback requires both
`CUA_ALLOW_PUBLIC_BIND=1` and `CUA_API_TOKEN`. Without both, the
process refuses to start.

The sandbox container is `cua-environment` (`cua-ubuntu:latest`). It
is isolated Ubuntu/XFCE, not a guarantee that the model cannot reach
the network or change data inside that desktop.

## Your obligations

- Use throwaway or test accounts inside the sandbox.
- Do not put production credentials, health, financial, or government
  records in the task, the sandbox, or attached files unless you accept
  sending them to the selected provider.
- Keep `AGENT_SERVICE_TOKEN` and `VNC_PASSWORD` secret on the host.
- If you share a machine, treat `data/` and `.env` as private.
- You decide when to prune or back up `data/computer-use-v2.sqlite3`
  and `data/audit-frames` (see docs/deployment.md).

The MIT license ([LICENSE](LICENSE)) provides the software as-is, with
no warranty.
