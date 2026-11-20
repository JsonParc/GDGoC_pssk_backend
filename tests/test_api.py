import pytest
from fastapi.testclient import TestClient

from app import extract as extractor
from app.main import MAX_BATCH, app

FLOOD = {"title": "부산 폭우로 도로 침수", "url": "https://news.example.com/1", "published_at": "2026-11-20"}
OTHER = {"title": "주가 상승 마감", "url": "https://news.example.com/2"}


TOKEN = "test-token"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("INGEST_TOKEN", TOKEN)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)  # 기본은 키워드 규칙, Gemini는 test_extract.py에서
    return TestClient(app, headers={"X-Ingest-Token": TOKEN})


def test_ingest_creates_disaster_with_source(client):
    res = client.post("/api/articles", json=[FLOOD, OTHER])
    assert res.json() == {"received": 2, "new": 2, "skipped": 0, "failed": 0, "disasters_created": 1}

    items = client.get("/api/disasters").json()["items"]
    assert [d["type"] for d in items] == ["flood"]
    detail = client.get(f"/api/disasters/{items[0]['id']}").json()
    assert detail["sources"][0]["url"] == FLOOD["url"]


def test_duplicate_urls_are_not_analyzed_again(client, monkeypatch):
    analyzed = []
    original = extractor.extract_batch
    monkeypatch.setattr(extractor, "extract_batch", lambda arts: analyzed.extend(arts) or original(arts))

    client.post("/api/articles", json=[FLOOD, FLOOD])
    res = client.post("/api/articles", json=[FLOOD])

    assert len(analyzed) == 1
    assert res.json()["skipped"] == 1


def test_articles_are_sent_in_batches(client, monkeypatch):
    sizes = []
    monkeypatch.setattr(extractor, "extract_batch", lambda arts: sizes.append(len(arts)) or [])
    batch = [{"title": "t", "url": f"https://news.example.com/b{i}"} for i in range(extractor.BATCH_SIZE + 5)]
    client.post("/api/articles", json=batch)
    assert sizes == [extractor.BATCH_SIZE, 5]


def test_same_event_articles_become_one_disaster(client, monkeypatch):
    group = {"article_ids": [0, 1], "type": "flood", "title": "부산 폭우", "summary": "요약",
             "lat": 35.1, "lon": 129.0, "viz_params": {"rainfall_mm": 300, "flood_depth_m": None}}
    monkeypatch.setattr(extractor, "extract_batch", lambda arts: [group])
    second = {**FLOOD, "url": "https://news.example.com/1b"}
    res = client.post("/api/articles", json=[FLOOD, second, OTHER]).json()
    assert (res["new"], res["disasters_created"]) == (3, 1)

    detail = client.get("/api/disasters/1").json()
    assert len(detail["sources"]) == 2
    assert detail["viz_params"] == {"rainfall_mm": 300}


def test_unknown_article_ids_are_ignored(client, monkeypatch):
    monkeypatch.setattr(extractor, "extract_batch", lambda arts: [{"article_ids": [7], "type": "flood", "title": "x", "summary": ""}])
    res = client.post("/api/articles", json=[FLOOD]).json()
    assert (res["new"], res["disasters_created"]) == (1, 0)


def test_failed_extraction_is_retried_next_time(client, monkeypatch):
    def boom(articles):
        raise RuntimeError("gemini down")

    original = extractor.extract_batch
    monkeypatch.setattr(extractor, "extract_batch", boom)
    assert client.post("/api/articles", json=[FLOOD]).json()["failed"] == 1

    monkeypatch.setattr(extractor, "extract_batch", original)
    assert client.post("/api/articles", json=[FLOOD]).json()["disasters_created"] == 1


def test_invalid_extract_output_is_retried(client, monkeypatch):
    monkeypatch.setattr(extractor, "extract_batch", lambda arts: [{"article_ids": [0], "type": "volcano", "title": "x"}])
    res = client.post("/api/articles", json=[FLOOD, OTHER]).json()
    assert (res["new"], res["failed"]) == (1, 1)


def test_filter_by_type_and_reject_unknown_type(client):
    client.post("/api/articles", json=[FLOOD, {"title": "강원 산불 확산", "url": "https://news.example.com/3"}])
    assert client.get("/api/disasters?type=wildfire").json()["total"] == 1
    assert client.get("/api/disasters?type=volcano").status_code == 422


def test_ingest_rejects_missing_or_wrong_token(client):
    bare = TestClient(app)
    assert bare.post("/api/articles", json=[FLOOD]).status_code == 401
    assert bare.post("/api/articles", json=[FLOOD], headers={"X-Ingest-Token": "wrong"}).status_code == 401
    assert client.post("/api/articles", json=[FLOOD]).status_code == 200


def test_ingest_closed_when_token_not_configured(client, monkeypatch):
    monkeypatch.delenv("INGEST_TOKEN")
    assert client.post("/api/articles", json=[FLOOD]).status_code == 503


def test_batch_size_limit(client):
    batch = [{"title": "t", "url": f"https://news.example.com/{i}"} for i in range(MAX_BATCH + 1)]
    assert client.post("/api/articles", json=batch).status_code == 413


def test_not_found(client):
    assert client.get("/api/disasters/999").status_code == 404
    assert client.get("/api/guides/volcano").status_code == 404
    guide = client.get("/api/guides/flood").json()
    assert guide["steps"] and guide["source"].startswith("https://")


def test_card_types_match_guide_types(client, monkeypatch):
    from app import guides
    assert set(guides.DISASTER_TYPES) == set(guides.GUIDE_BY_TYPE) | {"other"}
    group = {"article_ids": [0], "type": "heavysnow", "title": "전북 대설", "summary": "요약",
             "viz_params": {"snowfall_cm": 15.3, "max_wind_ms": 30}}
    monkeypatch.setattr(extractor, "extract_batch", lambda arts: [group])
    client.post("/api/articles", json=[FLOOD])
    card = client.get("/api/disasters?type=heavysnow").json()["items"][0]
    assert card["viz_params"] == {"snowfall_cm": 15.3, "max_wind_ms": 30}
    assert client.get(f"/api/guides/{card['type']}").status_code == 200  # 카드 종류로 바로 행동지침 조회
