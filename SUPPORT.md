# Support and community

This repository is open source (MIT). Clone it, run it on your own
machine, and use your own API keys. **Contributions are always
welcome** — testers, bug reports, ideas, docs, and pull requests.
First-time and issue-only help counts.

There is no hosted product, no SLA, and no multi-tenant support desk.

**Do not send money.** This project does not take donations, sponsorship,
bounties, or paid support. Time, test notes, issues, and patches are
the help that matters.

Files and other data you put into a local clone (PDFs, documents,
sandbox contents) are your responsibility only. See [DATA.md](DATA.md).

## How you can help

| You want to | Do this |
|---|---|
| Try the workbench | [TESTING.md](TESTING.md) smoke steps, then [USAGE.md](USAGE.md) |
| Report a defect | [Bug report](.github/ISSUE_TEMPLATE/bug.yml) |
| Suggest a feature or improvement | [Feature or improvement](.github/ISSUE_TEMPLATE/feature.yml) |
| Send a fix or small change | [CONTRIBUTING.md](CONTRIBUTING.md) and a PR against `main` |
| Report a vulnerability | [SECURITY.md](SECURITY.md) — email, not a public issue |

Search existing [GitHub Issues](https://github.com/pypi-ahmad/computer-use/issues)
before opening a new one.

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
`CUA_API_TOKEN`, OAuth secrets, live desktop screenshots, or payment
offers.

## How to file a report

1. Search existing GitHub Issues.
2. Confirm the problem still happens after USAGE.md troubleshooting.
3. Open an issue with the matching template (bug or idea).

Include version (`v3.1.0` or the commit SHA), OS, whether you used
`run.cmd` / `dev.py` / `http://127.0.0.1:8100`, and redacted logs.

A missing provider key, an `OPEN` circuit after repeated provider
failures, or `HOST != 127.0.0.1` without the public-bind pair are not
product bugs. See TESTING.md.

## Out of scope for this repo

- Bugs in OpenAI, Anthropic, or Google APIs or quotas — report those
  to the provider unless this repo's integration is at fault.
- Help running the workbench as a public multi-tenant service. README
  and deployment.md say not to do that.
- Recovering data you deleted from `data/computer-use-v2.sqlite3` or
  `data/audit-frames`.
- Actions the Computer Use model took inside the sandbox. The operator
  owns those accounts and that data. See DATA.md.
- Financial support, sponsorship, or paid consulting.

## Contributing a fix

Use [CONTRIBUTING.md](CONTRIBUTING.md). The software is MIT-licensed
([LICENSE](LICENSE)).
