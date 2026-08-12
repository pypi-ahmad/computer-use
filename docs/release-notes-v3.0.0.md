# Computer Use Workbench v3.0.0

Released 2026-08-12.

## Highlights

- Narrows the supported model catalog to three provider-native Computer Use routes: GPT-5.6 Luna, Claude Sonnet 5, and Gemini 3.6 Flash.
- Adds process-local API-key credential sessions for all providers and a PKCE-protected Google OAuth flow.
- Adds authenticated application shutdown from the dashboard and coordinated cleanup of backend, frontend, and Docker processes.
- Standardizes local startup on port 8505 and fixes Docker dependency installation by running `uv sync` from `/app`.
- Refreshes the operator, technical, business, security, and onboarding documentation, including the standalone Zero to Hero handbook.
- Publishes refreshed Understand Anything and Graphify knowledge graphs for repository navigation.

## Breaking changes

- Removes all previously advertised model routes except the three supported provider-native routes.
- Removes OpenRouter, Azure OpenAI, Bedrock, and Vertex execution routes.
- Removes retired and preview model identifiers and their obsolete configuration guidance.

Review `CHANGELOG.md`, `docs/migration-v2.md`, and `USAGE.md` before upgrading an existing deployment.
