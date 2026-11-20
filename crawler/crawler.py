"""뉴스 기사 크롤러 → 재난 데이터 API 서버(POST /api/articles)로 전송

기사를 세 가지 방법으로 모아 한 요청으로 보냅니다.
  [최근] 1) 지정한 목록 페이지(LIST_URLS)
  [최근] 2) 언론사 뉴스 사이트맵(NEWS_SITEMAPS): 최근 2~3일 기사 중 제목에 TITLE_KEYWORDS가 있는 기사
         → 원문 주소·본문까지 보냄. 하루 한 번 이상 실행하면 등록한 언론사의 최근 기사를 놓치지 않음
  [과거] 3) 구글 뉴스 RSS 기간 검색: 서버의 재난 카드(GET /api/disasters)에서 위치·유형을 읽어
         그 지역의 과거 재난·복구공사·방재 기사를 찾음 → 제목·언론사·날짜만 보냄 (본문 없음)
  robots.txt에서 AI 사용(AI_AGENTS, 기본 Gemini용 Google-Extended)이 막힌 주소·언론사는 모두 제외합니다.

실행 예)
  python crawler/crawler.py                          # 위 세 가지를 모두 수집해서 전송
  python crawler/crawler.py --dry-run                # 서버로 보내지 않고 보낼 내용만 확인
  python crawler/crawler.py --past "부산 침수"        # 과거 기사 검색어를 직접 지정 (재난 카드와 별개로)
  python crawler/crawler.py --past-years 10          # 과거 몇 년까지 찾을지 (기본 5)
  python crawler/crawler.py --no-lists --no-sitemaps --no-past   # 원하는 방법만 끄기
  python crawler/crawler.py https://news.daum.net/climate   # 이 목록 페이지만 수집 (+ 사이트맵·과거)

환경 변수
  API_URL       서버 주소 (기본: http://localhost:8001)
  INGEST_TOKEN  서버에 INGEST_TOKEN이 설정돼 있으면 같은 값을 넣어야 함 (X-Ingest-Token 헤더)
"""

import argparse
import html as html_lib
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

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

# ── [최근] 언론사 뉴스 사이트맵 ──
# 언론사가 검색엔진용으로 공개하는 최근 기사 목록 (robots.txt의 'Sitemap:' 줄에서 찾을 수 있음)
# 새 언론사는 이름과 사이트맵 주소만 추가하면 됩니다.
NEWS_SITEMAPS = {
    "연합뉴스": [
        "https://www.yna.co.kr/news-sitemap3.xml",
        "https://www.yna.co.kr/news-sitemap4.xml",
        "https://www.yna.co.kr/news-sitemap5.xml",
        "https://www.yna.co.kr/news-sitemap6.xml",
    ],
    "KBS": ["https://news.kbs.co.kr/sitemap/recentNewsList.xml"],
    "동아일보": ["https://www.donga.com/sitemap/donga-newsmap.xml"],
    "매일경제": ["https://www.mk.co.kr/sitemap/latest-articles/"],
}
# 사이트맵 기사 중 제목에 이 단어가 하나라도 있는 기사만 엽니다.
TITLE_KEYWORDS = (
    "폭우", "호우", "장마", "침수", "홍수", "범람", "태풍", "강풍", "풍랑", "해일",
    "산불", "지진", "여진", "산사태", "땅꺼짐", "싱크홀",
    "폭염", "열대야", "한파", "대설", "폭설", "가뭄", "주의보", "특보",
    "기후변화", "기후위기", "이상기후", "재난", "재해", "이재민",
)
# 키워드가 들어 있지만 재난과 무관한 단어 (제목에서 지운 뒤 키워드를 확인)
TITLE_EXCLUDE = ("재해석",)
RECENT_DAYS = 3  # 사이트맵에서 최근 며칠 기사까지 볼지

# ── [과거] 구글 뉴스 RSS 기간 검색 ──
# 재난 유형(서버 DisasterIn.type)별로 '{위치} {검색어}'로 찾음: 과거 피해 + 복구공사·방재시설 기사
PAST_QUERIES = {
    "flood": ["침수", "홍수", "배수시설", "복구공사", "방재시설"],
    "wildfire": ["산불", "산불 피해 복구", "산림 복원"],
    "heatwave": ["폭염", "폭염 대책"],
    "other": [],  # 유형이 정해지지 않은 재난은 과거 검색을 하지 않음
}
PAST_YEARS = 5         # 재난 발생일 이전 몇 년까지 찾을지
PAST_TRIGGER_DAYS = 30  # 최근 며칠 안에 발생한 재난 카드만 과거 검색 (과거 기사로 만들어진 과거 카드가
                        # 또 과거 검색을 부르는 연쇄를 막음. 날짜가 없는 카드는 최근 재난으로 봄)
