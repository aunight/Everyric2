"""video_id만으로 유튜브 자막을 조달해 싱크를 만드는 경로 테스트.

여기서 지키는 계약:
  ① 사용자는 자막 트랙을 고르지 않는다 — 원어 판정은 서버가 신호(ASR 언어)로 한다.
  ② 자막 텍스트는 정리해서 가사로 쓰고 타임스탬프는 버린다(정렬은 CTC가 새로 한다).
  ③ 잡 생성은 /generate와 **같은 코드 경로**를 탄다 — 중복 방지·대기열이 복제되지 않는다.

네트워크는 절대 타지 않는다: yt-dlp 진입점(extract_caption_info/download_track_lines)만
목으로 갈아끼우고 나머지는 실제 코드다. DB는 기존 서버 테스트 규약대로 격리된 in-memory
SQLite로 connection.async_session을 몽키패치하고 라우트 코루틴을 직접 await한다.
"""

import asyncio
import contextlib

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from everyric2.config.settings import get_settings
from everyric2.server import worker as worker_core
from everyric2.server.api.sync import (
    GenerateFromCaptionRequest,
    generate_sync_from_caption,
)
from everyric2.server.db import connection as db_conn
from everyric2.server.db.models import Base, Job
from everyric2.server.db.repository import JobRepository, SyncRepository, hash_lyrics
from everyric2.server.services import youtube_captions as yc

VIDEO = "CAPCAPCAP01"

# 실측 사례 재현: 팬 번역 수동작성 15종 + 자동 생성 원어(ja). 수동작성 트랙 수·순서는
# 번역 인기도를 따를 뿐이라 다수결로는 원어를 절대 고를 수 없다.
MANUAL_15 = {
    lang: [{"name": lang.upper(), "ext": "json3"}]
    for lang in (
        "gl", "ru", "vi", "es", "ar", "en", "oc", "id", "ja", "zh",
        "tr", "pl", "fr", "fil", "ko",
    )
}
# 자동 생성 목록에는 번역본 ~150종이 함께 들어온다 — 원어는 '-orig'만이 가린다
AUTO_WITH_ORIG = {
    "ja": [{"name": "일본어 (자동 생성됨)"}],
    "ja-orig": [{"name": "일본어 (자동 생성됨) - 원본"}],
    "en": [{"name": "영어"}],
    "ko": [{"name": "한국어"}],
    "es": [{"name": "스페인어"}],
}


def _lines(*texts: str) -> list[dict]:
    """자막 라인 목록 — 시간은 정리 단계에서 버려지므로 형식만 맞춘다."""
    return [
        {"start": float(i), "end": float(i) + 1.0, "text": t} for i, t in enumerate(texts)
    ]


# ── 원어 판정 규칙 ────────────────────────────────────────────────


def _counts(kana=0, hangul=0, han=0, latin=0):
    return {"kana": kana, "hangul": hangul, "han": han, "latin": latin,
            "total": kana + hangul + han + latin}


# 임계는 저장된 가사 283건 실측(2026-07-26)에서 나왔다:
#   language=ja 196건 — kana/CJK 비율 최소 0.500 · 중앙 0.755 · hangul 비율 최대 0.088
#   language=ko  83건 — hangul 비율 중앙 1.000
#   language=zh   1건 — han 1.000, kana 0


def test_body_language_reads_japanese_from_kana_even_with_many_kanji():
    """일본어 가사의 한자 비율은 정상적으로 0.500까지 올라간다 — 한자가 많다고 중국어가 아니다."""
    assert yc.body_language(_counts(kana=755, han=245)) == "ja"
    assert yc.body_language(_counts(kana=500, han=500)) == "ja"


def test_body_language_compares_kana_against_hangul_instead_of_any_presence():
    """실측 반례 — 2zilNT7hgFc는 hangul 485자(98%)에 kana가 8자 섞인 한국 곡이다.

    「가나가 한 글자라도 있으면 ja」(script_lang_hint의 규칙)는 이 곡을 일본어로 판정한다.
    저장되는 language는 정렬 어댑터와 독음 표기를 가르므로 한 글자로 뒤집혀선 안 된다.
    """
    assert yc.body_language(_counts(kana=8, hangul=485, latin=135)) == "ko"


def test_body_language_needs_han_only_for_chinese():
    assert yc.body_language(_counts(han=100)) == "zh"
    # 가나가 있으면 중국어가 아니다 — 중국어 가사에 가나가 나올 이유가 없다
    assert yc.body_language(_counts(kana=1, han=100)) == "ja"


