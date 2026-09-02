"""SQLite 持久任务存储，记录阶段、日志、结果与恢复信息。"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any


class TaskStore:
    FINAL_STATUSES = {"completed", "failed", "cancelled"}

    def __init__(self, database: Path):
        self.database = database
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self):
        with closing(self._connect()) as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    novel TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL DEFAULT '',
                    progress INTEGER NOT NULL DEFAULT 0,
                    input_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '',
                    not_before TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    progress INTEGER,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_updated ON tasks(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_events_task ON task_events(task_id, id);
            """)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()}
            if "not_before" not in columns:
                connection.execute("ALTER TABLE tasks ADD COLUMN not_before TEXT NOT NULL DEFAULT ''")

    def create(self, novel: str, kind: str, title: str, payload: dict | None = None,
               status: str = "running", not_before: str = "") -> str:
        task_id = uuid.uuid4().hex
        now = datetime.now().isoformat()
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                "INSERT INTO tasks(id,novel,kind,title,status,input_json,not_before,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (task_id, novel, kind, title, status, json.dumps(payload or {}, ensure_ascii=False), str(not_before or ""), now, now),
            )
            connection.commit()
        return task_id

    def create_if_idle(
        self, novel: str, kind: str, title: str, payload: dict | None = None,
        status: str = "running", not_before: str = "", allowed_active_task_id: str | None = None,
    ) -> str | None:
        """原子创建同书任务；内部后继只允许忽略当前唯一运行任务。"""
        task_id = uuid.uuid4().hex
        now = datetime.now().isoformat()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                "SELECT id,status FROM tasks WHERE novel=? AND status IN ('queued','running','paused')",
                (novel,),
            ).fetchall()
            if allowed_active_task_id:
                predecessor = next((row for row in active if row["id"] == allowed_active_task_id), None)
                if not predecessor or predecessor["status"] != "running":
                    connection.commit()
                    return None
                active = [row for row in active if row["id"] != allowed_active_task_id]
            if active:
                connection.commit()
                return None
            connection.execute(
                "INSERT INTO tasks(id,novel,kind,title,status,input_json,not_before,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (task_id, novel, kind, title, status, json.dumps(payload or {}, ensure_ascii=False), str(not_before or ""), now, now),
            )
            connection.commit()
        return task_id

    def claim_next(self, kinds: set[str] | None = None) -> dict | None:
        """原子领取一个排队任务，确保单工作器不会重复执行。"""
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            query = "SELECT id FROM tasks WHERE status='queued' AND (not_before='' OR not_before<=?)"
            params: list[Any] = [datetime.now().isoformat()]
            if kinds:
                placeholders = ",".join("?" for _ in kinds)
                query += f" AND kind IN ({placeholders})"
                params.extend(sorted(kinds))
            query += " ORDER BY created_at LIMIT 1"
            row = connection.execute(query, params).fetchone()
            if not row:
                connection.commit()
                return None
            now = datetime.now().isoformat()
            connection.execute(
                "UPDATE tasks SET status='running',stage='准备执行',updated_at=? WHERE id=? AND status='queued'",
                (now, row["id"]),
            )
            connection.commit()
        return self.get(row["id"])

    def requeue(self, task_id: str) -> bool:
        now = datetime.now().isoformat()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute(
                "SELECT novel,status FROM tasks WHERE id=?", (task_id,),
            ).fetchone()
            if not task or task["status"] not in {"failed", "interrupted"}:
                connection.commit()
                return False
            conflicting = connection.execute(
                "SELECT 1 FROM tasks WHERE novel=? AND id<>? AND status IN ('queued','running','paused') LIMIT 1",
                (task["novel"], task_id),
            ).fetchone()
            if conflicting:
                connection.commit()
                return False
            cursor = connection.execute(
                "UPDATE tasks SET status='queued',stage='等待执行',progress=0,error='',updated_at=? "
                "WHERE id=? AND status IN ('failed','interrupted')",
                (now, task_id),
            )
            connection.commit()
            return cursor.rowcount == 1

    def recover_interrupted(self, kinds: set[str]) -> int:
        if not kinds:
            return 0
        placeholders = ",".join("?" for _ in kinds)
        now = datetime.now().isoformat()
        with self._lock, closing(self._connect()) as connection:
            cursor = connection.execute(
                f"UPDATE tasks SET status='queued',stage='服务重启后重新排队',updated_at=? WHERE status='interrupted' AND kind IN ({placeholders})",
                [now, *sorted(kinds)],
            )
            connection.commit()
            return cursor.rowcount

    def event(self, task_id: str, message: str, progress: int | None = None, level: str = "info", stage: str | None = None):
        now = datetime.now().isoformat()
        with self._lock, closing(self._connect()) as connection:
            task = connection.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task or task["status"] in self.FINAL_STATUSES or task["status"] == "paused":
                return
            connection.execute(
                "INSERT INTO task_events(task_id,level,message,progress,created_at) VALUES(?,?,?,?,?)",
                (task_id, level, message, progress, now),
            )
            fields = ["updated_at=?"]
            values: list[Any] = [now]
            if progress is not None:
                fields.append("progress=?")
                values.append(max(0, min(100, int(progress))))
            if stage is not None:
                fields.append("stage=?")
                values.append(stage)
            values.append(task_id)
            connection.execute(
                f"UPDATE tasks SET {','.join(fields)} WHERE id=? AND status NOT IN ('completed','failed','cancelled','paused')",
                values,
            )
            connection.commit()

    def finish(self, task_id: str, result: dict | None = None):
        self._set_final(task_id, "completed", result=result, allowed={"running"})

    def fail(self, task_id: str, error: str):
        self._set_final(task_id, "failed", error=error, allowed={"running"})

    def cancel(self, task_id: str) -> bool:
        return self._set_final(task_id, "cancelled", error="用户取消", allowed={"queued", "running", "paused", "interrupted"})

    def pause(self, task_id: str) -> bool:
        now = datetime.now().isoformat()
        with self._lock, closing(self._connect()) as connection:
            cursor = connection.execute("UPDATE tasks SET status='paused',stage='用户暂停',updated_at=? WHERE id=? AND status IN ('queued','running')", (now, task_id))
            connection.commit()
            return cursor.rowcount == 1

    def resume(self, task_id: str) -> bool:
        now = datetime.now().isoformat()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute(
                "SELECT novel,status FROM tasks WHERE id=?", (task_id,),
            ).fetchone()
            if not task or task["status"] not in {"paused", "failed", "interrupted"}:
                connection.commit()
                return False
            conflicting = connection.execute(
                "SELECT 1 FROM tasks WHERE novel=? AND id<>? AND status IN ('queued','running','paused') LIMIT 1",
                (task["novel"], task_id),
            ).fetchone()
            if conflicting:
                connection.commit()
                return False
            cursor = connection.execute("UPDATE tasks SET status='queued',stage='恢复后等待执行',error='',updated_at=? WHERE id=? AND status IN ('paused','failed','interrupted')", (now, task_id))
            connection.commit()
            return cursor.rowcount == 1

    def _set_final(
        self, task_id: str, status: str, result: dict | None = None, error: str = "",
        allowed: set[str] | None = None,
    ) -> bool:
        now = datetime.now().isoformat()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT status,progress FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row or (allowed is not None and row["status"] not in allowed):
                connection.commit()
                return False
            progress = 100 if status == "completed" else int(row["progress"])
            connection.execute(
                "UPDATE tasks SET status=?,progress=?,result_json=?,error=?,updated_at=? WHERE id=?",
                (status, progress, json.dumps(result or {}, ensure_ascii=False), error, now, task_id),
            )
            connection.commit()
            return True

    def get(self, task_id: str) -> dict | None:
        with closing(self._connect()) as connection:
            task = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task:
                return None
            events = connection.execute("SELECT * FROM task_events WHERE task_id=? ORDER BY id", (task_id,)).fetchall()
        result = dict(task)
        input_raw = result.pop("input_json") or "{}"
        result_raw = result.pop("result_json") or "{}"
        result["input"], input_valid = self._parse_json_object(input_raw)
        result["result"], result_valid = self._parse_json_object(result_raw)
        result["payload_corrupt"] = not input_valid or not result_valid
        result["events"] = [dict(item) for item in events]
        return result

    def list(self, novel: str | None = None, limit: int = 30) -> list[dict]:
        query = "SELECT * FROM tasks"
        params: list[Any] = []
        if novel:
            query += " WHERE novel=?"
            params.append(novel)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, min(200, limit)))
        with closing(self._connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [{key: value for key, value in dict(row).items() if key not in {"input_json", "result_json"}} for row in rows]

    def active_for_novel(self, novel: str) -> list[dict]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT id,title,status,stage,progress FROM tasks WHERE novel=? AND status IN ('queued','running','paused') ORDER BY created_at",
                (novel,),
            ).fetchall()
        return [dict(row) for row in rows]

    def patch_input(self, task_id: str, values: dict):
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute("SELECT input_json FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise ValueError("任务不存在")
            payload, _valid = self._parse_json_object(row["input_json"] or "{}")
            payload.update(values)
            connection.execute(
                "UPDATE tasks SET input_json=?,updated_at=? WHERE id=?",
                (json.dumps(payload, ensure_ascii=False), datetime.now().isoformat(), task_id),
            )
            connection.commit()

    def approve_waiting_review(self, task_id: str) -> bool:
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT input_json,status FROM tasks WHERE id=?", (task_id,),
            ).fetchone()
            if not row or row["status"] != "paused":
                connection.commit()
                return False
            payload, _valid = self._parse_json_object(row["input_json"] or "{}")
            waiting = payload.get("waiting_review", {})
            if not isinstance(waiting, dict) or not waiting.get("chapter"):
                connection.commit()
                return False
            try:
                chapter = int(waiting["chapter"])
            except (TypeError, ValueError):
                connection.commit()
                return False
            if chapter < 1:
                connection.commit()
                return False
            kind = str(waiting.get("kind", "")).strip()
            if kind not in {"quality", "preflight", "consistency", "section_review", "volume_review"}:
                connection.commit()
                return False
            content_hash = str(waiting.get("content_hash", "")).strip()
            if not content_hash:
                connection.commit()
                return False
            approved = [
                item for item in payload.get("approved_reviews", [])
                if isinstance(item, dict) and item.get("kind") and item.get("content_hash")
            ] if isinstance(payload.get("approved_reviews"), list) else []
            approval = {
                "kind": kind, "chapter": chapter, "content_hash": content_hash,
                "planning_fingerprint": str(waiting.get("planning_fingerprint", "")),
                "approved_at": datetime.now().isoformat(),
            }
            deduplicated = []
            for item in approved:
                try:
                    item_chapter = int(item.get("chapter", 0) or 0)
                except (TypeError, ValueError):
                    continue
                if (
                    item.get("kind") == kind and item_chapter == chapter
                    and item.get("content_hash") == content_hash
                    and item.get("planning_fingerprint", "") == approval["planning_fingerprint"]
                ):
                    continue
                deduplicated.append(item)
            approved = deduplicated
            approved.append(approval)
            payload.update({"approved_reviews": approved[-100:], "waiting_review": {}})
            connection.execute(
                "UPDATE tasks SET input_json=?,updated_at=? WHERE id=? AND status='paused'",
                (json.dumps(payload, ensure_ascii=False), datetime.now().isoformat(), task_id),
            )
            connection.commit()
            return True

    @staticmethod
    def _parse_json_object(raw: str) -> tuple[dict, bool]:
        try:
            value = json.loads(raw or "{}")
            return (value, True) if isinstance(value, dict) else ({}, False)
        except (TypeError, json.JSONDecodeError):
            return {}, False

    def mark_interrupted(self):
        """进程重启后，旧 running 任务标记为可重试，避免永远显示运行中。"""
        now = datetime.now().isoformat()
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                "UPDATE tasks SET status='interrupted',stage='服务重启后等待重试',updated_at=? WHERE status='running'",
                (now,),
            )
            connection.commit()

    def clear_all(self):
        with self._lock, closing(self._connect()) as connection:
            running = connection.execute("SELECT COUNT(*) AS count FROM tasks WHERE status IN ('running','queued')").fetchone()["count"]
            if running:
                raise RuntimeError("仍有运行或排队任务，请先停止任务")
            connection.execute("DELETE FROM task_events")
            connection.execute("DELETE FROM tasks")
            connection.commit()
