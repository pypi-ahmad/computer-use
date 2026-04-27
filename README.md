# computer-use

Local workbench for provider-native Computer Use in an isolated Docker desktop.

This repo intentionally keeps the product simple:

**Computer Use + optional Web Search + optional provider file retrieval.**

The React/Vite UI lets an operator choose a provider, model, task, Web Search
toggle, and optional reference files. The FastAPI backend translates that into
the provider's documented Computer Use loop. Mouse, keyboard, scroll, and
screenshot actions are executed only inside the Ubuntu/XFCE Docker sandbox, not
on the host desktop.

## Product Contract

| Request state | Tool availability |
|---|---|
| Web Search off, no files | Computer Use only |
| Web Search on, no files | Computer Use + provider Web Search |
| Files uploaded, Web Search off | Computer Use + provider file retrieval for OpenAI/Anthropic |
| Files uploaded, Web Search on | Computer Use + Web Search + provider file retrieval for OpenAI/Anthropic |
| Gemini with files | Rejected before provider call |

Provider details:

| Provider | Computer Use | Web Search | Reference files |
|---|---|---|---|
| OpenAI | Responses API `computer` tool | Responses `web_search` tool | `file_search` with a per-run vector store |
| Anthropic | Messages beta computer tool | `web_search_20250305` server tool | Files API/document blocks |
| Google Gemini | `computer_use` tool | `google_search` grounding tool | Rejected for Computer Use |

Gemini reference files are intentionally disabled for Computer Use sessions.
Gemini File Search is documented as its own retrieval feature and is not used in
this app's Computer Use path.

## Quickstart

Requirements:

- Docker 24+
- Python 3.11+
- Node.js 20+
- At least one provider API key

First run:

```powershell
git clone https://github.com/pypi-ahmad/computer-use.git
cd computer-use
cp .env.example .env
# add your API key to .env
python dev.py --bootstrap
```

Daily run:

```powershell
python dev.py
```

Open the workbench:

```text
http://localhost:3000
```

`dev.py` starts the Docker sandbox, FastAPI backend, and Vite frontend. It also
clears the default app ports before startup so stale local processes do not
block the app.

## How A Run Works

1. You choose a provider, model, Web Search setting, optional files, and task.
2. The backend validates that the requested tool combination is supported.
3. The backend starts or reuses the Docker desktop sandbox.
4. The provider receives the documented tool set for that request.
5. The model requests computer actions.
6. `backend/executor.py` dispatches those actions to the sandbox service.
7. Screenshots and events stream back to the UI.
8. Provider safety confirmations are surfaced to the operator when required.

The model never controls your host desktop. Actions run inside the Docker
container.

## Code Map

```text
backend/
  server.py          FastAPI API, WebSocket events, session lifecycle
  loop.py            bridges server requests to provider execution
  executor.py        screenshot capture and desktop action dispatch
  files.py           provider file upload/retrieval preparation
  prompts.py         provider-specific computer-use prompts
  safety.py          operator safety confirmation registry
  providers/
    openai.py        OpenAI run(...) provider loop wrapper
    anthropic.py     Anthropic run(...) provider loop wrapper
    gemini.py        Gemini run(...) provider loop wrapper
  engine/
    openai.py        OpenAI Responses Computer Use client
    claude.py        Anthropic Computer Use client
    gemini.py        Gemini Computer Use client

frontend/            React workbench
docker/              Ubuntu desktop sandbox and action service
tests/               provider, server, executor, file, and sandbox tests
```

Each provider wrapper follows this public shape:

```python
run(task, *, tools, files, on_event, on_safety, executor)
```

Provider-specific SDK details stay inside the provider/client files. The shared
executor only knows how to capture screenshots and perform desktop actions.

## Official Provider References

- Anthropic Computer Use: https://docs.claude.com/en/docs/agents-and-tools/tool-use/computer-use-tool
- Anthropic Web Search: https://docs.claude.com/en/docs/agents-and-tools/tool-use/web-search-tool
- Anthropic Files API: https://platform.claude.com/docs/en/build-with-claude/files
- Gemini Computer Use: https://ai.google.dev/gemini-api/docs/computer-use
- Gemini Google Search grounding: https://ai.google.dev/gemini-api/docs/google-search
- Gemini File Search: https://ai.google.dev/gemini-api/docs/file-search
- OpenAI Computer Use: https://platform.openai.com/docs/guides/tools-computer-use
- OpenAI Web Search: https://platform.openai.com/docs/guides/tools-web-search
- OpenAI File Search: https://developers.openai.com/api/docs/guides/tools-file-search

## Running Tests

```powershell
python -m pytest -p no:cacheprovider tests evals --tb=short
```

Focused checks:

```powershell
python -m pytest tests/test_provider_run_contract.py tests/test_files.py tests/test_executor_split.py --tb=short
```

## Documentation

- [USAGE.md](USAGE.md) - install, run, and operate the app
- [TECHNICAL.md](TECHNICAL.md) - concise architecture and module contracts
- [docs/computer-use-prompt-guide.md](docs/computer-use-prompt-guide.md) - prompt patterns for this app
- [docs/gemini-successor-evaluation.md](docs/gemini-successor-evaluation.md) - Gemini model replacement checklist
- [docker/SECURITY_NOTES.md](docker/SECURITY_NOTES.md) - sandbox action-surface notes
- [evals/README.md](evals/README.md) - deterministic runtime-boundary evals

## Security Notes

This is a local single-user workbench. Ports bind to loopback by default. The
Docker sandbox is isolated from the host desktop, but it is still an automation
environment with network access. Do not expose it directly to the public
internet, and do not use it for irreversible actions without watching the run.

## License

See [LICENSE](LICENSE).