def test_body_language_is_none_without_any_cjk():
    assert yc.body_language(_counts(latin=500)) is None
    assert yc.body_language(_counts()) is None


def test_title_script_hint_uses_title_and_channel():
    """유튜브 신호(-orig · info['language'])를 대신하는 근거 — 업로더가 쓴 글자다."""
    assert yc.title_script_hint({"title": "YOASOBI「アイドル」 Official"}) == "ja"
    assert yc.title_script_hint({"title": "[MV] 아이유 - 밤편지"}) == "ko"
    # 제목이 로마자뿐이면 채널명이 남은 단서다
    assert yc.title_script_hint({"title": "Overdose", "uploader": "なとり"}) == "ja"


def test_title_script_hint_is_none_for_latin_only():
    """영어 제목을 붙인 곡에는 힌트가 없다 — 실측 폐기 44건 중 6건이 이 부류였다."""
    assert yc.title_script_hint({"title": "ROSE & Bruno Mars - APT.", "uploader": "ROSE"}) is None
    assert yc.title_script_hint({}) is None


def test_verify_accepts_body_matching_the_title_script():
    assert yc.verify_track_body("ja", _counts(kana=100, han=30)) == "ja"


def test_verify_rejects_fan_translation_whose_body_disagrees_with_the_title():
    """제목이 일본어인데 본문이 한글이면 그 트랙은 원어가 아니라 한국어 팬 번역이다.

    이 관문이 없으면 «수동 자막이 하나뿐이면 원어»라는 옛 규칙의 오염이 그대로 통과한다
    (MoRef 집계: sole_manual 근거로 고른 트랙의 62.5%가 오염).
    """
    assert yc.verify_track_body("ja", _counts(hangul=400)) is None


def test_verify_falls_back_to_cjk_presence_without_a_hint():
    """제목에 힌트가 없으면 반박할 근거가 없다 — CJK이기만 하면 받는다."""
    assert yc.verify_track_body(None, _counts(kana=100)) == "ja"
    assert yc.verify_track_body(None, _counts(latin=300)) is None


def test_verify_follows_the_title_when_the_body_is_kanji_only():
    """한자는 일본어·중국어가 공유하는 문자다 — 그것만으로는 두 언어를 가릴 수 없다.

    실측: language=ja 196건의 한자 비율이 최대 0.500까지 올라간다. 짧은 자막이나 한자
    위주 줄에서는 가나가 아예 없을 수 있는데, 그것을 «중국어라서 제목과 불일치»로 버리면
    일본어 곡의 자막을 잃는다.
    """
    assert yc.verify_track_body("ja", _counts(han=9)) == "ja"
    assert yc.verify_track_body("zh", _counts(han=9)) == "zh"


def test_verify_does_not_accept_kana_for_a_chinese_title():
    """반대 방향은 거른다 — 가나가 있는 본문은 중국어 곡의 가사가 아니다."""
    assert yc.verify_track_body("zh", _counts(kana=100, han=20)) is None


def test_order_puts_the_title_script_language_first_not_the_majority():
    """수동작성 트랙 수는 팬 번역 인기도를 따를 뿐 원어와 무관하다 — 다수결·순서로는
    절대 고를 수 없다(실측: 수동 15종 중 원어가 ja인 곡)."""
    info = {"subtitles": MANUAL_15, "title": "隠しきれない / 初音ミク"}
    assert yc.order_manual_tracks(info, None, 4)[0] == "ja"


def test_order_ignores_youtube_original_language_signals():
    """자동 더빙 업로드에서 일본어 곡에 vi-orig가 붙는다(실측 zyRt-nBM3dY) — 그 신호를
    순서에 넣으면 틀린 트랙을 먼저 받아 본다."""
    info = {
        "subtitles": {"ja": [{}], "vi": [{}]},
        "automatic_captions": {"vi-orig": [{}]},
        "language": "vi",
        "title": "シニカルナイトプラン / 初音ミク",
    }
    assert yc.order_manual_tracks(info, None, 2)[0] == "ja"


def test_order_prefers_our_own_lyrics_hint_over_the_title():
    """가사가 이미 있으면 그 문자 체계가 제목보다 강한 신호다(앵커 경로)."""
    info = {"subtitles": {"ja": [{}], "ko": [{}]}, "title": "밤편지 (Japanese ver.)"}
    assert yc.order_manual_tracks(info, "ja", 2)[0] == "ja"


