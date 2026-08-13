# Security Policy

## Supported Versions

Security fixes are developed for the current default branch and the active 3.x
release line.

| Version | Supported |
|---|---|
| `main` / latest 3.x | Yes |
| 2.x and earlier | No |

The application is a local, single-user workbench. It is not a hardened
multi-tenant service; follow the deployment and network boundaries in
[USAGE.md](USAGE.md) and [TECHNICAL.md](TECHNICAL.md).

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

Reports about upstream provider services should be sent to that provider unless
the issue is caused by this repository's integration. Ordinary bugs and feature
requests belong in GitHub Issues.

## Research and Disclosure Expectations

- Test only systems and accounts you own or are authorized to assess.
- Minimize access to data and stop once the issue is demonstrated.
- Do not disrupt services, retain private data, or attempt social engineering.
- Allow time to validate and remediate the issue before public disclosure.
- Coordinate publication timing and credit with the maintainer.

## Response Process

The maintainer will acknowledge the report as capacity allows, validate its
scope and severity, request missing information when necessary, and coordinate
a fix and verification. Confirmed issues will be disclosed through release
notes, `CHANGELOG.md`, or a GitHub security advisory when remediation is ready.
No fixed response or remediation deadline is promised.
