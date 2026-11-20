"""재난별 행동지침. 이 파일을 직접 고치고 서버를 다시 켜면 반영된다.

- 문구는 출처 원문 그대로다. 기상청 페이지는 행정안전부 국민행동요령(국민안전24) 요약본이다.
- steps가 빈 항목은 공식 문구를 찾지 못한 것으로, 직접 작성해야 한다(needs_writing: true로 나감).
- warning_code는 기상특보 종류 코드(kma.WARNING_TYPES)이고, 그 특보가 발효 중일 때 이 지침을 보여 준다.
- type과 warning_code는 겹치면 안 된다(tests/test_guides.py가 확인).
- levels는 강도 단계별 내용이다. 기상특보는 예비특보·주의보·경보(폭염은 중대경보까지),
  지진은 진도 Ⅰ~Ⅻ, 태풍은 강도 1~5(최대풍속 기준). 단계마다
  criteria(기상청 공식 기준·설명)와 steps(그 단계에서 추가로 할 일)가 있다.
  공통 steps는 모든 단계에 같이 보여 주고, 단계 steps가 비면 그 단계는 직접 작성 필요다.
- 고칠 때는 따옴표("...")와 쉼표(,)를 지워지지 않게 주의한다. 고친 뒤 테스트로 확인: .venv\Scripts\python -m pytest tests/test_guides.py
"""

KMA = "https://www.weather.go.kr/w/hazard/safety-guide"
KMA_QUAKE = "https://www.weather.go.kr/w/earthquake-volcano/evacuation.do"

