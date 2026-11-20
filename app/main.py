import json
import logging
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path as FilePath
from typing import Annotated, Literal

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, HttpUrl, ValidationError

from . import db, extract as extractor, kma

load_dotenv(FilePath(__file__).resolve().parent.parent / ".env")

log = logging.getLogger(__name__)

DisasterType = Literal["flood", "wildfire", "heatwave", "other"]
MAX_BATCH = 500


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with httpx.AsyncClient() as client:
        app.state.http = client
        yield


app = FastAPI(title="재난 데이터 API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "http://localhost:8000").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class ArticleIn(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    url: HttpUrl
    source: str | None = Field(None, max_length=100)
    published_at: str | None = Field(None, max_length=40)
    # 분석에만 쓰고 저장하지 않음 (계획서 안전장치: 본문 미저장)
    content: str | None = Field(None, max_length=50_000)


class DisasterIn(BaseModel):
    """extract_batch()가 돌려주는 재난 하나. Gemini 결과는 외부 입력이라 여기서 검증합니다."""

    type: DisasterType
    title: str = Field(min_length=1, max_length=500)
    summary: str | None = Field("", max_length=2000)
    location: str | None = Field(None, max_length=200)
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)
    date: str | None = Field(None, max_length=40)
    scale: str | None = Field(None, max_length=200)
    viz_params: dict | None = None


def get_db():
    conn = db.connect(os.getenv("DB_PATH", db.DEFAULT_PATH))
    try:
        yield conn
    finally:
        conn.close()


def _require_token(env_name: str, header_name: str, given: str | None) -> None:
    expected = os.getenv(env_name)
    if not expected:  # 설정을 빠뜨린 채 배포돼도 아무나 쓰지 못하게 막음
        raise HTTPException(503, f"서버에 {env_name}이 설정되지 않았습니다.")
    if not secrets.compare_digest(given or "", expected):
        raise HTTPException(401, f"{header_name}이 올바르지 않습니다.")


def check_ingest_token(x_ingest_token: str | None = Header(None)):
    _require_token("INGEST_TOKEN", "X-Ingest-Token", x_ingest_token)


def check_admin_token(x_admin_token: str | None = Header(None)):
    _require_token("ADMIN_TOKEN", "X-Admin-Token", x_admin_token)


def save_disaster(conn: sqlite3.Connection, d: DisasterIn) -> int:
    viz = {k: v for k, v in (d.viz_params or {}).items() if v is not None}
    cur = conn.execute(
        "INSERT INTO disasters (type, title, summary, location, lat, lon, date, scale, viz_params)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (d.type, d.title, d.summary or "", d.location, d.lat, d.lon, d.date, d.scale, json.dumps(viz)),
    )
    return cur.lastrowid


def store_chunk(conn: sqlite3.Connection, chunk: list[ArticleIn], groups: list[dict]) -> tuple[int, int, int]:
    """Gemini 결과로 재난을 만들고 기사를 저장. (저장한 기사 수, 만든 재난 수, 실패한 기사 수)"""
    disaster_of: dict[int, int] = {}
    rejected: set[int] = set()
    for group in groups:
        ids = [
            i for i in group.get("article_ids") or []
            if isinstance(i, int) and 0 <= i < len(chunk) and i not in disaster_of and i not in rejected
        ]
        if not ids:
            continue
        try:
            disaster = DisasterIn.model_validate(group)
        except ValidationError as e:
            # 저장하지 않으므로 다음 전송 때 다시 분석됨
            log.warning("재난 정보 형식 오류로 기사 %s개 보류: %s", len(ids), e.errors()[:1])
            rejected.update(ids)
            continue
        disaster_id = save_disaster(conn, disaster)
        disaster_of.update({i: disaster_id for i in ids})

    for i, article in enumerate(chunk):
        if i not in rejected:
            conn.execute(
                "INSERT INTO articles (url, title, source, published_at, disaster_id) VALUES (?, ?, ?, ?, ?)",
                (str(article.url), article.title, article.source, article.published_at, disaster_of.get(i)),
            )
    return len(chunk) - len(rejected), len(set(disaster_of.values())), len(rejected)