def test_order_excludes_auto_tracks_and_live_chat():
    info = {"subtitles": {"ja": [{}], "live_chat": [{}]}, "automatic_captions": {"ja-orig": [{}]}}
    assert yc.order_manual_tracks(info, None, 5) == ["ja"]


def test_manual_only_means_asr_video_has_no_caption_path():
    """자동 생성만 있는 영상은 자막 경로를 쓰지 않는다.

    ASR 전사는 가사로 쓰지 않는다 — 사용자 확인(2026-07-26): 표시는 되지만 인식 품질이
    가사로 쓸 수준이 아니다. 언어 라벨까지 못 믿는다는 것도 실측으로 확인됐다(일본어 곡에
    vi-orig · th-orig). 실측 대가: 어젯밤 300곡 중 97곡이 ASR만 있었다.
    """
    with _ytdlp({"subtitles": {}, "automatic_captions": {"ja-orig": [{}], "vi-orig": [{}]}}, []):
        with pytest.raises(yc.CaptionUnavailable) as e:
            yc.fetch_lyrics_from_captions(VIDEO)
    assert e.value.code == "no_manual_captions"
    assert e.value.http_status == 404
    assert e.value.terminal is True


def test_live_chat_is_not_a_caption_track():
    """라이브 채팅 리플레이가 유일한 '자막'이면 사람이 쓴 자막이 없는 것과 같다."""
    with _ytdlp({"subtitles": {"live_chat": [{}]}, "automatic_captions": {}}, []):
        with pytest.raises(yc.CaptionUnavailable) as e:
            yc.fetch_lyrics_from_captions(VIDEO)
    assert e.value.code == "no_manual_captions"


def test_align_language_only_passed_for_engine_supported_langs():
    """CTC가 모르는 언어 코드를 그대로 넘기면 엉뚱한 어댑터가 잡힌다 — None으로 비운다."""
    ja = yc.CaptionLyrics(
        track=yc.TrackChoice("ja", False, "JA", "title_script", "ja"), lines=["a", "b", "c"]
    )
    gl = yc.CaptionLyrics(
        track=yc.TrackChoice("gl", False, "GL", "body_script", "gl"), lines=["a", "b", "c"]
    )
    assert ja.align_language == "ja"
    assert gl.align_language is None


# ── 자막 텍스트 정리 ──────────────────────────────────────────────


def test_clean_drops_effect_annotations_and_speaker_labels():
    lines = _lines(
        "[음악]",
        "【拍手】",
        "(Applause)",
        ">> 隠しきれない",
        "ミク: 次の行",
        "♪ 装飾つき ♪",
        "본문 [박수] 이어짐",
    )
    assert yc.clean_caption_lines(lines) == [
        "隠しきれない",
        "次の行",
        "装飾つき",
        "본문 이어짐",
    ]


def test_clean_keeps_parenthesised_backing_vocals():
    """소괄호는 코러스 표기로 실제 가사에 흔하다 — 효과음 단어일 때만 버린다."""
    assert yc.clean_caption_lines(_lines("君を (ah ah)", "(음악)")) == ["君を (ah ah)"]


def test_clean_drops_consecutive_duplicates_but_keeps_refrain():
    """붙어 있는 중복만 자막 잡음이다 — 떨어져 반복되는 후렴은 진짜 가사다."""
    out = yc.clean_caption_lines(_lines("같은 줄", "같은 줄", "다른 줄", "같은 줄"))
    assert out == ["같은 줄", "다른 줄", "같은 줄"]


def test_clean_merges_rolling_captions_only_when_asked():
    """자동 생성 자막은 «앞부분 → 앞부분+뒷부분»으로 누적된다."""
    rolling = _lines("君は", "君は僕を", "君は僕を見て")
    assert yc.clean_caption_lines(rolling, merge_rolling=True) == ["君は僕を見て"]
    # 업로더 자막은 롤링하지 않고 접두사 관계가 진짜 가사일 수 있으므로 기본은 보존
    assert len(yc.clean_caption_lines(rolling)) == 3


def test_clean_accepts_plain_strings_and_drops_empties():
    assert yc.clean_caption_lines(["  ", "[음악]", "가사"]) == ["가사"]


def test_json3_events_to_lines_still_exported_from_router_module():
    """구 라우트/테스트가 쓰던 임포트 경로가 서비스 이관 후에도 살아 있어야 한다."""
    from everyric2.server.api.captions import json3_events_to_lines

    assert json3_events_to_lines is yc.json3_events_to_lines


