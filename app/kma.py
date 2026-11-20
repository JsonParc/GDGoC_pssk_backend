"""기상청 API허브 호출: 기상특보, 단기예보. 인증키는 환경변수 KMA_API_KEY."""

import json
import logging
import math
import os
import time
from datetime import datetime, timedelta, timezone

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://apihub.kma.go.kr/api"
KST = timezone(timedelta(hours=9))
TIMEOUT_SECONDS = 60  # 응답이 15초 넘게 걸린 적이 있음

WARNING_TYPES = {
    "W": "강풍", "R": "호우", "C": "한파", "D": "건조", "O": "폭풍해일", "N": "지진해일",
    "V": "풍랑", "T": "태풍", "S": "대설", "Y": "황사", "H": "폭염", "F": "안개",
}
WARNING_LEVELS = {"1": "예비특보", "2": "주의보", "3": "경보", "4": "중대경보"}
CLEARED_COMMANDS = {"3", "4", "7"}  # 해제, 대치해제, 변경해제
# shortcut: 최근 14일 발표분만 보고 발효 중인지 판단. 더 오래 이어진 특보는 빠짐 -> wrn_now_data.php 활용신청 후 교체
WARNING_WINDOW_DAYS = 14

WARNINGS_TTL = 10 * 60
REGIONS_TTL = 24 * 60 * 60
FORECAST_TTL = 30 * 60
STALE_GRACE = 6 * 60 * 60  # 기상청이 실패하면 6시간 안의 이전 결과를 대신 줌
RETRY_STATUSES = {502, 503, 504}  # 기상청이 가끔 30초 뒤 504를 줌, 바로 다시 부르면 대개 성공
FORECAST_BASE_HOURS = (2, 5, 8, 11, 14, 17, 20, 23)
FORECAST_NUMERIC = {"TMP", "REH", "POP", "WSD", "TMN", "TMX", "UUU", "VVV", "VEC", "WAV"}


class KmaError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# shortcut: 프로세스 메모리 캐시라 서버를 여러 개 띄우면 각자 호출함 -> 그때 Redis 등으로 교체
_cache: dict = {}


async def _cached(key, ttl: int, load):
    hit = _cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    try:
        value = await load()
    except KmaError:
        if hit and hit[0] + STALE_GRACE > time.monotonic():
            log.warning("기상청 호출 실패, 이전 결과 사용: %s", key)
            return hit[1]
        raise
    _cache[key] = (time.monotonic() + ttl, value)
    return value


async def _get(client: httpx.AsyncClient, path: str, params: dict) -> str:
    key = os.getenv("KMA_API_KEY")
    if not key:
        raise KmaError(503, "KMA_API_KEY가 설정되지 않았습니다.")
    for attempt in (1, 2):
        try:
            res = await client.get(f"{BASE_URL}/{path}", params={**params, "authKey": key}, timeout=TIMEOUT_SECONDS)
        except httpx.HTTPError as e:
            # 예외 메시지에 키가 든 URL이 있어 종류만 남김
            log.warning("기상청 호출 실패: %s %s", path, type(e).__name__)
            raise KmaError(502, "기상청 API에 연결하지 못했습니다.") from None
        if res.status_code not in RETRY_STATUSES:
            break
        log.warning("기상청 응답 %s (%s번째): %s", res.status_code, attempt, path)
    if res.status_code != 200:
        log.warning("기상청 응답 오류: %s %s", path, res.status_code)
        raise KmaError(502, f"기상청 API 오류 ({res.status_code})")
    try:
        return res.content.decode("utf-8")
    except UnicodeDecodeError:  # typ01 일부(wrn_reg 등)는 EUC-KR로 응답
        return res.content.decode("cp949", "replace")


def _data_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip() and not line.startswith("#")]


def parse_regions(text: str, now: str) -> dict[str, str]:
    """wrn_reg 응답 -> {특보구역코드: 이름}. 지금 유효한 구역만."""
    regions = {}
    for line in _data_lines(text):
        parts = line.split()
        if len(parts) >= 6 and parts[2] >= now:
            regions[parts[0]] = " ".join(parts[6:]) or parts[5]
    return regions


def parse_active_warnings(text: str, regions: dict[str, str]) -> list[dict]:
    """wrn_met_data 응답 -> 지금 발효 중인 특보. (구역, 종류)마다 마지막 명령이 해제가 아니면 발효 중."""
    latest = {}
    for line in _data_lines(text):
        fields = [f.strip() for f in line.split(",")]
        if len(fields) < 8:
            continue
        issued_at, effective_at, _, _, region_id, kind, level, command = fields[:8]
        key = (region_id, kind)
        if key not in latest or effective_at >= latest[key]["effective_at"]:
            latest[key] = {"issued_at": issued_at, "effective_at": effective_at, "level": level, "command": command}

    active = [
        {
            "region_id": region_id,
            "region": regions.get(region_id),
            "type": kind,
            "type_name": WARNING_TYPES.get(kind, kind),
            "level": WARNING_LEVELS.get(w["level"], w["level"]),
            "issued_at": w["issued_at"],
            "effective_at": w["effective_at"],
        }
        for (region_id, kind), w in latest.items()
        if w["command"] not in CLEARED_COMMANDS
    ]
    return sorted(active, key=lambda w: (w["type"], w["region_id"]))