GUIDES = [
    {
        "type": "flood", "warning_code": "R", "title": "호우특보 중 행동요령", "source": f"{KMA}.do",
        "steps": [
            "외출을 자제하고 수시로 기상상황 및 거주지역 주변 위험상황, 재난정보를 파악합니다.",
            "침수된 도로, 지하차도, 교량 등은 사람과 차량의 통행을 엄격히 금지하고 주변에 있는 사람들에게 알려 진입하지 않도록 합니다.",
            "산간·계곡, 하천, 해안가 등 급류에 휩쓸릴 수 있는 지역에서 빠르게 벗어납니다.",
            "건물 안에 물이 조금이라도 차오를 경우 즉시 높은 곳 혹은 주변 대피장소로 이동합니다.",
            "정전이 발생한 경우 양초를 사용하지 말고 손전등, 휴대폰 등을 사용합니다.",
        ],
        "levels": {
            "예비특보": {
                "criteria": [],
                "criteria_source": None,
                "steps": [
                    "기상 상황을 수시로 확인하고 침수 위험 지역과 하천 주변 접근을 자제하세요.",
                ],
            },
            "주의보": {
                "criteria": [],
                "criteria_source": None,
                "steps": [
                    "저지대와 지하공간의 침수에 대비하고, 외출 시 하천이나 계곡 주변을 피하세요.",
                ],
            },
            "경보": {
                "criteria": [],
                "criteria_source": None,
                "steps": [
                    "침수 위험 지역에서 즉시 대피하고, 지하차도와 하천 주변에는 절대 접근하지 마세요.",
                ],
            },
        },
    },
    {
        "type": "typhoon", "warning_code": "T", "title": "태풍 특보 중 행동요령", "source": f"{KMA}/typhoon.do",
        "steps": [
            "외출을 자제하고 수시로 기상상황 및 거주지역 주변 위험상황, 재난정보를 파악합니다.",
            "침수된 도로, 지하차도, 교량 등은 사람과 차량의 통행을 엄격히 금지하고 주변에 있는 사람들에게 알려 진입하지 않도록 합니다.",
            "건물의 출입문, 창문은 닫아서 파손되지 않도록 하고, 창문이나 유리문에서 되도록 떨어져 있도록 합니다.",
            "공사장 작업이나 크레인 운행 등 야외작업을 중지합니다.",
            "강풍 발생 시 인접한 차로의 차와 안전한 거리를 유지하고, 강한 돌풍은 차를 차선 밖으로 밀어낼 수 있으므로 주의합니다.",
        ],
        "levels": {
            "예비특보": {
                "criteria": [],
                "criteria_source": None,
                "steps": [
                    "태풍의 이동 경로를 확인하고 창문과 외부 시설물을 미리 점검하세요.",
                ],
            },
            "주의보": {
                "criteria": [],
                "criteria_source": None,
                "steps": [
                    "외출을 자제하고 강풍과 집중호우에 대비해 안전한 실내에 머무르세요.",
                ],
            },
            "경보": {
                "criteria": [],
                "criteria_source": None,
                "steps": [
                    "외출을 금지하고 침수 및 강풍 위험 지역 주민은 안전한 장소로 대피하세요.",
                ],
            },
            "강도 1": {
                "criteria": [
                    "최대풍속 17m/s(61km/h, 34kt) 이상 ~ 25m/s(90km/h, 48kt) 미만",
                ],
                "criteria_source": "https://www.weather.go.kr/w/hazard/typhoon/basic/info3.do",
                "steps": [
                    "기상정보를 확인하고 외부 시설물과 주변 위험 요소를 점검하세요.",
                ],
            },
            "강도 2": {
                "criteria": [
                    "최대풍속 25m/s(90km/h, 48kt) 이상 ~ 33m/s(119km/h, 64kt) 미만",
                ],
                "criteria_source": "https://www.weather.go.kr/w/hazard/typhoon/basic/info3.do",
                "steps": [
                    "강풍과 집중호우에 대비하고 불필요한 외출을 자제하세요.",
                ],
            },
            "강도 3": {
                "criteria": [
                    "최대풍속 33m/s(119km/h, 64kt) 이상 ~ 44m/s(158km/h, 85kt) 미만",
                ],
                "criteria_source": "https://www.weather.go.kr/w/hazard/typhoon/basic/info3.do",
                "steps": [
                    "야외활동을 중단하고 강풍에 의한 낙하물과 시설물 파손에 주의하세요.",
                ],
            },
            "강도 4": {
                "criteria": [
                    "최대풍속 44m/s(158km/h, 85kt) 이상 ~ 54m/s(194km/h, 105kt) 미만",
                ],
                "criteria_source": "https://www.weather.go.kr/w/hazard/typhoon/basic/info3.do",
                "steps": [
                    "외출을 삼가고 위험 지역 주민은 안전한 대피 장소로 이동하세요.",
                ],
            },
            "강도 5": {
                "criteria": [
                    "최대풍속 54m/s(194km/h, 105kt) 이상",
                ],
                "criteria_source": "https://www.weather.go.kr/w/hazard/typhoon/basic/info3.do",
                "steps": [
                    "즉시 안전한 실내나 대피 장소로 이동하고 재난 당국의 안내에 따라 행동하세요.",
                ],
            },
        },
    },
    {
        "type": "heavysnow", "warning_code": "S", "title": "대설 특보 중 행동요령", "source": f"{KMA}/heavy-snow.do",
        "steps": [
            "눈이 많이 올 때에는 되도록 외출을 자제하여 피해를 사전에 방지합니다.",
            "내 집 앞, 내 점포 앞 보행로와 지붕 및 옥상에 내린 눈은 가족이나 이웃과 함께 치워 사고를 예방합니다.",
            "차량이 고립·정체된 경우 되도록 차량에서 대기하고, 부득이 차량을 벗어날 경우 연락처와 열쇠를 꽂아 둔 채로 대피합니다.",
            "노후가옥은 가족이나 이웃과 함께 쌓인눈의 무게로 무너지지 않도록 안전점검과 보강을 하고, 고립이 우려되는 지역은 경찰서, 관공서와 비상연락체계를 유지하도록 합니다.",
        ],
        "levels": {
            "예비특보": {
                "criteria": [],
                "criteria_source": None,
                "steps": [
                    "기상정보를 확인하고 제설도구와 방한용품을 미리 준비하세요.",
                ],
            },
            "주의보": {
                "criteria": [],
                "criteria_source": None,
                "steps": [
                    "눈길과 빙판길 보행에 주의하고 차량 운행 시 감속 및 안전거리를 확보하세요.",
                ],
            },
            "경보": {
                "criteria": [],
                "criteria_source": None,
                "steps": [
                    "불필요한 외출과 운전을 자제하고 폭설로 인한 시설물 붕괴 위험에 주의하세요.",
                ],
            },
        },
    },
    {
        "type": "coldwave", "warning_code": "C", "title": "한파 발생 시 행동요령", "source": f"{KMA}/coldwave.do",
        "steps": [
            "야외활동은 되도록 자제하고, 부득이 외출을 하는 경우에는 내복, 목도리, 모자, 장갑 등으로 노출 부분의 보온에 유의해야 합니다.",
            "심한 한기, 기억상실, 방향감각 상실, 불분명한 발음, 심한 피로 등을 느낄 때는 저체온 증세를 의심하고 바로 병원으로 가야 합니다.",
            "동상에 걸렸을 때는 비비거나 갑자기 불에 쬐어서는 안 되며, 따뜻한 물로 세척 후에 보온을 유지한 채로 즉시 병원으로 가야 합니다.",
            "외출 시에는 되도록 대중교통을 이용하고, 가족에게 행선지와 시간 계획을 알려둡니다.",
            "수도계량기, 수도관, 보일러 배관 등은 헌 옷 등 보온재로 채우고 외부는 테이프로 밀폐시켜 찬 공기가 들어가지 않도록 합니다.",
        ],
        "levels": {
            "예비특보": {
                "criteria": [],
                "criteria_source": None,
                "steps": [
                    "방한용품을 준비하고 수도관 동파와 난방시설 상태를 미리 점검하세요.",
                ],
            },
            "주의보": {
                "criteria": [
                    "10월~4월 사이의 기간 중에",
                    "아침 최저기온이 전날보다 10℃ 이상 하강하여 3℃ 이하이면서 평년값보다 3℃ 이상 낮을 것으로 예상될 때",
                    "아침 최저기온 -12℃ 이하가 2일 이상 지속될 것으로 예상될 때",
                ],
                "criteria_source": "https://www.weather.go.kr/w/hazard/safety-guide/coldwave.do",
                "steps": [
                    "외출 시 보온에 신경 쓰고 장시간 야외활동을 자제하세요.",
                ],
            },
            "경보": {
                "criteria": [
                    "10월~4월 사이의 기간 중에",
                    "아침 최저기온이 전날보다 15℃ 이상 하강하여 3℃ 이하이면서 평년값보다 3℃ 이상 낮을 것으로 예상될 때",
                    "아침 최저기온 -15℃ 이하가 2일 이상 지속될 것으로 예상될 때",
                ],
                "criteria_source": "https://www.weather.go.kr/w/hazard/safety-guide/coldwave.do",
                "steps": [
                    "야외활동을 최소화하고 저체온증과 동상에 주의하며 실내 보온을 유지하세요.",
                ],
            },
        },
    },
    {
        "type": "heatwave", "warning_code": "H", "title": "폭염발생 시 행동요령", "source": f"{KMA}/heatwave.do",
        "steps": [
            "야외활동을 최대한 자제하고, 외출이 꼭 필요한 경우에는 창이 넓은 모자와 가벼운 옷차림을 하고 물병을 반드시 휴대합니다.",
            "물을 많이 마시고, 카페인이 들어간 음료나 주류는 마시지 않습니다.",
            "냉방이 되지 않는 실내에서는 햇볕을 가리고 맞바람이 불도록 환기를 합니다.",
            "창문이 닫힌 자동차 안에는 노약자나 어린이를 홀로 남겨두지 않습니다.",
            "현기증, 메스꺼움, 두통, 근육경련 등의 증세가 보이는 경우에는 시원한 곳으로 이동하여 휴식을 취하고 시원한 음료를 천천히 마십니다.",
        ],
        "levels": {
            "예비특보": {
                "criteria": [],
                "criteria_source": None,
                "steps": [
                    "폭염에 대비해 충분한 식수와 냉방용품을 준비하고 야외활동 시간을 조정하세요.",
                ],
            },
            "주의보": {
                "criteria": [
                    "일 최고 체감온도 33℃ 이상이 2일 이상 지속될 것으로 예상될 때",
                ],
                "criteria_source": "https://www.weather.go.kr/w/hazard/safety-guide/heatwave.do",
                "steps": [
                    "가장 더운 시간대의 야외활동을 줄이고 충분한 수분을 섭취하세요.",
                ],
            },
            "경보": {
                "criteria": [
                    "일 최고 체감온도 35℃ 이상이 2일 이상 지속될 것으로 예상될 때",
                ],
                "criteria_source": "https://www.weather.go.kr/w/hazard/safety-guide/heatwave.do",
                "steps": [
                    "야외작업과 격렬한 활동을 중단하고 시원한 실내에서 휴식을 취하세요.",
                ],
            },
            "중대경보": {
                "criteria": [
                    "일 최고 체감온도 35℃ 이상이 2일 이상 관측된 지역에서 다음 각 목의 어느 하나에 해당하는 경우",
                    "일 최고 체감온도 38℃ 이상이 1일 이상 지속될 것으로 예상될 때",
                    "일 최고기온 39℃ 이상이 1일 이상 지속될 것으로 예상될 때",
                ],
                "criteria_source": "https://www.weather.go.kr/w/hazard/safety-guide/heatwave.do",
                "steps": [
                    "“생존을 위한 3단계 행동수칙” [중단/Stop, 이동/Move, 확인/Check]",
                    "중단(Stop) : 최대한 지금 즉시 모든 야외활동을 중단하시기 바랍니다.",
                    "이동(Move) : 냉방시설이 없는 실내는 위험합니다. 무더위쉼터, 그늘 등 시원한 곳으로 즉시 이동하시고, 수분을 보충하며 휴식하시기 바랍니다.",
                    "확인(Check) : 당신의 가족, 이웃, 차 안에 남겨진 생명을 확인하십시오.",
                ],
            },
        },
    },
    {
        "type": "strongwind", "warning_code": "W", "title": "강풍 발생 시 행동요령", "source": f"{KMA}/strong-wind.do",
        "steps": [
            "대피 시에는 쓰러질 위험이 있는 나무 밑이나 전신주 밑을 피하고 안전한 건물을 이용합니다.",
            "유리창 근처는 유리가 깨지면 다칠 위험이 있으므로 피하도록 합니다.",
            "강풍 발생 시 지붕 위나 바깥에서의 작업은 위험하니 자제하고 가급적 집 안팎의 전기 수리도 하지 않습니다.",
            "바닷가는 파도에 휩쓸릴 위험이 있으니 나가지 않습니다.",
            "공사장 작업이나 크레인 운행 등 야외작업을 중지합니다.",
        ],
        "levels": {
            "예비특보": {
                "criteria": [],
                "criteria_source": None,
                "steps": [
                    "창문과 간판 등 외부 시설물을 점검하고 강풍에 대비하세요.",
                ],
            },
            "주의보": {
                "criteria": [
                    "육상에서 풍속 14m/s(50.4km/h)이상 또는 순간풍속 20m/s(72km/h)이상, 다만 산지는 풍속 17m/s(61.2km/h)이상 또는 순간풍속 25m/s(90km/h)이상이 예상될 때",
                ],
                "criteria_source": "https://www.weather.go.kr/w/hazard/safety-guide/strong-wind.do",
                "steps": [
                    "간판과 낙하물에 주의하고 가급적 외출을 자제하세요.",
                ],
            },
            "경보": {
                "criteria": [
                    "육상에서 풍속 21m/s(75.6km/h) 이상 또는 순간풍속 26m/s(93.6km/h) 이상, 다만 산지는 풍속 24m/s(86.4km/h)이상 또는 순간풍속 30m/s(108km/h)이상이 예상될 때",
                ],
                "criteria_source": "https://www.weather.go.kr/w/hazard/safety-guide/strong-wind.do",
                "steps": [
                    "외출을 삼가고 창문에서 떨어져 안전한 실내 공간에 머무르세요.",
                ],
            },
        },
    },
    {
        "type": "wave", "warning_code": "V", "title": "풍랑특보 시 대비요령", "source": f"{KMA}/wind-wave.do",
        "steps": [
            "TV, 라디오 등을 통해 풍랑정보를 수시로 확인하고 관공서의 재난 예·경보를 청취합시다.",
            "해안가의 낚시꾼, 야영객, 행락객 등은 인근의 안전한 곳으로 대피합시다.",
            "파도에 휩쓸릴 위험이 있으니 바닷가로 나가지 않도록 주의합니다.",
            "높은 파도가 발생할 위험이 있는 방파제, 방조제 등에 가지 맙시다.",
            "항해 중 또는 조업 어선은 인근 선박이나 관계기관(어업무선국 등)에 연락하고 대피합시다.",
        ],
        "levels": {
            "예비특보": {
                "criteria": [],
                "criteria_source": None,
                "steps": [
                    "해상 기상정보를 확인하고 어선과 선박의 안전 상태를 점검하세요.",
                ],
            },
            "주의보": {
                "criteria": [
                    "해상에서 풍속 50.4km/h(14m/s) 이상이 3시간 이상 지속되거나 유의파고가 3m 이상 예상될 때",
                ],
                "criteria_source": "https://www.weather.go.kr/w/hazard/safety-guide/wind-wave.do",
                "steps": [
                    "해안가 접근과 해상 활동을 자제하고 선박은 안전한 곳에 대피시키세요.",
                ],
            },
            "경보": {
                "criteria": [
                    "해상에서 풍속 75.6km/h(21m/s) 이상이 3시간 이상 지속되거나 유의파고가 5m 이상 예상될 때",
                ],
                "criteria_source": "https://www.weather.go.kr/w/hazard/safety-guide/wind-wave.do",
                "steps": [
                    "선박 운항을 중단하고 해안가와 방파제 주변에 접근하지 마세요.",
                ],
            },
        },
    },
    {
        "type": "yellowdust", "warning_code": "Y", "title": "황사 발생 시 행동요령", "source": f"{KMA}/asiandust.do",
        "steps": [
            "창문을 닫고 가급적 외출을 삼가되, 외출시 보호안경, 마스크를 착용하고 귀가 후에는 손과 발 등을 깨끗이 씻어 줍니다.",
            "황사에 노출된 채소, 과일 등 농수산물은 충분히 세척 후 섭취합니다.",
            "노약자, 호흡기 질환자의 경우 실외활동을 금지합니다.",
            "어린이집과 각급학교의 실외활동 금지하고 수업 단축 또는 휴업을 합니다.",
        ],
        "levels": {
            "예비특보": {
                "criteria": [],
                "criteria_source": None,
                "steps": [
                    "황사 발생에 대비해 마스크를 준비하고 실내 공기 관리에 신경 쓰세요.",
                ],
            },
            "주의보": {
                "criteria": [],
                "criteria_source": None,
                "steps": [
                    "외출을 자제하고 외출 시 보건용 마스크를 착용하며 귀가 후 손과 얼굴을 씻으세요.",
                ],
            },
            "경보": {
                "criteria": [
                    "황사로 인해 1시간 평균 미세먼지(PM-10) 농도 800㎍/㎥ 이상이 2시간 이상 지속될 것으로 예상될 때",
                ],
                "criteria_source": "https://www.weather.go.kr/w/hazard/safety-guide/asiandust.do",
                "steps": [
                    "불필요한 외출을 삼가고 창문을 닫아 실내 유입을 최소화하세요.",
                ],
            },
        },
    },
    {
        "type": "tsunami", "warning_code": "N", "title": "지진해일 대피요령", "source": KMA_QUAKE,
        "steps": [
            "바닷물이 평소와 다르게 갑자기 빠져나가면? 해안가에서 멀리 높은 곳으로 피하기!",
            "만약! 해안가에 있을 때 진동을 느꼈다면? 곧 지진해일이 올 수 있으니 지진해일 긴급 대피장소나 높은 지대로 대피!(높이 3층 이상 건물)",
            "지진해일은 수 시간 동안 반복될 수 있으니 지진해일 특보가 해제될 때까지 재난방송 확인하기!",
        ],
        "levels": {
            "예비특보": {
                "criteria": [],
                "criteria_source": None,
                "steps": [
                    "해안 지역의 지진해일 정보를 확인하고 대피 경로와 고지대 위치를 미리 파악하세요.",
                ],
            },
            "주의보": {
                "criteria": [],
                "criteria_source": None,
                "steps": [
                    "해안가 접근과 해수욕을 중단하고 안내에 따라 안전한 곳으로 이동하세요.",
                ],
            },
            "경보": {
                "criteria": [],
                "criteria_source": None,
                "steps": [
                    "즉시 해안에서 멀리 떨어진 높은 곳으로 대피하고 재난 안내가 있을 때까지 돌아오지 마세요.",
                ],
            },
        },
    },
    {
        "type": "earthquake", "warning_code": None, "title": "지진 대피요령", "source": KMA_QUAKE,
        "steps": [
            "지진재난문자를 받거나 진동을 느꼈다면 책이나 방석으로 머리를 감싸고 책상 밑으로 숨어요",
            "흔들릴 때 이동하면 더 위험! 진동이 멈출 때까지 기다리세요",
            "가스와 전깃불을 차단! 흔들림이 멈춘 후 당황하지 말고 화재에 대비하여 전깃불을 끔",
            "문을 열어 출구를 확보! 문이나 창문을 열어 언제든 대피할 수 있도록 출구를 확보. 흔들림이 완전히 멈추면 출구를 통해 밖으로 이동",
            "꼭 계단으로 대피! 밖으로 이동할 때는 엘리베이터를 타지 말고 비상계단을 이용하여 건물 밖으로 대피",
        ],
        "levels": {
            "진도 Ⅰ": {
                "criteria": [
                    "대부분 사람들은 느낄 수 없으나, 지진계에는 기록된다.",
                ],
                "criteria_source": "https://www.weather.go.kr/w/earthquake-volcano/phenomenon.do",
                "steps": [
                    "대부분 느끼지 못하는 수준이므로 평소와 같이 생활하되 지진정보를 확인하세요.",
                ],
            },
            "진도 Ⅱ": {
                "criteria": [
                    "조용한 상태나 건물 위층에 있는 소수의 사람만 느낀다.",
                ],
                "criteria_source": "https://www.weather.go.kr/w/earthquake-volcano/phenomenon.do",
                "steps": [
                    "민감한 사람만 흔들림을 느낄 수 있으므로 침착하게 주변 상황을 살피세요.",
                ],
            },
            "진도 Ⅲ": {
                "criteria": [
                    "실내, 특히 건물 위층에 있는 사람이 현저하게 느끼며, 정지하고 있는 차가 약간 흔들린다.",
                ],
                "criteria_source": "https://www.weather.go.kr/w/earthquake-volcano/phenomenon.do",
                "steps": [
                    "실내에서 흔들림을 느낄 수 있으므로 떨어질 수 있는 물건에 주의하세요.",
                ],
            },
            "진도 Ⅳ": {
                "criteria": [
                    "실내에서 많은 사람이 느끼고, 밤에는 잠에서 깨기도 하며, 그릇과 창문 등이 흔들린다.",
                ],
                "criteria_source": "https://www.weather.go.kr/w/earthquake-volcano/phenomenon.do",
                "steps": [
                    "창문과 유리 주변에서 떨어지고, 흔들리는 물건에 부딪히지 않도록 주의하세요.",
                ],
            },
            "진도 Ⅴ": {
                "criteria": [
                    "거의 모든 사람이 진동을 느끼고, 그릇, 창문 등이 깨지기도 하며, 불안정한 물체는 넘어진다.",
                ],
                "criteria_source": "https://www.weather.go.kr/w/earthquake-volcano/phenomenon.do",
                "steps": [
                    "책상 아래로 들어가 머리와 몸을 보호하고 흔들림이 멈출 때까지 기다리세요.",
                ],
            },
            "진도 Ⅵ": {
                "criteria": [
                    "모든 사람이 느끼고, 일부 무거운 가구가 움직이며, 벽의 석회가 떨어지기도 한다.",
                ],
                "criteria_source": "https://www.weather.go.kr/w/earthquake-volcano/phenomenon.do",
                "steps": [
                    "가구와 물건이 넘어질 수 있으므로 튼튼한 탁자 아래에서 몸을 보호하세요.",
                ],
            },
            "진도 Ⅶ": {
                "criteria": [
                    "일반 건물에 약간의 피해가 발생하며, 부실한 건물에는 상당한 피해가 발생한다.",
                ],
                "criteria_source": "https://www.weather.go.kr/w/earthquake-volcano/phenomenon.do",
                "steps": [
                    "건물과 시설물의 피해가 발생할 수 있으므로 흔들림이 멈춘 후 안전한 장소로 대피하세요.",
                ],
            },
            "진도 Ⅷ": {
                "criteria": [
                    "일반 건물에 부분적 붕괴 등 상당한 피해가 발생하며, 부실한 건물에는 심각한 피해가 발생한다.",
                ],
                "criteria_source": "https://www.weather.go.kr/w/earthquake-volcano/phenomenon.do",
                "steps": [
                    "건물과 구조물이 크게 파손될 수 있으므로 낙하물에 주의하고 신속히 안전한 곳으로 이동하세요.",
                ],
            },
            "진도 Ⅸ": {
                "criteria": [
                    "잘 설계된 건물에도 상당한 피해가 발생하며, 일반 건축물에는 붕괴 등 큰 피해가 발생한다.",
                ],
                "criteria_source": "https://www.weather.go.kr/w/earthquake-volcano/phenomenon.do",
                "steps": [
                    "건물의 심각한 피해와 붕괴 위험이 있으므로 머리를 보호하고 안전한 대피 경로를 확보하세요.",
                ],
            },
            "진도 Ⅹ": {
                "criteria": [
                    "대부분의 석조 및 골조 건물이 파괴되고, 기차선로가 휘어진다.",
                ],
                "criteria_source": "https://www.weather.go.kr/w/earthquake-volcano/phenomenon.do",
                "steps": [
                    "광범위한 건물 파손과 지반 피해가 발생할 수 있으므로 위험 시설물에서 멀리 떨어지세요.",
                ],
            },
            "진도 Ⅺ": {
                "criteria": [
                    "남아있는 구조물이 거의 없으며, 다리가 무너지고, 기차선로가 심각하게 휘어진다.",
                ],
                "criteria_source": "https://www.weather.go.kr/w/earthquake-volcano/phenomenon.do",
                "steps": [
                    "대부분의 건축물이 심각하게 파괴될 수 있으므로 잔해와 지반 균열을 피해 안전한 장소로 대피하세요.",
                ],
            },
            "진도 Ⅻ": {
                "criteria": [
                    "모든 것이 피해를 입고, 지표면이 심각하게 뒤틀리며, 물체가 공중으로 튀어 오른다.",
                ],
                "criteria_source": "https://www.weather.go.kr/w/earthquake-volcano/phenomenon.do",
                "steps": [
                    "광범위한 파괴와 지형 변화가 발생할 수 있으므로 구조 당국의 안내에 따라 안전한 지역으로 대피하세요.",
                ],
            },
        },
    },
    {
        # 국민안전24를 출처로 밝힌 인천광역시 페이지 (산림청·국민안전24 원문은 자동 조회 불가)
        "type": "wildfire", "warning_code": None, "title": "산불 발생 시 행동요령",
        "source": "https://www.incheon.go.kr/safe/SAFE020202",
        "steps": [
            "산불을 발견하면 119에 신고 합니다.",
            "초기의 작은 산불을 외투, 나뭇가지 등을 이용해 두드리거나 덮어서 불을 끕니다.",
            "산불 규모가 커지면 산불 발생 지역에서 멀리 떨어진 안전한 곳으로 불길을 등지고 바람이 불어오는 방향으로 빨리 대피 합니다.",
            "대피할 여유가 없을 때는 낙엽이나 나뭇가지 등이 없는 곳에서 얼굴 등을 가리고 불길이 지나갈 때까지 엎드려 있습니다.",
        ],
    },
    # 기상청에 공식 행동요령 페이지가 없어 팀에서 작성한 문구 (공식 문구 아님)
    # 참고: 모든 재난의 단계별(levels) steps도 팀 작성 문구다. 폭염 중대경보만 기상청 공식 문구.
    {
        "type": "dry", "warning_code": "D", "title": "건조특보 중 행동요령", "source": None,
        "steps": [
            "산불 및 화재 예방을 위해 불씨 관리를 철저히 하고, 야외 소각이나 흡연을 자제하세요.",
        ],
        "levels": {
            "예비특보": {
                "criteria": [],
                "criteria_source": None,
                "steps": [
                    "산불 위험에 대비해 논밭 소각을 금지하고 화기 취급에 주의하세요.",
                ],
            },
            "주의보": {
                "criteria": [],
                "criteria_source": None,
                "steps": [
                    "작은 불씨도 큰 화재로 번질 수 있으므로 담배꽁초와 화기 취급에 주의하세요.",
                ],
            },
            "경보": {
                "criteria": [],
                "criteria_source": None,
                "steps": [
                    "산불 위험이 매우 높으므로 야외 화기 사용과 소각 행위를 금지하고, 산불 발생 시 즉시 119에 신고하세요.",
                ],
            },
        },
    },
    {
        "type": "stormsurge", "warning_code": "O", "title": "폭풍해일특보 중 행동요령", "source": None,
        "steps": [
            "해안가와 저지대 접근을 피하고, 해안 지역 주민은 안전한 고지대나 지정된 대피 장소로 이동하세요.",
        ],
        "levels": {
            "예비특보": {
                "criteria": [],
                "criteria_source": None,
                "steps": [
                    "해안 저지대의 침수 가능성을 확인하고 대피 장소와 경로를 미리 파악하세요.",
                ],
            },
            "주의보": {
                "criteria": [],
                "criteria_source": None,
                "steps": [
                    "해안가와 방파제 접근을 자제하고 침수 위험 지역 주민은 대피에 대비하세요.",
                ],
            },
            "경보": {
                "criteria": [],
                "criteria_source": None,
                "steps": [
                    "해안 저지대에서 즉시 벗어나 고지대로 대피하고 안전이 확인될 때까지 접근하지 마세요.",
                ],
            },
        },
    },
    {
        "type": "fog", "warning_code": "F", "title": "안개특보 중 행동요령", "source": None,
        "steps": [
            "시야 확보가 어려우므로 교통안전에 유의하고, 기상정보와 교통 통제 상황을 수시로 확인하세요.",
        ],
        "levels": {
            "예비특보": {
                "criteria": [],
                "criteria_source": None,
                "steps": [
                    "안개 발생에 대비해 교통정보를 확인하고 운전 시 감속과 안전거리 확보를 준비하세요.",
                ],
            },
            "주의보": {
                "criteria": [],
                "criteria_source": None,
                "steps": [
                    "차량 운행 시 속도를 줄이고 전조등과 안개등을 켜며, 앞차와의 안전거리를 충분히 확보하세요.",
                ],
            },
            "경보": {
                "criteria": [],
                "criteria_source": None,
                "steps": [
                    "가시거리가 매우 짧으므로 불필요한 외출과 운전을 자제하고, 운전 중이라면 안전한 장소에 정차하세요.",
                ],
            },
        },
    },
]

