# USAGE

Operator guide for `computer-use`.

The app runs provider-native Computer Use against a Docker desktop. Web Search
and reference-file retrieval are optional request-time additions. They do not
add a separate orchestration system.

## Install

Requirements:

- Docker 24+
- Python 3.11+
- Node.js 20+
- One or more provider API keys

First run:

```powershell
git clone https://github.com/pypi-ahmad/computer-use.git
cd computer-use
cp .env.example .env
# add OPENAI_API_KEY, ANTHROPIC_API_KEY, or GEMINI_API_KEY
python dev.py --bootstrap
```

Daily run:

```powershell
python dev.py
```

Open `http://localhost:3000`.

`dev.py` also clears the app's default ports before launching services. This
helps recover from interrupted Vite, backend, noVNC, or sandbox processes.

## Manual Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cd frontend
npm install
cd ..
docker compose up -d
python -m backend.main
```

In another terminal:

```powershell
cd frontend
npm run dev
```

## Tool Modes

| Request state | OpenAI | Anthropic | Gemini |
|---|---|---|---|
| Web Search off, no files | `computer` | computer tool | `computer_use` |
| Web Search on, no files | `computer` + `web_search` | computer tool + `web_search_20250305` | `computer_use` + `google_search` |
| Files uploaded, Web Search off | `computer` + `file_search` | computer tool + Files API context | rejected |
| Files uploaded, Web Search on | `computer` + `web_search` + `file_search` | computer tool + web search + Files API context | rejected |

Gemini file uploads are rejected for Computer Use because Gemini File Search is
not used in the documented Computer Use path this app implements.

The Web Search toggle is intentionally boolean:

- Off means only the provider's Computer Use tool is made available.
- On means the provider's official Web Search tool is made available in the
  same Computer Use run.

The Web Search toggle is the full public search contract.

## Starting A Session

1. Choose provider and model.
2. Enter an API key or rely on `.env`.
3. Toggle Web Search if the task needs live web context.
4. Upload reference files only when the task should use them and the provider
   is OpenAI or Anthropic.
5. Write a concrete desktop task.
6. Start the run and watch the live desktop or screenshot stream.

Use the Stop button if the model is looping, operating in the wrong place, or
approaching an irreversible action.

## API Keys

The UI accepts a manual key for the selected provider. For day-to-day local
use, `.env` can also provide:

```text
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
GOOGLE_API_KEY=...
GEMINI_API_KEY=...
```

`GOOGLE_API_KEY` and `GEMINI_API_KEY` are both accepted for Gemini.

## Prompt Shape

Good task prompts name:

- outcome
- starting point or URL
- allowed evidence
- constraints
- stop condition
- final answer format

Example:

```text
Open the browser and go to the official OpenAI docs.
Find the Computer Use guide.
Do not sign in or change any settings.
Stop when the guide page is visible.
Tell me the page title and the first section heading.
```

With files:

```text
Use the attached product notes as the source of truth.
Open the browser and compare the visible pricing page against the attached notes.
Do not submit forms or start purchases.
Stop after you identify any mismatch.
Return a short list of differences.
```

## Files

Supported upload types are handled by `backend/files.py`.

- OpenAI uploads are prepared for vector-store `file_search`.
- Anthropic uploads use the Files API when legal for document blocks; formats
  that are not legal document blocks are extracted to text where supported.
- Gemini uploads are rejected for Computer Use sessions.

Files are uploaded to the backend before the run starts. They become opaque
local ids in the start request, then the provider adapter prepares the official
provider-side retrieval shape. Removing a file from the UI removes it from the
next run request.

## Ports

Default local ports:

| Port | Purpose |
|---|---|
| `3000` | Vite frontend |
| `8100` | FastAPI backend |
| `6080` | noVNC web client |
| `5900` | VNC |
| `9222` | sandbox action service |

`dev.py` clears the app ports before startup.

## Common Fixes

Container does not start:

```powershell
docker logs cua-environment
docker compose down
docker compose up -d
```

Frontend dependency error:

```powershell
cd frontend
npm install
npm run dev
```

Backend import or dependency error:

```powershell
pip install -r requirements.txt
python -m py_compile backend/server.py
```

Full reset:

```powershell
python dev.py --bootstrap
```

## Tests

```powershell
python -m pytest -p no:cacheprovider tests evals --tb=short
```

Useful focused checks:

```powershell
python -m pytest tests/test_provider_run_contract.py tests/test_files.py tests/test_server_validation.py --tb=short
```

Frontend build:

```powershell
cd frontend
npm run build
```