async def fetch_regions(client: httpx.AsyncClient) -> dict[str, str]:
    async def load():
        text = await _get(client, "typ01/url/wrn_reg.php", {"tmfc": "0"})
        return parse_regions(text, datetime.now(KST).strftime("%Y%m%d%H%M"))

    return await _cached("regions", REGIONS_TTL, load)


async def fetch_warnings(client: httpx.AsyncClient) -> list[dict]:
    async def load():
        regions = await fetch_regions(client)
        now = datetime.now(KST)
        start = now - timedelta(days=WARNING_WINDOW_DAYS)
        text = await _get(client, "typ01/url/wrn_met_data.php", {
            "reg": "0", "wrn": "A", "disp": "0", "help": "0",
            "tmfc1": start.strftime("%Y%m%d%H%M"), "tmfc2": now.strftime("%Y%m%d%H%M"),
        })
        return parse_active_warnings(text, regions)

    return await _cached("warnings", WARNINGS_TTL, load)


def latlon_to_grid(lat: float, lon: float) -> tuple[int, int]:
    """위경도 -> 단기예보 격자(nx, ny). 기상청 공식 람베르트 투영식."""
    re_, grid, slat1, slat2, olon, olat, xo, yo = 6371.00877, 5.0, 30.0, 60.0, 126.0, 38.0, 43, 136
    rad = math.pi / 180
    re_ /= grid
    s1, s2, olon, olat = slat1 * rad, slat2 * rad, olon * rad, olat * rad
    sn = math.log(math.cos(s1) / math.cos(s2)) / math.log(
        math.tan(math.pi / 4 + s2 / 2) / math.tan(math.pi / 4 + s1 / 2)
    )
    sf = math.tan(math.pi / 4 + s1 / 2) ** sn * math.cos(s1) / sn
    ro = re_ * sf / math.tan(math.pi / 4 + olat / 2) ** sn
    ra = re_ * sf / math.tan(math.pi / 4 + lat * rad / 2) ** sn
    theta = (lon * rad - olon + math.pi) % (2 * math.pi) - math.pi
    theta *= sn
    return int(ra * math.sin(theta) + xo + 0.5), int(ro - ra * math.cos(theta) + yo + 0.5)


def latest_base(now: datetime) -> tuple[str, str]:
    """가장 최근 단기예보 발표 시각. 발표 후 약 10분 뒤부터 조회됨."""
    ready = now - timedelta(minutes=10)
    hours = [h for h in FORECAST_BASE_HOURS if h <= ready.hour]
    if not hours:
        ready -= timedelta(days=1)
        hours = [23]
    return ready.strftime("%Y%m%d"), f"{hours[-1]:02d}00"


def parse_forecast(data: dict) -> list[dict]:
    """getVilageFcst JSON -> 시각별 묶음. 수치 항목은 숫자로, PCP·SNO 등은 기상청 문자열 그대로."""
    header = data["response"]["header"]
    if header["resultCode"] != "00":
        raise KmaError(502, f"기상청 응답: {header['resultMsg']}")
    slots: dict[str, dict] = {}
    for item in data["response"]["body"]["items"]["item"]:
        when = item["fcstDate"] + item["fcstTime"]
        value = item["fcstValue"]
        slot = slots.setdefault(when, {"time": when})
        slot[item["category"]] = float(value) if item["category"] in FORECAST_NUMERIC else value
    return [slots[k] for k in sorted(slots)]


async def fetch_forecast(client: httpx.AsyncClient, lat: float, lon: float) -> dict:
    nx, ny = latlon_to_grid(lat, lon)
    base_date, base_time = latest_base(datetime.now(KST))

    async def load():
        text = await _get(client, "typ02/openApi/VilageFcstInfoService_2.0/getVilageFcst", {
            "pageNo": "1", "numOfRows": "1000", "dataType": "JSON",
            "base_date": base_date, "base_time": base_time, "nx": nx, "ny": ny,
        })
        try:
            items = parse_forecast(json.loads(text))
        except (ValueError, KeyError, TypeError):
            log.warning("단기예보 응답 형식이 예상과 다름")
            raise KmaError(502, "기상청 응답 형식이 예상과 다릅니다.") from None
        return {"nx": nx, "ny": ny, "base": base_date + base_time, "items": items}

    return await _cached(("forecast", nx, ny, base_date, base_time), FORECAST_TTL, load)


# ---- 지진 (지진/화산 1, eqk_now.php) ----

EARTHQUAKE_TTL = 5 * 60
EARTHQUAKE_TYPES = {
    "2": "국외지진정보", "3": "국내지진통보", "5": "지진정보(재통보)", "10": "지진현장경보",
    "11": "지진조기경보", "12": "국외지진조기경보", "14": "지진속보",
}
OVERSEAS_EARTHQUAKE_TYPES = {"2", "12"}