@app.post("/api/articles", dependencies=[Depends(check_ingest_token)])
def ingest_articles(articles: list[ArticleIn], conn: sqlite3.Connection = Depends(get_db)):
    """크롤러가 기사 여러 개를 한 번에 보냅니다. 이미 받은 링크는 다시 분석하지 않습니다.

    새 기사는 extractor.BATCH_SIZE개씩 묶어 Gemini를 한 번씩 부릅니다.
    """
    if len(articles) > MAX_BATCH:
        raise HTTPException(413, f"한 번에 최대 {MAX_BATCH}개까지 보낼 수 있습니다.")

    seen: set[str] = set()
    fresh = []
    for article in articles:
        url = str(article.url)
        if url not in seen and not conn.execute("SELECT 1 FROM articles WHERE url = ?", (url,)).fetchone():
            seen.add(url)
            fresh.append(article)

    stored = created = failed = 0
    for start in range(0, len(fresh), extractor.BATCH_SIZE):
        chunk = fresh[start:start + extractor.BATCH_SIZE]
        try:
            groups = extractor.extract_batch(chunk)
        except Exception:
            # 저장하지 않으므로 다음 전송 때 다시 분석됨
            log.exception("기사 %s개 분석 실패", len(chunk))
            failed += len(chunk)
            continue
        s, c, f = store_chunk(conn, chunk, groups)
        stored, created, failed = stored + s, created + c, failed + f
    conn.commit()
    return {
        "received": len(articles),
        "new": stored,
        "skipped": len(articles) - len(fresh),
        "failed": failed,
        "disasters_created": created,
    }


