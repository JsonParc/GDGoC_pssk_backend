"""기사 -> 재난 정보 추출.

지금은 테스트용 임시 규칙(키워드)입니다. Gemini 담당 팀원이 이 함수 내용을 교체하면 됩니다.

- 입력: ArticleIn (title, url, source, published_at, content)
- 반환: 재난 dict(main.DisasterIn 형식) 또는 None(재난 기사가 아님)
- 예외를 던지면 그 기사는 저장되지 않아, 크롤러가 다음에 다시 보낼 때 재분석됩니다.
- 같은 사건 기사 병합도 아직 없습니다(기사 1개 = 재난 1개).
"""

KEYWORDS = {
    "flood": ("홍수", "침수", "폭우", "flood"),
    "wildfire": ("산불", "wildfire"),
    "heatwave": ("폭염", "heatwave", "heat wave"),
}


def extract(article) -> dict | None:
    text = f"{article.title} {article.content or ''}".lower()
    for disaster_type, words in KEYWORDS.items():
        if any(word in text for word in words):
            return {
                "type": disaster_type,
                "title": article.title,
                "date": article.published_at,
            }
    return None
