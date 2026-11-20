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
| GET | `/api/disasters?type=heavysnow&limit=20&offset=0` | 재난 카드 목록. 종류는 행동지침 종류와 같음(`flood` `typhoon` `heavysnow` `coldwave` `heatwave` `strongwind` `wave` `yellowdust` `tsunami` `earthquake` `wildfire` `dry` `stormsurge` `fog`) + `other`. 카드의 `type`으로 `/api/guides/{type}`를 바로 조회 |
| GET | `/api/disasters/{id}` | 재난 상세 + 원문 기사 링크 + **`map_effect`**(프론트 3D 지도 재난 효과 입력값, 아래 참고). 목록에는 `map_effect_supported`·`map_effect_type`만 |
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

기사를 모아 위 형식으로 보냅니다(100개씩). 서버와 따로 실행하고, **하루 한 번 이상** 돌려야 최근 기사를 놓치지 않습니다.

| 수집 방법 | 내용 | 보내는 값 |
| --- | --- | --- |
| 목록 페이지 (`LIST_URLS`) | 경향신문·뉴스펭귄·한겨레·Daum 기후 섹션 첫 페이지 | 원문 주소 + 본문 |
| 뉴스 사이트맵 (`NEWS_SITEMAPS`) | 연합뉴스·KBS·동아일보·매일경제가 공개한 최근 2~3일 기사 중 제목이 재난·기후 기사로 보이는 기사 (아래 제목 필터) | 원문 주소 + 본문 |

```
.venv\Scripts\python -m pip install -r crawler\requirements.txt
.venv\Scripts\python crawler\crawler.py              # 목록 페이지 + 사이트맵 수집 후 전송
.venv\Scripts\python crawler\crawler.py --dry-run    # 보내지 않고 보낼 내용만 확인
.venv\Scripts\python crawler\crawler.py --no-lists   # 사이트맵만 (--no-sitemaps: 목록 페이지만)
.venv\Scripts\python crawler\crawler.py <목록 주소>   # 원하는 목록 페이지만 수집
```

**robots.txt에서 Gemini의 AI 사용(`Google-Extended`)을 막은 주소는 열지 않습니다.** 지금 경향신문·KBS 기사가 빠집니다.

사이트맵 기사 제목 필터 (`crawler.py` 위쪽에서 단어를 더하거나 뺄 수 있음):

| 설정 | 동작 | 예 |
| --- | --- | --- |
| `TITLE_KEYWORDS` | 이 단어가 제목에 있어야 수집 | 폭우·침수·태풍·산불·지진·대설·한파·폭염·주의보·특보·기후변화 … |
| `TITLE_EXCLUDE` | 이 단어만 지운 뒤 키워드를 다시 확인 | "재난문자"를 지우면 "수능 위급재난문자"는 빠지고, "대설 재난문자"는 '대설'이 남아 수집 |
| `TITLE_REJECT` | 이 표현이 있으면 제목째 제외 | 영화·방송(박스오피스, 따옴표 친 '폭설'), 증시, 비유(유동성 한파·보이스피싱 주의보), 수능 부정행위 |

| 환경 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `API_URL` | `http://localhost:8001` | 서버 주소 |
| `INGEST_TOKEN` | 없음 | **필수.** 서버의 `INGEST_TOKEN`과 같은 값. `X-Ingest-Token` 헤더로 보냅니다 (없으면 서버가 401·503) |

- `published_at`은 `YYYY-MM-DD`(한국 시간 기준 날짜)로 보냅니다.
- 본문이 없는 기사(이미지만 있는 기사 등)는 보내지 않습니다.
- 보냈거나 제외한 URL은 `crawler/sent_urls.txt`에 기록해 다음 실행 때 다시 열지 않습니다.
- 전송에 실패한 기사는 `crawler/pending_articles.json`에 두었다가 다음 실행 때 먼저 보냅니다.
- 언론사를 추가하려면 `crawler.py`의 `SITE_RULES`에 목록 링크·본문 CSS 선택자를 넣습니다. 규칙이 없는 사이트는 자동 판별로 수집합니다.

### 지도 재난 효과 입력값 (`app/map_effect.py`)

카드 상세의 `map_effect`를 프론트 브이월드 3D 지도에 그대로 넣으면 됩니다.

```js
const card = await (await fetch(`http://localhost:8001/api/disasters/${id}`)).json();
const e = card.map_effect;
if (e.supported) koreaDisaster.set(e.type, e.params, e.position);
```

```json
"map_effect": {"supported": true, "type": "snow", "params": {"wind": 30.0, "temp": -6.0},
               "position": {"longitude": 127.153, "latitude": 35.7175}, "sources": {"wind": "article", "temp": "article"}}
```

- 종류와 값 이름·단위는 뷰어 `js/disaster.js`의 `DisasterFX.TYPES`에 맞춤 (카드 `earthquake` → `quake`, `heavysnow`·`coldwave` → `snow`). 범위를 넘는 값과 빈 값은 프론트가 최소·최대·기본값으로 맞춤
- 지원: 태풍·호우·지진·지진해일·산불·폭염·대설·한파. 나머지(강풍·풍랑·황사·건조·폭풍해일·안개·기타)는 `supported: false`
- `position`은 카드 좌표(Gemini가 지역 이름으로 추정한 지역 중심)
- `sources`: `article` 기사 값, `derived` 기사 값으로 계산(총량 ÷ 지속 시간), `kma_current` 기상청 지금 날씨(카드 날짜가 오늘·어제이고 한국일 때 빈 값만)
- 목록(`/api/disasters`)에는 `map_effect_supported`·`map_effect_type`만 (기상청 호출 없음)

### Gemini 분석 (`app/extract.py`)

- 새 기사를 **20개씩 묶어 Gemini를 한 번** 부릅니다. 같은 사건 기사는 재난 하나로 묶습니다(언어가 달라도).
- 기사마다 본문은 앞 1,500자만 보내고, 본문은 저장하지 않습니다.
- 뽑는 값: 종류(행동지침 14종 + other, `app/guides.py`의 `DISASTER_TYPES`), 한국어 제목·요약, 위치·위경도, 날짜, 피해 규모, `viz_params`(`rainfall_mm`, `flood_depth_m`, `burned_area_ha`, `max_temp_c`, `min_temp_c`, `snowfall_cm`, `max_wind_ms`, `magnitude`, `rain_rate_mm_h`, `duration_hours`, `duration_days`, `snow_rate_cm_h`, `humidity_pct`, `wind_dir_deg`, `depth_km`, `wave_height_m`, 기사에 수치가 없으면 비움).
- 결과는 `app/main.py`의 `DisasterIn`으로 검증합니다. 형식이 틀렸거나 Gemini가 실패한 기사는 저장하지 않아서, 다음 전송 때 다시 분석됩니다.
- 모델이 붐비면(503·429·시간 초과) `GEMINI_MODELS`의 다음 모델로 한 번 넘어갑니다.
- `GEMINI_API_KEY`가 없으면 키워드 규칙으로 대신합니다(테스트·연습용).
- 응답은 보통 수 초, Google이 붐비면 1분 가까이 걸립니다. 크롤러는 한 요청에 100개 이하로 보내는 것을 권장합니다.
