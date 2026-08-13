# Business Guide: Evaluating Computer Use Workbench

This guide explains the repository in business terms. It is written for sponsors, operations leaders, risk owners, and product managers deciding whether a Computer Use pilot is appropriate. Technical implementation details remain available in the linked handbook and architecture references.

> Computer Use agents operate graphical interfaces by looking at screenshots and issuing mouse and keyboard actions. They can make destructive mistakes. This repository is a local, single-user workbench for controlled evaluation; it is not a multi-tenant automation platform.

## Executive view

Computer Use Workbench lets a person give an AI model a task, observe it operating an isolated desktop, approve sensitive actions, and review what happened afterward. It supports three direct provider-native routes:

- GPT-5.6 Luna through OpenAI Responses.
- Claude Sonnet 5 through Anthropic Messages.
- Gemini 3.6 Flash through Google Interactions.

The current release is v3.0.1. On Windows 11, `START.bat` installs
prerequisites, starts the local stack, and opens the dashboard at
`http://127.0.0.1:8505` after the backend health check succeeds.

The practical value is not “AI that can do everything.” The useful capability is operating software that lacks a stable API, spans several visual steps, or still requires human judgment at checkpoints. The workbench is best treated as an evaluation environment for discovering which tasks are reliable enough to automate and which still need a person.

## What the workbench provides

| Capability | Business meaning |
|---|---|
| Isolated desktop | Model actions occur in a disposable Docker sandbox instead of directly on the host workstation. |
| Live session view | An operator can watch the task and stop the session. |
| Safety policies | Sessions can use provider defaults, require confirmation for mutating actions, or run read-only. |
| Human approval | A proposed sensitive action pauses until the operator approves or denies it. |
| Provider choice | A task can use one of three direct model routes and explicitly configured fallbacks. |
| Credential sessions | Provider credentials remain process-local and expire after at most eight hours. |
| Audit history | Sessions, actions, events, metrics, workflow versions, and optional frames are retained locally. |
| Session export | An operator can export an audit package for review or evidence. |
| Reusable workflows | Teams can version task instructions and compile them with controlled variables. |

Evidence: `README.md`, `TECHNICAL.md`, `USAGE.md`, `backend/v2/api.py`, and `backend/v2/credentials.py`.

## Good pilot candidates

Choose tasks with a clear starting state, observable success condition, reversible consequences, and a small number of applications. Useful pilot shapes include:

- Copying approved information between test systems that do not share an API.
- Navigating a repeatable web workflow and collecting a result for human review.
- Exercising a user interface during exploratory quality assurance.
- Preparing a draft or completing a form without submitting it.
- Following a documented checklist in a non-production environment.

Begin with read-only or draft-producing work. Use test accounts and synthetic data. A task that is already reliable through a normal API or deterministic browser script should normally keep that simpler implementation.

## Poor pilot candidates

Do not begin with tasks where one incorrect click can create an irreversible or regulated outcome. Examples include unattended payments, production deletion, legal acceptance, privileged identity administration, health decisions, or workflows containing unrestricted sensitive data.

The current repository also does not provide the tenant isolation, durable identity system, organization policy engine, centralized secret management, or distributed coordination expected from a shared enterprise service. Those are product requirements beyond this local workbench.

Evidence: `SECURITY.md`, `docs/codebase/CONCERNS.md`, and `docs/deployment.md`.

## Operating model

### Before a run

1. Choose a test account and non-sensitive dataset.
2. Define one observable completion condition.
3. Set a step limit appropriate to the task.
4. Select `read_only` or `confirm_mutating` unless the pilot explicitly evaluates provider-default safety.
5. Decide which actions always require a person.
6. Record the expected manual time and error rate for comparison.

### During a run

1. Keep the live session visible.
2. Review the action explanation before approving a safety prompt.
3. Deny any action that exceeds the written task.
4. Stop the session if it repeats actions, reaches unexpected data, or leaves the approved workflow.

### After a run

1. Verify the result in the target application.
2. Review the action and event history.
3. Export the session if evidence must be retained.
4. Classify the outcome: successful, successful with intervention, recoverable failure, or unsafe failure.
5. Tighten the prompt or workflow before increasing the step budget.

## Pilot scorecard

Measure a fixed set of tasks over repeated runs. Do not evaluate the system from a single impressive demonstration.

| Measure | Suggested definition |
|---|---|
| Task success | Percentage of runs meeting the written completion condition. |
| Human intervention | Approvals, corrections, or restarts required per successful run. |
| Unsafe proposal rate | Runs that propose an action outside the approved boundary. |
| Recovery rate | Failed runs that can be safely retried without manual cleanup. |
| Cycle time | Elapsed time from task start to verified result. |
| Resource use | Input/output tokens and infrastructure time recorded for each route. |
| Audit completeness | Runs with sufficient actions, events, and exported evidence for review. |

Set acceptance thresholds before the pilot. Compare routes using the same task, starting state, safety policy, and success definition.

## Risk and control map

| Risk | Current control | Remaining decision |
|---|---|---|
| Model performs an unwanted action | Docker sandbox, action allowlist, stop control, and safety confirmation | Define organization-specific prohibited actions and approval ownership. |
| Credential exposure | Process-local credential vault with bounded lifetime | Decide whether a production design requires a managed secret store and workforce identity. |
| Unapproved network exposure | Loopback default; public bind requires an explicit flag and shared API token | Add deployment-specific TLS, identity, network policy, and monitoring before shared use. |
| Incomplete evidence | SQLite audit data, optional retained frames, and session export | Define retention, access, and deletion rules for the pilot. |
| Provider or model variability | Explicit route selection, retries, fallback, and circuit breaking | Establish a regression suite and approval process for model changes. |
| Sensitive information in screenshots | Local frame retention is bounded and can be disabled per session | Classify permitted data and verify deletion/backup handling. |

These controls reduce risk; they do not prove that a task is safe. Human oversight and task-specific policy remain necessary.

## Deployment and ownership questions

Before moving beyond a local pilot, assign owners and answer:

- Who can start, stop, approve, and export sessions?
- Which accounts, applications, URLs, and data classifications are permitted?
- Which action categories always require approval?
- How long are database records, exports, logs, and audit frames retained?
- Who reviews provider-model changes and regression results?
- What is the incident response path for an unsafe action or credential concern?
- Is local single-user operation sufficient, or is a separately designed shared service required?

## Go, revise, or stop

**Proceed to a larger controlled pilot** when repeated tasks meet the predefined success threshold, unsafe proposals remain within the accepted limit, evidence is complete, and operators can consistently intervene.

**Revise the workflow** when outcomes are recoverable but prompts, starting state, or approval boundaries are unclear. Improve the task specification before changing models or increasing limits.

**Stop the candidate** when failures are irreversible, sensitive data cannot be bounded, the task needs capabilities outside the sandbox, or success depends on ignoring required human judgment.

## Where to continue

- `USAGE.md` provides the complete operator manual.
- `docs/zero-to-hero-study-handbook.md` teaches Computer Use concepts and execution flows.
- `TECHNICAL.md` summarizes current runtime contracts.
- `docs/codebase/ARCHITECTURE.md` and `docs/codebase/CONCERNS.md` describe implementation boundaries and known risks.
- `docs/deployment.md` covers local and public-bind configuration.

## Evidence

- `README.md`
- `USAGE.md`
- `TECHNICAL.md`
- `SECURITY.md`
- `backend/v2/api.py`
- `backend/v2/credentials.py`
- `backend/v2/routing.py`
- `backend/v2/persistence.py`
- `backend/server/__init__.py`
- `docker/agent_service.py`