# ── video_id → 가사 (yt-dlp 진입점만 목) ──────────────────────────


@contextlib.contextmanager
def _ytdlp(info, lines):
    """extract_caption_info / download_track_lines만 갈아끼운다 — 판정·정리는 실코드.

    `lines`가 dict면 **트랙별 본문**이다. 이 경로는 후보를 순서대로 받아 보고 본문으로
    판정하므로, 모든 트랙에 같은 텍스트를 주면 «어느 트랙을 받았는지»가 판정에 아무 영향을
    주지 않아 검증이 무의미해진다. dict에 없는 트랙은 `empty_caption`으로 떨어진다.
    """
    calls: dict = {"tracks": []}

    def fake_extract(video_id):
        calls["video_id"] = video_id
        return info

    def fake_download(video_id, lang, auto):
        calls["track"] = (lang, auto)
        calls["tracks"].append(lang)
        if isinstance(lines, dict):
            if lang not in lines:
                raise yc.CaptionUnavailable("empty_caption", f"no {lang}")
            return lines[lang]
        return lines

    orig = (yc.extract_caption_info, yc.download_track_lines)
    yc.extract_caption_info = fake_extract
    yc.download_track_lines = fake_download
    try:
        yield calls
    finally:
        yc.extract_caption_info, yc.download_track_lines = orig


def test_fetch_lyrics_uses_selected_track_and_cleans_text():
    info = {"subtitles": MANUAL_15, "automatic_captions": AUTO_WITH_ORIG,
            "title": "隠しきれない / 初音ミク"}
    raw = _lines("[음악]", "隠しきれない", "隠しきれない", ">> 次の行", "三行目")
    with _ytdlp(info, {"ja": raw}) as calls:
        found = yc.fetch_lyrics_from_captions(VIDEO)
    # 제목 문자와 맞는 트랙을 첫 번째로 받고, 그 뒤 번역용 한국어 트랙을 한 번 더 본다
    # (이 목에는 ko 본문이 없어 번역은 붙지 않는다)
    assert calls["tracks"][0] == "ja"
    assert found.translations is None
    assert found.lines == ["隠しきれない", "次の行", "三行目"]
    assert found.text == "隠しきれない\n次の行\n三行目"
    assert found.track.reason == "title_script"
    assert found.align_language == "ja"


def test_fetch_lyrics_takes_the_language_from_the_body_not_the_track_code():
    """한국 팬이 일본어 원문을 ko 트랙에 올리는 일이 흔하다 — 트랙 코드를 그대로 쓰면
    일본어 가사가 language=ko로 저장된다(실측: 살아 있는 싱크 33건이 그 상태였다).
    그러면 한국어 어댑터로 정렬되고 한글 독음도 붙지 않는다."""
    info = {"subtitles": {"ko": [{}]}, "title": "初音ミク - メルト"}
    with _ytdlp(info, {"ko": _lines("隠しきれない", "次の行", "三行目")}):
        found = yc.fetch_lyrics_from_captions(VIDEO)
    assert found.track.lang == "ko"  # 받아 온 트랙은 ko다
    assert found.track.language == "ja"  # 저장되는 언어는 본문에서 온다
    assert found.align_language == "ja"


def test_fetch_lyrics_skips_a_fan_translation_and_takes_the_original():
    """제목이 일본어인 곡에서 ko 본문 트랙은 팬 번역이다 — 건너뛰고 ja를 받아야 한다."""
    info = {"subtitles": {"en": [{}], "ko": [{}], "ja": [{}]}, "title": "初音ミク - メルト"}
    bodies = {
        "ko": _lines("숨길 수 없는", "다음 줄", "세 번째 줄"),
        "ja": _lines("隠しきれない", "次の行", "三行目"),
        "en": _lines("cannot hide", "next line", "third line"),
    }
    with _ytdlp(info, bodies) as calls:
        found = yc.fetch_lyrics_from_captions(VIDEO)
    assert calls["tracks"][0] == "ja", "제목 문자와 맞는 트랙을 먼저 받아야 한다"
    assert found.lines == ["隠しきれない", "次の行", "三行目"]
    assert found.track.language == "ja"


