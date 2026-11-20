import json
import logging
import math
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path as FilePath
from typing import Literal

import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Path, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, HttpUrl, ValidationError

from . import db, extract as extractor, frontend, guides, kma, kakao, map_effect, regions

load_dotenv(FilePath(__file__).resolve().parent.parent / ".env")

log = logging.getLogger(__name__)

DisasterType = Literal[guides.DISASTER_TYPES]  # 행동지침 종류 + other
MAX_BATCH = 500


def db_path() -> str:
    return os.getenv("DB_PATH", db.DEFAULT_PATH)


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect(db_path())
    if stale := regions.recover_stale_jobs(conn):
        log.warning("재시작으로 끊긴 지역 분석 %s건을 실패로 표시했습니다.", stale)
    conn.close()
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
    conn = db.connect(db_path())
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


def get_http(request: Request) -> httpx.AsyncClient:
    # 서버는 lifespan에서 만들어 둠. 시작 단계를 거치지 않는 테스트 클라이언트에서는 처음 쓸 때 만듦
    if not hasattr(request.app.state, "http"):
        request.app.state.http = httpx.AsyncClient()
    return request.app.state.http


KOREA_LAT, KOREA_LON = (32, 39.5), (124, 132)
CURRENT_WEATHER_DAYS = 1  # 카드 날짜가 오늘·어제일 때만 기상청 지금 날씨로 빈 지도 효과 값을 채움


def with_effect_flag(card: dict) -> dict:
    """목록용: 지도 재난 효과 지원 여부와 프론트 종류만 (기상청 호출 없음)"""
    front = map_effect.FRONT_TYPE.get(card["type"])
    return {**card, "map_effect_supported": front is not None, "map_effect_type": front}


def _needs_current_weather(card: dict) -> bool:
    if map_effect.FRONT_TYPE.get(card["type"]) not in map_effect.FROM_KMA_CURRENT:
        return False
    lat, lon, date = card.get("lat"), card.get("lon"), card.get("date") or ""
    if lat is None or lon is None or not (KOREA_LAT[0] <= lat <= KOREA_LAT[1] and KOREA_LON[0] <= lon <= KOREA_LON[1]):
        return False
    since = (datetime.now(kma.KST) - timedelta(days=CURRENT_WEATHER_DAYS)).strftime("%Y-%m-%d")
    return date[:10] >= since


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
    items = [with_effect_flag(db.row_to_disaster(r)) for r in rows]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


EARTH_RADIUS_KM = 6371


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


@app.get("/api/disasters/nearby")
def nearby_disasters(
    lat: float = Query(ge=-90, le=90),
    lon: float = Query(ge=-180, le=180),
    radius_km: float = Query(20, gt=0, le=200),
    limit: int = Query(50, ge=1, le=200),
    conn: sqlite3.Connection = Depends(get_db),
):
    """지도에서 클릭한 지점 근처의 재난 카드 (이미 DB에 있는 것, 바로 응답). 카드 좌표는 기사 속 지역의 대략적인 중심"""
    dlat = radius_km / 111
    dlon = radius_km / (111 * max(math.cos(math.radians(lat)), 0.01))
    rows = conn.execute("SELECT * FROM disasters WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
                        (lat - dlat, lat + dlat, lon - dlon, lon + dlon)).fetchall()
    items = []
    for r in rows:
        d = distance_km(lat, lon, r["lat"], r["lon"])
        if d <= radius_km:
            items.append({**with_effect_flag(db.row_to_disaster(r)), "distance_km": round(d, 1)})
    items.sort(key=lambda c: c["date"] or "", reverse=True)
    return {"items": items[:limit], "total": len(items), "radius_km": radius_km}


