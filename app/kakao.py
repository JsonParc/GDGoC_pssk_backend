"""카카오 다음 웹문서 검색 API (GET https://dapi.kakao.com/v2/search/web).

- 키: KAKAO_REST_API_KEY (카카오 개발자센터 > 내 애플리케이션 > 앱 키 > REST API 키). 헤더 Authorization: KakaoAK {키}
- 파라미터: query, sort(accuracy|recency), page 1~50, size 1~50. 웹문서 검색 하루 30,000회 (2026-11-21 공식 문서 확인)
- 응답 documents: title, contents(본문 일부), url, datetime(ISO 8601). title·contents에 <b> 강조 태그가 섞여 있어 벗겨냄
- 오류 본문에 code: -10 요청 한도 초과, -401 키 오류, -3·-5 권한·설정 문제. 키 없이 부르면 401 AccessDeniedError (직접 확인)
- 뉴스 전용이 아니라 웹문서 전체 검색이라 블로그 등도 섞임. 본문은 가져오지 않고 제목·요약만 분석에 씀
"""

import html
import logging
import os
import re
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

API_URL = "https://dapi.kakao.com/v2/search/web"
TIMEOUT_SECONDS = 10
_TAG = re.compile(r"<[^>]+>")


class NewsSearchError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def configured() -> bool:
    return bool(os.getenv("KAKAO_REST_API_KEY"))


def clean(text: str | None) -> str:
    return html.unescape(_TAG.sub("", text or "")).strip()


def parse_doc(doc: dict) -> dict:
    url = doc.get("url") or ""
    stamp = doc.get("datetime") or ""
    return {
        "url": url,
        "title": clean(doc.get("title")),
        "summary": clean(doc.get("contents")),
        "published_at": stamp[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", stamp) else None,
        "publisher": urlsplit(url).hostname,  # 매체 이름은 주지 않아 주소의 도메인으로 대신
    }


def _error(res: httpx.Response) -> NewsSearchError:
    try:
        code = res.json().get("code")
    except ValueError:
        code = None
    log.warning("카카오 검색 %s: %s", res.status_code, res.text[:200])
    if code == -10:
        return NewsSearchError(429, "뉴스 검색 하루 사용 한도를 넘었습니다. 잠시 후 다시 시도해 주세요.")
    if res.status_code in (401, 403):
        return NewsSearchError(503, "뉴스 검색 키 또는 앱 설정이 올바르지 않습니다.")
    return NewsSearchError(503, f"뉴스 검색 응답 오류 ({res.status_code})")


async def search_news(http: httpx.AsyncClient, query: str, size: int = 10) -> list[dict]:
    if not configured():
        raise NewsSearchError(503, "뉴스 검색 키(KAKAO_REST_API_KEY)가 설정되지 않았습니다.")
    try:
        res = await http.get(API_URL, params={"query": query, "size": size, "sort": "accuracy"},
                             headers={"Authorization": f"KakaoAK {os.environ['KAKAO_REST_API_KEY']}"},
                             timeout=TIMEOUT_SECONDS)
    except httpx.HTTPError as e:
        raise NewsSearchError(503, f"뉴스 검색에 연결하지 못했습니다: {type(e).__name__}") from None
    if res.status_code != 200:
        raise _error(res)
    docs = [parse_doc(d) for d in res.json().get("documents", [])]
    return [d for d in docs if d["url"].startswith(("http://", "https://")) and d["title"]]
