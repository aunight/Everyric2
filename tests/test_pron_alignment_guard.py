"""비한글 발음의 독음 정렬·표시 유입 차단 — 다국어화가 재유입시킬 뻔한 붕괴 가드.

다국어화 이후 비ko 사용자의 line_meta엔 romaji(en)·가타카나(ja) 발음이 실릴 수 있다
(번역 API의 결정론 매트릭스). 라틴은 kor 어댑터에서 정렬되지 않으므로(latin_hangul.py
헤더 실측) 그런 발음이 coverage 게이트나 정렬 입력에 들어가면 안 된다 — «없음»과
동일 취급되어 원문 폴백을 타야 한다(감사 치명 #1의 정렬 입력 쪽).

같은 값이 ``merge_line_meta``를 거쳐 legacy ``seg["pronunciation"]``(한글 전용 계약)에
박히는 **병합·표시 경로**도 같은 구멍이었다(감사 치명 #1의 서버 잔여) — 정렬 게이트는
막았지만 병합은 문자 체계를 검사하지 않아 romaji가 그대로 들어갔고, 그 위에
``attach_pron_variants``가 ``pron["hangul"] = romaji``를 얹어 재생성 없이는 안 지워지는
오염(모든 ko 사용자가 한글 칸에서 로마자를 봄)을 만들었다.
"""
from everyric2.server.worker import (
    _alignable_pron,
    _index_line_meta,
    _pron_coverage,
    merge_line_meta,
)


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


def test_merge_line_meta_skips_non_hangul_pronunciation_but_keeps_translation():
    # 감사 치명 #1의 서버 잔여 재현: line_meta에 romaji 발음이 실린 줄(비ko 사용자
    # 요청 등)을 병합해도 legacy seg["pronunciation"]에 로마자가 박히면 안 되고,
    # 그 위에 attach_pron_variants가 pron["hangul"]=romaji를 얹어서도 안 된다.
    # 번역 병합·attach 호출 자체는 그대로 동작해야 한다.
    text = "本能が狂い始める"
    seg = {"text": text, "start": 0.0, "end": 4.0}
    line_meta = [
        {"text": text, "pronunciation": "honnoo ga kurui hajimeru", "translation": "본능이 미쳐가기 시작해"}
    ]

    merged = merge_line_meta([seg], line_meta)

    assert merged == 1
    assert "pronunciation" not in seg  # 한글 전용 legacy 슬롯에 로마자가 안 박힌다
    assert "pron" not in seg  # attach_pron_variants도 pron.hangul을 만들지 않는다(빈 발음 취급)
    assert seg["translation"] == "본능이 미쳐가기 시작해"  # 번역 병합은 그대로


def test_merge_line_meta_skips_katakana_pronunciation_too():
    # 가타카나(ja 사용자 line_meta)도 같은 구멍 — 한글이 아니면 전부 막혀야 한다.
    text = "本能が狂い始める"
    seg = {"text": text, "start": 0.0, "end": 4.0}
    line_meta = [{"text": text, "pronunciation": "ホンノウガ クルイ ハジメル"}]

    merge_line_meta([seg], line_meta)

    assert "pronunciation" not in seg
    assert "pron" not in seg


def test_merge_line_meta_still_merges_hangul_pronunciation():
    # 회귀 방지 — 정상 한글 발음은 이전처럼 legacy 슬롯과 pron.hangul에 그대로 들어가야 한다.
    text = "本能が狂い始める"
    seg = {"text": text, "start": 0.0, "end": 4.0}
    line_meta = [{"text": text, "pronunciation": "혼노오가 쿠루이 하지메루"}]

    merge_line_meta([seg], line_meta)

    assert seg["pronunciation"] == "혼노오가 쿠루이 하지메루"
    assert seg["pron"]["hangul"] == "혼노오가 쿠루이 하지메루"