def test_fetch_lyrics_rejects_when_only_fan_translations_exist():
    """제목은 일본어인데 일본어 자막이 없으면 팬 번역만 있는 것이다 — 가사로 쓰면 안 된다."""
    info = {"subtitles": {"ko": [{}], "en": [{}]}, "title": "初音ミク - メルト"}
    bodies = {
        "ko": _lines("숨길 수 없는", "다음 줄", "세 번째 줄"),
        "en": _lines("cannot hide", "next line", "third line"),
    }
    with _ytdlp(info, bodies):
        with pytest.raises(yc.CaptionUnavailable) as e:
            yc.fetch_lyrics_from_captions(VIDEO)
    assert e.value.code == "no_original_track"
    assert e.value.terminal is True


def test_fetch_lyrics_rejects_caption_with_too_few_usable_lines():
    """자막이 안내 문구·효과음뿐이면 GPU를 태우기 전에 거절한다.

    후보가 전부 같은 사유로 떨어졌으면 그 사유를 그대로 전한다 — 「가사 줄이 부족하다」를
    「원어 트랙이 없다」로 뭉개면 사용자가 할 일이 달라진다.
    """
    info = {"subtitles": {"ja": [{}]}, "automatic_captions": {"ja-orig": [{}]},
            "title": "初音ミク"}
    with _ytdlp(info, {"ja": _lines("[음악]", "구독과 좋아요")}):
        with pytest.raises(yc.CaptionUnavailable) as e:
            yc.fetch_lyrics_from_captions(VIDEO)
    assert e.value.code == "too_short"
    assert e.value.terminal is True


def test_fetch_lyrics_rejects_video_without_captions():
    with _ytdlp({"subtitles": {}, "automatic_captions": {}}, []):
        with pytest.raises(yc.CaptionUnavailable) as e:
            yc.fetch_lyrics_from_captions(VIDEO)
    assert e.value.code == "no_manual_captions"


def test_fetch_lyrics_accepts_cjk_body_when_the_title_gives_no_hint():
    """영어 제목을 붙인 보카로 곡 — 힌트가 없으면 CJK이기만 하면 받는다."""
    info = {"subtitles": {"ja": [{}]}, "title": "GUMI - KING (Kanaria)"}
    with _ytdlp(info, {"ja": _lines("隠しきれない", "次の行", "三行目")}):
        found = yc.fetch_lyrics_from_captions(VIDEO)
    assert found.track.reason == "body_script"
    assert found.track.language == "ja"


def test_fetch_lyrics_rejects_latin_only_body_without_a_hint():
    """제목도 본문도 로마자면 원어를 반박할 근거가 없다 — CJK 게이트가 막는다.

    실측 대가: 폐기 44건 중 진짜 비CJK 곡은 2건(4.5%)뿐이었다.
    """
    info = {"subtitles": {"en": [{}]}, "title": "INSANE (Hazbin Hotel Song)"}
    with _ytdlp(info, {"en": _lines("cannot hide", "next line", "third line")}):
        with pytest.raises(yc.CaptionUnavailable) as e:
            yc.fetch_lyrics_from_captions(VIDEO)
    assert e.value.code == "non_cjk_caption"


def test_fetch_lyrics_rejects_malformed_video_id():
    with pytest.raises(yc.CaptionUnavailable):
        yc.fetch_lyrics_from_captions("too-short")


# ── 같은 영상의 한국어 자막을 번역으로 ────────────────────────────
#
# 어젯밤 300곡 실측: 원문이 한국어가 아닌데 한국어 수동 자막이 있는 곡이 93곡(31%)이다.
# 원문을 유튜브 자막에서 가져오는 곡이라면 같은 영상의 사람 번역이 기계 번역보다 낫고,
# 시간축이 같아 줄 대응도 자연스럽다.


def _ev(*triples):
    return [{"start": s, "end": e, "text": t} for s, e, t in triples]


def test_translation_matches_by_time_not_by_index():
    """줄 순서로 맞출 수 없다 — 버려지는 줄(효과음·중복) 수가 트랙마다 다르다."""
    originals = _ev((1.0, 3.0, "一行目"), (3.0, 5.0, "二行目"), (5.0, 7.0, "三行目"))
    # 번역 트랙은 첫 줄이 없고(번역자가 안 옮겼다) 시각만 맞다
    translations = _ev((3.1, 4.9, "둘째 줄"), (5.2, 6.8, "셋째 줄"))
    assert yc.match_translation_lines(originals, translations) == ["", "둘째 줄", "셋째 줄"]


def test_translation_picks_the_largest_overlap():
    originals = _ev((10.0, 14.0, "원문"))
    translations = _ev((9.0, 10.3, "스친 것"), (10.5, 13.8, "겹치는 것"))
    assert yc.match_translation_lines(originals, translations) == ["겹치는 것"]


