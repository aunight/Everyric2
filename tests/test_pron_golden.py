"""한글 발음 경로 골든 스냅샷 — 다국어 렌더러 주입 리팩터링의 회귀 관문.

2026-07-28, 렌더러에 script 파라미터를 넣기 **직전**의 실제 출력값을 박제했다.
이후 리팩터링(script="hangul" 기본 경로)은 이 값들과 바이트 동일해야 한다.
값이 달라지는 변경은 표기 정책 변경이며, 의도적일 때만 이 파일을 함께 갱신한다.

라인 선정 근거: 요음·촉음·장음(フラッシュバック), 오쿠리가나(背負った), 조사 は/へ
(アルバイト·左から右へと), 라틴 조밀 음차(Take/Are you ready), 애매어휘 심판 후보
(観て → 미테/미에테), 라틴 느슨 후보(테익→테이크) — 렌더 규칙의 전 축을 덮는다.
"""
import pytest

from everyric2.text.pron_style import pronunciation_candidates, wiki_pronunciation

GOLDEN = {
    "アルバイトはネクラモード": "아루바이토와 네쿠라 모오도",
    "フラッシュバック・蝉の声・二度とは帰らぬ君": "후랏슈밧쿠 세미노 코에 니도토와 카에라누 키미",
    "二人きりこの儘 愛し合えるさ―。": "후타리키리 코노 마마 아이시아에루사―.",
    "背負った": "세옷타",
    "ずっと見 てたよ": "즛토 미 테타요",
    "Take it easy なんて言葉じゃ": "테익 잇 이시 난테 코토바자",
    "Are you ready?": "얼 유 레디?",
    "縋って 縋って": "스갓테 스갓테",
    "何かを攫う": "나니카오 사라우",
    "左から右へと": "히다리카라 미기에토",
    "止められない衝動": "토메라레나이 쇼오도오",
    "ずっと観てたよ": "즛토 미테타요",
    "独りで泣いた": "히토리데 나이타",
    "欲しいんだって I'll take it": "호시인닷테 아일 테익 잇",
}


@pytest.mark.parametrize("text,expected", sorted(GOLDEN.items()))
def test_wiki_pronunciation_golden(text, expected):
    assert wiki_pronunciation(text) == expected


# 후보 목록은 순서까지 계약이다 — [0]이 기본값, 이후가 심판 후보.
GOLDEN_CANDIDATES = {
    "ずっと観てたよ": ["즛토 미테타요", "즛토 미에테타요"],
    "Take it easy なんて言葉じゃ": ["테익 잇 이시 난테 코토바자", "테이크 이트 이시 난테 코토바자"],
    "欲しいんだって I'll take it": ["호시인닷테 아일 테익 잇", "호시인닷테 아일 테이크 이트"],
    # 애매어휘·라틴이 없는 라인은 후보 1개(심판 미발동)
    "独りで泣いた": ["히토리데 나이타"],
}


@pytest.mark.parametrize("text,expected", sorted(GOLDEN_CANDIDATES.items()))
def test_candidates_golden(text, expected):
    assert pronunciation_candidates(text) == expected
