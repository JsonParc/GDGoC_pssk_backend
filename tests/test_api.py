import pytest
from fastapi.testclient import TestClient

from app import extract as extractor
from app.main import MAX_BATCH, app

FLOOD = {"title": "부산 폭우로 도로 침수", "url": "https://news.example.com/1", "published_at": "2026-11-20"}
OTHER = {"title": "주가 상승 마감", "url": "https://news.example.com/2"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.delenv("INGEST_TOKEN", raising=False)
    return TestClient(app)


def test_ingest_creates_disaster_with_source(client):
    res = client.post("/api/articles", json=[FLOOD, OTHER])
    assert res.json() == {"received": 2, "new": 2, "skipped": 0, "failed": 0, "disasters_created": 1}

    items = client.get("/api/disasters").json()["items"]
    assert [d["type"] for d in items] == ["flood"]
    detail = client.get(f"/api/disasters/{items[0]['id']}").json()
    assert detail["sources"][0]["url"] == FLOOD["url"]


def test_duplicate_urls_are_not_analyzed_again(client, monkeypatch):
    calls = []
    original = extractor.extract
    monkeypatch.setattr(extractor, "extract", lambda a: calls.append(a) or original(a))

    client.post("/api/articles", json=[FLOOD, FLOOD])
    res = client.post("/api/articles", json=[FLOOD])

    assert len(calls) == 1
    assert res.json()["skipped"] == 1


def test_failed_extraction_is_retried_next_time(client, monkeypatch):
    def boom(article):
        raise RuntimeError("gemini down")

    original = extractor.extract
    monkeypatch.setattr(extractor, "extract", boom)
    assert client.post("/api/articles", json=[FLOOD]).json()["failed"] == 1

    monkeypatch.setattr(extractor, "extract", original)
    assert client.post("/api/articles", json=[FLOOD]).json()["disasters_created"] == 1


def test_invalid_extract_output_is_rejected(client, monkeypatch):
    monkeypatch.setattr(extractor, "extract", lambda a: {"type": "tsunami", "title": "x"})
    assert client.post("/api/articles", json=[FLOOD]).json()["failed"] == 1


def test_filter_by_type_and_reject_unknown_type(client):
    client.post("/api/articles", json=[FLOOD, {"title": "강원 산불 확산", "url": "https://news.example.com/3"}])
    assert client.get("/api/disasters?type=wildfire").json()["total"] == 1
    assert client.get("/api/disasters?type=tsunami").status_code == 422


def test_token_required_when_configured(client, monkeypatch):
    monkeypatch.setenv("INGEST_TOKEN", "secret")
    assert client.post("/api/articles", json=[FLOOD]).status_code == 401
    assert client.post("/api/articles", json=[FLOOD], headers={"X-Ingest-Token": "secret"}).status_code == 200


def test_batch_size_limit(client):
    batch = [{"title": "t", "url": f"https://news.example.com/{i}"} for i in range(MAX_BATCH + 1)]
    assert client.post("/api/articles", json=batch).status_code == 413


def test_not_found(client):
    assert client.get("/api/disasters/999").status_code == 404
    assert client.get("/api/guides/tsunami").status_code == 404
    assert client.get("/api/guides/flood").json()["steps"]