def test_translation_ignores_a_barely_touching_neighbour():
    """인접 줄의 꼬리에 0.2초 스친 것을 번역으로 오인하면 줄마다 엉뚱한 번역이 붙는다."""
    originals = _ev((10.0, 14.0, "원문"))
    assert yc.match_translation_lines(originals, _ev((9.8, 10.2, "이전 줄 꼬리"))) == [""]


def test_translation_allows_one_line_to_cover_two_originals():
    """번역자가 두 줄을 한 줄로 합쳐 적는 일이 흔하다 — 빈칸보다 같은 번역이 낫다."""
    originals = _ev((1.0, 3.0, "前半"), (3.0, 5.0, "後半"))
    merged = _ev((1.0, 5.0, "앞뒤를 합친 번역"))
    assert yc.match_translation_lines(originals, merged) == [
        "앞뒤를 합친 번역", "앞뒤를 합친 번역",
    ]


def test_translation_needs_timing_on_both_sides():
    """시각이 없으면 맞출 근거가 없다 — 아무것도 붙이지 않는다."""
    assert yc.match_translation_lines([{"text": "원문"}], _ev((1.0, 2.0, "번역"))) == [""]


def test_korean_track_is_not_used_when_the_original_is_korean():
    """원문이 한국어면 그 자막은 번역이 아니라 원문이다."""
    info = {"subtitles": {"ko": [{}], "en": [{}]}}
    assert yc._translation_track_key(info, "ko") is None
    assert yc._translation_track_key(info, "ja") == "ko"


def test_fetch_lyrics_attaches_the_korean_caption_as_translation():
    info = {"subtitles": {"ja": [{}], "ko": [{}]}, "title": "初音ミク - メルト"}
    bodies = {
        "ja": _ev((1.0, 3.0, "隠しきれない"), (3.0, 5.0, "次の行"), (5.0, 7.0, "三行目")),
        "ko": _ev((1.0, 3.0, "숨길 수 없는"), (3.0, 5.0, "다음 줄"), (5.0, 7.0, "셋째 줄")),
    }
    with _ytdlp(info, bodies) as calls:
        found = yc.fetch_lyrics_from_captions(VIDEO)
    assert found.lines == ["隠しきれない", "次の行", "三行目"]
    assert found.translations == ["숨길 수 없는", "다음 줄", "셋째 줄"]
    assert "ko" in calls["tracks"], "번역용 한국어 트랙을 받아야 한다"


def test_fetch_lyrics_leaves_translations_none_without_a_korean_track():
    info = {"subtitles": {"ja": [{}], "en": [{}]}, "title": "初音ミク - メルト"}
    with _ytdlp(info, {"ja": _ev((1.0, 3.0, "隠しきれない"), (3.0, 5.0, "次の行"),
                                 (5.0, 7.0, "三行目"))}):
        found = yc.fetch_lyrics_from_captions(VIDEO)
    assert found.translations is None


def test_translation_failure_does_not_break_lyric_fetching():
    """번역은 «있으면 좋은» 것이다 — 한국어 트랙을 못 받아도 가사는 나와야 한다."""
    info = {"subtitles": {"ja": [{}], "ko": [{}]}, "title": "初音ミク - メルト"}
    # ko는 bodies에 없으므로 목이 empty_caption을 던진다
    with _ytdlp(info, {"ja": _ev((1.0, 3.0, "隠しきれない"), (3.0, 5.0, "次の行"),
                                 (5.0, 7.0, "三行目"))}):
        found = yc.fetch_lyrics_from_captions(VIDEO)
    assert found.lines == ["隠しきれない", "次の行", "三行目"]
    assert found.translations is None


def test_clean_events_keep_timing_and_merge_duplicates():
    """정리 규칙은 `clean_caption_lines`와 같아야 한다 — 한쪽만 고치면 번역 대응이 어긋난다."""
    raw = _ev((1.0, 2.0, "[음악]"), (2.0, 3.0, "같은 줄"), (3.0, 4.5, "같은 줄"),
              (5.0, 6.0, ">> 다음 줄"))
    events = yc.clean_caption_events(raw)
    assert [e["text"] for e in events] == ["같은 줄", "다음 줄"]
    assert events[0]["end"] == 4.5, "연속 중복은 뒤엣것의 끝까지 한 줄로 본다"
    assert yc.clean_caption_lines(raw) == [e["text"] for e in events]


# ── 엔드포인트: 기존 잡 생성 경로 재사용 ──────────────────────────


