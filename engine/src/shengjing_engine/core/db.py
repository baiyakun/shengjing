"""SQLite 存储：任务、音色、项目、逐句渲染版本。"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    params TEXT NOT NULL,
    result TEXT,
    error TEXT,
    progress REAL NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status, created_at);

CREATE TABLE IF NOT EXISTS voices (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    audio_path TEXT NOT NULL,
    transcript TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT 'zh',
    tags TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT 'imported',
    consent TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS renders (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    line_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    path TEXT NOT NULL,
    model TEXT NOT NULL,
    duration REAL NOT NULL,
    qa TEXT NOT NULL DEFAULT '{}',
    selected INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS renders_line ON renders(project_id, line_id, version);
"""


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Database:
    def __init__(self, path: Path) -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _loads(value: str | None, default: Any) -> Any:
    if value is None or value == "":
        return default
    return json.loads(value)


# ---------------- 音色库 ----------------


class VoiceStore:
    def __init__(self, db: Database, voices_dir: Path) -> None:
        self._db = db
        self.dir = voices_dir

    def create(
        self,
        *,
        name: str,
        audio_path: Path,
        transcript: str,
        language: str,
        tags: list[str],
        source: str,
        consent: dict[str, Any],
        voice_id: str | None = None,
    ) -> dict[str, Any]:
        vid = voice_id or new_id("voice")
        self._db.execute(
            "INSERT INTO voices (id, name, audio_path, transcript, language, tags, source, "
            "consent, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                vid,
                name,
                str(audio_path),
                transcript,
                language,
                json.dumps(tags, ensure_ascii=False),
                source,
                json.dumps(consent, ensure_ascii=False),
                time.time(),
            ),
        )
        voice = self.get(vid)
        assert voice is not None
        return voice

    def get(self, voice_id: str) -> dict[str, Any] | None:
        rows = self._db.query("SELECT * FROM voices WHERE id = ?", (voice_id,))
        return self._row(rows[0]) if rows else None

    def list(self) -> list[dict[str, Any]]:
        return [self._row(r) for r in self._db.query("SELECT * FROM voices ORDER BY created_at")]

    def update_transcript(self, voice_id: str, transcript: str) -> None:
        self._db.execute("UPDATE voices SET transcript = ? WHERE id = ?", (transcript, voice_id))

    def delete(self, voice_id: str) -> None:
        self._db.execute("DELETE FROM voices WHERE id = ?", (voice_id,))

    @staticmethod
    def _row(row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["tags"] = _loads(row["tags"], [])
        row["consent"] = _loads(row["consent"], {})
        return row


# ---------------- 项目与逐句渲染 ----------------


class ProjectStore:
    def __init__(self, db: Database, projects_dir: Path) -> None:
        self._db = db
        self.dir = projects_dir

    def create(self, kind: str, title: str, data: dict[str, Any]) -> dict[str, Any]:
        pid = new_id("proj")
        now = time.time()
        self._db.execute(
            "INSERT INTO projects (id, kind, title, data, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (pid, kind, title, json.dumps(data, ensure_ascii=False), now, now),
        )
        (self.dir / pid).mkdir(parents=True, exist_ok=True)
        project = self.get(pid)
        assert project is not None
        return project

    def get(self, project_id: str) -> dict[str, Any] | None:
        rows = self._db.query("SELECT * FROM projects WHERE id = ?", (project_id,))
        if not rows:
            return None
        row = rows[0]
        row["data"] = _loads(row["data"], {})
        return row

    def list(self, kind: str | None = None) -> list[dict[str, Any]]:
        if kind:
            rows = self._db.query(
                "SELECT id, kind, title, created_at, updated_at FROM projects WHERE kind = ? "
                "ORDER BY updated_at DESC",
                (kind,),
            )
        else:
            rows = self._db.query(
                "SELECT id, kind, title, created_at, updated_at FROM projects "
                "ORDER BY updated_at DESC"
            )
        return rows

    def update(self, project_id: str, *, title: str | None = None, data: dict | None = None):
        project = self.get(project_id)
        if project is None:
            raise KeyError(project_id)
        self._db.execute(
            "UPDATE projects SET title = ?, data = ?, updated_at = ? WHERE id = ?",
            (
                title if title is not None else project["title"],
                json.dumps(data if data is not None else project["data"], ensure_ascii=False),
                time.time(),
                project_id,
            ),
        )
        return self.get(project_id)

    def project_dir(self, project_id: str) -> Path:
        path = self.dir / project_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    # 每句话可以有多个版本，selected=1 的版本参与混音
    def add_render(
        self,
        project_id: str,
        line_id: str,
        *,
        path: str,
        model: str,
        duration: float,
        qa: dict[str, Any],
        select: bool = True,
    ) -> dict[str, Any]:
        rows = self._db.query(
            "SELECT COALESCE(MAX(version), 0) AS v FROM renders "
            "WHERE project_id = ? AND line_id = ?",
            (project_id, line_id),
        )
        version = int(rows[0]["v"]) + 1
        rid = new_id("render")
        if select:
            self._db.execute(
                "UPDATE renders SET selected = 0 WHERE project_id = ? AND line_id = ?",
                (project_id, line_id),
            )
        self._db.execute(
            "INSERT INTO renders (id, project_id, line_id, version, path, model, duration, qa, "
            "selected, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rid,
                project_id,
                line_id,
                version,
                path,
                model,
                duration,
                json.dumps(qa, ensure_ascii=False),
                1 if select else 0,
                time.time(),
            ),
        )
        return {"id": rid, "version": version, "path": path, "duration": duration, "qa": qa}

    def renders(self, project_id: str, line_id: str | None = None) -> list[dict[str, Any]]:
        if line_id:
            rows = self._db.query(
                "SELECT * FROM renders WHERE project_id = ? AND line_id = ? ORDER BY version",
                (project_id, line_id),
            )
        else:
            rows = self._db.query(
                "SELECT * FROM renders WHERE project_id = ? ORDER BY line_id, version",
                (project_id,),
            )
        for r in rows:
            r["qa"] = _loads(r["qa"], {})
            r["selected"] = bool(r["selected"])
        return rows

    def selected_renders(self, project_id: str) -> dict[str, dict[str, Any]]:
        return {r["line_id"]: r for r in self.renders(project_id) if r["selected"]}

    def select_render(self, project_id: str, render_id: str) -> None:
        rows = self._db.query(
            "SELECT line_id FROM renders WHERE id = ? AND project_id = ?", (render_id, project_id)
        )
        if not rows:
            raise KeyError(render_id)
        line_id = rows[0]["line_id"]
        self._db.execute(
            "UPDATE renders SET selected = CASE WHEN id = ? THEN 1 ELSE 0 END "
            "WHERE project_id = ? AND line_id = ?",
            (render_id, project_id, line_id),
        )
