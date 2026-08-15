# Contributing

Contributions are always welcome — clones, forks, testers, issues, docs,
and pull requests. First-time and issue-only help counts. Use this
workbench on your own machine with your own API keys. Keep changes
focused, reviewable, and backed by tests or documentation that show the
observable result.

You do not need to send money. This project does not take donations,
sponsorship, or paid support. A test note, a bug, an idea, or a PR is
enough.

You are solely responsible for data you run through a clone (PDFs and
other uploads, sandbox files, keys). See [DATA.md](DATA.md).

## Before You Start

- Use GitHub Issues for reproducible bugs and feature proposals. Bug
  reports: [.github/ISSUE_TEMPLATE/bug.yml](.github/ISSUE_TEMPLATE/bug.yml).
  Ideas: [.github/ISSUE_TEMPLATE/feature.yml](.github/ISSUE_TEMPLATE/feature.yml).
  An issue-only contribution (no PR) is welcome.
- How to test a running install and the offline CI commands:
  [TESTING.md](TESTING.md).
- How to ask for help: [SUPPORT.md](SUPPORT.md).
- What data stays on the operator machine: [DATA.md](DATA.md).
- Do not disclose vulnerabilities or credentials in an issue. Follow
  [SECURITY.md](SECURITY.md) instead.
- By participating, you agree to [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
- The project is MIT-licensed ([LICENSE](LICENSE)).

## Development Setup

The supported development environment is Python 3.12–3.14, Node.js 22+, Docker,
and [`uv`](https://docs.astral.sh/uv/).

```powershell
git clone https://github.com/pypi-ahmad/computer-use.git
cd computer-use
Copy-Item .env.example .env
# Set AGENT_SERVICE_TOKEN and VNC_PASSWORD. Optional: OPENAI_API_KEY,
# ANTHROPIC_API_KEY. Prefer a process-level GOOGLE_API_KEY — dotenv
# does not override it (backend/infra/config.py).
uv sync --frozen
Set-Location frontend
npm ci
Set-Location ..
```

On Windows, `run.cmd` is the one-file installer and daily launcher
(installs missing host tools, then `dev.py --open-browser`). `START.bat`
always runs `setup.bat --bootstrap-only` first. `dev.py` runs
`docker compose up -d --wait --wait-timeout 90`, waits for
`GET /api/health`, starts Vite on `127.0.0.1:8505` (through Node on
Windows), and with `--open-browser` opens that URL once Vite responds.
For normal development keep `uv run python dev.py` in the terminal.

Never commit `.env`, `data/`, or live desktop screenshots. Live provider
tests are opt-in (`pytest -m integration`) and must use credentials from
the local environment. CI does not use real provider keys.

## Making a Change

1. Fork the repository and create a branch from `main`.
2. Keep the branch limited to one logical change; avoid unrelated refactors.
3. Add or update tests for behavior changes.
4. Update operator, technical, and changelog documentation when a public
   contract, configuration variable, model, or workflow changes. Put
   user-visible work under `## [Unreleased]` in `CHANGELOG.md`.
5. Model or route changes must update
   `backend/models/computer_use_models.v2.json` and
   `backend/models/allowed_models.json`, cite current first-party docs
   in the pull request, and include offline contract coverage. Do not
   make CI depend on real provider credentials.
6. Regenerate the standalone handbook after changing one of its source
   files (`scripts/build_handbook_site.py` `SOURCES`, including README,
   USAGE, TECHNICAL, SECURITY, and `docs/`):

   ```powershell
   uv run python scripts/build_handbook_site.py
   uv run python scripts/build_handbook_site.py --check
   ```

## Validation

Run the narrowest tests while iterating, then run the relevant CI-equivalent
checks before opening a pull request.

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -p no:warnings --tb=short --cov=backend --cov-report=term-missing --cov-fail-under=60
uv run pytest -p no:warnings --tb=short -o addopts='' evals/
uv run pip-audit

Set-Location frontend
npm run lint
npm run typecheck
npm run test:run
npm run build
npm audit --audit-level=high
```

These commands match `.github/workflows/ci.yml`. Docker image construction
and the blocking HIGH/CRITICAL Trivy scan also run in CI. If your change
touches the sandbox or `docker-compose.yml`, also verify
`docker compose build` locally.

## Pull Requests

Use a clear title and explain the user-visible result, implementation approach,
and verification performed. Link the related issue when one exists.

- [ ] The change is scoped to one logical concern.
- [ ] Tests cover the changed behavior.
- [ ] Documentation and `CHANGELOG.md` are updated where applicable.
- [ ] Generated handbook output is current where applicable.
- [ ] Relevant local checks pass.
- [ ] No secrets, tokens, private data, or generated credential files are
      committed.
