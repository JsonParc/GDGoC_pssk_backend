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
| GET | `/api/guides/{type}` | 재난별 행동요령 |
| GET | `/api/warnings?type=H` | 지금 발효 중인 기상특보 (H 폭염, R 호우, D 건조, C 한파, S 대설, W 강풍 …) |
| GET | `/api/forecast?lat=37.34&lon=126.73` | 위치의 단기예보 (TMP 기온, PCP 강수량, POP 강수확률, REH 습도 …) |

기상청 결과는 특보 10분, 예보 30분, 특보구역 하루 동안 재사용합니다. 기상청이 실패하면(가끔 30초 뒤 504) 한 번 다시 부르고, 그래도 안 되면 6시간 안의 이전 결과를 줍니다.

### 크롤러 → 서버 (`POST /api/articles`)

헤더에 `X-Ingest-Token: <INGEST_TOKEN 값>`을 넣고, 한 번 수집할 때마다 **모은 기사를 한 요청으로** 보내 주세요. 토큰 값은 서버 담당에게 따로(메신저 등) 받으세요. 코드나 git에 넣지 마세요. 이미 받은 `url`은 다시 분석하지 않고 건너뜁니다.

```json
[
  {"title": "부산 폭우로 도로 침수", "url": "https://...", "source": "연합뉴스", "published_at": "2026-11-20", "content": "본문(선택)"}
]
```

응답: `{"received": 1, "new": 1, "skipped": 0, "failed": 0, "disasters_created": 1}`

`content`는 분석에만 쓰고 저장하지 않습니다.

### Gemini 분석 (`app/extract.py`)

- 새 기사를 **20개씩 묶어 Gemini를 한 번** 부릅니다. 같은 사건 기사는 재난 하나로 묶습니다(언어가 달라도).
- 기사마다 본문은 앞 1,500자만 보내고, 본문은 저장하지 않습니다.
- 뽑는 값: 종류(flood·wildfire·heatwave·other), 한국어 제목·요약, 위치·위경도, 날짜, 피해 규모, `viz_params`(`rainfall_mm`, `flood_depth_m`, `burned_area_ha`, `max_temp_c`, 기사에 수치가 없으면 비움).
- 결과는 `app/main.py`의 `DisasterIn`으로 검증합니다. 형식이 틀렸거나 Gemini가 실패한 기사는 저장하지 않아서, 다음 전송 때 다시 분석됩니다.
- 모델이 붐비면(503·429·시간 초과) `GEMINI_MODELS`의 다음 모델로 한 번 넘어갑니다.
- `GEMINI_API_KEY`가 없으면 키워드 규칙으로 대신합니다(테스트·연습용).
- 응답은 보통 수 초, Google이 붐비면 1분 가까이 걸립니다. 크롤러는 한 요청에 100개 이하로 보내는 것을 권장합니다.
