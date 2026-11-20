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

-- 지역 변화 분석 (온디맨드). 위 두 테이블은 그대로 두고 추가만 함
CREATE TABLE IF NOT EXISTS regions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    region_key TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    center_lat REAL NOT NULL,
    center_lon REAL NOT NULL,
    radius_km REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS analysis_jobs (
    id TEXT PRIMARY KEY,
    region_id INTEGER NOT NULL REFERENCES regions(id),
    disaster_type TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);
-- 기사 전문은 저장하지 않음: 제목·날짜·매체·주소·검색 API가 준 짧은 요약만
CREATE TABLE IF NOT EXISTS source_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    normalized_url TEXT NOT NULL UNIQUE,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    publisher TEXT,
    published_at TEXT,
    event_date TEXT,
    short_summary TEXT NOT NULL DEFAULT '',
    retrieval_time TEXT NOT NULL,
    source_kind TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS region_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    region_id INTEGER NOT NULL REFERENCES regions(id),
    disaster_type TEXT NOT NULL,
    change_type TEXT NOT NULL,
    facility_name TEXT,
    description TEXT NOT NULL,
    location_lat REAL,
    location_lon REAL,
    occurred_at TEXT,
    started_at TEXT,
    completed_at TEXT,
    status TEXT NOT NULL,
    observed_metrics_json TEXT NOT NULL DEFAULT '{}',
    confidence REAL,
    evidence_status TEXT NOT NULL,
    conflicts_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS change_sources (
    change_id INTEGER NOT NULL REFERENCES region_changes(id) ON DELETE CASCADE,
    source_document_id INTEGER NOT NULL REFERENCES source_documents(id),
    evidence_note TEXT,
    PRIMARY KEY (change_id, source_document_id)
);
CREATE TABLE IF NOT EXISTS region_scenarios (
    region_id INTEGER NOT NULL REFERENCES regions(id),
    disaster_type TEXT NOT NULL,
    scenario_json TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    PRIMARY KEY (region_id, disaster_type)
);
CREATE TABLE IF NOT EXISTS region_analysis_cache (
    query_key TEXT PRIMARY KEY,
    region_id INTEGER NOT NULL REFERENCES regions(id),
    job_id TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    analysis_version INTEGER NOT NULL,
    result_metadata_json TEXT NOT NULL DEFAULT '{}'
);
"""

# 근거 문서 검색용. trigram은 3글자 이상 부분 일치. FTS5가 없는 SQLite면 LIKE 검색으로 대신함
FTS_SCHEMA = "CREATE VIRTUAL TABLE IF NOT EXISTS source_documents_fts USING fts5(doc_id UNINDEXED, title, short_summary, tokenize='trigram')"


def connect(path: str) -> sqlite3.Connection:
    # 요청마다 새 연결을 만들고 그 요청 안에서만 차례로 씀. async 주소는 연결을 만든 스레드와 다른 스레드에서 쓰므로 허용
    conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")  # 분석 작업과 요청이 동시에 쓸 때 잠시 기다림
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    try:
        conn.execute(FTS_SCHEMA)
    except sqlite3.OperationalError:
        pass  # has_fts()가 False -> LIKE 검색
    return conn


def has_fts(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'source_documents_fts'").fetchone() is not None


def row_to_disaster(row: sqlite3.Row) -> dict:
    return {**dict(row), "viz_params": json.loads(row["viz_params"])}
