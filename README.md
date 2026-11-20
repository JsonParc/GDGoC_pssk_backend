# 재난 데이터 API 서버

크롤러가 보낸 기사를 받아 재난 카드로 저장하고, 프론트엔드에 목록·상세·행동요령을 줍니다.

## 실행

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --reload --port 8001
```

API 문서와 직접 호출해 보기: http://localhost:8001/docs

테스트: `.venv\Scripts\python -m pytest`

## 환경 변수

`.env`에 적거나 실행 전에 설정합니다. `.env`는 git에 올라가지 않습니다.

| 이름 | 기본값 | 설명 |
| --- | --- | --- |
| `KMA_API_KEY` | 없음 | 기상청 API허브 인증키. 없으면 `/api/warnings`, `/api/forecast`가 503 |
| `INGEST_TOKEN` | 없음 | 설정하면 `POST /api/articles`에 `X-Ingest-Token` 헤더가 필요합니다 |
| `ALLOWED_ORIGINS` | `http://localhost:8000` | 프론트엔드 주소(쉼표로 여러 개) |
| `DB_PATH` | `data.db` | SQLite 파일 위치 |

## API

| 메서드 | 주소 | 설명 |
| --- | --- | --- |
| POST | `/api/articles` | 크롤러가 기사 목록을 한 번에 보냄 (최대 500개) |
| GET | `/api/disasters?type=flood&limit=20&offset=0` | 재난 목록 (`flood` `wildfire` `heatwave` `other`) |
| GET | `/api/disasters/{id}` | 재난 상세 + 원문 기사 링크 |
| GET | `/api/guides/{type}` | 재난별 행동요령 |
| GET | `/api/warnings?type=H` | 지금 발효 중인 기상특보 (H 폭염, R 호우, D 건조, C 한파, S 대설, W 강풍 …) |
| GET | `/api/forecast?lat=37.34&lon=126.73` | 위치의 단기예보 (TMP 기온, PCP 강수량, POP 강수확률, REH 습도 …) |

기상청 결과는 특보 10분, 예보 30분, 특보구역 하루 동안 재사용합니다. 기상청이 실패하면(가끔 30초 뒤 504) 한 번 다시 부르고, 그래도 안 되면 6시간 안의 이전 결과를 줍니다.

### 크롤러 → 서버 (`POST /api/articles`)

한 번 수집할 때마다 **모은 기사를 한 요청으로** 보내 주세요. 이미 받은 `url`은 다시 분석하지 않고 건너뜁니다.

```json
[
  {"title": "부산 폭우로 도로 침수", "url": "https://...", "source": "연합뉴스", "published_at": "2026-11-20", "content": "본문(선택)"}
]
```

응답: `{"received": 1, "new": 1, "skipped": 0, "failed": 0, "disasters_created": 1}`

`content`는 분석에만 쓰고 저장하지 않습니다.

### 크롤러 (`crawler/`)

뉴스 목록 페이지에서 기사를 모아 위 형식으로 한 번에 보냅니다. 서버와 따로 실행합니다.

```
.venv\Scripts\python -m pip install -r crawler\requirements.txt
.venv\Scripts\python crawler\crawler.py              # 기본 목록 4곳(경향신문·뉴스펭귄·한겨레·Daum 기후) 수집 후 전송
.venv\Scripts\python crawler\crawler.py --dry-run    # 보내지 않고 보낼 내용만 확인
.venv\Scripts\python crawler\crawler.py <목록 주소>   # 원하는 목록 페이지만 수집
```

| 환경 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `API_URL` | `http://localhost:8001` | 서버 주소 |
| `INGEST_TOKEN` | 없음 | 서버에 설정한 값과 같게 넣으면 `X-Ingest-Token` 헤더로 보냅니다 |

- `published_at`은 `YYYY-MM-DD`(한국 시간 기준 날짜)로 보냅니다.
- 본문이 없는 기사(이미지만 있는 기사 등)는 보내지 않습니다.
- 보냈거나 제외한 URL은 `crawler/sent_urls.txt`에 기록해 다음 실행 때 다시 열지 않습니다.
- 전송에 실패한 기사는 `crawler/pending_articles.json`에 두었다가 다음 실행 때 먼저 보냅니다.
- 언론사를 추가하려면 `crawler.py`의 `SITE_RULES`에 목록 링크·본문 CSS 선택자를 넣습니다. 규칙이 없는 사이트는 자동 판별로 수집합니다.

### Gemini 담당

`app/extract.py`의 `extract()`를 교체하면 됩니다. 지금은 키워드로 홍수·산불·폭염만 구분하는 임시 규칙입니다. 반환 형식은 `app/main.py`의 `DisasterIn`이고, 서버가 검증한 뒤 저장합니다.