PAST_PER_QUERY = 20    # 검색어마다 보낼 과거 기사 수 (구글 뉴스 RSS는 최대 100건)
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"

API_URL = os.getenv("API_URL", "http://localhost:8001")
INGEST_TOKEN = os.getenv("INGEST_TOKEN")

# 서버(app/main.py의 ArticleIn) 제한에 맞춤
MAX_BATCH = 100  # 서버는 최대 500개까지 받지만, Gemini 분석 시간 때문에 README가 100개 이하를 권장
MAX_TITLE = 500
MAX_SOURCE = 100
MAX_CONTENT = 50_000

# 스크립트와 같은 폴더에 저장되는 기록 파일
BASE_DIR = Path(__file__).resolve().parent
SENT_FILE = BASE_DIR / "sent_urls.txt"           # 처리한 기사 URL: 서버로 보냈거나 본문이 없어 제외 (다음 실행 때 다시 크롤링하지 않음)
PENDING_FILE = BASE_DIR / "pending_articles.json"  # 전송 실패한 기사 (다음 실행 때 먼저 다시 보냄)
PAST_DONE_FILE = BASE_DIR / "past_searched.txt"    # 과거 검색을 마친 '위치|유형' (같은 지역을 매번 다시 검색하지 않음)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

# robots.txt에서 이 AI 봇이 막힌 주소는 크롤링하지 않습니다.
# 수집한 기사는 Gemini로 분석하므로 구글이 정한 Gemini용 이름(Google-Extended)을 기준으로 합니다.
# 이 이름 전용 규칙이 없으면 robots.txt 표준대로 일반 규칙(User-agent: *)을 따릅니다.
AI_AGENTS = ("Google-Extended",)

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


def fetch_bytes(url: str) -> bytes:
  """XML(사이트맵·RSS)용: 인코딩을 바꾸지 않은 원본 바이트"""
  resp = requests.get(url, headers=HEADERS, timeout=20)
  resp.raise_for_status()
  return resp.content


def parse_robots(text: str) -> list[tuple[list[str], list[tuple[bool, str]]]]:
  """robots.txt → [(user-agent 목록, [(허용 여부, 경로 패턴)])]"""
  groups, agents, rules, last_was_agent = [], [], [], False
  for raw in text.splitlines():
    line = raw.split("#", 1)[0].strip()
    if ":" not in line:
      continue
    key, value = (x.strip() for x in line.split(":", 1))
    key = key.lower()
    if key == "user-agent":
      if not last_was_agent and agents:  # 규칙 뒤에 새 user-agent가 오면 새 묶음
        groups.append((agents, rules))
        agents, rules = [], []
      agents.append(value.lower())
      last_was_agent = True
    elif key in ("allow", "disallow"):
      last_was_agent = False
      if agents and value:  # 빈 Disallow는 '모두 허용'
        rules.append((key == "allow", value))
  if agents:
    groups.append((agents, rules))
  return groups


def agent_rules(groups, agent: str) -> list[tuple[bool, str]]:
  """그 봇 전용 묶음의 규칙, 없으면 일반(*) 묶음의 규칙"""
  agent = agent.lower()
  if any(agent in agents for agents, _ in groups):
    return [r for agents, rs in groups if agent in agents for r in rs]
  return [r for agents, rs in groups if "*" in agents for r in rs]


def path_allowed(rules: list[tuple[bool, str]], path: str) -> bool:
  """구글 방식: 가장 길게 일치하는 규칙을 따름 (길이가 같으면 허용 우선, * 와 $ 지원)"""
  best = None
  for allow, pattern in rules:
    regex = "^" + re.escape(pattern).replace(r"\*", ".*")
    if regex.endswith(r"\$"):
      regex = regex[:-2] + "$"
    if re.match(regex, path):
      candidate = (len(pattern), allow)
      best = candidate if best is None or candidate > best else best
  return True if best is None else best[1]


_robots_cache: dict[str, list | bool] = {}


