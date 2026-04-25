# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Changed

- Revert `gemini-3.1-pro-preview.supports_computer_use` to `false`.
  Google has not enabled Computer Use on this model as of
  2026-04-24. The Gemini 3 developer guide implies Pro support but
  the official Computer Use docs page (updated 2026-03-25) lists
  only `gemini-3-flash-preview` and `gemini-2.5-computer-use-preview-10-2025`
  as CU-supported SKUs. Forum report of `400 INVALID_ARGUMENT:
  Computer Use is not enabled for models/gemini-3.1-pro-preview`
  (2026-03-12) remains unresolved. Re-enable when Google adds the
  model to the official docs page or confirms in the forum thread.
