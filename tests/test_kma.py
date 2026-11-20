from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app import kma
from app.main import app, get_http

# 실제 특보현황(wrn_now_data) 응답 형식
WARNINGS = """#START7777
L1014000, 서해5도                                 , L1014200, 백령도.대청도                           , 202611180400, 202611180500, 강풍  , 주의    , 발표, 21일 오전(09시~12시),=
L1020000, 강원도                                  , L1020300, 태백                                    , 202611201000, 202611201000, 건조  , 주의    , 변경, ,=
L1010000, 경기도                                  , L1010200, 광명                                    , 202611190900, 202611191000, 폭염  , 경보    , 해제, ,=
#7777END"""

FORECAST = {"response": {"header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"}, "body": {"items": {"item": [
    {"fcstDate": "20261120", "fcstTime": "0600", "category": "TMP", "fcstValue": "-2"},
    {"fcstDate": "20261120", "fcstTime": "0600", "category": "PCP", "fcstValue": "강수없음"},
    {"fcstDate": "20261120", "fcstTime": "0700", "category": "TMP", "fcstValue": "-1"},
]}}}}


@pytest.fixture
def kma_api(monkeypatch, tmp_path):
    """기상청 대신 응답하는 가짜 서버. 호출된 경로를 calls에 기록."""
    monkeypatch.setenv("KMA_API_KEY", "test-key")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    kma._cache.clear()
    state = {"calls": [], "status": 200}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"].append(request.url.path)
        if state["status"] != 200:
            return httpx.Response(state["status"], text="denied")
        if request.url.path.endswith("wrn_now_data.php"):
            return httpx.Response(200, content=WARNINGS.encode("cp949"))  # 실제 기상청처럼 EUC-KR
        return httpx.Response(200, json=FORECAST)

    app.dependency_overrides[get_http] = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    yield state
    app.dependency_overrides.clear()
    kma._cache.clear()


@pytest.fixture
def client():
    return TestClient(app)


def test_latlon_to_grid_matches_kma_seoul():
    assert kma.latlon_to_grid(37.5665, 126.9780) == (60, 127)


@pytest.mark.parametrize("now, expected", [
    (datetime(2026, 11, 20, 5, 5), ("20261120", "0200")),
    (datetime(2026, 11, 20, 5, 15), ("20261120", "0500")),
    (datetime(2026, 11, 20, 1, 0), ("20261119", "2300")),
])
def test_latest_base(now, expected):
    assert kma.latest_base(now) == expected


def test_warnings_from_current_status(client, kma_api):
    items = client.get("/api/warnings").json()["items"]
    # 해제된 폭염은 빠지고, 종류 이름은 코드로 바꾸고, 행동지침 종류를 붙임
    assert items == [
        {"region_id": "L1020300", "region": "태백", "region_parent": "강원도", "type": "D", "type_name": "건조",
         "level": "주의보", "command": "변경", "issued_at": "202611201000", "effective_at": "202611201000",
         "end_notice": None, "guide_type": "dry"},
        {"region_id": "L1014200", "region": "백령도.대청도", "region_parent": "서해5도", "type": "W", "type_name": "강풍",
         "level": "주의보", "command": "발표", "issued_at": "202611180400", "effective_at": "202611180500",
         "end_notice": "21일 오전(09시~12시)", "guide_type": "strongwind"},
    ]


def test_warnings_are_cached(client, kma_api):
    client.get("/api/warnings")
    client.get("/api/warnings?type=W")
    assert len(kma_api["calls"]) == 1


def test_warnings_filter_validation(client, kma_api):
    assert client.get("/api/warnings?type=W").json()["total"] == 1
    assert client.get("/api/warnings?type=heat").status_code == 422


def test_forecast_groups_by_time(client, kma_api):
    body = client.get("/api/forecast?lat=37.5665&lon=126.9780").json()
    assert (body["nx"], body["ny"]) == (60, 127)
    assert body["items"][0] == {"time": "202611200600", "TMP": -2.0, "PCP": "강수없음"}
    assert len(body["items"]) == 2


def test_forecast_rejects_outside_korea(client, kma_api):
    assert client.get("/api/forecast?lat=51.5&lon=-0.1").status_code == 422


def test_kma_error_hides_key(client, kma_api):
    kma_api["status"] = 403
    res = client.get("/api/warnings")
    assert res.status_code == 502
    assert "test-key" not in res.text


def test_retries_once_on_504(client, kma_api):
    kma_api["status"] = 504
    assert client.get("/api/warnings").status_code == 502
    assert kma_api["calls"] == ["/api/typ01/url/wrn_now_data.php"] * 2


def test_serves_previous_result_when_kma_fails(client, kma_api, monkeypatch):
    first = client.get("/api/warnings").json()
    monkeypatch.setattr(kma, "WARNINGS_TTL", 0)
    kma._cache["warnings"] = (0, first["items"])  # 만료된 이전 결과
    monkeypatch.setattr(kma.time, "monotonic", lambda: 1.0)
    kma_api["status"] = 504
    assert client.get("/api/warnings").json() == first


def test_missing_key(client, kma_api, monkeypatch):
    monkeypatch.delenv("KMA_API_KEY")
    assert client.get("/api/forecast?lat=37.5&lon=127").status_code == 503
    assert kma_api["calls"] == []


CURRENT = {"response": {"header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"}, "body": {"items": {"item": [
    {"category": "T1H", "obsrValue": "-0.4"},
    {"category": "RN1", "obsrValue": "0"},
    {"category": "REH", "obsrValue": "37"},
]}}}}


@pytest.mark.parametrize("now, expected", [
    (datetime(2026, 11, 20, 19, 45), ("20261120", "1900")),
    (datetime(2026, 11, 20, 19, 30), ("20261120", "1800")),
    (datetime(2026, 11, 20, 0, 10), ("20261119", "2300")),
])
def test_latest_observation(now, expected):
    assert kma.latest_observation(now) == expected


def test_current_weather(client, kma_api, monkeypatch):
    async def fake_get(http, path, params):
        assert path.endswith("getUltraSrtNcst") and (params["nx"], params["ny"]) == (60, 127)
        return __import__("json").dumps(CURRENT)

    monkeypatch.setattr(kma, "_get", fake_get)
    body = client.get("/api/current?lat=37.5665&lon=126.9780").json()
    assert body["observed"] is True
    assert body["values"] == {"T1H": -0.4, "RN1": 0.0, "REH": 37.0}


def test_retries_once_on_connection_error(client, kma_api):
    tries = []

    def flaky(request):
        tries.append(1)
        if len(tries) == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, content=WARNINGS.encode("cp949"))

    app.dependency_overrides[get_http] = lambda: httpx.AsyncClient(transport=httpx.MockTransport(flaky))
    assert client.get("/api/warnings").status_code == 200
    assert len(tries) == 2
