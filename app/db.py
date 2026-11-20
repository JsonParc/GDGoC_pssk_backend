import json
import sqlite3
from pathlib import Path

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
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def row_to_disaster(row: sqlite3.Row) -> dict:
    return {**dict(row), "viz_params": json.loads(row["viz_params"])}
