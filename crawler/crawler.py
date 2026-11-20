"""뉴스 기사 크롤러 → 재난 데이터 API 서버(POST /api/articles)로 전송

실행 예)
  python crawler/crawler.py                                # LIST_URLS의 목록 페이지를 모두 수집해서 전송
  python crawler/crawler.py https://news.daum.net/climate  # 원하는 목록 페이지만 수집
  python crawler/crawler.py --dry-run                      # 서버로 보내지 않고 보낼 내용만 확인

환경 변수
  API_URL       서버 주소 (기본: http://localhost:8001)
  INGEST_TOKEN  서버에 INGEST_TOKEN이 설정돼 있으면 같은 값을 넣어야 함 (X-Ingest-Token 헤더)
"""

import argparse
import json
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import trafilatura
from bs4 import BeautifulSoup

# 수집할 목록 페이지 (실행할 때 주소를 직접 넣지 않으면 이 목록을 사용)
LIST_URLS = [
    "https://www.khan.co.kr/environment/climate/articles",
    "https://www.newspenguin.com/news/articleList.html?sc_section_code=S1N43&view_type=sm",
    "https://www.hani.co.kr/arti/society/environment",
    "https://news.daum.net/climate",
]

API_URL = os.getenv("API_URL", "http://localhost:8001")
INGEST_TOKEN = os.getenv("INGEST_TOKEN")

# 서버(app/main.py의 ArticleIn) 제한에 맞춤
MAX_BATCH = 500
MAX_TITLE = 500
MAX_SOURCE = 100
MAX_CONTENT = 50_000

# 스크립트와 같은 폴더에 저장되는 기록 파일
BASE_DIR = Path(__file__).resolve().parent
SENT_FILE = BASE_DIR / "sent_urls.txt"           # 처리한 기사 URL: 서버로 보냈거나 본문이 없어 제외 (다음 실행 때 다시 크롤링하지 않음)
PENDING_FILE = BASE_DIR / "pending_articles.json"  # 전송 실패한 기사 (다음 실행 때 먼저 다시 보냄)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

# ─────────────────────────────────────────────────────────────
# 언론사별 규칙: 새 언론사는 여기에 두 줄만 추가하면 됩니다.
#   list : 목록 페이지에서 기사 링크를 고르는 CSS 선택자
#   body : 기사 페이지에서 본문 문단을 고르는 CSS 선택자
# 등록되지 않은 언론사는 자동 판별 + trafilatura로 수집합니다.
# ─────────────────────────────────────────────────────────────
SITE_RULES = {
    "khan.co.kr": {
        "list": 'div.list a[href*="/article/"]',
        "body": "p.content_text",
    },
    "newspenguin.com": {
        "list": "#section-list a[href*='articleView']",
        "body": "#article-view-content-div p",
    },
    # 목록은 news.daum.net, 기사는 v.daum.net → "daum.net" 하나로 둘 다 적용됨
    "daum.net": {
        "list": "article.content-article a[href*='v.daum.net/v/']",  # 오른쪽 aside(다른 분야 뉴스) 제외
        "body": "div.article_view p",
    },
}


# ───────────────────────── 크롤링 ─────────────────────────

def get_domain(url: str) -> str:
  host = urlparse(url).netloc.lower()
  return host[4:] if host.startswith("www.") else host


def get_rule(url: str) -> dict | None:
  domain = get_domain(url)
  for site, rule in SITE_RULES.items():
    if domain == site or domain.endswith("." + site):
      return rule
  return None


def get_html(url: str) -> str:
  headers = {**HEADERS, "Referer": f"{urlparse(url).scheme}://{urlparse(url).netloc}/"}
  resp = requests.get(url, headers=headers, timeout=10)
  resp.raise_for_status()
  resp.encoding = resp.apparent_encoding or "utf-8"
  return resp.text


def normalize_url(url: str) -> str:
  """비교용 URL 정리: #조각 제거, 경로에 기사 번호가 있으면 ?쿼리도 제거"""
  url = url.split("#")[0]
  if re.search(r"\d{4,}", urlparse(url).path):
    url = url.split("?")[0]
  return url


def url_shape(url: str) -> str:
  """URL의 '모양': 숫자를 #로 바꾸고 쿼리는 키 이름만 남김
  예) /news/articleView.html?idxno=22471 → /news/articleView.html?idxno=#"""
  p = urlparse(url)
  path = re.sub(r"\d+", "#", p.path)
  keys = sorted(k.split("=")[0] for k in p.query.split("&") if k)
  return path + ("?" + "&".join(k + "=#" for k in keys) if keys else "")


