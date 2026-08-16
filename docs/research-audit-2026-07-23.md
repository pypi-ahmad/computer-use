# Computer Use Model and Platform Audit

Verified July 23, 2026 against official vendor documentation. The catalog records eligibility, not account entitlement or regional availability.

## Current implementation status (2026-08-16)

This audit remains a historical research record. The **active** application
catalog is five Computer Use models on three executable routes, defined in
`backend/models/allowed_models.json` and
`backend/models/computer_use_models.v2.json`:

- `gpt-5.6-luna` and `gpt-5.6-terra` through OpenAI Responses (`openai-direct`)
- `claude-sonnet-5` through Anthropic Messages (`anthropic-direct`)
- `gemini-3.7-flash` and `gemini-3.5-flash-lite` through Google Interactions (`gemini-direct`)

API keys are supported for all three providers. `GOOGLE_API_KEY` is
preferred over `GEMINI_API_KEY`. `backend/infra/config.py` snapshots
those names into `_USER_ENV` before `load_dotenv(..., override=False)`,
so a user-env `GOOGLE_API_KEY` wins over `.env`.
Gemini also supports process-local browser OAuth. The dated tables below
describe the July 23, 2026 research snapshot, not the live allowlist.

## Eligible catalog

| Transport | Models represented in v2 catalog | Runtime status |
|---|---|---|
| OpenAI Responses | GPT-5.6 Sol/Terra/Luna, GPT-5.5, GPT-5.4, GPT-5.4 Mini, GPT-5.4 Pro | Executable |
| Azure OpenAI | Deployment-mapped GPT-5.6 trio, GPT-5.5, GPT-5.4, GPT-5.4 Mini | Catalogued; bridge unavailable |
| Anthropic Messages | Claude Sonnet 5; Opus 4.8/4.7/4.6/4.5; Sonnet 4.6/4.5; Haiku 4.5 | Executable |
| AWS Bedrock Claude | Eligible Anthropic model-card routes | Catalogued; bridge unavailable |
| Vertex Claude | Eligible Anthropic Vertex routes | Catalogued; bridge unavailable |
| Gemini API | Gemini 3.6 Flash, 3.5 Flash, 3 Flash Preview | Executable |
| Vertex Gemini | Gemini 3.5 Flash, 3 Flash Preview | Catalogued; bridge unavailable |

Model-specific context/output limits, coordinate spaces, tool versions, beta headers, and endpoint IDs live in `backend/models/computer_use_models.v2.json` and are validated at startup.

## Removed or excluded

- OpenAI `computer-use-preview`, GPT-5.5 Pro, and GPT-5.4 Nano.
- Gemini 2.5 Computer Use Preview and Gemini 3.5 Flash-Lite. The model-specific Flash-Lite page takes precedence over a conflicting overview statement.
- Retired Claude models and Claude Fable 5, which is not listed in Anthropic's Computer Use support matrix.
- OpenRouter. Its official API documents generic function tools and provider selection, not a vendor-native Computer Use request/action protocol. Treating generic tool routing as native OS control would be an unsupported inference.

## Sources

- [OpenAI Computer Use guide and model catalog](https://developers.openai.com/api/docs/guides/tools-computer-use)
- [Anthropic Computer Use tool matrix](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool)
- [Google Gemini Computer Use](https://ai.google.dev/gemini-api/docs/computer-use)
- [Google Cloud Vertex Computer Use](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/computer-use)
- [AWS Bedrock Computer Use](https://docs.aws.amazon.com/bedrock/latest/userguide/computer-use.html)
- [Azure models sold directly by Azure](https://learn.microsoft.com/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure)
- [OpenRouter API overview](https://openrouter.ai/docs/api-reference/overview) and [provider routing](https://openrouter.ai/docs/guides/routing/provider-selection)

## Codebase audit outcomes

- Replaced flat provider/model selection with a transport-aware logical catalog.
- Added canonical action validation and deterministic, audited fallback with circuit breaking.
- Coalesced screenshot demand and separated canonical inference frames from compressed preview frames.
- Added SQLite WAL records and bounded filesystem frame retention instead of browser-only history.
- Replaced the single workbench with live, audit, workflow, provider, and analytics views.
- Migrated the frontend to strict TypeScript and made static analysis blocking in CI.

## Verification boundaries

The local baseline before this upgrade had 470 passing backend tests, nine sandbox temporary-directory setup errors, 490 Ruff findings, and 36 mypy errors. Frontend execution was locally blocked by an esbuild `spawn EPERM` sandbox restriction. v2 release publication remains gated until clean CI establishes the final counts. No Azure, Bedrock, or Vertex execution claim is made.
