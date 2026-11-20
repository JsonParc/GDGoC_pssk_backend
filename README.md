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
| `FRONTEND_DIR` | 옆 폴더 `../GDGoC_TUK_pssk` | 프론트(뷰어 저장소) 폴더. 있으면 이 서버가 프론트 화면도 같이 보냄(아래 '서버 하나로 운영') |
| `ALLOWED_ORIGINS` | `http://localhost:8000` | 프론트엔드 주소(쉼표로 여러 개) |
| `DB_PATH` | `data.db` | SQLite 파일 위치. **Render는 재배포·재시작 때 파일이 지워지므로 디스크(예: `/var/data`)를 붙이고 `/var/data/data.db`로 지정** |
| `KAKAO_REST_API_KEY` | 없음 | 카카오 REST API 키(클릭 지점 재난 이력 분석용 웹 검색). 없으면 새 지역 분석이 503 (이미 분석한 지역의 캐시는 보임) |
| `REGION_CACHE_HOURS` | `24` | 지역 분석 결과를 다시 쓰는 시간 |

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

### 서버 하나로 운영 (프론트 + API, `app/frontend.py`)

이 서버가 프론트 화면 파일까지 같이 보냅니다. 주소 하나로 화면과 API를 함께 씁니다.

| 주소 | 내용 |
| --- | --- |
| `/`, `/korea/vworld.html` … | 프론트 파일 (`FRONTEND_DIR` 폴더) |
| `/api/...`, `/docs` | 백엔드 API (프론트 파일보다 먼저) |

- 같은 주소라서 프론트는 `fetch('/api/disasters/1')`처럼 부르면 되고, `ALLOWED_ORIGINS`는 필요 없습니다.
- `.git`, `.env`처럼 점(.)으로 시작하는 파일·폴더는 보내지 않습니다.
- 내 PC: 백엔드 폴더 옆에 `GDGoC_TUK_pssk`(프론트 저장소)가 있으면 자동으로 씁니다. 서버를 켜고 http://localhost:8001/ 를 엽니다.
- 배포(Render 웹 서비스 예):
  - Build: `pip install -r requirements.txt && git clone --depth 1 https://github.com/JsonParc/GDGoC_TUK_pssk.git frontend`
  - Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
  - 환경 변수: `.env.example` 값 + `FRONTEND_DIR=frontend`
  - 프론트를 바꾸면 이 서비스를 다시 배포해야 반영됩니다 (Render의 Manual Deploy 또는 Deploy Hook).

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

### 클릭한 지점의 과거 재난 이력 (온디맨드, `app/regions.py` · `app/region_extract.py` · `app/kakao.py`)

지도에서 한 지점을 클릭하면 그 지점이 속한 지역의 과거 재난 이력을 **그때** 검색·분석합니다. 전국을 미리 모으지 않습니다.

프론트 순서:
1. 클릭 좌표로 `GET /api/disasters/nearby?lat=&lon=&radius_km=20` → DB에 이미 있는 근처 재난 카드를 바로 보여 줌
2. 클릭 좌표를 동네 이름으로 바꿈(프론트의 브이월드 주소 변환, 예: "서울특별시 강남구 대치동")
3. `POST /api/regions/analyze`에 이름+좌표를 보냄 → 캐시가 있으면 바로 결과, 없으면 `job_id`
4. `GET /api/analysis/{job_id}`를 2~3초마다 불러 진행률 표시 → 완료되면 `result.changes`(이력 타임라인)

| 메서드 | 주소 | 설명 |
| --- | --- | --- |
| GET | `/api/disasters/nearby?lat=37.50&lon=127.03&radius_km=20` | 반경 안의 재난 카드(최신순, `distance_km` 포함). 카드 좌표는 기사 속 지역의 대략적인 중심 |
| POST | `/api/regions/analyze` | `{"region_name":"서울특별시 강남구","latitude":37.50,"longitude":127.03,"radius_km":5,"disaster_type":"all","force_refresh":false}`. `disaster_type`: `all`(모든 재난 이력, 기본) 또는 `flood`(홍수 + 배수시설만). 캐시가 있으면 `{"cached":true,"result":{…}}`, 없으면 202 `{"job_id","status":"queued","region_id","cached":false}`. 같은 지역이 분석 중이면 그 `job_id` |
| GET | `/api/analysis/{job_id}` | `status`(queued·running·completed·failed), `stage`(news_search·extraction·timeline·scenario·done), `progress` 0~100, `error`(실패 시 다시 시도 안내), 완료 시 `result`(`changes`, `scenario`, `notice`, 자료가 없으면 `empty_message: "확인된 자료 없음"`) |
| GET | `/api/regions/{region_id}/timeline?disaster_type=all` | 날짜순 이력. 항목마다 `change_type`(`disaster_event` 재난 발생 또는 시설 변화), `disaster_type`(행동지침 종류와 같음 → `/api/guides/{disaster_type}`), `status`(occurred 발생·planned 계획·under_construction 착공·completed 준공·unknown 확인불가), `metrics`(기사 수치), `sources`(제목·URL·날짜·근거 문구), `evidence_status`, 기사끼리 상태가 다르면 `conflicts` |
| GET | `/api/regions/{region_id}/scenarios?disaster_type=all` | 홍수 전·후: 같은 비·시간·위치에서 `drain`(배수 능력 mm/h)만 바꾼 `before`·`after`. 프론트는 `koreaDisaster.set(before.type, before.params, position)` ↔ `after`로 전환 |

