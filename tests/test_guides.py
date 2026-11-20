import httpx
import pytest
from fastapi.testclient import TestClient

from app import guides, kma
from app.main import app, get_http

WARNINGS = """#START7777
L1020000, 강원도, L1020300, 태백, 202611201000, 202611201000, 건조  , 주의, 발표, ,=
L1020000, 강원도, L1021910, 삼척시평지, 202611201000, 202611201000, 강풍  , 주의, 발표, 21일 밤,=
L1010000, 경기도, L1010200, 광명, 202611190900, 202611191000, 대설  , 주의, 발표, ,=
#7777END"""


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("KMA_API_KEY", "test-key")
    kma._cache.clear()
    mock = httpx.MockTransport(lambda r: httpx.Response(200, content=WARNINGS.encode("cp949")))
    app.dependency_overrides[get_http] = lambda: httpx.AsyncClient(transport=mock)
    yield TestClient(app)
    app.dependency_overrides.clear()
    kma._cache.clear()


def test_guide_file_is_valid():
    """app/guides.py를 고친 뒤 이 테스트로 확인하세요."""
    types = [g["type"] for g in guides.GUIDES]
    codes = [g["warning_code"] for g in guides.GUIDES if g["warning_code"]]
    assert len(types) == len(set(types)), "type이 겹쳐요"
    assert len(codes) == len(set(codes)), "warning_code가 겹쳐요"
    assert set(codes) <= set(kma.WARNING_TYPES), "warning_code는 기상특보 종류 코드여야 해요"
    for g in guides.GUIDES:
        assert g["title"] and isinstance(g["steps"], list), g["type"]
        assert all(isinstance(s, str) and s.strip() for s in g["steps"]), f"{g['type']}: 빈 문장이 있어요"
        assert g["source"] is None or g["source"].startswith("https://"), g["type"]


def test_list_guides(client):
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


def test_get_one_guide_and_not_found(client):
    assert client.get("/api/guides/earthquake").json()["title"] == "지진 대피요령"
    assert client.get("/api/guides/volcano").status_code == 404
