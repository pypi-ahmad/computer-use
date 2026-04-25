# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

- Expand the shared Ubuntu sandbox app set with XFCE Settings/Task
    Manager, Ristretto, galculator, GIMP, Inkscape, and VS Code (`code`)
    so desktop demos and evals can exercise richer non-browser surfaces
    without a custom image.

### Changed

- Document the current provider-native attachment flow: OpenAI uploads
    into a vector store, Gemini uses File Search through a one-shot RAG
    pre-step before the Computer Use loop, and Anthropic uses the Files
    API for `.pdf` / `.txt` with inline-text fallback for `.md` /
    `.docx`.
- Refresh README, USAGE, TECHNICAL, and security notes to match the
    current Gemini Playwright default, the single supported Gemini CU SKU,
    the sandbox app surface, and the current pricing assumptions surfaced
    by the frontend estimator.

### Removed

- Drop `gemini-3.1-pro-preview` from `backend/allowed_models.json`,
  `frontend/src/utils/pricing.js`, `frontend/src/pages/workbench/constants.js`,
  and all docs. The repo now exposes a single Gemini SKU,
  `gemini-3-flash-preview`, matching the only Gemini id on Google's
  official Computer Use supported-model list that this project
  ships against. `gemini-2.5-computer-use-preview-10-2025` is also
  not listed; callers that need it can re-add it locally.