def get_robots(url: str) -> list | bool:
  """그 사이트의 robots.txt 묶음 (없으면 [] = 전부 허용, 접근 금지면 False = 전부 금지)"""
  p = urlparse(url)
  base = f"{p.scheme}://{p.netloc}"
  if base not in _robots_cache:
    try:
      resp = requests.get(base + "/robots.txt", headers=HEADERS, timeout=10)
      if resp.status_code in (401, 403):
        _robots_cache[base] = False
      elif resp.status_code >= 400:
        _robots_cache[base] = []
      else:
        _robots_cache[base] = parse_robots(resp.text)
    except requests.RequestException:
      _robots_cache[base] = []  # 확인할 수 없으면 허용 (기사 요청도 실패할 가능성이 큼)
  return _robots_cache[base]


def ai_blocked(url: str) -> str | None:
  """robots.txt가 이 주소의 AI 사용을 막았으면 이유를, 아니면 None"""
  groups = get_robots(url)
  if groups is False:
    return "robots.txt 접근 금지"
  p = urlparse(url)
  path = (p.path or "/") + (f"?{p.query}" if p.query else "")
  for agent in AI_AGENTS:
    if not path_allowed(agent_rules(groups, agent), path):
      return f"robots.txt에서 AI 사용 제한({agent})"
  return None


def site_blocks_ai(site_url: str) -> bool:
  """사이트 전체를 AI에 막았는지 (Disallow: / 이면서 허용 규칙이 하나도 없음)
  기사 주소를 모르는 과거 기사(구글 뉴스)의 원래 언론사를 확인할 때 사용"""
  groups = get_robots(site_url)
  if groups is False:
    return True
  for agent in AI_AGENTS:
    rules = agent_rules(groups, agent)
    if (False, "/") in rules and not any(allow for allow, _ in rules):
      return True
  return False


def filter_ai_allowed(urls: list[str], label: str = "") -> list[str]:
  """AI 사용이 제한된 주소를 빼고, 뺀 건수를 언론사별로 알려 줌"""
  allowed, blocked = [], defaultdict(int)
  for url in urls:
    if ai_blocked(url):
      blocked[get_domain(url)] += 1
    else:
      allowed.append(url)
  if blocked:
    detail = ", ".join(f"{d} {n}건" for d, n in blocked.items())
    print(f"  {label}robots.txt AI 사용 제한으로 제외: {detail}")
  return allowed


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


def xml_name(tag: str) -> str:
  """'{http://www.sitemaps.org/...}loc' → 'loc'"""
  return tag.split("}")[-1].lower()


def clean_text(value: str) -> str:
  """HTML 태그와 &quot; 같은 HTML 문자를 정리"""
  return html_lib.unescape(re.sub(r"<[^>]+>", "", value or "")).strip()


# ───────────────────────── [최근] 뉴스 사이트맵 ─────────────────────────

def read_sitemap(url: str, depth: int = 0) -> list[dict]:
  """사이트맵 → [{url, title, date}]. 사이트맵 묶음(sitemapindex)이면 안쪽 사이트맵까지 읽음"""
  root = ET.fromstring(fetch_bytes(url))
  if xml_name(root.tag) == "sitemapindex":
    if depth >= 1:
      return []
    children = [c.text.strip() for c in root.iter() if xml_name(c.tag) == "loc" and c.text]
    return [entry for child in children[:10] for entry in read_sitemap(child, depth + 1)]

  entries = []
  for node in root:
    if xml_name(node.tag) != "url":
      continue
    # 기사 주소는 <url> 바로 아래의 <loc>만 사용 (<image:loc> 같은 사진 주소와 섞이지 않게)
    loc = next(((c.text or "").strip() for c in node if xml_name(c.tag) == "loc"), "")
    fields = {"loc": loc}
    for c in node.iter():
      fields.setdefault(xml_name(c.tag), (c.text or "").strip())
    if fields.get("loc"):
      entries.append({
          "url": normalize_url(fields["loc"]),
          "title": clean_text(fields.get("title", "")),  # 뉴스 사이트맵의 <news:title>
          "date": fields.get("publication_date") or fields.get("lastmod"),
      })
  return entries


def title_matches(title: str) -> bool:
  for word in TITLE_EXCLUDE:
    title = title.replace(word, "")
  return any(k in title for k in TITLE_KEYWORDS)


