# Modernization Plan

**Snapshot:** remote `https://github.com/pypi-ahmad/computer-use.git`, branch
`main`, HEAD `7cad1619821e5169909081f9940ddd63a7f5e017`. Architecture evidence
base: [docs/codebase/ARCHITECTURE.md](docs/codebase/ARCHITECTURE.md),
[docs/codebase/STACK.md](docs/codebase/STACK.md),
[docs/codebase/CONCERNS.md](docs/codebase/CONCERNS.md),
[TECHNICAL.md](TECHNICAL.md), [README.md](README.md). Those two codebase docs
carry a stale package-version line (`3.1.1`; `pyproject.toml` is now `3.2.0`)
and an older HEAD in their snapshot header — a small living-doc drift folded
into Phase 1 below (H8), not a reason to distrust their content, which was
re-verified against source for this plan.

## 1. Executive summary

This is not a legacy-rescue case. `docs/codebase/ARCHITECTURE.md` already
concluded, from its own EOL scan: *"Nothing on the runtime path is EOL.
Leave-it-alone is valid."* This plan independently re-verified that
conclusion and agrees. The system already runs a full, currently-green CI
gate (lint, format, typecheck, `pip-audit`, pytest across Python 3.12–3.14
with coverage floor, frontend lint/typecheck/test/build, `npm audit`,
container build + Trivy scan) on every push and PR to `main`, from locked
dependencies (`uv.lock`, `frontend/package-lock.json`), on a currently-supported
stack (Python 3.12–3.14, Node 22, Ubuntu 24.04, React 19, FastAPI 0.141).
There is no target-architecture change to recommend and no rewrite, upgrade,
or strangler-fig candidate. The only real modernization-adjacent work found
is three small, already-named debt items (one dead code path, one duplicate
manifest file, one deprecated env-var alias) plus a docs-version drift and a
one-line CI runner-version inconsistency. Scope: one phase, one PR, purely
additive/subtractive cleanup, no behavior change to any shipped feature.

## 2. Current state assessment

- **Tech stack:** Python `>=3.12,<3.15`, FastAPI 0.141.1, Pydantic 2.13.0,
  provider SDKs (`openai` 2.30.0, `anthropic` 0.88.0, `google-genai` 2.7.0),
  React 19 / Vite 6 / TypeScript 5.7, SQLite WAL, Docker/Compose sandbox on
  Ubuntu 24.04. All current, actively-supported majors — confirmed again this
  pass, not just carried over from the existing docs.
- **Feature/domain map:** three Computer Use provider routes
  (`openai-direct`, `anthropic-direct`, `gemini-direct`), six-tab dashboard
  (Live, Audit, Cost, Workflows, Providers, Analytics), Docker/XFCE/noVNC
  sandbox, SQLite-backed audit/session persistence, safety-policy gate,
  `mcp_fetch` web-fetch tool.
- **Pain points already identified** (from
  [docs/codebase/CONCERNS.md](docs/codebase/CONCERNS.md), independently
  re-read): shared workbench token in WebSocket/noVNC URLs off loopback;
  single-process/in-memory runtime state; shared-desktop sandbox concurrency;
  unbounded SQLite growth with no automatic retention policy; large
  high-churn transport modules. All five are **named, accepted operating
  tradeoffs** ("retain X unless product scope changes"), not modernization
  debt — CONCERNS.md itself frames them that way, and this plan agrees: none
  of the five names a stack, dependency, or architecture that needs
  upgrading, swapping, or rewriting.
- **Deployment:** local-only. `run.cmd`/`START.bat` (Windows),
  `setup.sh`/`dev.sh` (Linux/macOS), `dev.py` dev orchestration, Docker
  Compose for the sandbox, optional single-process production mode serving
  `frontend/dist` from Uvicorn.

## 3. Feasibility spike result & strategy

Per-component spike (all three probed independently this pass, not assumed):

