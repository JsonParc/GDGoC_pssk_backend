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

`.env.example`을 `.env`로 복사해 값을 채우거나, 배포 환경의 환경 변수로 설정합니다. `.env`는 git에 올라가지 않습니다.

| 이름 | 기본값 | 설명 |
| --- | --- | --- |
| `KMA_API_KEY` | 없음 | 기상청 API허브 인증키. 없으면 `/api/warnings`, `/api/forecast`가 503 |
| `GEMINI_API_KEY` | 없음 | Google AI Studio 키. 없으면 기사 분석을 키워드 규칙으로 대신함 |
| `GEMINI_MODELS` | `gemini-3.5-flash,gemini-3.5-flash-lite` | 쓸 모델 순서(앞 모델이 붐비면 다음 모델) |
| `INGEST_TOKEN` | 없음 | **필수.** 크롤러는 `POST /api/articles`에 `X-Ingest-Token` 헤더로 이 값을 보냅니다. 설정하지 않으면 기사를 받지 않습니다(503) |
| `ALLOWED_ORIGINS` | `http://localhost:8000` | 프론트엔드 주소(쉼표로 여러 개) |
| `DB_PATH` | `data.db` | SQLite 파일 위치 |

## API

| 메서드 | 주소 | 설명 |
| --- | --- | --- |
| POST | `/api/articles` | 크롤러가 기사 목록을 한 번에 보냄 (최대 500개) |
| GET | `/api/disasters?type=flood&limit=20&offset=0` | 재난 목록 (`flood` `wildfire` `heatwave` `other`) |
| GET | `/api/disasters/{id}` | 재난 상세 + 원문 기사 링크 |
| GET | `/api/guides` | 재난별 행동지침 전체 (호우·태풍·대설·한파·폭염·강풍·풍랑·황사·지진해일·지진·산불·건조·폭풍해일·안개). `needs_writing: true`는 공식 문구가 없어 직접 작성 필요 |
| GET | `/api/guides/active?region=강원` | **지금 특보가 발효 중인** 재난의 행동지침을 특보 **단계(예비특보·주의보·경보)별로** 묶어서, 해당 특보 지역과 함께 줌. 특보가 해제되면 빠짐 |
| GET | `/api/guides/{type}?level=경보` | 재난 하나의 행동지침. 공통 `steps` + 단계 `level`(공식 기준 `criteria`, 단계 지침 `steps`). `level`을 비우면 모든 단계. 단계 이름: 특보 `예비특보·주의보·경보`(폭염 `중대경보`), 지진 `진도 Ⅰ`~`진도 Ⅻ`, 태풍 `강도 1`~`강도 5` |
| (고치기) | `app/guides.py` | 행동지침 문구는 이 파일을 직접 고치고 서버를 다시 켜면 반영됩니다. 고친 뒤 `.venv\Scripts\python -m pytest tests/test_guides.py`로 확인 |
| GET | `/api/warnings?type=H` | 지금 발효 중인 기상특보 (특보현황 API). H 폭염, R 호우, D 건조, C 한파, S 대설, W 강풍 … `end_notice`는 기상청 해제 예고, `guide_type`은 볼 행동지침 |
| GET | `/api/forecast?lat=37.34&lon=126.73` | 위치의 **예보** (단기예보, 1시간 간격 약 3일). TMP 기온, PCP 강수량, POP 강수확률, REH 습도 … |
| GET | `/api/current?lat=37.34&lon=126.73` | 위치의 **지금 실제 날씨** (초단기실황, 매시 관측). T1H 기온, RN1 1시간 강수량, REH 습도, WSD 풍속 … |
| GET | `/api/earthquakes?overseas=false` | 최근 지진 (진앙 위경도·규모·진도). `max_intensity`와 `guide_level`(예: 진도 Ⅳ)로 볼 행동지침 단계를 알 수 있음 |
| GET | `/api/typhoons?year=2025` | 연도별 태풍 목록 (`korea_effect`: 상륙·직접영향·간접영향·없음) |
| GET | `/api/typhoons/{year}/{seq}` | 태풍 경로 (시간별 위경도·중심기압·최대풍속·15/25m/s 반경, `observed` true 분석 / false 예측). `strength` 강도 1~5(최대풍속), `size` 소형~초대형(15m/s 반경, 기상청 참고 분류), `guide_level` |

기상청 결과는 특보 10분, 예보 30분, 지금 날씨 10분, 지진 5분, 태풍 목록 1시간, 태풍 경로 30분 동안 재사용합니다. 태풍 시각은 UTC입니다(한국 시간 = UTC + 9시간). 기상청이 실패하면(가끔 30초 뒤 504, 연결 끊김) 한 번 다시 부르고, 그래도 안 되면 6시간 안의 이전 결과를 줍니다.

### 크롤러 → 서버 (`POST /api/articles`)

헤더에 `X-Ingest-Token: <INGEST_TOKEN 값>`을 넣고, 한 번 수집할 때마다 **모은 기사를 한 요청으로** 보내 주세요. 토큰 값은 서버 담당에게 따로(메신저 등) 받으세요. 코드나 git에 넣지 마세요. 이미 받은 `url`은 다시 분석하지 않고 건너뜁니다.

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
| `INGEST_TOKEN` | 없음 | **필수.** 서버의 `INGEST_TOKEN`과 같은 값. `X-Ingest-Token` 헤더로 보냅니다 (없으면 서버가 401·503) |

- `published_at`은 `YYYY-MM-DD`(한국 시간 기준 날짜)로 보냅니다.
- 본문이 없는 기사(이미지만 있는 기사 등)는 보내지 않습니다.
- 보냈거나 제외한 URL은 `crawler/sent_urls.txt`에 기록해 다음 실행 때 다시 열지 않습니다.
- 전송에 실패한 기사는 `crawler/pending_articles.json`에 두었다가 다음 실행 때 먼저 보냅니다.
- 언론사를 추가하려면 `crawler.py`의 `SITE_RULES`에 목록 링크·본문 CSS 선택자를 넣습니다. 규칙이 없는 사이트는 자동 판별로 수집합니다.

### Gemini 분석 (`app/extract.py`)

- 새 기사를 **20개씩 묶어 Gemini를 한 번** 부릅니다. 같은 사건 기사는 재난 하나로 묶습니다(언어가 달라도).
- 기사마다 본문은 앞 1,500자만 보내고, 본문은 저장하지 않습니다.
- 뽑는 값: 종류(flood·wildfire·heatwave·other), 한국어 제목·요약, 위치·위경도, 날짜, 피해 규모, `viz_params`(`rainfall_mm`, `flood_depth_m`, `burned_area_ha`, `max_temp_c`, 기사에 수치가 없으면 비움).
- 결과는 `app/main.py`의 `DisasterIn`으로 검증합니다. 형식이 틀렸거나 Gemini가 실패한 기사는 저장하지 않아서, 다음 전송 때 다시 분석됩니다.
- 모델이 붐비면(503·429·시간 초과) `GEMINI_MODELS`의 다음 모델로 한 번 넘어갑니다.
- `GEMINI_API_KEY`가 없으면 키워드 규칙으로 대신합니다(테스트·연습용).
- 응답은 보통 수 초, Google이 붐비면 1분 가까이 걸립니다. 크롤러는 한 요청에 100개 이하로 보내는 것을 권장합니다.