def find_recent_from_sitemaps(seen: set[str]) -> list[dict]:
  """NEWS_SITEMAPS에서 최근 RECENT_DAYS일 기사 중 제목에 TITLE_KEYWORDS가 있는 새 기사"""
  since = (datetime.now(KST) - timedelta(days=RECENT_DAYS)).strftime("%Y-%m-%d")
  targets = []
  for name, sitemap_urls in NEWS_SITEMAPS.items():
    entries = []
    for sitemap_url in sitemap_urls:
      try:
        entries += read_sitemap(sitemap_url)
      except (requests.RequestException, ET.ParseError) as e:
        print(f"  {name} 사이트맵을 읽지 못했습니다: {sitemap_url} - {e}")
    recent = [e for e in entries if (to_date(e["date"]) or since) >= since]
    matched = [e for e in recent if title_matches(e["title"])]
    new = [e["url"] for e in matched if e["url"] not in seen]
    seen.update(new)
    allowed = [u for u in new if not ai_blocked(u)]
    targets += [{"url": u} for u in allowed]
    print(f"  {name}: 사이트맵 {len(entries)}건 → 최근 {RECENT_DAYS}일 {len(recent)}건 → "
          f"재난·기후 제목 {len(matched)}건 → 새 기사 {len(new)}건 → "
          f"AI 사용 허용 {len(allowed)}건" + (" (robots.txt AI 사용 제한)" if new and not allowed else ""))
  return targets


# ───────────────────────── [과거] 구글 뉴스 RSS 기간 검색 ─────────────────────────

def search_google_news(query: str, after: str, before: str, count: int) -> list[dict]:
  """구글 뉴스 RSS 기간 검색 → 서버 형식 기사 목록 (본문 없음)
  구글 뉴스는 원문 주소 대신 구글 중계 주소만 주므로 url은 그 주소, content는 None"""
  q = f"{query} after:{after} before:{before}"
  rss_url = GOOGLE_NEWS_RSS.format(q=quote(q))
  reason = ai_blocked(rss_url)
  if reason:  # 구글 뉴스 자체가 AI 사용을 막으면 과거 검색을 하지 않음
    raise requests.RequestException(f"구글 뉴스 {reason}")
  root = ET.fromstring(fetch_bytes(rss_url))
  results = []
  for item in root.iter("item"):
    source_tag = item.find("source")
    source = (source_tag.text or "").strip() if source_tag is not None else ""
    site_url = source_tag.get("url", "") if source_tag is not None else ""
    if site_url.startswith("http") and site_blocks_ai(site_url):
      continue  # 원래 언론사가 사이트 전체의 AI 사용을 막음 (예: 경향신문, KBS)
    title = clean_text(item.findtext("title") or "")
    if source:  # "기사 제목 - 연합뉴스" → "기사 제목"
      title = re.sub(rf"\s*-\s*{re.escape(source)}\s*$", "", title)
    link = (item.findtext("link") or "").strip()
    if title and link.startswith("http"):
      results.append({
          "title": title[:MAX_TITLE],
          "url": link,
          "source": (source or "Google 뉴스")[:MAX_SOURCE],
          "published_at": to_date(item.findtext("pubDate")),
          "content": None,
      })
  return results[:count]


