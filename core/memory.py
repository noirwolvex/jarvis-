from __future__ import annotations

import json
import sqlite3
from pathlib import Path


class MemoryStore:
    def __init__(self, db_path: str = ".jarvis/memory.db") -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
            db.commit()

    def add(self, kind: str, content: str) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO memories(kind, content) VALUES (?, ?)", (kind, content))
            db.commit()

    def recent(self, limit: int = 20) -> list[dict[str, str]]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT kind, content, created_at FROM memories ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"kind": k, "content": c, "created_at": t} for k, c, t in rows]

    def export(self) -> str:
        return json.dumps(self.recent(1000), ensure_ascii=False, indent=2)
