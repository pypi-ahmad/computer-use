# Evals

Offline, deterministic evals for Computer Use behaviors that are
clearer at the HTTP/runtime boundary than in focused unit tests.

Related documentation:

- [Technical Architecture](../TECHNICAL.md)
- [Operator Usage Guide](../USAGE.md)
- [Computer Use Prompt Guide](../docs/computer-use-prompt-guide.md)

## What's in here

| File | What it asserts |
| --- | --- |
| `test_degraded_container_startup.py` | `POST /api/agent/start` returns **HTTP 409** when the container is up but the in-container agent is `unready`, and no session is registered. |

## Running

From the repo root:

```bash
pytest evals/
```

Evals are fully offline:

- No real Docker container (the container-state helpers are mocked).
- No real provider calls (API-key resolution is mocked).

## Adding a new eval

Add an eval when the behavior depends on request validation,
container readiness, session registration, or another boundary that is
awkward to cover with a narrower unit test. Keep evals deterministic:
mock Docker, provider keys, and external network calls.
