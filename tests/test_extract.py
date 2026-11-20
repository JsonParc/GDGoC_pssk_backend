import json

import httpx
import pytest

from app import extract as extractor
from app.main import ArticleIn

ARTICLES = [
    ArticleIn(title="부산 폭우로 도로 침수", url="https://news.example.com/1", content="시간당 80mm 비가 내렸다."),
    ArticleIn(title="주가 상승 마감", url="https://news.example.com/2"),
]
DISASTERS = [{"article_ids": [0], "type": "flood", "title": "부산 폭우", "summary": "요약"}]


def gemini_reply(payload: dict, status: int = 200) -> httpx.Response:
    body = {"candidates": [{"content": {"parts": [{"text": json.dumps(payload, ensure_ascii=False)}]}}]}
    return httpx.Response(status, json=body, request=httpx.Request("POST", "https://x"))


@pytest.fixture
def sent(monkeypatch):
    """httpx.post를 가로채 Gemini 대신 응답. 보낸 요청을 기록."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "json": json})
        return calls_reply[0]

    calls_reply = [gemini_reply({"disasters": DISASTERS})]
    monkeypatch.setattr(extractor.httpx, "post", fake_post)
    return calls, calls_reply


def test_sends_all_articles_in_one_call(sent):
    calls, _ = sent
    assert extractor.extract_batch(ARTICLES) == DISASTERS
    assert len(calls) == 1
    prompt = calls[0]["json"]["contents"][0]["parts"][0]["text"]
    assert "[0] 제목: 부산 폭우로 도로 침수" in prompt and "[1] 제목: 주가 상승 마감" in prompt
    assert calls[0]["headers"] == {"x-goog-api-key": "test-key"}
    assert calls[0]["json"]["generationConfig"]["responseSchema"] == extractor.RESPONSE_SCHEMA


def test_long_content_is_trimmed(sent):
    calls, _ = sent
    long = ArticleIn(title="t", url="https://news.example.com/3", content="가" * 5000)
    extractor.extract_batch([long])
    assert "가" * (extractor.CONTENT_CHARS + 1) not in calls[0]["json"]["contents"][0]["parts"][0]["text"]


def test_error_status_raises(sent):
    _, reply = sent
    reply[0] = httpx.Response(429, text="quota", request=httpx.Request("POST", "https://x"))
    with pytest.raises(extractor.GeminiError):
        extractor.extract_batch(ARTICLES)


def test_busy_model_falls_back_once(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODELS", "big,small")
    tried = []

    def fake_post(url, headers, json, timeout):
        tried.append(url.split("/models/")[1].split(":")[0])
        if len(tried) == 1:
            return httpx.Response(503, text="busy", request=httpx.Request("POST", url))
        return gemini_reply({"disasters": DISASTERS})

    monkeypatch.setattr(extractor.httpx, "post", fake_post)
    assert extractor.extract_batch(ARTICLES) == DISASTERS
    assert tried == ["big", "small"]


def test_all_models_busy_raises(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODELS", "big,small")
    monkeypatch.setattr(extractor.httpx, "post", lambda url, **kw: httpx.Response(503, request=httpx.Request("POST", url)))
    with pytest.raises(extractor.GeminiError):
        extractor.extract_batch(ARTICLES)


def test_malformed_reply_raises(sent):
    _, reply = sent
    reply[0] = gemini_reply({"wrong": []})
    with pytest.raises(extractor.GeminiError):
        extractor.extract_batch(ARTICLES)


def test_keyword_fallback_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert extractor.extract_batch(ARTICLES) == [
        {"type": "flood", "title": "부산 폭우로 도로 침수", "date": None, "article_ids": [0]}
    ]


def busy(status=429, **headers):
    return httpx.Response(status, json={"error": {"details": [{"retryDelay": "7s"}]}}, headers=headers,
                          request=httpx.Request("POST", "https://x"))


@pytest.fixture
def scripted(monkeypatch):
    """Gemini 응답을 순서대로 돌려주고, 기다린 초를 기록 (실제로 자지 않음)"""
    monkeypatch.setenv("GEMINI_MODELS", "m1,m2")
    replies, slept, clock = [], [], [1000.0]
    monkeypatch.setattr(extractor.httpx, "post", lambda url, headers, json, timeout: replies.pop(0))
    monkeypatch.setattr(extractor.time, "sleep", lambda s: (slept.append(s), clock.__setitem__(0, clock[0] + s)))
    monkeypatch.setattr(extractor.time, "monotonic", lambda: clock[0])
    return replies, slept, clock


def test_all_models_busy_waits_retry_after_then_succeeds(scripted):
    replies, slept, clock = scripted
    replies += [busy(429, **{"Retry-After": "3"}), busy(503), gemini_reply({"ok": 1})]
    assert extractor.call_gemini("t", {}, "k", deadline=clock[0] + 240) == {"ok": 1}
    assert slept == [7]  # 두 모델 중 더 긴 대기(본문 retryDelay 7초)


def test_no_deadline_means_no_retry(scripted):
    replies, slept, _ = scripted
    replies += [busy(), busy(), gemini_reply({"ok": 1})]
    with pytest.raises(extractor.GeminiError):
        extractor.call_gemini("t", {}, "k")
    assert slept == []


def test_gives_up_when_job_time_is_almost_over(scripted):
    replies, slept, clock = scripted
    replies += [busy(), busy()]
    with pytest.raises(extractor.GeminiError, match="시간이 없음"):
        extractor.call_gemini("t", {}, "k", deadline=clock[0] + 15)
    assert slept == []


def test_retry_after_reads_header_or_body():
    assert extractor.retry_after(busy(429, **{"Retry-After": "12"})) == 12
    assert extractor.retry_after(busy(503)) == 7
    assert extractor.retry_after(httpx.Response(503, text="x", request=httpx.Request("POST", "https://x"))) is None
