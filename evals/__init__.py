"""Deterministic eval harness for the Computer Use agent runtime.

Evals are offline, mocked-provider tests that assert on behavioural
invariants of a *completed* session by replaying its
:class:`backend.tracing.SessionTrace`. They complement the unit and
integration tests in :mod:`tests/` by focusing on whole-session
properties (e.g. "a safety-required event was followed by an
approval_resolved event before any tool batch ran").

See :file:`evals/README.md` for the full rationale and run
instructions.
"""
