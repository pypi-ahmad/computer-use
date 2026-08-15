# Support

This repository is open source (MIT). Clone it, run it on your own
machine, and use your own API keys. There is no hosted product, no SLA,
and no multi-tenant support desk.

Files and other data you put into a local clone (PDFs, documents,
sandbox contents) are your responsibility only. See [DATA.md](DATA.md).

## First, read these

| Question | File |
|---|---|
| Install, dashboard, credentials, troubleshooting | [USAGE.md](USAGE.md) |
| How to verify an install | [TESTING.md](TESTING.md) |
| What is stored on disk and who owns it | [DATA.md](DATA.md) |
| Architecture and bind/auth rules | [TECHNICAL.md](TECHNICAL.md) |
| Deploy on a workstation | [docs/deployment.md](docs/deployment.md) |
| Security / vulnerability reports | [SECURITY.md](SECURITY.md) |

Maintainer contact already used by this project: `pypi.ahmad@gmail.com`.

- Security: subject `[computer-use security]` — see SECURITY.md.
- Conduct: subject `[computer-use conduct]` — see CODE_OF_CONDUCT.md.

Do not email API keys, `AGENT_SERVICE_TOKEN`, `VNC_PASSWORD`,
`CUA_API_TOKEN`, OAuth secrets, or live desktop screenshots.

## How to ask for help

1. Search existing GitHub Issues.
2. Confirm the problem still happens after USAGE.md troubleshooting.
3. Open an issue with the [bug template](.github/ISSUE_TEMPLATE/bug.yml).

Include version (`v3.1.0` or the commit SHA), OS, whether you used
`run.cmd` / `dev.py` / `http://127.0.0.1:8100`, and redacted logs.

## Out of scope for this repo

- Bugs in OpenAI, Anthropic, or Google APIs or quotas — report those
  to the provider unless this repo's integration is at fault.
- Help running the workbench as a public multi-tenant service. README
  and deployment.md say not to do that.
- Recovering data you deleted from `data/computer-use-v2.sqlite3` or
  `data/audit-frames`.
- Actions the Computer Use model took inside the sandbox. The operator
  owns those accounts and that data. See DATA.md.

## Contributing a fix

Use [CONTRIBUTING.md](CONTRIBUTING.md). The software is MIT-licensed
([LICENSE](LICENSE)).
