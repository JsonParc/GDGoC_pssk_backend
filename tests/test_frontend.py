from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import frontend


def make_site(tmp_path):
    site = tmp_path / "site"
    (site / "korea").mkdir(parents=True)
    (site / "index.html").write_text("<p>home</p>", encoding="utf-8")
    (site / "korea" / "vworld.html").write_text("<p>map</p>", encoding="utf-8")
    (site / ".git").mkdir()
    (site / ".git" / "config").write_text("secret", encoding="utf-8")
    (site / ".env").write_text("KEY=secret", encoding="utf-8")
    return site


def client_for(site):
    app = FastAPI()

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    app.mount("/", frontend.static_app(site))
    return TestClient(app)


def test_serves_frontend_and_api_first(tmp_path):
    c = client_for(make_site(tmp_path))
    assert c.get("/").text == "<p>home</p>"
    assert c.get("/korea/vworld.html").text == "<p>map</p>"
    assert c.get("/api/ping").json() == {"ok": True}  # API가 먼저
    assert c.get("/nope.html").status_code == 404


def test_hidden_files_are_never_served(tmp_path):
    c = client_for(make_site(tmp_path))
    for path in ("/.git/config", "/.env", "/korea/../.env", "/%2Egit/config"):
        res = c.get(path)
        assert res.status_code == 404 and "secret" not in res.text, path


def test_frontend_dir_setting(tmp_path, monkeypatch):
    site = make_site(tmp_path)
    monkeypatch.setenv("FRONTEND_DIR", str(site))
    assert frontend.frontend_dir() == site.resolve()
    monkeypatch.setenv("FRONTEND_DIR", str(tmp_path / "missing"))
    assert frontend.frontend_dir() is None