@contextlib.asynccontextmanager
async def _env(**server_overrides):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    orig = db_conn.async_session
    db_conn.async_session = sm

    server = get_settings().server
    saved = {k: getattr(server, k) for k in server_overrides}
    for k, v in server_overrides.items():
        object.__setattr__(server, k, v)
    worker_core._PENDING_TITLE.clear()
    worker_core._PENDING_ATTRIBUTION.clear()
    try:
        yield sm
    finally:
        db_conn.async_session = orig
        for k, v in saved.items():
            object.__setattr__(server, k, v)
        worker_core._PENDING_TITLE.clear()
        worker_core._PENDING_ATTRIBUTION.clear()
        await engine.dispose()


# 제목을 함께 둔다 — 원어 판정이 제목·채널명의 문자 체계에서 나오므로, 제목이 없으면
# 힌트가 없어 알파벳순 첫 트랙(ar)을 받아 본다. 실제 영상에는 제목이 항상 있다.
CAPTION_INFO = {
    "subtitles": MANUAL_15,
    "automatic_captions": AUTO_WITH_ORIG,
    "title": "熱異常 / いよわ",
}
CAPTION_RAW = _lines("一行目", "二行目", "三行目")
CAPTION_LYRICS = "一行目\n二行目\n三行目"


def test_generate_from_caption_creates_job_through_generate_path():
    async def body():
        async with _env(local_worker=False) as sm:
            with _ytdlp(CAPTION_INFO, CAPTION_RAW):
                resp = await generate_sync_from_caption(
                    GenerateFromCaptionRequest(
                        video_id=VIDEO, title="熱異常", artist="いよわ"
                    ),
                    BackgroundTasks(),
                )
            assert resp.status == "processing"
            assert (resp.lang, resp.auto, resp.reason) == ("ja", False, "title_script")
            assert resp.line_count == 3

            async with sm() as s:
                job = await JobRepository(s).get_by_id(resp.job_id)
                # 자막 타임스탬프는 버리고 텍스트 줄만 가사로 넘어간다
                assert job.lyrics == CAPTION_LYRICS
                assert job.lyrics_hash == hash_lyrics(CAPTION_LYRICS)
                assert job.language == "ja"
                # 자막 경로는 line_meta_pending으로 잡을 만든다(번역·독음을 서버가 뒤이어
                # 붙인다) — 그래서 응답 시점엔 아직 pending이고, 번역이 붙거나 대기 상한이
                # 지난 뒤에 queued로 넘어가 원격 워커가 클레임한다. 그 전이 자체는
                # test_caption_line_meta.py가 백그라운드 작업을 실제로 돌려 검증한다.
                assert job.status == "pending"

            # 출처는 실제로 쓴 트랙을 밝힌다
            attribution = worker_core._PENDING_ATTRIBUTION[resp.job_id]
            assert attribution["name"] == "유튜브 자막 · JA"
            assert VIDEO in attribution["url"]
            assert worker_core.peek_title(resp.job_id) == ("熱異常", "いよわ")

    asyncio.run(body())


def test_generate_from_caption_reuses_existing_sync_without_new_job():
    """같은 자막 가사로 이미 싱크가 있으면 /generate와 똑같이 즉시 completed."""

    async def body():
        async with _env(local_worker=False) as sm:
            async with sm() as s:
                await SyncRepository(s).create(
                    video_id=VIDEO,
                    lyrics_hash=hash_lyrics(CAPTION_LYRICS),
                    timestamps=[{"text": "一行目", "start": 1.0, "end": 2.0}],
                    engine="ctc",
                )
                await s.commit()

            with _ytdlp(CAPTION_INFO, CAPTION_RAW):
                resp = await generate_sync_from_caption(
                    GenerateFromCaptionRequest(video_id=VIDEO), BackgroundTasks()
                )
            assert resp.status == "completed"
            assert resp.estimated_time == 0
            async with sm() as s:
                assert await JobRepository(s).get_by_id(resp.job_id) is None

    asyncio.run(body())


def test_generate_from_caption_joins_active_job_instead_of_duplicating():
    """버튼 연타로 같은 잡이 중복 생성되지 않는다 (/generate의 합류 로직 재사용)."""

    async def body():
        async with _env(local_worker=False):
            with _ytdlp(CAPTION_INFO, CAPTION_RAW):
                first = await generate_sync_from_caption(
                    GenerateFromCaptionRequest(video_id=VIDEO), BackgroundTasks()
                )
                second = await generate_sync_from_caption(
                    GenerateFromCaptionRequest(video_id=VIDEO), BackgroundTasks()
                )
            assert second.job_id == first.job_id
            assert second.status == "processing"

    asyncio.run(body())