GUIDE_BY_TYPE = {guide["type"]: guide for guide in GUIDES}
# 재난 카드 종류 = 행동지침 종류 + other. 지침을 추가하면 카드 종류도 같이 늘어남
DISASTER_TYPES = (*GUIDE_BY_TYPE, "other")
# Gemini에게 알려 주는 종류 설명
DISASTER_TYPE_HINTS = {
    "flood": "호우·홍수·침수", "typhoon": "태풍", "heavysnow": "대설·폭설", "coldwave": "한파",
    "heatwave": "폭염", "strongwind": "강풍", "wave": "풍랑·높은 파도", "yellowdust": "황사",
    "tsunami": "지진해일(쓰나미)", "earthquake": "지진", "wildfire": "산불", "dry": "건조(산불 위험)",
    "stormsurge": "폭풍해일", "fog": "안개", "other": "그 밖의 기후 재난(가뭄 등)",
}
GUIDE_TYPE_BY_WARNING = {guide["warning_code"]: guide["type"] for guide in GUIDES if guide["warning_code"]}


def _level_view(name: str, level: dict) -> dict:
    return {"name": name, **level, "needs_writing": not level["steps"]}


def to_response(guide: dict, level: str | None = None) -> dict:
    """level을 주면 그 단계만, 안 주면 모든 단계를 붙임. 없는 단계면 KeyError."""
    base = {k: v for k, v in guide.items() if k != "levels"}
    base["needs_writing"] = not guide["steps"]
    levels = guide.get("levels", {})
    if level is None:
        return {**base, "levels": [_level_view(n, lv) for n, lv in levels.items()]}
    return {**base, "level": _level_view(level, levels[level])}
