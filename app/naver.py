"""네이버 뉴스 검색 API (https://openapi.naver.com/v1/search/news.json).

- 키: NAVER_CLIENT_ID, NAVER_CLIENT_SECRET (네이버 개발자센터 > 애플리케이션 등록 > 검색 API). 하루 25,000회
- 응답 items: title, originallink(언론사 원문), link(네이버 뉴스), description(본문 앞부분 요약), pubDate(RFC 822)
  title·description에는 <b> 강조 태그와 &quot; 같은 HTML 문자가 섞여 있어 벗겨냄
- 키 없이 부르면 401 {"errorMessage", "errorCode": "024"} (2026-11-21 직접 확인)
- 본문은 가져오지 않음: 검색 API가 준 제목·요약만 분석에 씀 (기사 전문 저장·크롤링 없음)
"""

import html
import logging
import os
import re
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

API_URL = "https://openapi.naver.com/v1/search/news.json"
TIMEOUT_SECONDS = 10
_TAG = re.compile(r"<[^>]+>")


class NewsSearchError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def configured() -> bool:
    return bool(os.getenv("NAVER_CLIENT_ID") and os.getenv("NAVER_CLIENT_SECRET"))


def clean(text: str) -> str:
    return html.unescape(_TAG.sub("", text or "")).strip()


def to_date(pub_date: str | None) -> str | None:
    try:
        return parsedate_to_datetime(pub_date).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def parse_item(item: dict) -> dict:
    url = item.get("originallink") or item.get("link") or ""
    return {
        "url": url,
        "title": clean(item.get("title")),
        "summary": clean(item.get("description")),
        "published_at": to_date(item.get("pubDate")),
        "publisher": urlsplit(url).hostname,  # 검색 API는 언론사 이름을 주지 않아 주소의 도메인으로 대신
    }


async def search_news(http: httpx.AsyncClient, query: str, display: int = 10) -> list[dict]:
    if not configured():
        raise NewsSearchError(503, "뉴스 검색 키(NAVER_CLIENT_ID, NAVER_CLIENT_SECRET)가 설정되지 않았습니다.")
    headers = {"X-Naver-Client-Id": os.environ["NAVER_CLIENT_ID"], "X-Naver-Client-Secret": os.environ["NAVER_CLIENT_SECRET"]}
    try:
        res = await http.get(API_URL, params={"query": query, "display": display, "sort": "sim"},
                             headers=headers, timeout=TIMEOUT_SECONDS)
    except httpx.HTTPError as e:
        raise NewsSearchError(503, f"뉴스 검색에 연결하지 못했습니다: {type(e).__name__}") from None
    if res.status_code == 429:
        raise NewsSearchError(429, "뉴스 검색 하루 사용 한도를 넘었습니다. 잠시 후 다시 시도해 주세요.")
    if res.status_code != 200:
        log.warning("네이버 뉴스 검색 %s: %s", res.status_code, res.text[:200])
        raise NewsSearchError(503, f"뉴스 검색 응답 오류 ({res.status_code})")
    items = [parse_item(i) for i in res.json().get("items", [])]
    return [i for i in items if i["url"].startswith(("http://", "https://")) and i["title"]]
