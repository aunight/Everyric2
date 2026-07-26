"""자막 원어 판정 오류로 인한 가사 오염 게이트 회귀 테스트.

실측 버그(2026-07-26): `zyRt-nBM3dY`는 일본어 보카로 곡인데 `video_language='vi'`이고 유일한
`-orig` ASR 트랙이 `vi-orig`다. 그래서 `detect_original_language`가 `('vi', 'asr_orig')`을
돌려주고, 자막 경로가 **일본어 오디오에 베트남어 ASR을 돌린 전사를 곡 가사로 저장한다**
(야간 배치 예행에서 `th-orig` 태국어로도 재현).

여기서 못박는 것: 라틴 전용 자막이 포기되는가, 가나·한글·한자가 하나라도 있으면 통과하는가,
사유가 호출부에 전달되는가(4xx = 확정 판정), 설정으로 끌 수 있는가, 판정 규칙
(`select_original_track`)은 그대로인가.
"""

import contextlib

import pytest

from everyric2.alignment.caption_anchors import script_counts
from everyric2.config.settings import get_settings
from everyric2.server.services import youtube_captions as yc

VIDEO = "zyRt-nBM3dY"

# 실측 트랙 구성 — 수동 12종, 자동 vi-orig. 유튜브 신호는 전부 vi를 가리킨다.
REAL_INFO = {
    "subtitles": {
        lang: [{"name": lang}]
        for lang in "ar zh-TW en fil id ja ko ms es th tr vi".split()
    },
    "automatic_captions": {"vi-orig": [{"name": "Vietnamese"}]},
    "language": "vi",
}


def _lines(*texts):
    return [{"start": i * 2.0, "end": i * 2.0 + 1.5, "text": t} for i, t in enumerate(texts)]


@contextlib.contextmanager
def _ytdlp(info, lines):
    """extract_caption_info / download_track_lines만 갈아끼운다 — 판정·정리·게이트는 실코드."""
    calls: dict = {"tracks": []}

    def fake_extract(video_id):
        calls["video_id"] = video_id
        return info

    def fake_download(video_id, lang, auto):
        calls["track"] = (lang, auto)
        calls["tracks"].append(lang)
        return lines

    orig = (yc.extract_caption_info, yc.download_track_lines)
    yc.extract_caption_info = fake_extract
    yc.download_track_lines = fake_download
    try:
        yield calls
    finally:
        yc.extract_caption_info, yc.download_track_lines = orig


@contextlib.contextmanager
def _gate(enabled: bool):
    settings = get_settings()
    saved = settings.server.caption_require_cjk
    object.__setattr__(settings.server, "caption_require_cjk", enabled)
    try:
        yield
    finally:
        object.__setattr__(settings.server, "caption_require_cjk", saved)


# --------------------------------------------------------------------------
# 1) 버그가 실제로 존재한다 — 게이트가 필요한 이유
# --------------------------------------------------------------------------


def test_youtube_signals_really_do_point_at_the_wrong_language():
    """유튜브 신호가 이 영상에서 vi를 가리킨다는 **사실**을 못박는다.

    이것이 판정에서 유튜브 신호를 빼기로 한 근거다. 신호 자체는 그대로 있으므로 (실측
    데이터가 바뀐 것이 아니다) 그 사실을 여기서 확인하고, 아래에서 우리 판정이 그것을
    따르지 않는 것을 확인한다.
    """
    assert REAL_INFO["language"] == "vi"
    assert [k for k in REAL_INFO["automatic_captions"] if k.endswith("-orig")] == ["vi-orig"]


def test_our_ordering_does_not_follow_those_signals():
    """옛 판정은 vi-orig를 1순위로 봐서 vi 트랙을 골랐다 — 이제 그 신호를 쓰지 않는다."""
    order = yc.order_manual_tracks(REAL_INFO, None, 12)
    assert order[0] != "vi", f"유튜브 신호를 따라갔다: {order}"
    # 제목이 일본어면 그것이 ja를 첫 후보로 올린다 (실제 영상에는 제목이 있다)
    titled = yc.order_manual_tracks({**REAL_INFO, "title": "シニカルナイトプラン"}, None, 12)
    assert titled[0] == "ja"


# --------------------------------------------------------------------------
# 2) 게이트
# --------------------------------------------------------------------------


