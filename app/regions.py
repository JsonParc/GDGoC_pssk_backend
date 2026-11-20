"""지역 변화 분석 (온디맨드): 요청한 지역만 그때 검색·분석하고 결과를 DB에 캐시.

순서: 웹 검색(kakao) -> 근거 문서 저장(본문 없음) -> Gemini 추출·서버 검증·병합(region_extract)
     -> 타임라인 저장 -> 홍수 전·후 시나리오 -> 캐시
작업(job)은 HTTP 응답 뒤 백그라운드에서 돌고 analysis_jobs에 단계·진행률을 남김.
서버가 재시작되면 진행 중이던 작업은 failed(다시 요청 안내)로 바꿈.
"""

import asyncio
import json
import logging
import os
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from . import db, extract, kakao, map_effect, region_extract as rx

log = logging.getLogger(__name__)

ANALYSIS_VERSION = 1  # 분석 방식이 바뀌면 올림 -> 예전 캐시를 쓰지 않음
CACHE_HOURS = float(os.getenv("REGION_CACHE_HOURS", "24"))
REFRESH_COOLDOWN_MINUTES = 10  # 새로고침(force_refresh)도 이 시간 안에는 캐시를 돌려줌
JOB_TIMEOUT_SECONDS = 240
MAX_RUNNING_JOBS = 2  # 동시에 도는 분석 수
MAX_ACTIVE_JOBS = 6  # 대기 포함 최대 작업 수 (넘으면 429)
PER_QUERY = 30  # 검색어마다 받을 결과 수 (커뮤니티·위키를 거르고 나면 줄어듦)
KEEP_PER_QUERY = 7  # 검색어마다 분석에 넣을 수 (한 재난 종류가 자리를 다 차지하지 않게)
# 분석 범위(scope)별 검색어. all: 클릭한 지점의 모든 재난 이력, flood: 홍수 + 배수시설만
QUERIES = {
    "all": ("역대 최악 재난", "침수 피해", "대형 산불 피해", "태풍 피해", "폭설 피해", "지진", "폭염 피해", "산사태",
            "강풍 피해", "가뭄", "배수펌프장 준공", "차수벽 설치", "하천 정비 준공"),
    "flood": ("침수 피해", "배수펌프장", "빗물펌프장 증설", "차수벽 설치", "하천 정비 준공", "저류조 준공", "하수관로 정비"),
}
PROGRESS = {"queued": 0, "news_search": 10, "extraction": 40, "timeline": 75, "scenario": 90, "done": 100}
# 전·후 비교의 시연용 가정값 (실제 지역의 성능이 아님). 프론트 flood 기본값과 같음
ASSUMED = {"rain": 80, "hours": 12, "drain_before": 40, "drain_after": 60}
NOTICE = ("카카오(다음) 웹 검색 결과의 제목·요약만 분석합니다. 기사가 없던 재난, 오래된 기사, 검색되지 않는 글은 빠질 수 있고 "
          "블로그 등 뉴스가 아닌 글이 섞일 수 있습니다. 위치는 그 지점이 속한 지역(구·동) 단위입니다.")
EMPTY = "확인된 자료 없음"