def auto_pick_links(soup: BeautifulSoup, list_url: str) -> list:
  """규칙이 없는 사이트: 같은 모양으로 가장 많이 반복되는 링크 묶음을 기사로 판단"""
  for tag in soup.select("header, footer, nav, aside"):
    tag.decompose()  # 메뉴/하단 영역 링크는 제외

  domain = get_domain(list_url)
  groups = defaultdict(list)
  for a in soup.select("a[href]"):
    full_url = urljoin(list_url, a["href"].strip())
    if get_domain(full_url) != domain or not re.search(r"\d{3,}", full_url):
      continue  # 다른 사이트 링크, 번호 없는 메뉴 링크 제외
    groups[url_shape(full_url)].append(a)

  if not groups:
    return []
  # 제목이 붙은 서로 다른 링크가 가장 많은 묶음 선택
  return max(groups.values(),
             key=lambda links: len({urljoin(list_url, a["href"]) for a in links
                                    if len(a.get_text(strip=True)) >= 8}))


def crawl_list(url: str) -> list[str]:
  """1단계: 목록 페이지에서 기사 URL 수집 (페이지 순서 유지, 중복 제거)"""
  soup = BeautifulSoup(get_html(url), "html.parser")
  rule = get_rule(url)
  anchors = soup.select(rule["list"]) if rule else auto_pick_links(soup, url)

  urls = []
  for a in anchors:
    full_url = normalize_url(urljoin(url, a["href"].strip()))
    if not a.get_text(strip=True) or full_url in urls or full_url == normalize_url(url):
      continue  # 썸네일 등 제목 없는 링크, 중복 링크 제외
    urls.append(full_url)
  return urls


def get_source(soup: BeautifulSoup, url: str) -> str:
  """언론사 이름: og:site_name 사용 ('Daum | 수소신문'처럼 포털이면 원래 언론사만)"""
  tag = soup.select_one('meta[property="og:site_name"]')
  name = (tag.get("content") or "").strip() if tag else ""
  if "|" in name:
    name = name.split("|")[-1].strip()
  return name or get_domain(url)


KST = timezone(timedelta(hours=9))


def to_date(value: str | None) -> str | None:
  """여러 형식의 날짜를 서버 예시와 같은 'YYYY-MM-DD'로 변환 (시각이 있으면 한국 시간 기준 날짜)
  예) '2026-11-20T10:20:00+09:00', '2026-11-19T23:00:00Z', '2026. 11. 20.', '20261120171052'
      → '2026-11-20'"""
  if not value:
    return None
  value = value.strip()
  try:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is not None:
      dt = dt.astimezone(KST)
    return dt.strftime("%Y-%m-%d")
  except ValueError:
    pass
  m = re.match(r"(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})", value) or re.match(r"(\d{4})(\d{2})(\d{2})", value)
  if m:
    try:
      return datetime(int(m[1]), int(m[2]), int(m[3])).strftime("%Y-%m-%d")
    except ValueError:  # 13월 같은 잘못된 날짜
      return None
  return None


def crawl_article(url: str) -> dict:
  """2단계: 개별 기사에서 서버 형식(ArticleIn)에 맞는 값 수집"""
  html = get_html(url)
  soup = BeautifulSoup(html, "html.parser")
  rule = get_rule(url)

  if rule:
    paragraphs = [p.get_text(" ", strip=True) for p in soup.select(rule["body"])]
    content = "\n".join(p for p in paragraphs if p)
  else:
    content = trafilatura.extract(html, url=url, include_comments=False,
                                  include_tables=False) or ""

  # 제목·날짜는 대부분의 언론사가 쓰는 표준 meta 태그 → 없으면 trafilatura로 보완
  og_title = soup.select_one('meta[property="og:title"]')
  pub_time = soup.select_one('meta[property="article:published_time"]')
  meta = None
  if not og_title or not pub_time:
    meta = trafilatura.extract_metadata(html)

  title = og_title["content"] if og_title else (meta.title if meta else "") or ""
  source = get_source(soup, url)
  # "기사 제목 - 뉴스펭귄"처럼 뒤에 붙은 언론사 이름 제거
  title = re.sub(rf"\s*[-|:·]\s*{re.escape(source)}\s*$", "", title)
  published_at = pub_time["content"] if pub_time else (meta.date if meta else "") or ""

  return {
      "title": title.strip()[:MAX_TITLE],
      "url": url,
      "source": source[:MAX_SOURCE],
      "published_at": to_date(published_at),  # 'YYYY-MM-DD' (README 예시 형식)
      "content": content.strip()[:MAX_CONTENT] or None,
  }


# ───────────────────────── 기록 파일 ─────────────────────────

def load_sent_urls() -> set[str]:
  if not SENT_FILE.exists():
    return set()
  return {line.strip() for line in SENT_FILE.read_text(encoding="utf-8").splitlines() if line.strip()}


def add_sent_urls(urls: list[str]) -> None:
  with SENT_FILE.open("a", encoding="utf-8") as f:
    f.writelines(u + "\n" for u in urls)


def load_pending() -> list[dict]:
  if not PENDING_FILE.exists():
    return []
  articles = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
  for a in articles:  # 예전 형식으로 저장된 날짜도 'YYYY-MM-DD'로 맞춤
    a["published_at"] = to_date(a.get("published_at"))
  return articles


