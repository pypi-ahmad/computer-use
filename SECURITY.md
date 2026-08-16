# Security Policy

## Supported Versions

Security fixes are developed for the current default branch and the
active 3.x release line (current: **3.2.0**).

| Version | Supported |
|---|---|
| `main` / latest 3.x | Yes |
| 2.x and earlier | No |

This application is a **local, single-user workbench**. It is not a
hardened multi-tenant service. Follow the bind and network rules in
[USAGE.md](USAGE.md) and [TECHNICAL.md](TECHNICAL.md). Operator-owned
data (uploads, sandbox files, SQLite, keys) is described in
[DATA.md](DATA.md). In-container action surface:
[docker/SECURITY_NOTES.md](docker/SECURITY_NOTES.md).

## Workbench security model

Facts from the current tree. Do not treat this list as a guarantee that
the model cannot do harm inside the sandbox.

**Bind and workbench token**

- Default `HOST` is `127.0.0.1`. `backend/main.py` exits **2** when
  `HOST` is not loopback unless both `CUA_ALLOW_PUBLIC_BIND=1` and
  `CUA_API_TOKEN` (or deprecated `CUA_WS_TOKEN`) are set.
- When `CUA_API_TOKEN` is set, `hmac.compare_digest` gates `/api/*`
  (reads and writes) except the Google OAuth callback, plus `/ws`,
  `/api/v2/ws/*`, and `/vnc/websockify`. HTTP: `X-CUA-Token` or
  `?token=`. Browser WebSockets and noVNC: `token` query.
- Loopback with no token is **default-open**.
- Query tokens can appear in proxy logs and browser history. Do not
  expose `8100`, `6080`, `9222`, or `5900` on a network you do not
  trust. For non-loopback use, [docs/deployment.md](docs/deployment.md)
  also requires an independently authenticated TLS reverse proxy.

**Sandbox**

- `AGENT_SERVICE_TOKEN` is required between the backend and
  `docker/agent_service.py` (`X-Agent-Token`, `hmac.compare_digest`).
  A mismatch yields screenshot `401`.
- x11vnc starts with `-nopw` (`docker/entrypoint.sh`). Compose does not
  pass `VNC_PASSWORD`. `GET /api/v2/desktop` returns
  `/vnc/vnc.html?autoconnect=1&reconnect=1&resize=scale&path=vnc/websockify`
  with no `password=`. `desktopViewerSrc()` in `frontend/src/api.ts`
  strips leftover `password` and `token` query params. When
  `CUA_API_TOKEN` is set, the workbench token is placed on the
  websockify `path` only (`vnc/websockify?token=…`).
- Default action set is the executor mapping. `CUA_ENABLE_LEGACY_ACTIONS=1`
  re-enables shell/clipboard/window handlers. Do not enable that off
  loopback.
- Optional `CUA_ALLOWED_NAV_HOSTS` restricts `navigate` / `open_url`.
- The container can still open outbound connections from inside XFCE.
  Isolation is not a review of model actions.

**Secrets and audit**

- `backend/infra/config.py` snapshots `GOOGLE_API_KEY`, `GEMINI_API_KEY`,
  `OPENAI_API_KEY`, and `ANTHROPIC_API_KEY` from the process environment
  into `_USER_ENV` **before** `load_dotenv(..., override=False)`.
  `resolve_api_key()` order: UI key, then `_USER_ENV` (Google:
  `GOOGLE_API_KEY` then `GEMINI_API_KEY`), then values dotenv loaded
  into the process. A user-env `GOOGLE_API_KEY` wins over a repo-root
  `.env` assignment. v2 session start uses a credential-session secret
  when `credentialSessionId` is set; otherwise it calls
  `resolve_api_key()`.
- Providers-tab keys and Google OAuth tokens live only in process
  memory and expire within **8 hours** (28 800 s, `backend/v2/credentials.py`).
  They are not written to SQLite.
- Safety prompts carry a nonce; decisions use
  `POST /api/v2/sessions/{id}/safety-decisions` (dashboard) or
  `POST /api/agent/safety-confirm` (v1). Comparison is
  `hmac.compare_digest`. Unanswered prompts auto-deny after **60 s**
  (`backend/loop.py`).
- Ambiguous OS actions are not replayed or failed over.

**Host outbound fetch (web-search planning)**

- Live toggle `useBuiltinSearch` runs `backend/providers/planner.py`.
  That planner extracts or asks the selected provider for at most 3
  public `http(s)` URLs, then `backend/infra/mcp_fetch.py` fetches
  them through `uvx mcp-server-fetch` (override `CUA_MCP_FETCH_CMD`).
  This is URL fetch, not a search index. The Computer Use loop stays
  computer-only and does not attach provider `web_search` / Google
  Search tools.
- `_is_public_http_url` skips non-`http(s)`, `localhost`, `*.local`,
  hostnames without a dot, and non-global IP literals. Remaining
  hostnames are fetched from the **host** process, not the sandbox.
  That is not a complete SSRF guarantee (DNS names with a dot pass).
- Cap: 3 pages, 4000 characters each. MCP spawn or fetch failure
  returns an empty page list; the run continues without a brief.

**Out of scope for this policy**

- Provider-side quota, billing, or API bugs (report those to OpenAI,
  Anthropic, or Google unless this repo's integration is at fault).
- Recovering data the operator deleted from `data/`.
- Making Computer Use “safe” for production credentials. README
  requires test accounts and non-sensitive data.

## Reporting a Vulnerability

Email `pypi.ahmad@gmail.com` with the subject `[computer-use security]`.
Do not open a public GitHub issue or discussion containing vulnerability
details, credentials, tokens, screenshots, or exploit code.

Include, when available:

- the affected version, commit, endpoint, or component;
- a description of the impact and required attacker access;
- minimal reproduction steps or a proof of concept using dummy data;
- relevant logs with secrets and personal information removed; and
- any suggested remediation or disclosure constraints.

Reports about upstream provider services should be sent to that provider
unless the issue is caused by this repository's integration. Ordinary
bugs and feature requests belong in GitHub Issues
([.github/ISSUE_TEMPLATE/bug.yml](.github/ISSUE_TEMPLATE/bug.yml)).

## Research and Disclosure Expectations

- Test only systems and accounts you own or are authorized to assess.
- Minimize access to data and stop once the issue is demonstrated.
- Do not disrupt services, retain private data, or attempt social
  engineering.
- Allow time to validate and remediate the issue before public
  disclosure.
- Coordinate publication timing and credit with the maintainer.

## Response Process

The maintainer will acknowledge the report as capacity allows, validate
its scope and severity, request missing information when necessary, and
coordinate a fix and verification. Confirmed issues will be disclosed
through release notes, `CHANGELOG.md`, or a GitHub security advisory
when remediation is ready. No fixed response or remediation deadline is
promised. There is no paid bug bounty.
