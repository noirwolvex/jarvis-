from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Z0-9_.-]*(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|password|passwd|secret))"
    r"(\s*[:=]\s*)([\"']?)([^\s\"'`,;]+)\3"
)
_BEARER_TOKEN = re.compile(r"(?i)\b(authorization\s*:\s*bearer)\s+[^\s,;]+")
_KNOWN_TOKEN = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"glpat-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16})\b"
)
_JWT_TOKEN = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")


def redact_secrets(value: str) -> str:
    """Best-effort local secret scrubbing before text is persisted to memory."""
    text = str(value)
    text = _BEARER_TOKEN.sub(lambda match: f"{match.group(1)} [REDACTED]", text)
    text = _SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
        text,
    )
    text = _KNOWN_TOKEN.sub("[REDACTED_TOKEN]", text)
    text = _JWT_TOKEN.sub("[REDACTED_TOKEN]", text)
    return text


class MemoryStore:
    """SQLite-backed long-term memory with simple local relevance retrieval."""

    def __init__(self, db_path: str | None = None) -> None:
        workspace = Path(os.getenv("JARVIS_WORKSPACE", ".")).expanduser().resolve()
        self.path = Path(db_path).expanduser() if db_path else workspace / ".jarvis" / "memory.db"
        self.path = self.path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS memories ("
                "id INTEGER PRIMARY KEY, kind TEXT NOT NULL, content TEXT NOT NULL, "
                "created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS preferences ("
                "key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)"
            )
            db.commit()

    def add(self, kind: str, content: str) -> None:
        text = redact_secrets(str(content)).strip()
        if not text:
            return
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("INSERT INTO memories(kind, content) VALUES (?, ?)", (kind, text))
            db.commit()

    def remember_fact(self, content: str) -> None:
        self.add("fact", content)

    def set_preference(self, key: str, value: str) -> None:
        safe_value = redact_secrets(value.strip())
        with closing(sqlite3.connect(self.path)) as db:
            db.execute(
                "INSERT INTO preferences(key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP",
                (key.strip(), safe_value),
            )
            db.commit()

    def preferences(self) -> dict[str, str]:
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("SELECT key, value FROM preferences ORDER BY key").fetchall()
        return {key: value for key, value in rows}

    def recent(self, limit: int = 20) -> list[dict[str, str]]:
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute(
                "SELECT kind, content, created_at FROM memories ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [{"kind": k, "content": c, "created_at": t} for k, c, t in rows]

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {
            token for token in re.findall(r"[\w-]{2,}", text.lower())
            if token not in {"the", "and", "for", "with", "that", "this"}
        }

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        query_tokens = self._tokens(redact_secrets(query))
        if not query_tokens:
            return self.recent(limit)
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute(
                "SELECT id, kind, content, created_at FROM memories ORDER BY id DESC LIMIT 1000"
            ).fetchall()
        scored: list[tuple[float, dict[str, Any]]] = []
        for ident, kind, content, created_at in rows:
            tokens = self._tokens(content)
            overlap = len(query_tokens & tokens)
            if overlap == 0:
                continue
            score = overlap / max(1, len(query_tokens))
            if kind == "fact":
                score += 0.15
            scored.append((score, {"id": ident, "kind": kind, "content": content, "created_at": created_at}))
        scored.sort(key=lambda item: (-item[0], -int(item[1]["id"])))
        return [item[1] for item in scored[: max(1, limit)]]

    def context(self, query: str, recent_limit: int = 8, search_limit: int = 6) -> str:
        matches = self.search(query, search_limit)
        recent = self.recent(recent_limit)
        facts = [m for m in matches if m["kind"] == "fact"]
        lines: list[str] = []
        if facts:
            lines.append("Relevant long-term facts:")
            lines.extend(f"- {m['content']}" for m in facts)
        if matches:
            lines.append("Relevant memories:")
            lines.extend(f"- [{m['kind']}] {m['content']}" for m in matches if m not in facts)
        if recent:
            lines.append("Recent memories:")
            lines.extend(f"- [{m['kind']}] {m['content']}" for m in recent[:recent_limit])
        prefs = self.preferences()
        if prefs:
            lines.append("Saved preferences:")
            lines.extend(f"- {key}: {value}" for key, value in prefs.items())
        return "\n".join(lines)

    def export(self) -> str:
        with closing(sqlite3.connect(self.path)) as db:
            memories = db.execute(
                "SELECT kind, content, created_at FROM memories ORDER BY id ASC"
            ).fetchall()
        return json.dumps(
            {
                "memories": [
                    {"kind": k, "content": c, "created_at": t} for k, c, t in memories
                ],
                "preferences": self.preferences(),
            },
            ensure_ascii=False,
            indent=2,
        )