_slots = asyncio.Semaphore(MAX_RUNNING_JOBS)
_TRACKING = re.compile(r"^(utm_|fbclid|gclid)")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def region_key(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip()


def name_stem(name: str) -> str:
    """'서울특별시 강남구' -> '강남' (기사 관련성 확인용). 두 글자 이하는 그대로"""
    last = region_key(name).split(" ")[-1]
    return last[:-1] if len(last) > 2 and last[-1] in "시군구동읍면" else last


def search_name(name: str) -> str:
    """검색어용 짧은 이름. 정식 이름('강원특별자치도 강릉시')은 지역 안내 페이지만 잔뜩 나와서 '강릉'처럼 줄임.
    '중구'처럼 두 글자면 여러 도시에 있어서 앞 지역을 붙임('서울 중구')"""
    parts = region_key(name).split(" ")
    if len(parts[-1]) > 2 or len(parts) < 2:
        return name_stem(name)
    parent = re.sub(r"(특별자치도|특별자치시|특별시|광역시|도|시|군)$", "", parts[-2]) or parts[-2]
    return f"{parent} {parts[-1]}"


def normalize_url(url: str) -> str:
    p = urlsplit(url)
    query = urlencode(sorted((k, v) for k, v in parse_qsl(p.query) if not _TRACKING.match(k)))
    host = (p.hostname or "").removeprefix("www.").removeprefix("m.")
    return urlunsplit(("https", host, p.path.rstrip("/"), query, ""))


def query_key(key: str, disaster_type: str) -> str:
    return f"{key}|{disaster_type}|v{ANALYSIS_VERSION}"


# ───────── 지역·작업 ─────────

def upsert_region(conn: sqlite3.Connection, name: str, lat: float, lon: float, radius_km: float) -> int:
    key = region_key(name)
    conn.execute(
        "INSERT INTO regions (region_key, display_name, center_lat, center_lon, radius_km) VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(region_key) DO UPDATE SET center_lat = excluded.center_lat, center_lon = excluded.center_lon,"
        " radius_km = excluded.radius_km",
        (key, key, lat, lon, radius_km),
    )
    return conn.execute("SELECT id FROM regions WHERE region_key = ?", (key,)).fetchone()[0]


def get_region(conn: sqlite3.Connection, region_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM regions WHERE id = ?", (region_id,)).fetchone()
    return dict(row) if row else None


def cached(conn: sqlite3.Connection, key: str, disaster_type: str, force_refresh: bool) -> sqlite3.Row | None:
    row = conn.execute("SELECT * FROM region_analysis_cache WHERE query_key = ? AND expires_at > ?",
                       (query_key(key, disaster_type), now())).fetchone()
    if row is None:
        return None
    fresh_limit = (datetime.now(timezone.utc) - timedelta(minutes=REFRESH_COOLDOWN_MINUTES)).isoformat(timespec="seconds")
    return row if not force_refresh or row["generated_at"] > fresh_limit else None


def active_job(conn: sqlite3.Connection, region_id: int, disaster_type: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM analysis_jobs WHERE region_id = ? AND disaster_type = ?"
                        " AND status IN ('queued', 'running') ORDER BY created_at DESC",
                        (region_id, disaster_type)).fetchone()


def active_job_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM analysis_jobs WHERE status IN ('queued', 'running')").fetchone()[0]


def create_job(conn: sqlite3.Connection, region_id: int, disaster_type: str) -> str:
    job_id, t = uuid.uuid4().hex, now()
    conn.execute("INSERT INTO analysis_jobs (id, region_id, disaster_type, status, stage, progress, created_at, updated_at)"
                 " VALUES (?, ?, ?, 'queued', 'queued', 0, ?, ?)", (job_id, region_id, disaster_type, t, t))
    conn.commit()
    return job_id


def set_stage(conn: sqlite3.Connection, job_id: str, stage: str) -> None:
    status = "completed" if stage == "done" else "running"
    conn.execute("UPDATE analysis_jobs SET status = ?, stage = ?, progress = ?, updated_at = ?,"
                 " completed_at = CASE WHEN ? = 'completed' THEN ? END WHERE id = ?",
                 (status, stage, PROGRESS[stage], now(), status, now(), job_id))
    conn.commit()


def fail_job(conn: sqlite3.Connection, job_id: str, message: str) -> None:
    conn.execute("UPDATE analysis_jobs SET status = 'failed', error_message = ?, updated_at = ? WHERE id = ?",
                 (message, now(), job_id))
    conn.commit()


def recover_stale_jobs(conn: sqlite3.Connection) -> int:
    """서버 시작 때: 재시작으로 끊긴 작업을 실패로 바꿔 클라이언트가 다시 요청하게 함"""
    cur = conn.execute("UPDATE analysis_jobs SET status = 'failed', error_message = ?, updated_at = ?"
                       " WHERE status IN ('queued', 'running')",
                       ("서버가 다시 시작되어 분석이 중단됐습니다. 다시 요청해 주세요.", now()))
    conn.commit()
    return cur.rowcount


# ───────── 근거 문서 (RAG) ─────────

def store_documents(conn: sqlite3.Connection, items: list[dict]) -> list[dict]:
    """검색 결과를 저장(같은 주소는 한 번만)하고 문서 목록을 돌려줌"""
    fts = db.has_fts(conn)
    docs: dict[int, dict] = {}
    for item in items:
        norm = normalize_url(item["url"])
        row = conn.execute("SELECT * FROM source_documents WHERE normalized_url = ?", (norm,)).fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO source_documents (normalized_url, url, title, publisher, published_at, short_summary,"
                " retrieval_time, source_kind) VALUES (?, ?, ?, ?, ?, ?, ?, 'kakao_web')",
                (norm, item["url"], item["title"], item["publisher"], item["published_at"], item["summary"][:500], now()),
            )
            if fts:
                conn.execute("INSERT INTO source_documents_fts (doc_id, title, short_summary) VALUES (?, ?, ?)",
                             (cur.lastrowid, item["title"], item["summary"][:500]))
            row = conn.execute("SELECT * FROM source_documents WHERE id = ?", (cur.lastrowid,)).fetchone()
        docs[row["id"]] = doc_dict(row)
    return list(docs.values())


def doc_dict(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "url": row["url"], "title": row["title"], "summary": row["short_summary"],
            "published_at": row["published_at"], "publisher": row["publisher"]}


def search_documents(conn: sqlite3.Connection, term: str, limit: int = rx.MAX_DOCS) -> list[dict]:
    """이전에 모아 둔 근거 문서 중 지역 이름이 들어간 것 (FTS5 trigram은 3글자 이상, 아니면 LIKE)"""
    if db.has_fts(conn) and len(term) >= 3:
        sql = ("SELECT d.* FROM source_documents_fts f JOIN source_documents d ON d.id = f.doc_id"
               " WHERE source_documents_fts MATCH ? ORDER BY d.published_at DESC LIMIT ?")
        args = ('"' + term.replace('"', "") + '"', limit)
    else:
        sql = ("SELECT * FROM source_documents WHERE title LIKE ? OR short_summary LIKE ?"
               " ORDER BY published_at DESC LIMIT ?")
        args = (f"%{term}%", f"%{term}%", limit)
    return [doc_dict(r) for r in conn.execute(sql, args).fetchall()]


async def collect_documents(conn: sqlite3.Connection, http: httpx.AsyncClient, name: str, scope: str) -> list[dict]:
    stem = name_stem(name)
    relevant = []
    for q in QUERIES[scope]:
        found = await kakao.search_news(http, f"{search_name(name)} {q}", PER_QUERY)
        relevant += [i for i in found if stem in i["title"] + i["summary"]][:KEEP_PER_QUERY]
    docs = {d["id"]: d for d in store_documents(conn, relevant)}
    for d in search_documents(conn, stem):  # 예전에 모은 같은 지역 근거도 함께 (최신 기사만으로 과거를 덮어쓰지 않게)
        docs.setdefault(d["id"], d)
    conn.commit()
    return list(docs.values())[:rx.MAX_DOCS]  # 검색 순서 유지 (날짜순으로 자르면 오래된 재난이 빠짐)


def extract_changes(docs: list[dict]) -> list[dict]:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise extract.GeminiError("GEMINI_API_KEY가 설정되지 않았습니다.")
    text = f"{rx.PROMPT}\n검색 결과:\n{rx.format_docs(docs)}"
    raw = extract.call_gemini(text, rx.RESPONSE_SCHEMA, key).get("changes") or []
    return rx.merge([c for c in (rx.verify(r, docs) for r in raw) if c])


# ───────── 저장·조회 ─────────

def save_changes(conn: sqlite3.Connection, region_id: int, disaster_type: str, changes: list[dict]) -> None:
    """이번 분석 결과로 교체 (근거 문서는 지우지 않으므로 다음 분석 때 다시 쓰임)"""
    conn.execute("DELETE FROM region_changes WHERE region_id = ? AND scope = ?", (region_id, disaster_type))
    for c in changes:
        cur = conn.execute(
            "INSERT INTO region_changes (region_id, scope, disaster_type, change_type, facility_name, description,"
            " location_lat, location_lon, occurred_at, started_at, completed_at, status, observed_metrics_json,"
            " confidence, evidence_status, conflicts_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (region_id, disaster_type, c["disaster_type"], c["change_type"], c["facility_name"], c["description"], c["lat"], c["lon"],
             c["occurred_at"], c["started_at"], c["completed_at"], c["status"], json.dumps(c["metrics"]),
             c["confidence"], c["evidence_status"], json.dumps(c["conflicts"])),
        )
        conn.executemany("INSERT INTO change_sources (change_id, source_document_id, evidence_note) VALUES (?, ?, ?)",
                         [(cur.lastrowid, doc_id, note) for doc_id, note in c["sources"].items()])


# 이력 수치 이름 -> 카드 viz_params 이름 (같은 이름은 그대로). map_effect.build가 카드와 똑같이 지도 효과 값을 만듦
VIZ_NAME = {"rain_mm": "rainfall_mm"}


def with_map_effect(c: dict, region: dict | None) -> dict:
    """재난 발생 항목만 지도 재현 값. 위치는 항목 좌표, 없으면 지역 중심 (프론트는 클릭한 지점에 재현)"""
    if c["change_type"] != "disaster_event":
        return {**c, "viz_params": {}, "map_effect": None}
    viz = {VIZ_NAME.get(k, k): v for k, v in c["metrics"].items()}
    at = c["location"] or ({"latitude": region["center_lat"], "longitude": region["center_lon"]} if region else {})
    card = {"type": c["disaster_type"], "lat": at.get("latitude"), "lon": at.get("longitude"), "viz_params": viz}
    return {**c, "viz_params": viz, "map_effect": map_effect.build(card)}


def load_changes(conn: sqlite3.Connection, region_id: int, disaster_type: str) -> list[dict]:
    region = get_region(conn, region_id)
    rows = conn.execute("SELECT * FROM region_changes WHERE region_id = ? AND scope = ?",
                        (region_id, disaster_type)).fetchall()
    changes = []
    for r in rows:
        sources = conn.execute(
            "SELECT d.id, d.title, d.url, d.publisher, d.published_at, s.evidence_note FROM change_sources s"
            " JOIN source_documents d ON d.id = s.source_document_id WHERE s.change_id = ? ORDER BY d.published_at",
            (r["id"],)).fetchall()
        c = {"id": r["id"], "change_type": r["change_type"], "disaster_type": r["disaster_type"], "facility_name": r["facility_name"],
             "description": r["description"], "status": r["status"], "occurred_at": r["occurred_at"],
             "started_at": r["started_at"], "completed_at": r["completed_at"],
             "location": {"latitude": r["location_lat"], "longitude": r["location_lon"]} if r["location_lat"] is not None else None,
             "metrics": json.loads(r["observed_metrics_json"]), "confidence": r["confidence"],
             "evidence_status": r["evidence_status"], "conflicts": json.loads(r["conflicts_json"]),
             "sources": [dict(s) for s in sources]}
        changes.append(with_map_effect({**c, "date": rx.timeline_date(c)}, region))
    return sorted(changes, key=lambda c: (c["date"] is None, c["date"] or ""))


def build_scenario(changes: list[dict], region: dict) -> dict:
    """같은 비·시간·위치에서 배수 능력(drain)만 바꾼 홍수 전·후. 값마다 article / derived / assumed 표시"""
    floods = [c for c in changes if c["change_type"] == "disaster_event" and c["disaster_type"] == "flood" and c["evidence_status"] == "verified"]
    rain = hours = None
    evidence = {}
    for c in floods:
        m = c["metrics"]
        if m.get("rain_rate_mm_h"):
            r, how = m["rain_rate_mm_h"], "article"
        elif m.get("rain_mm") and m.get("duration_hours"):
            r, how = round(m["rain_mm"] / m["duration_hours"], 1), "derived"
        else:
            continue
        if rain is None or r > rain:
            rain, hours, evidence["rain"] = r, m.get("duration_hours"), how
    evidence["hours"] = "article" if hours else "assumed"
    if rain is None:
        evidence["rain"] = "assumed"

    done = [c for c in changes if c["change_type"] in rx.DRAINAGE_TYPES and c["status"] == "completed"
            and c["evidence_status"] == "verified"]
    done.sort(key=lambda c: c["completed_at"] or "")
    latest = done[-1] if done else None
    m = latest["metrics"] if latest else {}
    before, after = m.get("design_rain_before_mm_h"), m.get("design_rain_after_mm_h")
    evidence["drain_before"] = "article" if before else "assumed"
    evidence["drain_after"] = "article" if after else "assumed"

    base = {"rain": rain or ASSUMED["rain"], "hours": hours or ASSUMED["hours"]}
    drain_before, drain_after = before or ASSUMED["drain_before"], after or ASSUMED["drain_after"]
    assumptions = [f"{k} = {v} (시연용 가정값, 실제 측정·설계값 아님)"
                   for k, v in (("rain", base["rain"]), ("hours", base["hours"]),
                                ("drain_before", drain_before), ("drain_after", drain_after)) if evidence[k] == "assumed"]
    n_assumed = sum(v == "assumed" for v in evidence.values())
    used = floods + ([latest] if latest else [])
    return {
        "position": {"longitude": region["center_lon"], "latitude": region["center_lat"]},
        "base_conditions": base,
        "before": {"type": "flood", "params": {**base, "drain": drain_before}},
        "after": {"type": "flood", "params": {**base, "drain": drain_after}},
        "param_evidence": evidence,
        "assumptions": assumptions,
        "comparison_available": latest is not None,
        "reason": None if latest else f"{EMPTY}: 준공이 확인된 배수·방재 시설 변화가 없습니다.",
        "baseline_date": latest["completed_at"] if latest else None,
        "data_quality": "evidence" if n_assumed == 0 else "illustrative" if n_assumed == len(evidence) else "partial",
        "sources": [s for c in used for s in c["sources"]],
    }


def analysis_result(conn: sqlite3.Connection, region_id: int, disaster_type: str) -> dict:
    changes = load_changes(conn, region_id, disaster_type)
    row = conn.execute("SELECT scenario_json FROM region_scenarios WHERE region_id = ? AND disaster_type = ?",
                       (region_id, disaster_type)).fetchone()
    return {"region": get_region(conn, region_id), "disaster_type": disaster_type, "changes": changes,
            "scenario": json.loads(row[0]) if row else None, "notice": NOTICE,
            "empty_message": None if changes else EMPTY}


# ───────── 작업 실행 ─────────

async def _pipeline(conn: sqlite3.Connection, job: sqlite3.Row, http: httpx.AsyncClient) -> None:
    region = get_region(conn, job["region_id"])
    set_stage(conn, job["id"], "news_search")
    docs = await collect_documents(conn, http, region["display_name"], job["disaster_type"])
    set_stage(conn, job["id"], "extraction")
    changes = await asyncio.to_thread(extract_changes, docs) if docs else []
    set_stage(conn, job["id"], "timeline")
    save_changes(conn, region["id"], job["disaster_type"], changes)
    set_stage(conn, job["id"], "scenario")
    scenario = build_scenario(load_changes(conn, region["id"], job["disaster_type"]), region)
    t = now()
    conn.execute("INSERT OR REPLACE INTO region_scenarios (region_id, disaster_type, scenario_json, generated_at)"
                 " VALUES (?, ?, ?, ?)", (region["id"], job["disaster_type"], json.dumps(scenario, ensure_ascii=False), t))
    expires = (datetime.now(timezone.utc) + timedelta(hours=CACHE_HOURS)).isoformat(timespec="seconds")
    conn.execute("INSERT OR REPLACE INTO region_analysis_cache (query_key, region_id, job_id, generated_at, expires_at,"
                 " analysis_version, result_metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (query_key(region["region_key"], job["disaster_type"]), region["id"], job["id"], t, expires,
                  ANALYSIS_VERSION, json.dumps({"documents": len(docs), "changes": len(changes)})))
    set_stage(conn, job["id"], "done")


async def run_job(job_id: str, db_path: str, http: httpx.AsyncClient) -> None:
    conn = db.connect(db_path)
    try:
        job = conn.execute("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
        async with _slots:
            await asyncio.wait_for(_pipeline(conn, job, http), JOB_TIMEOUT_SECONDS)
    except kakao.NewsSearchError as e:
        fail_job(conn, job_id, e.message)
    except extract.GeminiError as e:
        fail_job(conn, job_id, f"AI 분석에 실패했습니다. 잠시 후 다시 시도해 주세요. ({e})")
    except asyncio.TimeoutError:
        fail_job(conn, job_id, "분석 시간이 초과됐습니다. 다시 시도해 주세요.")
    except Exception:
        log.exception("지역 분석 작업 %s 실패", job_id)
        fail_job(conn, job_id, "알 수 없는 오류로 분석에 실패했습니다. 다시 시도해 주세요.")
    finally:
        conn.close()
