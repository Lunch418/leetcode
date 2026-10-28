"""SQLite-хранилище задач и напоминаний. Все времена хранятся в UTC (ISO 8601)."""

import sqlite3
from datetime import datetime, timezone


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class Storage:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                notes TEXT,
                due_at TEXT,
                done INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                remind_at TEXT NOT NULL,
                sent INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(sent, remind_at);
            """
        )
        self.conn.commit()

    def add_task(self, chat_id: int, title: str, due_at: datetime | None,
                 notes: str | None, remind_at: list[datetime]) -> int:
        cur = self.conn.execute(
            "INSERT INTO tasks (chat_id, title, notes, due_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (chat_id, title, notes, _utc(due_at) if due_at else None,
             _utc(datetime.now(timezone.utc))),
        )
        task_id = cur.lastrowid
        self.set_reminders(task_id, remind_at)
        return task_id

    def set_reminders(self, task_id: int, remind_at: list[datetime]) -> int:
        """Заменяет все неотправленные напоминания задачи. Возвращает число будущих."""
        now = datetime.now(timezone.utc)
        future = [at for at in remind_at if at > now]
        self.conn.execute("DELETE FROM reminders WHERE task_id = ? AND sent = 0", (task_id,))
        self.conn.executemany(
            "INSERT INTO reminders (task_id, remind_at) VALUES (?, ?)",
            [(task_id, _utc(at)) for at in future],
        )
        self.conn.commit()
        return len(future)

    def add_reminder(self, task_id: int, remind_at: datetime) -> None:
        self.conn.execute(
            "INSERT INTO reminders (task_id, remind_at) VALUES (?, ?)", (task_id, _utc(remind_at))
        )
        self.conn.commit()

    def update_task(self, chat_id: int, task_id: int, title: str | None = None,
                    due_at: datetime | None = None, notes: str | None = None) -> bool:
        fields, values = [], []
        if title is not None:
            fields.append("title = ?")
            values.append(title)
        if due_at is not None:
            fields.append("due_at = ?")
            values.append(_utc(due_at))
        if notes is not None:
            fields.append("notes = ?")
            values.append(notes)
        if not fields:
            return self.get_task(chat_id, task_id) is not None
        cur = self.conn.execute(
            f"UPDATE tasks SET {', '.join(fields)} WHERE id = ? AND chat_id = ?",
            (*values, task_id, chat_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_task(self, chat_id: int, task_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM tasks WHERE id = ? AND chat_id = ?", (task_id, chat_id)
        ).fetchone()
        return self._task(row) if row else None

    def list_tasks(self, chat_id: int, include_done: bool = False) -> list[dict]:
        query = "SELECT * FROM tasks WHERE chat_id = ?"
        if not include_done:
            query += " AND done = 0"
        # Задачи без дедлайна — в конце
        query += " ORDER BY due_at IS NULL, due_at, id"
        return [self._task(r) for r in self.conn.execute(query, (chat_id,))]

    def set_done(self, chat_id: int, task_id: int, done: bool = True) -> bool:
        cur = self.conn.execute(
            "UPDATE tasks SET done = ? WHERE id = ? AND chat_id = ?",
            (int(done), task_id, chat_id),
        )
        if cur.rowcount and done:
            self.conn.execute("UPDATE reminders SET sent = 1 WHERE task_id = ?", (task_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def delete_task(self, chat_id: int, task_id: int) -> bool:
        if self.get_task(chat_id, task_id) is None:
            return False
        self.conn.execute("DELETE FROM reminders WHERE task_id = ?", (task_id,))
        self.conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        self.conn.commit()
        return True

    def due_reminders(self, now: datetime) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT r.id AS reminder_id, t.* FROM reminders r
            JOIN tasks t ON t.id = r.task_id
            WHERE r.sent = 0 AND t.done = 0 AND r.remind_at <= ?
            ORDER BY r.remind_at
            """,
            (_utc(now),),
        ).fetchall()
        return [{**self._task(r), "reminder_id": r["reminder_id"]} for r in rows]

    def mark_reminder_sent(self, reminder_id: int) -> None:
        self.conn.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (reminder_id,))
        self.conn.commit()

    def chats_with_open_tasks(self) -> list[int]:
        return [r[0] for r in self.conn.execute("SELECT DISTINCT chat_id FROM tasks WHERE done = 0")]

    @staticmethod
    def _task(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "chat_id": row["chat_id"],
            "title": row["title"],
            "notes": row["notes"],
            "due_at": _parse(row["due_at"]),
            "done": bool(row["done"]),
        }