오류: 400 대한민국 밖 좌표·반경 0.5~30km 밖, 429 요청이 많음(같은 IP 10분에 5번, 대기 작업 6개), 503 검색 키·Gemini 키 없음. 검색 한도 초과·시간 초과(240초)·서버 재시작은 작업을 `failed`로 바꾸고 `error`에 다시 시도 안내를 넣습니다(무한 대기 없음).

**만들어 내지 않기 위한 규칙 (`region_extract.py`)**
- 분석 자료는 **카카오 웹 검색 API가 준 제목·요약뿐**입니다. 본문을 크롤링하거나 저장하지 않습니다. 저장: 제목·날짜·매체(도메인)·URL·검색 요약(`source_documents`).
- Gemini가 낸 근거 문구(`evidence_quote`)가 그 검색 결과 제목·요약에 실제로 있으면 `verified`, 없으면 `inferred`.
- 수치는 인용한 글에 그 숫자가 있을 때만 남깁니다.
- `completed`(준공)는 verified 근거 문구에 준공·완공·완료·가동·개통·마무리가 있고 날짜가 미래가 아닐 때만. 아니면 `unknown`(확인불가). 예보·주의 안내만 있는 글은 이력에 넣지 않습니다.
- 같은 시설(종류+이름)·같은 날 같은 재난은 하나로 합치고, 이전 분석 때 모은 같은 지역 근거도 함께 다시 분석합니다(최신 글만으로 과거를 덮어쓰지 않음).
- 근거 문서 검색은 SQLite FTS5(trigram, 3글자 이상)이고, FTS5가 없거나 2글자 지역 이름이면 LIKE 검색으로 대신합니다.

**전·후 시나리오 값의 출처(`param_evidence`)**: `article`(기사 수치), `derived`(누적 강수량 ÷ 지속 시간), `assumed`(시연용 가정값 rain 80·hours 12·drain 40→60, 실제 성능 아님). `data_quality`는 evidence·partial·illustrative. 준공이 확인된 배수시설이 없으면 `comparison_available: false`, `reason`에 "확인된 자료 없음". 실제 침수 예측이 아니라 **기사 근거 + 가정 기반 비교**입니다.

**검색 제공자 한계 (카카오 다음 웹문서 검색 API)**: 무료, 웹문서 검색 하루 30,000회(분석 한 번에 검색 12회, `all` 기준). 뉴스 전용이 아니라 블로그 등이 섞이고, 검색어마다 정확도순 10건만 받아 오래된 기사는 빠질 수 있으며, 날짜 범위 지정이 안 됩니다. 요약은 글의 일부라 수치·준공 여부가 요약에 없으면 놓칩니다. 매체 이름은 주지 않아 URL 도메인으로 표시합니다. 위치는 지점이 아니라 그 지역(구·동) 단위입니다. 키 발급: [카카오 개발자센터](https://developers.kakao.com/console/app) → 애플리케이션 추가 → 앱 키의 **REST API 키**. (네이버 검색 API는 2026-11 기준 새 앱의 사용 API 목록에 없어 쓰지 않음)

**운영**: 작업은 응답 뒤 서버 안에서 돌고(동시 2개), 결과는 24시간 캐시합니다. 새로고침(`force_refresh`)도 10분 안에는 캐시를 줍니다. 요청 제한은 서버 메모리 기준이라 재시작하면 초기화됩니다.

### Gemini 분석 (`app/extract.py`)

- 새 기사를 **20개씩 묶어 Gemini를 한 번** 부릅니다. 같은 사건 기사는 재난 하나로 묶습니다(언어가 달라도).
- 기사마다 본문은 앞 1,500자만 보내고, 본문은 저장하지 않습니다.
- 뽑는 값: 종류(행동지침 14종 + other, `app/guides.py`의 `DISASTER_TYPES`), 한국어 제목·요약, 위치·위경도, 날짜, 피해 규모, `viz_params`(`rainfall_mm`, `flood_depth_m`, `burned_area_ha`, `max_temp_c`, `min_temp_c`, `snowfall_cm`, `max_wind_ms`, `magnitude`, `rain_rate_mm_h`, `duration_hours`, `duration_days`, `snow_rate_cm_h`, `humidity_pct`, `wind_dir_deg`, `depth_km`, `wave_height_m`, 기사에 수치가 없으면 비움).
- 결과는 `app/main.py`의 `DisasterIn`으로 검증합니다. 형식이 틀렸거나 Gemini가 실패한 기사는 저장하지 않아서, 다음 전송 때 다시 분석됩니다.
- 모델이 붐비면(503·429·시간 초과) `GEMINI_MODELS`의 다음 모델로 한 번 넘어갑니다.
- `GEMINI_API_KEY`가 없으면 키워드 규칙으로 대신합니다(테스트·연습용).
- 응답은 보통 수 초, Google이 붐비면 1분 가까이 걸립니다. 크롤러는 한 요청에 100개 이하로 보내는 것을 권장합니다.
