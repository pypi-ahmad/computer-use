# Open use

This repository is **MIT-licensed** ([LICENSE](LICENSE)). The GitHub
repo `pypi-ahmad/computer-use` is **public**.

You may clone, fork, run, modify, and share the software, subject to
the MIT license. There is **no hosted service**. You run the workbench
**on your own machine** with **your own API keys**.

## What you may do

- Clone or fork https://github.com/pypi-ahmad/computer-use
- Run it locally (`run.cmd` on Windows, or `uv run python dev.py`
  after setup — see [README.md](README.md))
- Use your own `GOOGLE_API_KEY` (preferred) / `GEMINI_API_KEY`,
  `OPENAI_API_KEY`, or `ANTHROPIC_API_KEY`, or a Providers-tab
  credential session (`backend/infra/config.py` `resolve_api_key()`,
  process-local vault in `backend/v2/credentials.py`)
- Contribute tests, bug reports, ideas, docs, or pull requests
  ([CONTRIBUTING.md](CONTRIBUTING.md), [SUPPORT.md](SUPPORT.md))

## What this repo does not do

- It does not host your desktop, files, or keys.
- It is not a multi-tenant or public SaaS. Default bind is
  `127.0.0.1` (`backend/main.py` exits 2 without
  `CUA_ALLOW_PUBLIC_BIND=1` and `CUA_API_TOKEN`).
- It does not accept donations, sponsorship, bounties, or paid
  support. There is no `FUNDING.yml`.

## Your data

All data you put in a clone — PDFs and other uploads, sandbox files,
screenshots, SQLite, `.env` — is **your responsibility only**.
[DATA.md](DATA.md).

## Related files

| Topic | File |
|---|---|
| License text | [LICENSE](LICENSE) |
| How to run | [README.md](README.md) |
| Test a clone | [TESTING.md](TESTING.md) |
| Report bugs / get help | [SUPPORT.md](SUPPORT.md) |
| Send a patch | [CONTRIBUTING.md](CONTRIBUTING.md) |
