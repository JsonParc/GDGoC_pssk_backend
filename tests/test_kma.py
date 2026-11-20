from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app import kma
from app.main import app, get_http

REGIONS = """#START7777
# REG_ID TM_ST TM_ED REG_SP REG_UP REG_KO REG_NAME
L1010200 200507010000 210012310000 00000013 L1010000 광명 광명시
L1010300 200507010000 210012310000 00000013 L1010000 과천 과천시
L9999999 200507010000 201001010000 00000013 L1010000 옛구역 옛구역
#7777END"""

WARNINGS = """#START7777
202611180900, 202611181000, 202611180850, 109, L1010200,   H,   2,   1,  00,   4,   101, =
202611190900, 202611191000, 202611190850, 109, L1010200,   H,   3,   2,  00,   4,   101, =
202611180900, 202611181000, 202611180850, 109, L1010300,   R,   2,   1,  00,   4,   101, =
202611190900, 202611191000, 202611190850, 109, L1010300,   R,   2,   3,  00,   4,   101, =
#7777END"""

FORECAST = {"response": {"header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"}, "body": {"items": {"item": [
    {"fcstDate": "20261120", "fcstTime": "0600", "category": "TMP", "fcstValue": "-2"},
    {"fcstDate": "20261120", "fcstTime": "0600", "category": "PCP", "fcstValue": "강수없음"},
    {"fcstDate": "20261120", "fcstTime": "0700", "category": "TMP", "fcstValue": "-1"},
]}}}}


@pytest.fixture
def kma_api(monkeypatch):
    """기상청 대신 응답하는 가짜 서버. 호출된 경로를 calls에 기록."""
    monkeypatch.setenv("KMA_API_KEY", "test-key")
    kma._cache.clear()
    state = {"calls": [], "status": 200}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"].append(request.url.path)
        if state["status"] != 200:
            return httpx.Response(state["status"], text="denied")
        if request.url.path.endswith("wrn_reg.php"):
            return httpx.Response(200, content=REGIONS.encode("cp949"))  # 실제 기상청처럼 EUC-KR
        if request.url.path.endswith("wrn_met_data.php"):
            return httpx.Response(200, text=WARNINGS)
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


def test_warnings_keep_active_with_region_names(client, kma_api):
    body = client.get("/api/warnings").json()
    # 폭염은 경보로 대치되어 발효 중, 호우는 해제됨
    assert body["items"] == [{
        "region_id": "L1010200", "region": "광명시", "type": "H", "type_name": "폭염",
        "level": "경보", "issued_at": "202611190900", "effective_at": "202611191000",
    }]


def test_warnings_are_cached(client, kma_api):
    client.get("/api/warnings")
    client.get("/api/warnings?type=H")
    assert len(kma_api["calls"]) == 2  # 구역 1번 + 특보 1번


def test_warnings_filter_validation(client, kma_api):
    assert client.get("/api/warnings?type=R").json()["total"] == 0
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
    assert kma_api["calls"] == ["/api/typ01/url/wrn_reg.php"] * 2


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
