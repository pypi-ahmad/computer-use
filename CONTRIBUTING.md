# Contributing

Thank you for helping improve Computer Use Workbench. Contributions should be
focused, reviewable, and backed by tests or documentation that demonstrate the
observable result.

## Before You Start

- Use GitHub Issues for reproducible bugs and feature proposals.
- Do not disclose vulnerabilities or credentials in an issue. Follow
  [SECURITY.md](SECURITY.md) instead.
- By participating, you agree to [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

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

On Windows, `START.bat` performs dependency checks, installs missing project
dependencies, starts the workbench, and opens the dashboard. For normal
development, use `uv run python dev.py` so backend and frontend output stays in
the terminal.

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
