# computer-use

Local workbench for provider-native Computer Use on an isolated Docker desktop.

The app does one thing clearly:

**Computer Use + optional Web Search + optional provider file retrieval.**

It gives you a React/Vite workbench, a FastAPI backend, and an Ubuntu/XFCE
sandbox so Anthropic, OpenAI, and Google models can see a screen, request
computer actions, and operate inside a controlled desktop instead of your host
machine.

## What It Supports

| Mode | Tools sent to provider |
|---|---|
| Web Search off, no files | Computer Use only |
| Web Search on, no files | Computer Use + provider Web Search |
| Web Search off, files uploaded | Computer Use + provider file retrieval |
| Web Search on, files uploaded | Computer Use + provider Web Search + provider file retrieval |

Provider details:

| Provider | Computer Use | Web Search | File retrieval |
|---|---|---|---|
| OpenAI | Responses API `computer` tool | Responses `web_search` tool | `file_search` with vector store |
| Anthropic | Messages beta computer tool | `web_search_20250305` tool | Files API / document blocks |
| Google Gemini | `computer_use` tool | `google_search` grounding tool | Rejected with Computer Use |

Gemini file retrieval is intentionally disabled for Computer Use sessions
because Gemini File Search is not documented as combinable with other tools.

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

Open:

```text
http://localhost:3000
```

`dev.py` starts the Docker sandbox, FastAPI backend, and Vite frontend. It also
clears the default app ports before startup so stale local processes do not
block the app.

## How A Run Works

1. You choose a provider, model, Web Search setting, optional files, and task.
2. The backend starts or reuses the Docker desktop sandbox.
3. The provider receives the documented tool set for that request.
4. The model requests computer actions.
5. `backend/executor.py` dispatches those actions to the sandbox service.
6. Screenshots and events stream back to the UI.
7. Safety confirmations are surfaced to the operator when a provider asks.

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

## Security Notes

This is a local single-user workbench. Ports bind to loopback by default. The
Docker sandbox is isolated from the host desktop, but it is still an automation
environment with network access. Do not expose it directly to the public
internet, and do not use it for irreversible actions without watching the run.

## License

See [LICENSE](LICENSE).
