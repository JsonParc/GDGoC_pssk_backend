import json
import logging
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, HttpUrl

from . import db, extract as extractor, kma
from .guides import GUIDES, SOURCE as GUIDE_SOURCE

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

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
    """extract()가 돌려주는 값. Gemini 결과는 외부 입력이라 여기서 검증합니다."""

    type: DisasterType
    title: str = Field(min_length=1, max_length=500)
    summary: str = Field("", max_length=2000)
    location: str | None = Field(None, max_length=200)
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)
    date: str | None = Field(None, max_length=40)
    scale: str | None = Field(None, max_length=200)
    viz_params: dict = {}


def get_db():
    conn = db.connect(os.getenv("DB_PATH", db.DEFAULT_PATH))
    try:
        yield conn
    finally:
        conn.close()


def check_ingest_token(x_ingest_token: str | None = Header(None)):
    expected = os.getenv("INGEST_TOKEN")
    if expected and not secrets.compare_digest(x_ingest_token or "", expected):
        raise HTTPException(401, "X-Ingest-Token이 올바르지 않습니다.")


def save_disaster(conn: sqlite3.Connection, found: dict) -> int:
    d = DisasterIn.model_validate(found)
    cur = conn.execute(
        "INSERT INTO disasters (type, title, summary, location, lat, lon, date, scale, viz_params)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (d.type, d.title, d.summary, d.location, d.lat, d.lon, d.date, d.scale, json.dumps(d.viz_params)),
    )
    return cur.lastrowid


@app.post("/api/articles", dependencies=[Depends(check_ingest_token)])
def ingest_articles(articles: list[ArticleIn], conn: sqlite3.Connection = Depends(get_db)):
    """크롤러가 기사 여러 개를 한 번에 보냅니다. 이미 받은 링크는 다시 분석하지 않습니다."""
    if len(articles) > MAX_BATCH:
        raise HTTPException(413, f"한 번에 최대 {MAX_BATCH}개까지 보낼 수 있습니다.")

    seen: set[str] = set()
    new = created = failed = 0
    for article in articles:
        url = str(article.url)
        if url in seen or conn.execute("SELECT 1 FROM articles WHERE url = ?", (url,)).fetchone():
            continue
        seen.add(url)
        try:
            found = extractor.extract(article)
            disaster_id = save_disaster(conn, found) if found else None
        except Exception:
            # 저장하지 않으므로 다음 전송 때 다시 분석됨
            log.exception("기사 분석 실패: %s", url)
            failed += 1
            continue
        conn.execute(
            "INSERT INTO articles (url, title, source, published_at, disaster_id) VALUES (?, ?, ?, ?, ?)",
            (url, article.title, article.source, article.published_at, disaster_id),
        )
        new += 1
        created += disaster_id is not None
    conn.commit()
    return {
        "received": len(articles),
        "new": new,
        "skipped": len(articles) - new - failed,
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


@app.get("/api/warnings")
async def list_warnings(
    type: str | None = Query(None, pattern="^[A-Z]$", description="특보 종류 코드 (H 폭염, R 호우, D 건조 등)"),
    http: httpx.AsyncClient = Depends(get_http),
):
    """지금 발효 중인 기상특보 (기상청)."""
    try:
        items = await kma.fetch_warnings(http)
    except kma.KmaError as e:
        raise HTTPException(e.status, e.message) from None
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


@app.get("/api/guides/{disaster_type}")
def get_guide(disaster_type: str):
    if disaster_type not in GUIDES:
        raise HTTPException(404, "행동요령이 없는 재난 종류입니다.")
    return {"type": disaster_type, "steps": GUIDES[disaster_type], "source": GUIDE_SOURCE}
