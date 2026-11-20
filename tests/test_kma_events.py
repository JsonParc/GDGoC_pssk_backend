import httpx
import pytest
from fastapi.testclient import TestClient

from app import kma
from app.main import app, get_http

# 실제 기상청 응답에서 가져온 줄
EARTHQUAKES = """#START7777
  2 202611190441 1348 20261119041423.000 6.1  -57.7500 -146.7500 남태평양 해령,,국외지진정보. 미국지질조사소(USGS) 분석결과임.,,=
  3 202611182124 1346 20261118202823.000 2.5   41.2500  129.3200 북한 함경북도 길주 북북서 33km 지역,최대진도 Ⅰ,자연지진으로 분석됨,,=
#7777END"""

TYPHOONS = """#START7777
2025    1    2    4 202506110000 202506141800 우딥                 WUTIP                제1호 태풍 우딥(WUTIP)은 마카오에서 제출한 이름으로 나비를 의미함.
2025    3    1    2 202507021800 202507080000 문                   MUN                  제3호 태풍 문(MUN)
#7777END"""

TRACK = """#START7777
   0 2025    1    2    0 202506110600 202506110600   16.8  112.5  WNW   26  990   20    260   -999    0    SW   170 베트남 다낭 동쪽 약 450 km 부근 해상                                                                   -,-999,
   0 2025    1    1    0 202506110000 202506110000   16.1  113.8   NE   38  994   18    260   -999    0    SW   170 베트남 다낭 동쪽 약 580 km 부근 해상                                                                   -,-999,
   1 2025    1    2   12 202506110600 202506111800   17.5  111.0    W   20  985   23    280    80   120    SW   170 베트남 다낭 동쪽 약 250 km 부근 해상                                                                   -,-999,
#7777END"""


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("KMA_API_KEY", "test-key")
    kma._cache.clear()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        path = request.url.path
        if path.endswith("eqk_now.php"):
            return httpx.Response(200, content=EARTHQUAKES.encode("cp949"))
        if path.endswith("typ_lst.php"):
            return httpx.Response(200, content=TYPHOONS.encode("cp949"))
        if path.endswith("typ_data.php"):
            text = TRACK if request.url.params["typ"] == "1" else "#START7777\n#7777END"
            return httpx.Response(200, content=text.encode("cp949"))
        return httpx.Response(404)

    app.dependency_overrides[get_http] = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    yield TestClient(app), calls
    app.dependency_overrides.clear()
    kma._cache.clear()


def test_earthquakes_have_epicenter(client):
    c, _ = client
    items = c.get("/api/earthquakes").json()["items"]
    assert items[0] == {
        "type": "2", "type_name": "국외지진정보", "overseas": True, "issued_at": "202611190441", "seq": "1348",
        "occurred_at": "20261119041423", "magnitude": 6.1, "lat": -57.75, "lon": -146.75,
        "location": "남태평양 해령", "intensity": None, "remark": "국외지진정보. 미국지질조사소(USGS) 분석결과임.",
    }
    assert items[1]["location"] == "북한 함경북도 길주 북북서 33km 지역"
    assert items[1]["intensity"] == "최대진도 Ⅰ"


def test_earthquakes_filter_domestic_and_cache(client):
    c, calls = client
    domestic = c.get("/api/earthquakes?overseas=false").json()
    assert [q["seq"] for q in domestic["items"]] == ["1346"]
    c.get("/api/earthquakes")
    assert len(calls) == 1


def test_typhoon_list(client):
    c, _ = client
    body = c.get("/api/typhoons?year=2025").json()
    assert body["total"] == 2
    assert body["items"][0] == {
        "year": 2025, "seq": 1, "name": "우딥", "name_en": "WUTIP", "ongoing": False, "korea_effect": "없음",
        "start_utc": "202506110000", "end_utc": "202506141800",
        "remark": "제1호 태풍 우딥(WUTIP)은 마카오에서 제출한 이름으로 나비를 의미함.",
    }
    assert body["items"][1]["korea_effect"] == "직접영향"


def test_typhoon_track_sorted_with_observed_flag(client):
    c, _ = client
    track = c.get("/api/typhoons/2025/1").json()["track"]
    assert [p["time_utc"] for p in track] == ["202506110000", "202506110600", "202506111800"]
    first, _, forecast = track
    assert first == {
        "observed": True, "time_utc": "202506110000", "analyzed_at_utc": "202506110000", "seq": 1,
        "lat": 16.1, "lon": 113.8, "direction": "NE", "speed_kmh": 38.0, "pressure_hpa": 994.0,
        "max_wind_ms": 18.0, "radius_15ms_km": 260.0, "radius_25ms_km": None,
        "location": "베트남 다낭 동쪽 약 580 km 부근 해상",
    }
    assert forecast["observed"] is False and forecast["radius_25ms_km"] == 80.0


def test_typhoon_track_not_found_and_validation(client):
    c, _ = client
    assert c.get("/api/typhoons/2025/2").status_code == 404
    assert c.get("/api/typhoons/2025/0").status_code == 422
    assert c.get("/api/typhoons?year=1800").status_code == 422