@app.get("/api/disasters")
def list_disasters(
    type: DisasterType | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(get_db),
):
    where, args = ("WHERE type = ?", [type]) if type else ("", [])
    total = conn.execute(f"SELECT COUNT(*) FROM disasters {where}", args).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM disasters {where} ORDER BY date DESC, id DESC LIMIT ? OFFSET ?",
        [*args, limit, offset],
    ).fetchall()
    return {"items": [db.row_to_disaster(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@app.get("/api/disasters/{disaster_id}")
def get_disaster(disaster_id: int, conn: sqlite3.Connection = Depends(get_db)):
    row = conn.execute("SELECT * FROM disasters WHERE id = ?", (disaster_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "재난을 찾을 수 없습니다.")
    sources = conn.execute(
        "SELECT title, url, source, published_at FROM articles WHERE disaster_id = ?", (disaster_id,)
    ).fetchall()
    return {**db.row_to_disaster(row), "sources": [dict(s) for s in sources]}


def get_http(request: Request) -> httpx.AsyncClient:
    return request.app.state.http


async def active_warnings_with_guides(http: httpx.AsyncClient, conn: sqlite3.Connection) -> list[dict]:
    try:
        warnings = await kma.fetch_warnings(http)
    except kma.KmaError as e:
        raise HTTPException(e.status, e.message) from None
    guide_of = dict(conn.execute("SELECT warning_code, type FROM guides WHERE warning_code IS NOT NULL").fetchall())
    return [{**w, "guide_type": guide_of.get(w["type"])} for w in warnings]


@app.get("/api/warnings")
async def list_warnings(
    type: str | None = Query(None, pattern="^[A-Z]$", description="특보 종류 코드 (H 폭염, R 호우, D 건조 등)"),
    http: httpx.AsyncClient = Depends(get_http),
    conn: sqlite3.Connection = Depends(get_db),
):
    """지금 발효 중인 기상특보 (기상청 특보현황). guide_type은 GET /api/guides/{guide_type}로 볼 행동지침."""
    items = await active_warnings_with_guides(http, conn)
    if type:
        items = [w for w in items if w["type"] == type]
    return {"items": items, "total": len(items)}


@app.get("/api/forecast")
async def get_forecast(
    lat: float = Query(ge=32, le=39.5),
    lon: float = Query(ge=124, le=132),
    http: httpx.AsyncClient = Depends(get_http),
):
    """위치의 단기예보 (기상청). TMP 기온, PCP 강수량, POP 강수확률, REH 습도 등."""
    try:
        return await kma.fetch_forecast(http, lat, lon)
    except kma.KmaError as e:
        raise HTTPException(e.status, e.message) from None


@app.get("/api/current")
async def get_current(
    lat: float = Query(ge=32, le=39.5),
    lon: float = Query(ge=124, le=132),
    http: httpx.AsyncClient = Depends(get_http),
):
    """위치의 지금 실제 날씨 (기상청 초단기실황, 매시 관측). T1H 기온, RN1 1시간 강수량, REH 습도, WSD 풍속 등."""
    try:
        return await kma.fetch_current(http, lat, lon)
    except kma.KmaError as e:
        raise HTTPException(e.status, e.message) from None


@app.get("/api/earthquakes")
async def list_earthquakes(
    overseas: bool | None = Query(None, description="true 국외만, false 국내만, 비우면 모두"),
    http: httpx.AsyncClient = Depends(get_http),
):
    """최근 지진 (기상청). 진앙 위경도·규모·진도."""
    try:
        items = await kma.fetch_earthquakes(http)
    except kma.KmaError as e:
        raise HTTPException(e.status, e.message) from None
    if overseas is not None:
        items = [q for q in items if q["overseas"] == overseas]
    return {"items": items, "total": len(items)}


@app.get("/api/typhoons")
async def list_typhoons(
    year: int = Query(default_factory=lambda: datetime.now(kma.KST).year, ge=1951, le=2100),
    http: httpx.AsyncClient = Depends(get_http),
):
    """연도별 태풍 목록 (기상청). korea_effect: 상륙·직접영향·간접영향·없음."""
    try:
        items = await kma.fetch_typhoons(http, year)
    except kma.KmaError as e:
        raise HTTPException(e.status, e.message) from None
    return {"year": year, "items": items, "total": len(items)}


@app.get("/api/typhoons/{year}/{seq}")
async def get_typhoon_track(
    year: int = Path(ge=1951, le=2100),
    seq: int = Path(ge=1, le=99, description="태풍 번호 (목록의 seq)"),
    http: httpx.AsyncClient = Depends(get_http),
):
    """태풍 경로 (기상청). observed=true는 분석(관측) 위치, false는 예측 위치."""
    try:
        track = await kma.fetch_typhoon_track(http, year, seq)
    except kma.KmaError as e:
        raise HTTPException(e.status, e.message) from None
    if not track:
        raise HTTPException(404, "해당 태풍의 경로 자료가 없습니다.")
    return {"year": year, "seq": seq, "track": track, "total": len(track)}


class GuideIn(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    steps: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(max_length=30)
    source: HttpUrl | None = None
    warning_code: str | None = Field(None, pattern="^[A-Z]$", description="이 특보가 발효 중일 때 보여 줌")


@app.get("/api/guides")
def list_guides(conn: sqlite3.Connection = Depends(get_db)):
    """모든 재난 행동지침. needs_writing=true는 공식 문구가 없어 직접 작성해야 하는 항목."""
    rows = conn.execute("SELECT * FROM guides ORDER BY type").fetchall()
    items = [db.row_to_guide(r) for r in rows]
    return {"items": items, "total": len(items)}


@app.get("/api/guides/active")
async def list_active_guides(
    region: str | None = Query(None, max_length=50, description="지역 이름 일부 (예: 강원, 태백). 비우면 전국"),
    http: httpx.AsyncClient = Depends(get_http),
    conn: sqlite3.Connection = Depends(get_db),
):
    """지금 특보가 발효 중인 재난의 행동지침. 특보가 내려진 때부터 해제될 때까지만 나옴."""
    warnings = await active_warnings_with_guides(http, conn)
    if region:
        warnings = [w for w in warnings if region in (w["region"] or "") or region in (w["region_parent"] or "")]
    grouped: dict[str, list[dict]] = {}
    for w in warnings:
        if w["guide_type"]:
            grouped.setdefault(w["guide_type"], []).append(w)
    items = []
    for guide_type, ws in grouped.items():
        row = conn.execute("SELECT * FROM guides WHERE type = ?", (guide_type,)).fetchone()
        items.append({**db.row_to_guide(row), "warnings": ws})
    return {"items": items, "total": len(items)}


@app.get("/api/guides/{disaster_type}")
def get_guide(disaster_type: str, conn: sqlite3.Connection = Depends(get_db)):
    row = conn.execute("SELECT * FROM guides WHERE type = ?", (disaster_type,)).fetchone()
    if row is None:
        raise HTTPException(404, "행동지침이 없는 재난 종류입니다.")
    return db.row_to_guide(row)


@app.put("/api/guides/{disaster_type}", dependencies=[Depends(check_admin_token)])
def put_guide(
    guide: GuideIn,
    disaster_type: str = Path(pattern="^[a-z_]{2,30}$"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """행동지침 쓰기·고치기 (X-Admin-Token 필요). 없는 종류면 새로 만듭니다."""
    conn.execute(
        "INSERT INTO guides (type, warning_code, title, steps, source, updated_at)"
        " VALUES (?, ?, ?, ?, ?, datetime('now'))"
        " ON CONFLICT(type) DO UPDATE SET warning_code = excluded.warning_code, title = excluded.title,"
        " steps = excluded.steps, source = excluded.source, updated_at = excluded.updated_at",
        (disaster_type, guide.warning_code, guide.title, json.dumps(guide.steps, ensure_ascii=False),
         str(guide.source) if guide.source else None),
    )
    conn.commit()
    return get_guide(disaster_type, conn)