def test_generate_from_caption_never_falls_back_to_an_auto_track():
    """수동 자막에 원어가 없으면 ASR 원어 트랙이 있어도 쓰지 않는다.

    예전에는 `ja-orig` ASR 트랙으로 폴백했다. 사용자 확인(2026-07-26)으로 그 전사의 인식
    품질이 가사로 쓸 수준이 아님이 확정됐고, 언어 라벨도 못 믿는다는 것이 실측으로
    확인됐다(일본어 곡에 vi-orig · th-orig). ASR은 순서 결정에도 쓰지 않는다.
    """

    async def body():
        async with _env(local_worker=False) as sm:
            # 제목은 일본어인데 수동 자막은 en·ko뿐이고, ASR에는 ja-orig가 있다
            info = {
                "subtitles": {"en": [{}], "ko": [{}]},
                "automatic_captions": AUTO_WITH_ORIG,
                "title": "熱異常 / いよわ",
            }
            bodies = {"en": _lines("line one", "line two", "line three"),
                      "ko": _lines("첫 줄", "둘째 줄", "셋째 줄")}
            with _ytdlp(info, bodies) as calls:
                with pytest.raises(HTTPException) as e:
                    await generate_sync_from_caption(
                        GenerateFromCaptionRequest(video_id=VIDEO), BackgroundTasks()
                    )
            assert e.value.detail["code"] == "no_original_track"
            assert "ja-orig" not in calls["tracks"], "ASR 트랙을 받아 봤다"
            # 실패한 요청은 잡을 만들지 않는다
            async with sm() as s:
                assert (await s.execute(select(Job))).scalars().all() == []

    asyncio.run(body())


@pytest.mark.parametrize(
    "info,raw,code,status",
    [
        # 자막이 아예 없는 경우와 ASR만 있는 경우가 같은 코드로 떨어진다 — 어느 쪽이든
        # 사용자가 할 일은 «가사를 직접 붙여넣기»다
        ({"subtitles": {}, "automatic_captions": {}}, [], "no_manual_captions", 404),
        (
            {"subtitles": {}, "automatic_captions": {"ja-orig": [{}], "vi-orig": [{}]}},
            [],
            "no_manual_captions",
            404,
        ),
        ({"subtitles": {"ja": [{}]}, "automatic_captions": {"ja-orig": [{}]},
          "title": "熱異常"},
         _lines("[음악]"), "too_short", 404),
    ],
)
def test_generate_from_caption_fails_explicitly_when_unusable(info, raw, code, status):
    """클라이언트가 '직접 붙여넣기'로 안내할 수 있도록 코드와 안내문을 준다."""

    async def body():
        async with _env(local_worker=False) as sm:
            with _ytdlp(info, raw):
                with pytest.raises(HTTPException) as e:
                    await generate_sync_from_caption(
                        GenerateFromCaptionRequest(video_id=VIDEO), BackgroundTasks()
                    )
            assert e.value.status_code == status
            assert e.value.detail["code"] == code
            assert "직접 붙여넣어" in e.value.detail["message"]
            # 실패한 요청은 잡을 만들지 않는다 — GPU가 도는 일이 없다
            async with sm() as s:
                assert (await s.execute(select(Job))).scalars().all() == []

    asyncio.run(body())


def test_generate_from_caption_reports_transient_failure_as_retryable():
    """조달 실패(5xx)는 확정 판정이 아니므로 붙여넣기 안내를 달지 않는다."""

    async def body():
        async with _env(local_worker=False):
            def boom(video_id):
                raise yc.CaptionUnavailable("listing_failed", "자막 목록을 가져오지 못했어요")

            orig = yc.extract_caption_info
            yc.extract_caption_info = boom
            try:
                with pytest.raises(HTTPException) as e:
                    await generate_sync_from_caption(
                        GenerateFromCaptionRequest(video_id=VIDEO), BackgroundTasks()
                    )
            finally:
                yc.extract_caption_info = orig
            assert e.value.status_code == 502
            assert e.value.detail["code"] == "listing_failed"
            assert "직접 붙여넣어" not in e.value.detail["message"]

    asyncio.run(body())


def test_generate_from_caption_rejects_malformed_video_id():
    with pytest.raises(Exception):
        GenerateFromCaptionRequest(video_id="too-short")