@app.get("/api/disasters/{disaster_id}")
async def get_disaster(
    disaster_id: int,
    conn: sqlite3.Connection = Depends(get_db),
    http: httpx.AsyncClient = Depends(get_http),
):
    """카드 상세 + 원문 기사 + map_effect(프론트 koreaDisaster.set(type, params, position)에 그대로 넣는 값)"""
    row = conn.execute("SELECT * FROM disasters WHERE id = ?", (disaster_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "재난을 찾을 수 없습니다.")
    sources = conn.execute(
        "SELECT title, url, source, published_at FROM articles WHERE disaster_id = ?", (disaster_id,)
    ).fetchall()
    card = db.row_to_disaster(row)

    current = None
    if _needs_current_weather(card):
        try:
            current = (await kma.fetch_current(http, card["lat"], card["lon"]))["values"]
        except kma.KmaError as e:  # 기상청이 실패해도 카드는 보여 줌 (기사 값만으로 지도 효과)
            log.warning("지도 효과용 지금 날씨 조회 실패: %s", e.message)
    effect = map_effect.build(card, current)
    return {**with_effect_flag(card), "sources": [dict(s) for s in sources], "map_effect": effect}


async def active_warnings_with_guides(http: httpx.AsyncClient) -> list[dict]:
    try:
        warnings = await kma.fetch_warnings(http)
    except kma.KmaError as e:
        raise HTTPException(e.status, e.message) from None
    return [{**w, "guide_type": guides.GUIDE_TYPE_BY_WARNING.get(w["type"]), "guide_level": w["level"]} for w in warnings]


@app.get("/api/warnings")
async def list_warnings(
    type: str | None = Query(None, pattern="^[A-Z]$", description="특보 종류 코드 (H 폭염, R 호우, D 건조 등)"),
    http: httpx.AsyncClient = Depends(get_http),
):
    """지금 발효 중인 기상특보 (기상청 특보현황). guide_type은 GET /api/guides/{guide_type}로 볼 행동지침."""
    items = await active_warnings_with_guides(http)
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
    items = [{**q, "guide_type": "earthquake",
              "guide_level": f"진도 {q['max_intensity']}" if q["max_intensity"] else None} for q in items]
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
    track = [{**p, "guide_type": "typhoon", "guide_level": f"강도 {p['strength']}" if p["strength"] else None}
             for p in track]
    return {"year": year, "seq": seq, "track": track, "total": len(track)}


@app.get("/api/guides")
def list_guides():
    """모든 재난 행동지침 (app/guides.py). needs_writing=true는 공식 문구가 없어 직접 작성해야 하는 항목."""
    items = [guides.to_response(g) for g in guides.GUIDES]
    return {"items": items, "total": len(items)}


@app.get("/api/guides/active")
async def list_active_guides(
    region: str | None = Query(None, max_length=50, description="지역 이름 일부 (예: 강원, 태백). 비우면 전국"),
    http: httpx.AsyncClient = Depends(get_http),
):
    """지금 특보가 발효 중인 재난의 행동지침. 특보가 내려진 때부터 해제될 때까지만 나옴."""
    warnings = await active_warnings_with_guides(http)
    if region:
        warnings = [w for w in warnings if region in (w["region"] or "") or region in (w["region_parent"] or "")]
    grouped: dict[tuple[str, str], list[dict]] = {}
    for w in warnings:
        guide = guides.GUIDE_BY_TYPE.get(w["guide_type"])
        if guide:
            level = w["guide_level"] if w["guide_level"] in guide.get("levels", {}) else None
            grouped.setdefault((w["guide_type"], level), []).append(w)
    items = [
        {**guides.to_response(guides.GUIDE_BY_TYPE[t], level), "warnings": ws}
        for (t, level), ws in grouped.items()
    ]
    return {"items": items, "total": len(items)}


@app.get("/api/guides/{disaster_type}")
def get_guide(
    disaster_type: str,
    level: str | None = Query(None, max_length=20, description="강도 단계 (예: 주의보, 경보, 진도 Ⅳ, 강도 3). 비우면 모든 단계"),
):
    """재난 하나의 행동지침. 공통 steps + 단계별 criteria(공식 기준)·steps."""
    guide = guides.GUIDE_BY_TYPE.get(disaster_type)
    if guide is None:
        raise HTTPException(404, "행동지침이 없는 재난 종류입니다.")
    try:
        return guides.to_response(guide, level)
    except KeyError:
        names = ", ".join(guide.get("levels", {}))
        raise HTTPException(404, f"없는 단계입니다. 가능한 단계: {names}") from None


# ───────── 지역 변화 분석 (온디맨드) ─────────

RATE_LIMIT = (5, 600)  # 같은 사용자(IP)는 10분에 5번까지 분석 요청
_recent_requests: dict[str, list[float]] = {}


class RegionAnalyzeIn(BaseModel):
    region_name: str = Field(min_length=1, max_length=50)
    latitude: float
    longitude: float
    radius_km: float = 5
    disaster_type: Literal["all", "flood"] = "all"  # all: 모든 재난 이력, flood: 홍수 + 배수시설만
    force_refresh: bool = False


def check_rate_limit(request: Request) -> None:
    # shortcut: 서버 메모리 기준이라 재시작하면 초기화됨, 서버를 여러 대로 늘리면 DB·Redis로 옮김
    forwarded = request.headers.get("x-forwarded-for", "")  # Render는 프록시 뒤라 실제 IP가 이 헤더에 옴
    client = forwarded.split(",")[0].strip() or (request.client.host if request.client else "?")
    count, seconds = RATE_LIMIT
    t = datetime.now().timestamp()
    recent = [s for s in _recent_requests.get(client, []) if t - s < seconds]
    if len(recent) >= count:
        raise HTTPException(429, f"분석 요청이 너무 많습니다. {seconds // 60}분 뒤 다시 시도해 주세요.")
    _recent_requests[client] = [*recent, t]


@app.post("/api/regions/analyze", status_code=202)
def analyze_region(
    body: RegionAnalyzeIn,
    request: Request,
    background: BackgroundTasks,
    conn: sqlite3.Connection = Depends(get_db),
    http: httpx.AsyncClient = Depends(get_http),
):
    """지도에서 클릭한 지점(이 속한 지역)의 과거 재난 이력을 그때 검색·분석. 캐시가 있으면 바로 결과, 없으면 job_id (GET /api/analysis/{job_id})"""
    if not (KOREA_LAT[0] <= body.latitude <= KOREA_LAT[1] and KOREA_LON[0] <= body.longitude <= KOREA_LON[1]):
        raise HTTPException(400, "대한민국 안의 좌표가 아닙니다. 지도에서 위치를 다시 선택해 주세요. (확인 필요)")
    if not 0.5 <= body.radius_km <= 30:
        raise HTTPException(400, "radius_km는 0.5~30 사이여야 합니다.")

    key = regions.region_key(body.region_name)
    hit = regions.cached(conn, key, body.disaster_type, body.force_refresh)
    if hit:
        return {"job_id": hit["job_id"], "status": "completed", "region_id": hit["region_id"], "cached": True,
                "generated_at": hit["generated_at"], "result": regions.analysis_result(conn, hit["region_id"], body.disaster_type)}

    region_id = regions.upsert_region(conn, key, body.latitude, body.longitude, body.radius_km)
    conn.commit()
    running = regions.active_job(conn, region_id, body.disaster_type)
    if running:  # 같은 지역 분석이 이미 진행 중이면 그 작업을 알려 줌 (중복 요청 합치기)
        return {"job_id": running["id"], "status": running["status"], "region_id": region_id, "cached": False}

    if not kakao.configured():
        raise HTTPException(503, "뉴스 검색이 설정되지 않아 새 지역을 분석할 수 없습니다.")
    if not os.getenv("GEMINI_API_KEY"):
        raise HTTPException(503, "AI 분석(GEMINI_API_KEY)이 설정되지 않아 새 지역을 분석할 수 없습니다.")
    if regions.active_job_count(conn) >= regions.MAX_ACTIVE_JOBS:
        raise HTTPException(429, "지금 분석 중인 지역이 많습니다. 잠시 후 다시 시도해 주세요.")
    check_rate_limit(request)

    job_id = regions.create_job(conn, region_id, body.disaster_type)
    background.add_task(regions.run_job, job_id, db_path(), http)
    return {"job_id": job_id, "status": "queued", "region_id": region_id, "cached": False}


@app.get("/api/analysis/{job_id}")
def get_analysis(job_id: str = Path(max_length=64), conn: sqlite3.Connection = Depends(get_db)):
    """분석 진행 상태. 완료되면 result에 타임라인(changes)·시나리오·출처. 실패하면 error에 다시 시도 안내"""
    job = conn.execute("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        raise HTTPException(404, "분석 작업을 찾을 수 없습니다. 서버가 초기화됐다면 다시 요청해 주세요.")
    done = job["status"] == "completed"
    return {"job_id": job["id"], "region_id": job["region_id"], "status": job["status"], "stage": job["stage"],
            "progress": job["progress"], "error": job["error_message"], "updated_at": job["updated_at"],
            "result": regions.analysis_result(conn, job["region_id"], job["disaster_type"]) if done else None}


def _region_or_404(conn: sqlite3.Connection, region_id: int) -> dict:
    region = regions.get_region(conn, region_id)
    if region is None:
        raise HTTPException(404, "분석한 적 없는 지역입니다.")
    return region


@app.get("/api/regions/{region_id}/timeline")
def get_region_timeline(region_id: int, disaster_type: Literal["all", "flood"] = "all",
                        conn: sqlite3.Connection = Depends(get_db)):
    """날짜순 도시 변화. evidence_status: verified(근거 문구 확인) / inferred(근거 문구 확인 안 됨)"""
    region = _region_or_404(conn, region_id)
    changes = regions.load_changes(conn, region_id, disaster_type)
    return {"region": region, "changes": changes, "notice": regions.NOTICE,
            "empty_message": None if changes else regions.EMPTY}


@app.get("/api/regions/{region_id}/scenarios")
def get_region_scenarios(region_id: int, disaster_type: Literal["all", "flood"] = "all",
                         conn: sqlite3.Connection = Depends(get_db)):
    """같은 비·시간에서 배수 능력만 바꾼 전·후. before/after를 koreaDisaster.set(type, params, position)에 넣음"""
    _region_or_404(conn, region_id)
    result = regions.analysis_result(conn, region_id, disaster_type)
    if result["scenario"] is None:
        raise HTTPException(404, "아직 분석이 끝나지 않은 지역입니다.")
    return result["scenario"]


class PointIn(BaseModel):
    latitude: float = Field(ge=32, le=39.5)
    longitude: float = Field(ge=124, le=132)


@app.post("/api/regions/changes/{change_id}/card")
def save_change_as_card(change_id: int, at: PointIn | None = None, conn: sqlite3.Connection = Depends(get_db)):
    """'이 지점 이력'의 재난 항목을 재난 카드로 저장 (최근 탭에 보임). 위치는 at(클릭한 지점), 없으면 항목·지역 좌표.
    분석 결과에 있는 항목만 올릴 수 있고, 같은 항목은 한 번만 만들어짐 (아무 내용이나 넣는 주소가 아님)"""
    saved = regions.change_to_card(conn, change_id, at.model_dump() if at else None)
    if saved is None:
        raise HTTPException(404, "저장할 수 있는 재난 이력 항목이 아닙니다. 지역을 다시 분석해 주세요.")
    disaster_id, created = saved
    return {"disaster_id": disaster_id, "created": created}


# 프론트 화면 (반드시 맨 끝: 위의 API 주소가 먼저 잡히고 나머지 주소만 프론트 파일로 감)
_frontend = frontend.frontend_dir()
if _frontend:
    log.warning("프론트 파일을 함께 보냅니다: %s", _frontend)
    app.mount("/", frontend.static_app(_frontend), name="frontend")
