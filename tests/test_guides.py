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
        for name, lv in g.get("levels", {}).items():
            assert set(lv) == {"criteria", "criteria_source", "steps"}, f"{g['type']} {name}: 칸 이름이 달라요"
            assert all(isinstance(s, str) and s.strip() for s in lv["criteria"] + lv["steps"]), f"{g['type']} {name}: 빈 문장"
        assert all(isinstance(s, str) and s.strip() for s in g["steps"]), f"{g['type']}: 빈 문장이 있어요"
        assert g["source"] is None or g["source"].startswith("https://"), g["type"]


def test_list_guides(client):
    items = {g["type"]: g for g in client.get("/api/guides").json()["items"]}
    assert {"flood", "typhoon", "earthquake", "heatwave", "coldwave", "wildfire"} <= set(items)
    assert items["typhoon"]["warning_code"] == "T" and items["typhoon"]["steps"]
    assert not any(g["needs_writing"] for g in items.values())  # 모든 재난에 공통 지침이 있음
    assert items["dry"]["steps"] and items["dry"]["source"] is None  # 팀 작성 문구


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


def test_levels_match_warning_and_quake_and_typhoon_names(client):
    levels = {g["type"]: set(g.get("levels", {})) for g in guides.GUIDES}
    assert {"예비특보", "주의보", "경보"} <= levels["flood"]
    assert "중대경보" in levels["heatwave"]
    assert {f"진도 {r}" for r in kma.ROMAN} == levels["earthquake"]
    assert {f"강도 {n}" for n in range(1, 6)} <= levels["typhoon"]
    assert {kma.WARNING_LEVELS[k] for k in ("예비", "주의", "경보")} <= levels["strongwind"]


def test_get_guide_by_level(client):
    body = client.get("/api/guides/heatwave?level=중대경보").json()
    assert body["steps"]  # 공통
    assert body["level"]["name"] == "중대경보"
    assert body["level"]["criteria"][0].startswith("일 최고 체감온도 35℃")
    assert body["level"]["steps"] and body["level"]["needs_writing"] is False

    caution = client.get("/api/guides/heatwave?level=주의보").json()["level"]
    assert caution["criteria"] and caution["steps"] == ["가장 더운 시간대의 야외활동을 줄이고 충분한 수분을 섭취하세요."]

    res = client.get("/api/guides/heatwave?level=초특급")
    assert res.status_code == 404 and "주의보" in res.json()["detail"]
    assert len(client.get("/api/guides/earthquake").json()["levels"]) == 12


def test_active_guides_are_split_by_level(client):
    items = client.get("/api/guides/active").json()["items"]
    wind = next(g for g in items if g["type"] == "strongwind")
    assert wind["level"]["name"] == "주의보"
    assert wind["level"]["criteria"][0].startswith("육상에서 풍속 14m/s")


def test_every_level_has_steps():
    empty = [(g["type"], n) for g in guides.GUIDES for n, lv in g.get("levels", {}).items() if not lv["steps"]]
    assert empty == []
