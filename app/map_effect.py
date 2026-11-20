"""재난 카드 -> 프론트 브이월드 3D 지도 재난 효과 입력값.

프론트(뷰어 저장소)는 응답의 map_effect를 그대로
    koreaDisaster.set(map_effect.type, map_effect.params, map_effect.position)
에 넣으면 된다. 종류·값 이름·단위는 뷰어 js/disaster.js의 DisasterFX.TYPES에 맞춘다.
범위를 넘는 값과 빈 값은 프론트(korea/disasters/fx.js)가 스스로 최소·최대·기본값으로 맞추므로 서버는 그대로 넘긴다.

값마다 sources에 출처를 적는다.
- article: 기사에서 Gemini가 뽑은 값
- derived: 기사 값으로 계산한 값 (예: 총 강수량 / 지속 시간 = 시간당 강수량)
- kma_current: 기상청 지금 날씨(초단기실황). 오늘·어제 한국 재난일 때 빈 칸만 채움
"""

# 우리 카드 종류 -> 프론트 재난 종류. 없는 종류는 지도 효과 미지원
FRONT_TYPE = {
    "typhoon": "typhoon",
    "flood": "flood",
    "earthquake": "quake",
    "tsunami": "tsunami",
    "wildfire": "wildfire",
    "heatwave": "heatwave",
    "heavysnow": "snow",
    "coldwave": "snow",
}

# 프론트 파라미터 <- 카드 viz_params 칸 (앞에 있는 칸부터 사용)
FROM_ARTICLE = {
    "typhoon": {"wind": ["max_wind_ms"], "rain": ["rain_rate_mm_h"], "dir": ["wind_dir_deg"]},
    "flood": {"rain": ["rain_rate_mm_h"], "hours": ["duration_hours"]},
    "quake": {"M": ["magnitude"], "depth": ["depth_km"]},
    "tsunami": {"height": ["wave_height_m"]},
    "wildfire": {"area": ["burned_area_ha"], "wind": ["max_wind_ms"], "dir": ["wind_dir_deg"], "humidity": ["humidity_pct"]},
    "heatwave": {"temp": ["max_temp_c"], "humidity": ["humidity_pct"], "days": ["duration_days"]},
    "snow": {"rate": ["snow_rate_cm_h"], "hours": ["duration_hours"], "wind": ["max_wind_ms"], "temp": ["min_temp_c", "max_temp_c"]},
}

# 기사에 시간당 값이 없을 때: 총량 / 지속 시간
DERIVED = {
    ("flood", "rain"): ("rainfall_mm", "duration_hours"),
    ("typhoon", "rain"): ("rainfall_mm", "duration_hours"),
    ("snow", "rate"): ("snowfall_cm", "duration_hours"),
}

# 기상청 초단기실황 칸 (T1H 기온, RN1 1시간 강수량, REH 습도, WSD 풍속, VEC 풍향)
FROM_KMA_CURRENT = {
    "typhoon": {"rain": "RN1", "dir": "VEC"},
    "flood": {"rain": "RN1"},
    "wildfire": {"wind": "WSD", "dir": "VEC", "humidity": "REH"},
    "heatwave": {"humidity": "REH"},
    "snow": {"wind": "WSD", "temp": "T1H"},
}


def build(card: dict, current: dict | None = None) -> dict:
    """card: 재난 카드(type, lat, lon, viz_params), current: 기상청 초단기실황 values (없으면 None)"""
    front = FRONT_TYPE.get(card["type"])
    if front is None:
        return {"supported": False, "type": None, "params": {}, "position": None, "sources": {}}

    viz = card.get("viz_params") or {}
    params, sources = {}, {}
    for key, fields in FROM_ARTICLE[front].items():
        value = next((viz[f] for f in fields if viz.get(f) is not None), None)
        if value is not None:
            params[key], sources[key] = value, "article"

    for (f_type, key), (total, hours) in DERIVED.items():
        if f_type == front and key not in params and viz.get(total) is not None and viz.get(hours):
            params[key], sources[key] = round(viz[total] / viz[hours], 2), "derived"

    for key, field in FROM_KMA_CURRENT.get(front, {}).items():
        if key not in params and current and current.get(field) is not None:
            params[key], sources[key] = current[field], "kma_current"

    lat, lon = card.get("lat"), card.get("lon")
    # 카드 좌표는 Gemini가 지역 이름으로 추정한 값(지역 중심 정도)
    position = {"longitude": lon, "latitude": lat} if lat is not None and lon is not None else None
    return {"supported": True, "type": front, "params": params, "position": position, "sources": sources}
