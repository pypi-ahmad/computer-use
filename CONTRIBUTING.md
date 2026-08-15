# Contributing

Clones, forks, and pull requests are welcome. Use this workbench on your
own machine with your own API keys. Contributions should be focused,
reviewable, and backed by tests or documentation that demonstrate the
observable result.

You are solely responsible for data you run through a clone (PDFs and
other uploads, sandbox files, keys). See [DATA.md](DATA.md).

## Before You Start

- Use GitHub Issues for reproducible bugs and feature proposals. Bug
  reports should use [.github/ISSUE_TEMPLATE/bug.yml](.github/ISSUE_TEMPLATE/bug.yml).
- How to test a running install and the offline CI commands:
  [TESTING.md](TESTING.md).
- How to ask for help: [SUPPORT.md](SUPPORT.md).
- What data stays on the operator machine: [DATA.md](DATA.md).
- Do not disclose vulnerabilities or credentials in an issue. Follow
  [SECURITY.md](SECURITY.md) instead.
- By participating, you agree to [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
- The project is MIT-licensed ([LICENSE](LICENSE)).

## Development Setup

The supported development environment is Python 3.12-3.14, Node.js 22+, Docker,
and [`uv`](https://docs.astral.sh/uv/).

```powershell
git clone https://github.com/pypi-ahmad/computer-use.git
cd computer-use
uv sync --frozen
Set-Location frontend
npm ci
Set-Location ..
```

On Windows, `START.bat` runs `setup.bat --bootstrap-only` then
`dev.bat --open-browser`. Setup rebuilds esbuild after a fresh `npm ci`. The
launcher waits for backend `/api/health`, starts Vite through Node on
`127.0.0.1:8505`, and opens that URL. For normal development, use
`uv run python dev.py` so backend and frontend output stays in the terminal.

Copy `.env.example` to `.env`, add only the credentials needed for your route,
and never commit the populated file. Live provider tests are opt-in and must use
credentials supplied through the local environment.

## Making a Change

1. Fork the repository and create a branch from `main`.
2. Keep the branch limited to one logical change; avoid unrelated refactors.
3. Add or update tests for behavior changes.
4. Update operator, technical, and changelog documentation when a public
   contract, configuration variable, model, or workflow changes.
5. Regenerate the standalone handbook after changing one of its source files:

   ```powershell
   uv run python scripts/build_handbook_site.py
   uv run python scripts/build_handbook_site.py --check
   ```

Model or provider changes must cite current first-party documentation in the
pull request and include offline contract coverage. Do not make CI depend on
real provider credentials.

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

Docker image construction and vulnerability scanning run in CI. If your change
touches the sandbox or container build, also verify `docker compose build`
locally.

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
