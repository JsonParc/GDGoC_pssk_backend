import json
import sqlite3
from pathlib import Path

from .guides import DEFAULT_GUIDES

DEFAULT_PATH = str(Path(__file__).resolve().parent.parent / "data.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS disasters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    location TEXT,
    lat REAL,
    lon REAL,
    date TEXT,
    scale TEXT,
    viz_params TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS articles (
    url TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source TEXT,
    published_at TEXT,
    disaster_id INTEGER REFERENCES disasters(id)
);
CREATE TABLE IF NOT EXISTS guides (
    type TEXT PRIMARY KEY,
    warning_code TEXT,
    title TEXT NOT NULL,
    steps TEXT NOT NULL DEFAULT '[]',
    source TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_seeded: set[str] = set()


def _seed_guides(conn: sqlite3.Connection) -> None:
    """기본 행동지침 중 DB에 없는 것만 넣음. 고친 내용은 덮어쓰지 않음."""
    conn.executemany(
        "INSERT OR IGNORE INTO guides (type, warning_code, title, steps, source) VALUES (?, ?, ?, ?, ?)",
        [(g["type"], g["warning_code"], g["title"], json.dumps(g["steps"], ensure_ascii=False), g["source"])
         for g in DEFAULT_GUIDES],
    )
    conn.commit()


def connect(path: str) -> sqlite3.Connection:
    # 요청마다 새 연결을 만들고 그 요청 안에서만 차례로 씀. async 주소는 연결을 만든 스레드와 다른 스레드에서 쓰므로 허용
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    if path not in _seeded:
        _seed_guides(conn)
        _seeded.add(path)
    return conn


def row_to_disaster(row: sqlite3.Row) -> dict:
    return {**dict(row), "viz_params": json.loads(row["viz_params"])}


def row_to_guide(row: sqlite3.Row) -> dict:
    steps = json.loads(row["steps"])
    return {**dict(row), "steps": steps, "needs_writing": not steps}
