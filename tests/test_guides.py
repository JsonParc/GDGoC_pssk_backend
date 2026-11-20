import httpx
import pytest
from fastapi.testclient import TestClient

from app import kma
from app.main import app, get_http

ADMIN = "admin-test"
WARNINGS = """#START7777
L1020000, 강원도, L1020300, 태백, 202611201000, 202611201000, 건조  , 주의, 발표, ,=
L1020000, 강원도, L1021910, 삼척시평지, 202611201000, 202611201000, 강풍  , 주의, 발표, 21일 밤,=
L1010000, 경기도, L1010200, 광명, 202611190900, 202611191000, 대설  , 주의, 발표, ,=
#7777END"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("ADMIN_TOKEN", ADMIN)
    monkeypatch.setenv("KMA_API_KEY", "test-key")
    kma._cache.clear()
    mock = httpx.MockTransport(lambda r: httpx.Response(200, content=WARNINGS.encode("cp949")))
    app.dependency_overrides[get_http] = lambda: httpx.AsyncClient(transport=mock)
    yield TestClient(app)
    app.dependency_overrides.clear()
    kma._cache.clear()


def test_default_guides_are_seeded(client):
    items = {g["type"]: g for g in client.get("/api/guides").json()["items"]}
    assert {"flood", "typhoon", "earthquake", "heatwave", "coldwave", "wildfire"} <= set(items)
    assert items["typhoon"]["warning_code"] == "T" and items["typhoon"]["steps"]
    assert items["dry"]["needs_writing"] is True  # 공식 문구 없음


def test_active_guides_follow_warnings_and_region(client):
    active = client.get("/api/guides/active").json()["items"]
    assert sorted(g["type"] for g in active) == ["dry", "heavysnow", "strongwind"]

    gangwon = client.get("/api/guides/active?region=강원").json()["items"]
    assert sorted(g["type"] for g in gangwon) == ["dry", "strongwind"]
    wind = next(g for g in gangwon if g["type"] == "strongwind")
    assert wind["warnings"][0]["region"] == "삼척시평지" and wind["warnings"][0]["end_notice"] == "21일 밤"


def test_put_guide_requires_admin_token(client):
    body = {"title": "건조특보 중 행동요령", "steps": ["산에 갈 때 라이터를 가져가지 않습니다."]}
    assert client.put("/api/guides/dry", json=body).status_code == 401
    assert client.put("/api/guides/dry", json=body, headers={"X-Admin-Token": "wrong"}).status_code == 401

    res = client.put("/api/guides/dry", json={**body, "warning_code": "D"}, headers={"X-Admin-Token": ADMIN})
    assert res.status_code == 200
    assert res.json()["steps"] == body["steps"] and res.json()["needs_writing"] is False
    assert client.get("/api/guides/dry").json()["steps"] == body["steps"]


def test_edited_guide_survives_restart_seed(client, tmp_path):
    client.put("/api/guides/flood", json={"title": "고친 제목", "steps": ["a"]}, headers={"X-Admin-Token": ADMIN})
    from app import db
    db._seeded.clear()  # 서버 재시작처럼 기본값 넣기를 다시 실행
    assert client.get("/api/guides/flood").json()["title"] == "고친 제목"


def test_put_validation_and_closed_without_admin_token(client, monkeypatch):
    headers = {"X-Admin-Token": ADMIN}
    assert client.put("/api/guides/BAD", json={"title": "t", "steps": []}, headers=headers).status_code == 422
    assert client.put("/api/guides/x_y", json={"title": "", "steps": []}, headers=headers).status_code == 422
    assert client.put("/api/guides/x_y", json={"title": "t", "steps": ["s"] * 31}, headers=headers).status_code == 422
    monkeypatch.delenv("ADMIN_TOKEN")
    assert client.put("/api/guides/x_y", json={"title": "t", "steps": []}, headers=headers).status_code == 503
