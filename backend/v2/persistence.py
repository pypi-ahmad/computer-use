"""SQLite WAL persistence for v2 sessions, audit records, metrics, and workflows."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class StoredSession:
    id: str
    task: str
    model: str
    primary_route: str
    status: str
    created_at: str


@dataclass(frozen=True)
class WorkflowVersion:
    id: str
    slug: str
    name: str
    version: int
    variables_schema: dict[str, Any]
    steps: list[str]
    created_at: str


@dataclass(frozen=True)
class Checkpoint:
    id: str
    session_id: str
    goal: str
    last_confirmed_action: int
    frame_hash: str | None
    safety_state: dict[str, Any]


class SqliteStore:
    def __init__(self, path: str | Path) -> None:
        raw_path = str(path)
        self.path = Path(raw_path)
        if raw_path != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(raw_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.executescript(_SCHEMA)

    @property
    def journal_mode(self) -> str:
        row = self._connection.execute("PRAGMA journal_mode").fetchone()
        return str(row[0]).lower()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def create_session(self, task: str, model: str, primary_route: str, *, fallbacks: list[str] | None = None) -> StoredSession:
        session = StoredSession(str(uuid.uuid4()), task, model, primary_route, "IDLE", _now())
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session.id, task, model, primary_route, json.dumps(fallbacks or []), session.status, session.created_at),
            )
        return session

    def get_session(self, session_id: str) -> StoredSession | None:
        row = self._connection.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            return None
        return StoredSession(row["id"], row["task"], row["model"], row["primary_route"], row["status"], row["created_at"])

    def list_sessions(self, *, cursor: int = 0, limit: int = 50) -> tuple[list[StoredSession], int | None]:
        rows = self._connection.execute("SELECT * FROM sessions ORDER BY created_at DESC, id LIMIT ? OFFSET ?", (limit + 1, cursor)).fetchall()
        items = [StoredSession(row["id"], row["task"], row["model"], row["primary_route"], row["status"], row["created_at"]) for row in rows[:limit]]
        return items, cursor + limit if len(rows) > limit else None

    def set_session_status(self, session_id: str, status: str) -> StoredSession | None:
        with self._lock, self._connection:
            changed = self._connection.execute("UPDATE sessions SET status=? WHERE id=?", (status, session_id)).rowcount
        return self.get_session(session_id) if changed else None

    def delete_session(self, session_id: str) -> bool:
        with self._lock, self._connection:
            return bool(self._connection.execute("DELETE FROM sessions WHERE id=?", (session_id,)).rowcount)

    def append_action(self, session_id: str, sequence: int, action_type: str, payload: dict[str, Any], *, confirmed: bool) -> str:
        action_id = str(uuid.uuid4())
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO actions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (action_id, session_id, sequence, action_type, json.dumps(payload), int(confirmed), _now()),
            )
        return action_id

    def append_event(self, session_id: str, event_type: str, payload: dict[str, Any]) -> str:
        event_id = str(uuid.uuid4())
        with self._lock, self._connection:
            self._connection.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?)", (event_id, session_id, event_type, json.dumps(payload), _now()))
        return event_id

    def append_metric(self, session_id: str, stage: str, duration_ms: float, input_tokens: int = 0, output_tokens: int = 0) -> str:
        metric_id = str(uuid.uuid4())
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO metrics VALUES (?, ?, ?, ?, ?, ?, ?)",
                (metric_id, session_id, stage, duration_ms, input_tokens, output_tokens, _now()),
            )
        return metric_id

    def list_actions(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._connection.execute("SELECT * FROM actions WHERE session_id=? ORDER BY sequence", (session_id,)).fetchall()
        return [{"id": row["id"], "sequence": row["sequence"], "type": row["action_type"], "payload": json.loads(row["payload"]), "isConfirmed": bool(row["confirmed"]), "createdAt": row["created_at"]} for row in rows]

    def list_events(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._connection.execute("SELECT * FROM events WHERE session_id=? ORDER BY created_at, rowid", (session_id,)).fetchall()
        return [{"id": row["id"], "type": row["event_type"], "payload": json.loads(row["payload"]), "createdAt": row["created_at"]} for row in rows]

    def list_metrics(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._connection.execute("SELECT * FROM metrics WHERE session_id=? ORDER BY created_at, rowid", (session_id,)).fetchall()
        return [{"id": row["id"], "stage": row["stage"], "durationMs": row["duration_ms"], "inputTokens": row["input_tokens"], "outputTokens": row["output_tokens"], "createdAt": row["created_at"]} for row in rows]

    def analytics(self, *, session_id: str | None = None, model: str | None = None, route: str | None = None) -> dict[str, Any]:
        row = self._connection.execute(
            "SELECT COUNT(m.id) count, COALESCE(SUM(m.duration_ms), 0) duration, COALESCE(SUM(m.input_tokens), 0) input_tokens, COALESCE(SUM(m.output_tokens), 0) output_tokens FROM metrics m JOIN sessions s ON s.id=m.session_id WHERE (? IS NULL OR s.id=?) AND (? IS NULL OR s.model=?) AND (? IS NULL OR s.primary_route=?)",
            (session_id, session_id, model, model, route, route),
        ).fetchone()
        return {"sampleCount": row["count"], "totalDurationMs": row["duration"], "inputTokens": row["input_tokens"], "outputTokens": row["output_tokens"]}

    def create_workflow(self, slug: str, name: str, variables_schema: dict[str, Any], steps: list[str]) -> WorkflowVersion:
        return self._insert_workflow(str(uuid.uuid4()), slug, name, 1, variables_schema, steps)

    def create_workflow_version(self, workflow_id: str, steps: list[str], variables_schema: dict[str, Any] | None = None) -> WorkflowVersion:
        row = self._connection.execute("SELECT * FROM workflow_versions WHERE workflow_id=? ORDER BY version DESC LIMIT 1", (workflow_id,)).fetchone()
        if row is None:
            raise KeyError(workflow_id)
        return self._insert_workflow(workflow_id, row["slug"], row["name"], int(row["version"]) + 1, variables_schema or json.loads(row["variables_schema"]), steps)

    def _insert_workflow(self, workflow_id: str, slug: str, name: str, version: int, variables_schema: dict[str, Any], steps: list[str]) -> WorkflowVersion:
        item = WorkflowVersion(workflow_id, slug, name, version, variables_schema, steps, _now())
        with self._lock, self._connection:
            self._connection.execute("INSERT INTO workflow_versions VALUES (?, ?, ?, ?, ?, ?, ?)", (item.id, slug, name, version, json.dumps(variables_schema), json.dumps(steps), item.created_at))
        return item

    def list_workflows(self) -> list[WorkflowVersion]:
        rows = self._connection.execute("SELECT w.* FROM workflow_versions w JOIN (SELECT workflow_id, MAX(version) version FROM workflow_versions GROUP BY workflow_id) latest ON w.workflow_id=latest.workflow_id AND w.version=latest.version ORDER BY w.slug").fetchall()
        return [WorkflowVersion(row["workflow_id"], row["slug"], row["name"], row["version"], json.loads(row["variables_schema"]), json.loads(row["steps"]), row["created_at"]) for row in rows]

    def get_workflow(self, workflow_id: str) -> WorkflowVersion | None:
        row = self._connection.execute("SELECT * FROM workflow_versions WHERE workflow_id=? ORDER BY version DESC LIMIT 1", (workflow_id,)).fetchone()
        if row is None:
            return None
        return WorkflowVersion(row["workflow_id"], row["slug"], row["name"], row["version"], json.loads(row["variables_schema"]), json.loads(row["steps"]), row["created_at"])

    def save_checkpoint(self, session_id: str, goal: str, last_confirmed_action: int, frame_hash: str | None, safety_state: dict[str, Any]) -> Checkpoint:
        item = Checkpoint(str(uuid.uuid4()), session_id, goal, last_confirmed_action, frame_hash, safety_state)
        with self._lock, self._connection:
            self._connection.execute("INSERT INTO checkpoints VALUES (?, ?, ?, ?, ?, ?, ?)", (item.id, session_id, goal, last_confirmed_action, frame_hash, json.dumps(safety_state), _now()))
        return item

    def load_checkpoint(self, session_id: str) -> Checkpoint | None:
        row = self._connection.execute(
            "SELECT * FROM checkpoints WHERE session_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return Checkpoint(
            row["id"],
            row["session_id"],
            row["goal"],
            row["last_confirmed_action"],
            row["frame_hash"],
            json.loads(row["safety_state"]),
        )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, task TEXT NOT NULL, model TEXT NOT NULL, primary_route TEXT NOT NULL, fallback_routes TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE, sequence INTEGER NOT NULL, action_type TEXT NOT NULL, payload TEXT NOT NULL, confirmed INTEGER NOT NULL, created_at TEXT NOT NULL, UNIQUE(session_id, sequence));
CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE, event_type TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS metrics(id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE, stage TEXT NOT NULL, duration_ms REAL NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workflow_versions(workflow_id TEXT NOT NULL, slug TEXT NOT NULL, name TEXT NOT NULL, version INTEGER NOT NULL, variables_schema TEXT NOT NULL, steps TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(workflow_id, version), UNIQUE(slug, version));
CREATE TABLE IF NOT EXISTS checkpoints(id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE, goal TEXT NOT NULL, last_confirmed_action INTEGER NOT NULL, frame_hash TEXT, safety_state TEXT NOT NULL, created_at TEXT NOT NULL);
"""