def test_latin_only_caption_is_refused_with_a_reason():
    vietnamese_asr = _lines(
        "Anh muốn chạm vào điều bí mật",
        "Mơ hồ về em",
        "Không có ý nghĩa gì cả",
    )
    with _gate(True), _ytdlp(REAL_INFO, vietnamese_asr) as calls:
        with pytest.raises(yc.CaptionUnavailable) as e:
            yc.fetch_lyrics_from_captions(VIDEO)
    # 후보를 여러 개 받아 봤지만 어느 것도 CJK가 아니라 전부 떨어졌다 (이 목은 모든 트랙에
    # 같은 베트남어 본문을 준다 — 자동 더빙 업로드에서 팬 번역 트랙까지 오염된 상황이다)
    assert calls["tracks"] and all(not k.endswith("-orig") for k in calls["tracks"])
    assert e.value.code == "non_cjk_caption"
    # 4xx = 이 영상은 자막으로 안 된다는 확정 판정 → 클라이언트가 붙여넣기로 안내한다
    assert e.value.http_status == 404
    assert e.value.terminal is True
    assert "가사로 쓰면" in e.value.message


def test_a_single_kana_is_enough_to_pass():
    """비율이 아니라 «하나도 없음»이 기준이다 — 진짜 CJK 곡을 잘못 버리지 않는다."""
    mostly_latin = _lines("Wow oh yeah", "Come on baby", "君")
    with _gate(True), _ytdlp(REAL_INFO, mostly_latin):
        found = yc.fetch_lyrics_from_captions(VIDEO)
    assert found.lines == ["Wow oh yeah", "Come on baby", "君"]


@pytest.mark.parametrize(
    "text", ["ゆらゆら", "너를 만나고", "我想見你", "ｱｲｳｴｵ", "ㅋㅋㅋ그래"]
)
def test_any_cjk_script_passes(text):
    with _gate(True), _ytdlp(REAL_INFO, _lines(text, "second", "third")):
        assert yc.fetch_lyrics_from_captions(VIDEO).lines[0] == text


def test_romaji_glossed_japanese_captions_pass():
    """이 곡 자막의 실제 형태 — 로마자를 괄호로 병기한다. 라틴이 더 많아도 통과해야 한다."""
    glossed = _lines(
        "(Furetemitai himitsu to) 触れてみたい秘密と",
        "(Aimai na anata no koto) 曖昧なあなたのこと",
        "(Shiyou yo) しようよ",
    )
    with _gate(True), _ytdlp(REAL_INFO, glossed):
        found = yc.fetch_lyrics_from_captions(VIDEO)
    assert len(found.lines) == 3
    counts = yc.caption_script_counts(found.lines)
    assert counts["latin"] > counts["kana"] + counts["han"], "라틴이 더 많은 구성이어야 한다"


def test_gate_can_be_switched_off():
    latin = _lines("Anh muốn chạm vào", "Mơ hồ về em", "Không có gì")
    with _gate(False), _ytdlp(REAL_INFO, latin):
        found = yc.fetch_lyrics_from_captions(VIDEO)
    assert len(found.lines) == 3, "설정으로 껐는데도 게이트가 동작했다"


def test_gate_stays_on_when_the_setting_cannot_be_read(monkeypatch):
    """설정 로드 실패는 게이트를 끄는 사유가 아니다 — 오염이 더 큰 손해다."""
    import everyric2.config.settings as settings_mod

    def boom():
        raise RuntimeError("no settings")

    monkeypatch.setattr(settings_mod, "get_settings", boom)
    assert yc._require_cjk_enabled() is True


# --------------------------------------------------------------------------
# 3) 문자 구성 판정 자체 (앵커 경로와 공유하는 유틸리티)
# --------------------------------------------------------------------------


def test_has_cjk_script():
    assert yc.has_cjk_script(script_counts("ゆらゆら"))
    assert yc.has_cjk_script(script_counts("너를"))
    assert yc.has_cjk_script(script_counts("影"))
    assert not yc.has_cjk_script(script_counts("Anh muốn chạm vào điều bí mật"))
    assert not yc.has_cjk_script(script_counts("just latin 123 !?"))
    assert not yc.has_cjk_script(script_counts(""))


def test_script_counts_ignores_whitespace_and_counts_by_family():
    c = script_counts("君は ABC 한글 漢")
    assert c == {"kana": 1, "hangul": 2, "han": 2, "latin": 3, "total": 8}
