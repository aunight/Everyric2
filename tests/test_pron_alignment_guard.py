"""비한글 발음의 독음 정렬 유입 차단 — 다국어화가 재유입시킬 뻔한 정렬 붕괴 가드.

다국어화 이후 비ko 사용자의 line_meta엔 romaji(en)·가타카나(ja) 발음이 실릴 수 있다
(번역 API의 결정론 매트릭스). 라틴은 kor 어댑터에서 정렬되지 않으므로(latin_hangul.py
헤더 실측) 그런 발음이 coverage 게이트를 열거나 정렬 입력에 들어가면 안 된다 —
«없음»과 동일 취급되어 원문 폴백을 타야 한다.
"""
from everyric2.server.worker import _alignable_pron, _index_line_meta, _pron_coverage


class _Line:
    def __init__(self, text: str, n: int):
        self.text = text
        self.line_number = n


def _meta(pairs):
    return _index_line_meta([{"text": t, "pronunciation": p} for t, p in pairs])


def test_alignable_pron_passes_hangul_only():
    assert _alignable_pron("혼노오가 쿠루이") == "혼노오가 쿠루이"
    # 라틴 음차 혼합 줄(한글 포함)은 정렬 가능 — 기존 라틴 음차 정책 유지
    assert _alignable_pron("아일 테익 잇 호시이") == "아일 테익 잇 호시이"
    assert _alignable_pron("honnoo ga kurui") == ""       # romaji
    assert _alignable_pron("ホンノウガ クルイ") == ""      # 가타카나
    assert _alignable_pron("  ") == ""
    assert _alignable_pron(None) == ""


def test_coverage_ignores_non_hangul_pron():
    lines = [_Line("本能が狂い始める", 1), _Line("追い詰められた", 2)]
    # 전부 romaji → 게이트가 열리면 안 된다 (0.0 → 원문 정렬 폴백)
    assert _pron_coverage(lines, _meta([
        ("本能が狂い始める", "honnoo ga kurui hajimeru"),
        ("追い詰められた", "oitsumerareta"),
    ])) == 0.0
    # 절반만 한글 → 0.5 (romaji 줄은 무발음 줄과 동일 취급)
    assert _pron_coverage(lines, _meta([
        ("本能が狂い始める", "혼노오가 쿠루이 하지메루"),
        ("追い詰められた", "oitsumerareta"),
    ])) == 0.5