def fetch_disasters() -> list[dict]:
  """서버의 재난 카드 전체 (GET /api/disasters, 100개씩)"""
  items, offset = [], 0
  while True:
    resp = requests.get(f"{API_URL.rstrip('/')}/api/disasters",
                        params={"limit": 100, "offset": offset}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    items += data["items"]
    offset += len(data["items"])
    if not data["items"] or offset >= data["total"]:
      return items


def load_past_done() -> set[str]:
  if not PAST_DONE_FILE.exists():
    return set()
  return {l.strip() for l in PAST_DONE_FILE.read_text(encoding="utf-8").splitlines() if l.strip()}


def add_past_done(keys: list[str]) -> None:
  with PAST_DONE_FILE.open("a", encoding="utf-8") as f:
    f.writelines(k + "\n" for k in keys)


def years_before(date: str, years: int) -> str:
  d = datetime.strptime(date, "%Y-%m-%d")
  try:
    return d.replace(year=d.year - years).strftime("%Y-%m-%d")
  except ValueError:  # 2월 29일
    return d.replace(year=d.year - years, day=28).strftime("%Y-%m-%d")


def find_past(manual_queries: list[str], years: int, seen: set[str], use_cards: bool) -> tuple[list[dict], list[str]]:
  """과거 기사 찾기 → (보낼 기사, 검색을 마친 '위치|유형' 키)
  1) 서버 재난 카드의 위치·유형으로 PAST_QUERIES 검색 (이미 검색한 지역은 건너뜀)
  2) --past로 직접 넣은 검색어"""
  today = datetime.now(KST).strftime("%Y-%m-%d")
  jobs = []  # (설명, 검색어, after, before, 완료 키)

  if use_cards:
    try:
      disasters = fetch_disasters()
    except (requests.RequestException, KeyError, ValueError) as e:
      print(f"  재난 카드를 불러오지 못해 카드 기반 과거 검색은 건너뜁니다 ({API_URL}): {e}")
      disasters = []
    done = load_past_done()
    trigger_since = (datetime.now(KST) - timedelta(days=PAST_TRIGGER_DAYS)).strftime("%Y-%m-%d")
    no_location = old = 0
    for d in disasters:
      location = (d.get("location") or "").strip()
      words = PAST_QUERIES.get(d.get("type"), [])
      if not location or not words:
        no_location += not location
        continue
      if (to_date(d.get("date")) or today) < trigger_since:
        old += 1  # 오래된 재난 카드(과거 기사로 만들어진 카드 등)는 과거 검색을 하지 않음
        continue
      key = f"{location}|{d['type']}"
      if key in done:
        continue
      done.add(key)
      before = to_date(d.get("date")) or today
      after = years_before(before, years)
      place = re.sub(r"^(대한민국|한국)\s+", "", location)  # Gemini 위치 "대한민국 부산" → 검색어 "부산"
      jobs += [(f"카드 #{d['id']} {location}", f"{place} {w}", after, before, key) for w in words]
    print(f"  재난 카드 {len(disasters)}개 (위치 없는 카드 {no_location}개, "
          f"{PAST_TRIGGER_DAYS}일보다 오래된 카드 {old}개는 건너뜀)")

  for q in manual_queries:
    jobs.append(("직접 지정", q, years_before(today, years), today, None))

  articles, done_keys = [], []
  for label, query, after, before, key in jobs:
    try:
      found = search_google_news(query, after, before, PAST_PER_QUERY)
    except (requests.RequestException, ET.ParseError) as e:
      print(f"  [{label}] '{query}' 검색 실패: {e}")
      continue
    finally:
      time.sleep(1)  # 구글 뉴스 요청 간 대기
    new = [a for a in found if a["url"] not in seen]
    seen.update(a["url"] for a in new)
    articles += new
    if key and key not in done_keys:
      done_keys.append(key)
    print(f"  [{label}] '{query}' ({after} ~ {before}): {len(found)}건 중 새 기사 {len(new)}건")
  return articles, done_keys


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
  for parse in (lambda v: datetime.fromisoformat(v.replace("Z", "+00:00")),
                parsedate_to_datetime):  # ISO 형식, RSS·네이버 형식('Fri, 20 Nov 2026 19:13:45 +0900')
    try:
      dt = parse(value)
    except (ValueError, TypeError, IndexError):
      continue
    if dt.tzinfo is not None:
      dt = dt.astimezone(KST)
    return dt.strftime("%Y-%m-%d")
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

  for start in range(0, len(articles), MAX_BATCH):  # 한 요청에 MAX_BATCH개씩
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

def crawl_targets(targets: list[dict], articles: list[dict], excluded: list[str]) -> None:
  """기사 주소 목록을 하나씩 열어 articles(보낼 기사)와 excluded(본문 없어 제외)에 담음
  targets: [{url}]"""
  for i, target in enumerate(targets, start=1):
    url = target["url"]
    prefix = f"  [{i}/{len(targets)}]"
    try:
      art = crawl_article(url)
    except requests.RequestException as e:
      print(f"{prefix} 실패: {url} - {e}")
      continue
    finally:
      time.sleep(1)  # 언론사 서버 부담을 줄이기 위해 요청 간 대기

    if not art["content"] or not art["title"]:  # 본문 없이 이미지만 있는 기사 등 제외
      print(f"{prefix} 제외(본문 없음): {art['title'] or url}")
      excluded.append(url)
      continue
    articles.append(art)
    print(f"{prefix} {art['source']} | {art['title']}")


def collect_recent(list_urls: list[str], use_sitemaps: bool, seen: set[str]) -> tuple[list[dict], list[str]]:
  """[최근] 목록 페이지 + 뉴스 사이트맵 → (보낼 기사, 본문이 없어 제외한 URL)"""
  articles = []
  excluded = []

  # 1) 지정한 목록 페이지
  for list_url in list_urls:
    mode = "등록된 규칙" if get_rule(list_url) else "자동 판별"
    print(f"\n▶ 목록: {list_url}  [{mode}]")
    try:
      urls = crawl_list(list_url)
    except requests.RequestException as e:
      print(f"  목록을 불러오지 못했습니다: {e}")
      continue
    if not urls:
      print("  기사를 찾지 못했습니다. 목록 페이지 주소인지 확인하거나 SITE_RULES에 규칙을 추가해 주세요.")
      continue

    new_urls = [u for u in urls if u not in seen]
    seen.update(new_urls)
    print(f"  목록 {len(urls)}건 중 새 기사 {len(new_urls)}건")
    new_urls = filter_ai_allowed(new_urls)
    crawl_targets([{"url": u} for u in new_urls], articles, excluded)

  # 2) 언론사 뉴스 사이트맵에서 재난·기후 관련 최근 기사 직접 찾기
  if use_sitemaps:
    print(f"\n▶ 뉴스 사이트맵 (최근 {RECENT_DAYS}일, 제목 키워드)")
    targets = find_recent_from_sitemaps(seen)
    crawl_targets(targets, articles, excluded)

  return articles, excluded


def main():
  parser = argparse.ArgumentParser(description="뉴스 기사를 수집해 재난 데이터 API 서버로 전송")
  parser.add_argument("urls", nargs="*", help="수집할 목록 페이지 주소 (생략하면 LIST_URLS 사용)")
  parser.add_argument("--past", action="append", default=[],
                      help='과거 기사 검색어 직접 지정 (예: --past "부산 침수", 여러 번 쓸 수 있음)')
  parser.add_argument("--past-years", type=int, default=PAST_YEARS,
                      help=f"과거 몇 년까지 찾을지 (기본 {PAST_YEARS})")
  parser.add_argument("--no-lists", action="store_true", help="목록 페이지(LIST_URLS)를 수집하지 않음")
  parser.add_argument("--no-sitemaps", action="store_true", help="뉴스 사이트맵을 수집하지 않음")
  parser.add_argument("--no-past", action="store_true", help="재난 카드 기반 과거 기사 검색을 하지 않음")
  parser.add_argument("--dry-run", action="store_true", help="서버로 보내지 않고 보낼 내용만 출력")
  args = parser.parse_args()

  list_urls = [] if args.no_lists else ([u.strip().lstrip("﻿") for u in args.urls] or LIST_URLS)
  sent_urls = load_sent_urls()
  pending = load_pending()
  # 예전에 저장된 미전송 기사 중 robots.txt로 AI 사용이 제한된 기사는 보내지 않음
  dropped = [a for a in pending if ai_blocked(a["url"])]
  if dropped:
    pending = [a for a in pending if a not in dropped]
    print(f"미전송 기사 중 robots.txt AI 사용 제한 기사 {len(dropped)}건은 보내지 않습니다.")
    if not args.dry_run:
      save_pending(pending)
  if pending:
    print(f"지난번에 보내지 못한 기사 {len(pending)}건을 함께 보냅니다.")
  seen = sent_urls | {a["url"] for a in pending}

  # [최근] 목록 페이지 + 뉴스 사이트맵 (본문 포함)
  recent, excluded = collect_recent(list_urls, not args.no_sitemaps, seen)

  # [과거] 지난 실행까지 만들어진 재난 카드의 지역 + --past 검색어 (제목·언론사·날짜만)
  past, done_keys = [], []
  if not args.no_past or args.past:
    print(f"\n▶ 과거 기사 (구글 뉴스 RSS, 최근 {args.past_years}년)")
    past, done_keys = find_past(args.past, args.past_years, seen, use_cards=not args.no_past)

  # 모은 기사를 한 요청으로 전송 (README: 한 번 수집할 때마다 모은 기사를 한 요청으로)
  articles = pending + recent + past
  print(f"\n보낼 기사: {len(articles)}건 (미전송 {len(pending)} / 최근 {len(recent)} / 과거 {len(past)})")

  if not args.dry_run:
    add_sent_urls(excluded)    # 본문 없는 기사도 기록해서 다음 실행 때 다시 열지 않음
    add_past_done(done_keys)   # 과거 검색을 마친 지역은 다음 실행 때 다시 검색하지 않음
  if not articles:
    return

  if args.dry_run:
    preview = [{**a, "content": (a["content"] or "")[:80] + "…"} for a in recent[:2]] + past[:3]
    print(json.dumps(preview, ensure_ascii=False, indent=2))
    print("--dry-run: 서버로 보내지 않았습니다.")
    return

  failed = send_articles(articles)
  save_pending(failed)
  if failed:
    print(f"보내지 못한 {len(failed)}건은 {PENDING_FILE}에 저장했습니다. 다음 실행 때 다시 보냅니다.")


if __name__ == "__main__":
  main()
