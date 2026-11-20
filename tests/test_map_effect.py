from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import extract as extractor
from app import guides, kma, map_effect
from app.main import app

TOKEN = "test-token"
ARTICLE = {"title": "전북 폭설", "url": "https://news.example.com/s1", "published_at": "2026-11-20"}
FRONT_TYPES = {"typhoon", "flood", "quake", "tsunami", "sealevel", "wildfire", "heatwave", "snow"}  # 뷰어 js/disaster.js


def test_mapping_uses_known_types_and_extracted_fields():
    for card_type, front in map_effect.FRONT_TYPE.items():
        assert card_type in guides.DISASTER_TYPES and front in FRONT_TYPES
    viz_fields = set(extractor.RESPONSE_SCHEMA["properties"]["disasters"]["items"]["properties"]["viz_params"]["properties"])
    used = {f for m in map_effect.FROM_ARTICLE.values() for fs in m.values() for f in fs}
    used |= {f for pair in map_effect.DERIVED.values() for f in pair}
    assert used <= viz_fields


def card(**fields):
    return {"type": "heavysnow", "lat": 35.7, "lon": 127.1, "viz_params": {}, **fields}


def test_snow_from_article_and_derived_with_position():
    e = map_effect.build(card(viz_params={"snowfall_cm": 15.3, "duration_hours": 3, "max_wind_ms": 30, "min_temp_c": -6}))
    assert e == {
        "supported": True, "type": "snow",
        "params": {"hours": 3, "wind": 30, "temp": -6, "rate": 5.1},  # 범위 맞추기는 프론트가 함
        "position": {"longitude": 127.1, "latitude": 35.7},
        "sources": {"hours": "article", "wind": "article", "temp": "article", "rate": "derived"},
    }


def test_article_value_wins_over_kma():
    e = map_effect.build(card(type="wildfire", viz_params={"burned_area_ha": 3000, "humidity_pct": 20}),
                         {"REH": 60, "WSD": 4, "VEC": 270})
    assert e["params"] == {"area": 3000, "humidity": 20, "wind": 4, "dir": 270}
    assert e["sources"] == {"area": "article", "humidity": "article", "wind": "kma_current", "dir": "kma_current"}


@pytest.mark.parametrize("card_type, front", [("earthquake", "quake"), ("coldwave", "snow"), ("typhoon", "typhoon")])
def test_type_names_follow_front(card_type, front):
    assert map_effect.build(card(type=card_type))["type"] == front


def test_no_position_without_coordinates():
    assert map_effect.build(card(lat=None))["position"] is None


def test_unsupported_type():
    assert map_effect.build(card(type="fog", viz_params={"max_wind_ms": 3})) == {
        "supported": False, "type": None, "params": {}, "position": None, "sources": {}}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("INGEST_TOKEN", TOKEN)
    monkeypatch.setenv("KMA_API_KEY", "test-key")
    kma._cache.clear()
    return TestClient(app, headers={"X-Ingest-Token": TOKEN})


def _add_card(client, monkeypatch, **fields):
    group = {"article_ids": [0], "title": "카드", "summary": "", **fields}
    monkeypatch.setattr(extractor, "extract_batch", lambda arts: [group])
    client.post("/api/articles", json=[ARTICLE])
    return client.get("/api/disasters").json()["items"][0]


def test_detail_fills_recent_korean_card_with_current_weather(client, monkeypatch):
    calls = []

    async def fake_current(http, lat, lon):
        calls.append((lat, lon))
        return {"values": {"WSD": 6.5, "T1H": -2.0}}

    monkeypatch.setattr(kma, "fetch_current", fake_current)
    today = datetime.now(kma.KST).strftime("%Y-%m-%d")
    item = _add_card(client, monkeypatch, type="heavysnow", lat=35.7, lon=127.1, date=today,
                     viz_params={"snowfall_cm": 15.3})
    assert (item["map_effect_supported"], item["map_effect_type"]) == (True, "snow")
    assert calls == []  # 목록은 기상청을 부르지 않음

    e = client.get(f"/api/disasters/{item['id']}").json()["map_effect"]
    assert e["params"] == {"wind": 6.5, "temp": -2.0}
    assert e["sources"] == {"wind": "kma_current", "temp": "kma_current"}
    assert e["position"] == {"longitude": 127.1, "latitude": 35.7}
    assert calls == [(35.7, 127.1)]


def test_detail_skips_kma_for_old_cards(client, monkeypatch):
    async def must_not_call(*a):
        raise AssertionError("기상청을 부르면 안 됨")

    monkeypatch.setattr(kma, "fetch_current", must_not_call)
    old = (datetime.now(kma.KST) - timedelta(days=10)).strftime("%Y-%m-%d")
    item = _add_card(client, monkeypatch, type="wildfire", lat=36.3, lon=128.7, date=old, viz_params={"burned_area_ha": 3000})
    assert client.get(f"/api/disasters/{item['id']}").json()["map_effect"]["params"] == {"area": 3000}


def test_detail_survives_kma_failure(client, monkeypatch):
    async def broken(*a):
        raise kma.KmaError(502, "기상청 오류")

    monkeypatch.setattr(kma, "fetch_current", broken)
    today = datetime.now(kma.KST).strftime("%Y-%m-%d")
    item = _add_card(client, monkeypatch, type="flood", lat=35.1, lon=129.0, date=today,
                     viz_params={"rainfall_mm": 240, "duration_hours": 6})
    res = client.get(f"/api/disasters/{item['id']}")
    assert res.status_code == 200
    assert res.json()["map_effect"]["params"] == {"hours": 6, "rain": 40.0}  # 240mm / 6시간
