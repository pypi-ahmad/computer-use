# Changelog

All notable changes to this project will be documented in this file.

## [2.0.0] - 2026-07-23

### Added

- Versioned Computer Use-only model catalog with transport-specific identifiers, coordinate spaces, limits, and lifecycle metadata.
- `/api/v2` sessions, credential sessions, provider readiness, workflows, analytics, cursor-paginated audit records, and structured errors.
- Deterministic primary/fallback routing, transient retry, per-route circuit breaking, and provider-neutral checkpoints.
- SQLite WAL session/action/event/metric persistence and bounded audit-frame retention.
- Coalesced frame capture, canonical full-frame inference input, ROI supplements, compressed previews, and binary `CUAF` WebSocket frames.
- Five-tab TypeScript dashboard for live execution, audit history, workflows, providers, and analytics.
- Locked uv project, Python 3.12–3.14 CI, strict frontend checks, dependency audits, image scanning, release packaging, deployment, migration, and rollback guides.

### Changed

- Production FastAPI serves the built React SPA when `frontend/dist` exists.
- Provider credentials use environment variables or non-persistent credential sessions with an eight-hour maximum lifetime.
- Provider selection is explicit and deterministic; there is no implicit price/latency router.
- Direct OpenAI, Anthropic, and Google routes execute. Azure OpenAI, Bedrock, Vertex Gemini, and Vertex Claude are visible but unavailable until verified execution bridges ship.

### Removed

- v1 request/WebSocket compatibility and inline `api_key` session payloads.
- OpenAI `computer-use-preview`, GPT-5.5 Pro, GPT-5.4 Nano, Gemini 2.5 Computer Use Preview, Gemini 3.5 Flash-Lite, and retired/non-CU Claude entries.
- OpenRouter integration; its documented API does not expose a vendor-native Computer Use protocol.

### Security

- Secrets are never persisted or returned, uncertain OS actions are not replayed during failover, and release CI blocks on high/critical dependency and image findings.

## [Unreleased]

### Added

- Expand the shared Ubuntu sandbox app set with XFCE Settings/Task
    Manager, Ristretto, galculator, GIMP, Inkscape, and VS Code (`code`)
    so desktop demos and evals can exercise richer non-browser surfaces
    without a custom image.

### Changed

- Document the current provider-native attachment flow: OpenAI uploads
    into a vector store and attaches `file_search`, Anthropic uses the
    Files API for `.pdf` / `.txt` with inline-text fallback for `.md` /
    `.docx`, and Gemini rejects reference-file uploads for Computer Use
    sessions because this repo does not combine Gemini File Search with
    Computer Use.
- Expand the documentation set with a professional Computer Use Prompt
    Guide, clearer README/USAGE entry points, technical prompt-contract
    notes, and cross-links across Gemini successor and sandbox-security docs.
- Drive Anthropic computer-use tool routing from registry metadata.
    `ClaudeCUClient` now rejects Anthropic models missing
    `cu_tool_version` / `cu_betas` registry metadata instead of
    selecting `tool_version` / `beta_flag` from model-name substrings.
- Remove the GPT-5.5 Pro slug from the OpenAI model
    registry/metadata entirely and reject unregistered GA `gpt-5.5*`
    slugs through the registry gate instead of a hardcoded
    per-model exception. See also `OpenAI default reasoning_effort changed from high to medium` below for the main GPT-5.5 behavior change that can shift latency and output quality after upgrading.
- Keep GPT-5.5 screenshot handling aligned with
    OpenAI's current docs by preserving `detail: "original"` up to
    10,240,000 pixels / 6000 px and regression-testing exact
    downscale/remap behavior on oversized screenshots.
- Add runtime replay coverage that confirms
    outbound assistant-message replay preserves GPT-5.5 `phase`
    verbatim for both `commentary` and `final_answer` items.
- Gemini history pruning now uses an atomic turn window instead of
    stripping fields from older kept turns. `GeminiCUClient` accepts a
    new optional `max_history_turns` argument (default `10`) so long
    tool-combination sessions can tune replay depth without losing
    `toolCall` / `toolResponse` / `functionCall` / `functionResponse`
    fields from retained turns.
- Replace Anthropic's local `CUA_ANTHROPIC_WEB_SEARCH_ENABLED` acknowledgement gate with a first-use Messages API probe that caches org-level web-search enablement per API key for 24 hours. `CUA_ANTHROPIC_WEB_SEARCH_ENABLED=1` still works, but now only as an optional skip-probe override for deployments that want to avoid the initial readiness check latency after they have already enabled web search in Claude Console.
- Refresh README, USAGE, TECHNICAL, and security notes to match the
    single supported Gemini CU SKU, the sandbox app surface, and the
    current pricing assumptions surfaced by the frontend estimator.

#### OpenAI default reasoning_effort changed from high to medium

- Old default: `high`.
- New default: `medium`.
- Source: OpenAI's latest-model guide for GPT-5.5: https://developers.openai.com/api/docs/guides/latest-model
- Callers that were relying on the old default should now set `reasoning_effort="high"` explicitly at construction.
- Observable changes for callers that keep the new default: shorter latency, potentially less deep reasoning on complex tasks, and lower token cost.
- See the GPT-5.5 note above if you are investigating an output-quality or latency regression after upgrading the OpenAI side of this wrapper.

### Removed

- Drop `gemini-3.1-pro-preview` from `backend/allowed_models.json`,
  `frontend/src/utils/pricing.js`, `frontend/src/pages/workbench/constants.js`,
  and all docs. The repo now exposes a single Gemini SKU,
  `gemini-3-flash-preview`, matching the only Gemini id on Google's
  official Computer Use supported-model list that this project
  ships against.