| Component | Installs from lockfile | Builds on current toolchain | Boots | Tests pass in CI | Testability Milestone |
|---|---|---|---|---|---|
| Backend (FastAPI/Python) | Yes — `uv sync --frozen` against committed `uv.lock` ([ci.yml:26](.github/workflows/ci.yml#L26)) | Yes — Python 3.12/3.13/3.14 matrix ([ci.yml:42](.github/workflows/ci.yml#L42)) | Yes — `uv run python -m backend.main` / `dev.py`, exercised in TESTING.md manual smoke | Yes — `pytest --cov-fail-under=60` green on every push/PR ([ci.yml:52](.github/workflows/ci.yml#L52)) | **Already crossed — day one of this checkout.** |
| Frontend (React/Vite) | Yes — `npm ci` against committed `frontend/package-lock.json` ([ci.yml:69](.github/workflows/ci.yml#L69)) | Yes — Node 22 ([ci.yml:64-66](.github/workflows/ci.yml#L64-L66)) | Yes — `npm run build` produces `frontend/dist`, uploaded as a CI artifact ([ci.yml:75-79](.github/workflows/ci.yml#L75-L79)) | Yes — `npm run test:run` (Vitest) green in CI | **Already crossed.** |
| Sandbox (Docker/XFCE) | N/A (no app dependency lockfile; base image pinned) | Yes — builds in CI ([ci.yml:81-101](.github/workflows/ci.yml#L81-L101)) | Yes — `docker-compose.yml` healthcheck requires both `9222/health` and `6080/vnc.html` | N/A (image build + Trivy HIGH/CRITICAL scan, `exit-code: 1`, is the gate) | **Already crossed** (build+scan gate, not a unit-test gate — appropriate for this component). |

**Migration strategy:** neither Strategy A (Freeze-then-lift) nor Strategy B
(Beachhead-then-expand) applies — both assume the system is dead or
near-dead and needs a net *before* or *instead of* running it. This system is
already alive, already tested, already releasing (`docs/release-notes-v3.2.0.md`
dated the same day as HEAD). The applicable frame is a **maintenance/cleanup
pass entirely within the lit regime**: every task below starts and ends
post-testability, with CI as the authoritative gate throughout.

**Safety-ladder rung:** **L4** for all three components, already achieved,
not something this plan needs to build. Residual risk: CI *runs* on every
push/PR but whether it is an **enforced required status check** (GitHub
branch protection) is `[UNVERIFIED]` from the local checkout — this is a
repo-admin setting, listed as an open action item in §9.

**CI Milestone:** already stood up, prior to this plan (all three workflows —
`ci.yml`, `release.yml`, `gemini-changelog-watchdog.yml` — exist and run
today). This plan introduces no new CI Milestone; Phase 1 below runs entirely
inside the existing gate.

**Additional feasibility-spike finding (Phase 2.5 / H3-style):**
`.github/workflows/gemini-changelog-watchdog.yml` pins
`actions/setup-python@v5` with `python-version: '3.11'`
([gemini-changelog-watchdog.yml:22-24](.github/workflows/gemini-changelog-watchdog.yml#L22-L24)),
one minor below the project's actual supported floor of Python 3.12
(`pyproject.toml`). The watchdog script (`scripts/gemini_changelog_watchdog.py`)
doesn't import the `backend` package, so this doesn't currently break
anything, but it's a runner-pin inconsistency worth closing in the same
cleanup pass rather than leaving it to drift further.

## 4. Target architecture

**No target-architecture change.** Current architecture (modular monolith +
one Docker sandbox, per
[docs/codebase/ARCHITECTURE.md](docs/codebase/ARCHITECTURE.md) Part 3) is the
target. Per the Decision Framework, every major component lands at:

- ✅ **Keep as-is:** FastAPI/Pydantic backend, React/Vite frontend, Docker
  sandbox, SQLite persistence, v2 API bridging `AgentLoop`, `mcp_fetch`
  tooling, safety-policy gate, all three provider engines. Nothing here is
  EOL, abandoned, or blocking a required capability.
- 🗑️ **Remove:** `maybe_plan_with_web_search()` dead helper
  (`backend/providers/_common.py`); `requirements.txt` (duplicate manifest,
  not consumed by `docker/Dockerfile` or any script — confirmed by grep this
  pass, only referenced from docs and `.dockerignore`'s allowlist line).
- 🔧 **Deprecation-window housekeeping (not a removal yet):**
  `CUA_WS_TOKEN` compatibility fallback — still read in 3 files
  (`backend/server/__init__.py`, `backend/infra/config.py`,
  `backend/main.py`); do not remove without a stated deprecation window
  since it's a public-facing env var (see §9).
- ⬆️ **Minor fix:** `gemini-changelog-watchdog.yml`'s Python 3.11 pin →
  3.12, to match the project floor.

No ADR is written for "keep as-is" — an ADR is for a decision with real
alternatives considered; there is no alternative under consideration here.
One ADR below covers the one active decision (deferring the `CUA_WS_TOKEN`
removal rather than doing it now).

#### ADR: Defer `CUA_WS_TOKEN` removal
- **Context:** `CUA_WS_TOKEN` is a documented "deprecated compatibility
  fallback" ([docs/codebase/CONCERNS.md:19](docs/codebase/CONCERNS.md#L19))
  for `CUA_API_TOKEN`, still read in three backend files.
- **Decision:** Do not remove it in this plan (Decision Framework level 5,
  Remove, does not apply yet). It's a public operator-facing environment
  variable; removing it without a announced deprecation window would be a
  breaking change for any operator still setting it.
- **Alternatives considered:** Remove immediately (rejected — breaking,
  unannounced); leave undocumented forever (rejected — CONCERNS.md already
  calls it deprecated, so a plan should act on that rather than ignore it).
- **Consequences:** Phase 1 adds a one-line runtime deprecation log warning
  when `CUA_WS_TOKEN` is used without `CUA_API_TOKEN` set, and records a
  target removal version in CHANGELOG.md's Unreleased section. Actual
  removal is **deferred**, not dropped — tracked as an open item in §9.

## 5. Per-feature migration analysis

Only one "feature" is in scope — everything else is explicitly not
migrating (see §4).

**Dead-code and duplicate-manifest cleanup**
1. **Current implementation:** `maybe_plan_with_web_search()` defined at
   `backend/providers/_common.py:59-117`, zero callers anywhere in
   `backend/` or `tests/` (confirmed by full-tree grep this pass).
   `requirements.txt` (14 lines) duplicates `pyproject.toml`'s direct
   dependencies; not referenced by `docker/Dockerfile`, only by
   `.dockerignore`'s `!requirements.txt` allowlist line and by docs.
2. **Migration strategy:** N/A (not a migration) — direct removal, Decision
   Framework level 5.
3. **Testability status:** already lit, L4. This change is purely additive
   (documentation) / subtractive (dead code), verified by the existing green
   CI gate — no new test infrastructure needed.
4. **Dependencies and coupling:** none. Confirmed no test, script, or
   Dockerfile references either artifact.
5. **Effort estimate:** XS (under an hour of changes, mechanical).
6. **Risk assessment:** Near zero. Both removals are provably dead per grep;
   CI (lint + full test suite) catches any missed reference immediately
   since it runs on every PR.
7. **Acceptance criteria:** `uv run ruff check .` and `uv run pytest` stay
   green after removal (they should be unaffected, since nothing calls
   either artifact); `.dockerignore`'s now-dangling `!requirements.txt` line
   removed in the same change.

## 6. Phased implementation plan

**Regime note:** every task below is post-testability ("lit") from the
start — there is no dark-regime work in this plan, so the usual
testability-gating discussion does not apply beyond what's stated in §3.

### Phase 1: Dead-code, duplicate-manifest, and doc-drift cleanup (T-shirt size: XS)

**Goal:** Remove the two confirmed-dead artifacts, add the deprecation
warning for `CUA_WS_TOKEN`, fix the stale version/HEAD lines in the codebase
docs, and align the watchdog workflow's Python pin — all in one small,
purely additive/subtractive PR with no behavior change to any shipped
feature.

**Regime:** post-testability ("lit") — backend, frontend, sandbox.
**Safety rung:** L4 (already the project's baseline; this phase doesn't
change the rung, it operates inside it).
**Prerequisites:** none.
**Duration estimate:** well under one sprint — a single small PR.

#### Tasks

| ID | Task | Component | Blocked by |
|----|------|-----------|------------|
| 1.1 | Delete `maybe_plan_with_web_search()` and its now-unused imports in `backend/providers/_common.py` | Backend | — |
| 1.2 | Delete `requirements.txt`; remove the now-dangling `!requirements.txt` line from `.dockerignore` | Backend/Docker | — |
| 1.3 | Add a one-line runtime deprecation warning log when `CUA_WS_TOKEN` is set without `CUA_API_TOKEN`, in whichever of `backend/server/__init__.py` / `backend/infra/config.py` / `backend/main.py` first observes it; add a CHANGELOG.md Unreleased entry naming a target removal version | Backend | — |
| 1.4 | Update `docs/codebase/ARCHITECTURE.md` and `docs/codebase/STACK.md` snapshot/version lines from `3.1.1` / stale HEAD to the current `3.2.0` / current HEAD | Docs | 1.1, 1.2, 1.3 (so the doc refresh captures the final diff) |
| 1.5 | Bump `actions/setup-python` in `.github/workflows/gemini-changelog-watchdog.yml` from `3.11` to `3.12` | CI | — |

#### Risks & Mitigations
- **Risk:** a reference to `maybe_plan_with_web_search` or `requirements.txt`
  was missed by grep (H1 incomplete-quarantine class). → **Mitigation:** CI's
  full lint + pytest run on the PR will fail loudly on any missed import;
  this is a lit-regime change, so CI is the authoritative check before
  merge.
- **Risk:** the `CUA_WS_TOKEN` deprecation warning is too aggressive and
  breaks an operator's existing setup that intentionally relies on it. →
  **Mitigation:** log-only warning, no functional change to the fallback
  behavior itself in this phase.

#### Decisions made
- `CUA_WS_TOKEN` **removal is deferred**, not dropped — target version to be
  chosen by the maintainer (open question, §9); this phase only adds the
  warning and the changelog entry.
- Hazard catalog (H1–H8) walked for this phase: **H1** cleared (full
  transitive reference set for both dead artifacts confirmed via grep, see
  §3/§5); **H2, H3** (except the noted watchdog Python-pin fix, folded into
  1.5), **H4, H5** not triggered — no major version bump, no route/auth
  rewrite, no stateful-store upgrade; **H6** not triggered — no transitional
  weak state introduced; **H7** not triggered — single phase, no stacking
  risk; **H8** triggered and addressed — task 1.4 updates the codebase docs
  in the same PR as the code change they describe.

#### Verification & Exit Criteria (Definition of Done)
- [ ] `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`,
      and `uv run pytest -p no:warnings --tb=short --cov=backend --cov-report=term-missing --cov-fail-under=60`
      all pass on the phase branch/PR (the existing CI gate, unmodified).
- [ ] `npm --prefix frontend run lint/typecheck/test:run/build` unaffected
      (this phase touches no frontend code) — CI green confirms no
      regression.
- [ ] Grep for `maybe_plan_with_web_search` and `requirements.txt` across
      the repo returns zero hits outside `CHANGELOG.md`'s new entry and git
      history.
- [ ] `docs/codebase/ARCHITECTURE.md` and `STACK.md` snapshot lines match
      the PR's actual merge commit and `pyproject.toml` version.
- [ ] Purely additive/subtractive: no behavior change to any shipped
      Computer Use feature, provider route, or UI surface — assert this
      explicitly in the PR description.

No Phase 2 is proposed. This is intentionally a one-phase plan — inventing
further phases to fill the template would violate the skill's own
"don't gold-plate" convention on a system that doesn't need it.

## 7. Execution governance

- **Branch:** one branch (e.g. `chore/dead-code-and-doc-drift-cleanup`) cut
  from `main`, one PR, merged to `main` on green CI. No phase stacking risk
  (H7) — there is only one phase.
- **Trunk:** confirmed `main` is the sole default branch (no `master` found
  in this checkout).
- **Gate:** green CI on the PR is the authoritative signal — this is a
  fully lit-regime change from a system already at L4.
- **CI Milestone:** N/A — already established before this plan; not
  reintroduced or modified here.
- **Enforcement:** whether `ci.yml` is a required status check /
  branch-protection rule is `[UNVERIFIED]` from this checkout and is **not**
  something this plan (or any agent) can configure — it's a manual
  GitHub → Settings → Branches action, listed in §9.
- **Living docs:** Phase 1 task 1.4 explicitly keeps `docs/codebase/
  ARCHITECTURE.md` and `STACK.md` in sync with the code change in the same
  PR (H8 compliance).
- Companion file `.github/copilot-instructions.modernization.md` written
  alongside this plan (see note below on the existing `copilot-instructions.md`).

**Note on `.github/copilot-instructions.md`:** the file already exists at
this path, but its content is an unrelated "caveman" terse-response style
guide (`AGENTS.md`'s companion), not a commands/gating table. Per this
skill's own rule, it was **not** overwritten. A sibling file
`.github/copilot-instructions.modernization.md` was written instead — the
user should decide whether/how to merge the two (e.g., append the
modernization commands table under a new heading in the existing file, or
keep them as two separate files GitHub Copilot both auto-loads from
`.github/`).

## 8. Migration safety net

- **Feature flags:** none needed — Phase 1 has no user-visible behavior
  change.
- **Data migration:** none — no schema, no persisted-data change.
- **Rollback plan:** revert the single PR; both removed artifacts are
  recoverable from git history if ever needed (they won't be — they're
  confirmed dead).
- **Transitional-insecure-state register:** empty — Phase 1 introduces no
  weakened security state.
- **Oracle & seam contracts:** not applicable — no behavior is being
  preserved-under-change; this phase removes things that produce no
  observable behavior today.
- **Testing strategy:** the existing CI suite is sufficient; no new test
  coverage is needed for a dead-code removal, beyond the existing suite
  proving nothing broke.
- **Observability:** none needed beyond CI's existing pass/fail signal.

## 9. Open questions / decisions needed from stakeholders

1. **[DECISION NEEDED]** Target removal version for `CUA_WS_TOKEN` — pick a
   version (e.g. "remove in the next minor after two releases carry the
   warning") so task 1.3's CHANGELOG entry can name it concretely.
2. **[Manual platform action, not an agent task]** Confirm whether
   `.github/workflows/ci.yml` is configured as a required status check /
   branch-protection rule on `main` (GitHub → Settings → Branches). This
   plan cannot verify or configure this from the local checkout.
3. **[User decision]** How to reconcile the new
   `.github/copilot-instructions.modernization.md` with the existing
   `.github/copilot-instructions.md` (caveman style guide) — merge into one
   file, or keep both.
4. Confirm the `gemini-changelog-watchdog.yml` Python 3.11→3.12 bump (task
   1.5) doesn't need to stay at 3.11 for some undocumented compatibility
   reason before merging — a quick sanity check, not expected to block
   anything given the script has no `backend` import.