def _number(value: str) -> float | None:
    """기상청 숫자 칸. 빈 값·-999(자료 없음)는 None."""
    try:
        n = float(value)
    except ValueError:
        return None
    return None if n <= -999 else n


def parse_earthquakes(text: str) -> list[dict]:
    """eqk_now 응답. 앞 7칸은 공백 구분, 그 뒤 '위치,진도,참고사항,정정사항,='은 쉼표 구분."""
    quakes = []
    for line in _data_lines(text):
        parts = line.split(None, 7)
        if len(parts) < 8:
            continue
        kind, issued_at, seq, occurred_at, magnitude, lat, lon, rest = parts
        location, intensity, remark = ([f.strip() for f in rest.split(",")] + ["", "", ""])[:3]
        quakes.append({
            "type": kind,
            "type_name": EARTHQUAKE_TYPES.get(kind, kind),
            "overseas": kind in OVERSEAS_EARTHQUAKE_TYPES,
            "issued_at": issued_at,
            "seq": seq,
            "occurred_at": occurred_at.split(".")[0],  # 년월일시분초
            "magnitude": _number(magnitude),
            "lat": _number(lat),
            "lon": _number(lon),
            "location": location,
            "intensity": intensity or None,
            "remark": remark or None,
        })
    return sorted(quakes, key=lambda q: q["occurred_at"], reverse=True)


async def fetch_earthquakes(client: httpx.AsyncClient) -> list[dict]:
    async def load():
        text = await _get(client, "typ01/url/eqk_now.php", {"tm": "", "disp": "0", "help": "0"})
        return parse_earthquakes(text)

    return await _cached("earthquakes", EARTHQUAKE_TTL, load)


# ---- 태풍 (태풍 1.1 typ_lst.php, 1.2 typ_data.php) ----

TYPHOON_LIST_TTL = 60 * 60
TYPHOON_TRACK_TTL = 30 * 60
KOREA_EFFECT = {"1": "상륙", "2": "직접영향", "3": "간접영향", "4": "없음"}


def parse_typhoon_list(text: str) -> list[dict]:
    """typ_lst 응답: 년도 번호 진행여부 한반도영향 시작 종료(UTC) 이름 영문이름 설명"""
    typhoons = []
    for line in _data_lines(text):
        parts = line.split(None, 7)
        if len(parts) < 7:
            continue
        year, seq, now, effect, start, end, name = parts[:7]
        name_en, _, remark = (parts[7] if len(parts) > 7 else "").partition(" ")
        typhoons.append({
            "year": int(year),
            "seq": int(seq),
            "name": name,
            "name_en": name_en or None,
            "ongoing": now == "1",
            "korea_effect": KOREA_EFFECT.get(effect, effect),
            "start_utc": start,
            "end_utc": end,
            "remark": remark.strip() or None,
        })
    return typhoons


def parse_typhoon_track(text: str) -> list[dict]:
    """typ_data 응답. 앞 18칸은 공백 구분, 그 뒤 '위치 예외방향25,예외반경25,'."""
    points = []
    for line in _data_lines(text):
        parts = line.split(None, 18)
        if len(parts) < 19:
            continue
        ft, _, _, seq, _, analyzed_at, forecast_at, lat, lon, direction, speed, pressure, wind, r15, r25 = parts[:15]
        location = parts[18].split(",")[0].rsplit(None, 1)[0].strip()  # 끝의 예외방향(ED25) 칸 제거
        observed = ft == "0"
        points.append({
            "observed": observed,  # True 분석(관측), False 예측
            "time_utc": analyzed_at if observed else forecast_at,
            "analyzed_at_utc": analyzed_at,
            "seq": int(seq),
            "lat": _number(lat),
            "lon": _number(lon),
            "direction": direction,
            "speed_kmh": _number(speed),
            "pressure_hpa": _number(pressure),
            "max_wind_ms": _number(wind),
            "radius_15ms_km": _number(r15),
            "radius_25ms_km": _number(r25),
            "location": location,
        })
    return sorted(points, key=lambda p: (p["time_utc"], not p["observed"]))


async def fetch_typhoons(client: httpx.AsyncClient, year: int) -> list[dict]:
    async def load():
        text = await _get(client, "typ01/url/typ_lst.php", {"YY": str(year), "disp": "0", "help": "0"})
        return parse_typhoon_list(text)

    return await _cached(("typhoons", year), TYPHOON_LIST_TTL, load)


async def fetch_typhoon_track(client: httpx.AsyncClient, year: int, seq: int) -> list[dict]:
    async def load():
        text = await _get(client, "typ01/url/typ_data.php", {
            "YY": str(year), "typ": str(seq), "seq": "", "mode": "0", "disp": "0", "help": "0",
        })
        return parse_typhoon_track(text)

    return await _cached(("typhoon_track", year, seq), TYPHOON_TRACK_TTL, load)