def save_pending(articles: list[dict]) -> None:
  if articles:
    PENDING_FILE.write_text(json.dumps(articles, ensure_ascii=False, indent=1), encoding="utf-8")
  elif PENDING_FILE.exists():
    PENDING_FILE.unlink()


# ───────────────────────── 서버 전송 ─────────────────────────

def send_articles(articles: list[dict]) -> list[dict]:
  """POST /api/articles로 전송. 보내지 못한 기사 목록을 돌려줌"""
  headers = {"X-Ingest-Token": INGEST_TOKEN} if INGEST_TOKEN else {}
  endpoint = f"{API_URL.rstrip('/')}/api/articles"

  for start in range(0, len(articles), MAX_BATCH):  # 서버 제한: 한 요청에 최대 500개
    batch = articles[start:start + MAX_BATCH]
    try:
      resp = requests.post(endpoint, json=batch, headers=headers, timeout=120)
    except requests.RequestException as e:
      print(f"서버에 연결하지 못했습니다 ({endpoint}): {e}")
      return articles[start:]

    if resp.status_code != 200:
      print(f"전송 실패 (HTTP {resp.status_code}): {resp.text[:300]}")
      return articles[start:]

    result = resp.json()
    print(f"전송 완료: 받음 {result['received']} / 새 기사 {result['new']} / "
          f"이미 있음 {result['skipped']} / 분석 실패 {result['failed']} / "
          f"재난 카드 생성 {result['disasters_created']}")
    add_sent_urls([a["url"] for a in batch])
  return []


# ───────────────────────── 실행 ─────────────────────────

def collect(list_urls: list[str], sent_urls: set[str]) -> tuple[list[dict], list[str]]:
  """(보낼 기사, 본문이 없어 제외한 URL)을 돌려줌"""
  articles = []
  excluded = []
  seen = set(sent_urls)
  for list_url in list_urls:
    mode = "등록된 규칙" if get_rule(list_url) else "자동 판별"
    print(f"\n▶ {list_url}  [{mode}]")
    try:
      urls = crawl_list(list_url)
    except requests.RequestException as e:
      print(f"  목록을 불러오지 못했습니다: {e}")
      continue
    if not urls:
      print("  기사를 찾지 못했습니다. 목록 페이지 주소인지 확인하거나 SITE_RULES에 규칙을 추가해 주세요.")
      continue

    new_urls = [u for u in urls if u not in seen]
    print(f"  목록 {len(urls)}건 중 새 기사 {len(new_urls)}건")
    for i, url in enumerate(new_urls, start=1):
      seen.add(url)
      try:
        art = crawl_article(url)
      except requests.RequestException as e:
        print(f"  [{i}/{len(new_urls)}] 실패: {url} - {e}")
        continue
      finally:
        time.sleep(1)  # 서버 부담을 줄이기 위해 요청 간 대기

      if not art["content"] or not art["title"]:  # 본문 없이 이미지만 있는 기사 등 제외
        print(f"  [{i}/{len(new_urls)}] 제외(본문 없음): {art['title'] or url}")
        excluded.append(url)
        continue
      articles.append(art)
      print(f"  [{i}/{len(new_urls)}] {art['source']} | {art['title']}")
  return articles, excluded


def main():
  parser = argparse.ArgumentParser(description="뉴스 기사를 수집해 재난 데이터 API 서버로 전송")
  parser.add_argument("urls", nargs="*", help="수집할 목록 페이지 주소 (생략하면 LIST_URLS 사용)")
  parser.add_argument("--dry-run", action="store_true", help="서버로 보내지 않고 보낼 내용만 출력")
  args = parser.parse_args()

  list_urls = [u.strip().lstrip("﻿") for u in args.urls] or LIST_URLS
  sent_urls = load_sent_urls()
  pending = load_pending()
  if pending:
    print(f"지난번에 보내지 못한 기사 {len(pending)}건을 함께 보냅니다.")

  pending_urls = {a["url"] for a in pending}
  new_articles, excluded = collect(list_urls, sent_urls | pending_urls)
  articles = pending + new_articles
  print(f"\n보낼 기사: {len(articles)}건")

  if not args.dry_run:
    add_sent_urls(excluded)  # 본문 없는 기사도 기록해서 다음 실행 때 다시 열지 않음
  if not articles:
    return

  if args.dry_run:
    preview = [{**a, "content": (a["content"] or "")[:80] + "…"} for a in articles[:3]]
    print(json.dumps(preview, ensure_ascii=False, indent=2))
    print("--dry-run: 서버로 보내지 않았습니다.")
    return

  failed = send_articles(articles)
  save_pending(failed)
  if failed:
    print(f"보내지 못한 {len(failed)}건은 {PENDING_FILE}에 저장했습니다. 다음 실행 때 다시 보냅니다.")


if __name__ == "__main__":
  main()
