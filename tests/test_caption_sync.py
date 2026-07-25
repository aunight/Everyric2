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


def test_detect_prefers_asr_orig_language_over_manual_majority():
    """수동작성 15종 중 어느 것도 아니고, ASR 원어(ja)가 답이다."""
    assert yc.detect_original_language(MANUAL_15, AUTO_WITH_ORIG) == ("ja", "asr_orig")


def test_detect_ignores_translation_only_auto_tracks_without_orig():
    """'-orig'가 없고 자동 트랙이 여럿이면 그건 번역본 목록 — ASR 신호가 아니다."""
    autos = {"en": [{}], "ko": [{}], "es": [{}]}
    assert yc.detect_original_language({"ja": [{}], "en": [{}]}, autos) is None


def test_detect_falls_back_to_sole_auto_track():
    """번역본이 노출되지 않아 자동 트랙이 하나뿐이면 그게 ASR 원어다."""
    assert yc.detect_original_language(MANUAL_15, {"ja": [{}]}) == ("ja", "asr_only")


def test_detect_falls_back_to_video_language():
    """ASR이 없으면 yt-dlp가 기본 오디오에서 뽑은 info['language']를 쓴다."""
    hit = yc.detect_original_language(MANUAL_15, {}, video_language="ja-JP")
    assert hit == ("ja", "video_language")


def test_detect_rejects_video_language_without_matching_track():
    """오디오 언어가 ja라도 ja 자막이 없으면 그 판정으로 트랙을 고를 수 없다."""
    assert yc.detect_original_language({"en": [{}], "ko": [{}]}, {}, video_language="ja") is None


def test_detect_falls_back_to_sole_manual_track():
    assert yc.detect_original_language({"ko": [{}]}, {}) == ("ko", "sole_manual")


def test_detect_returns_none_when_undeterminable():
    assert yc.detect_original_language({"en": [{}], "ko": [{}]}, {}) is None
    assert yc.detect_original_language({}, {}) is None


# ── 트랙 선택 ─────────────────────────────────────────────────────


def test_select_prefers_manual_track_of_the_detected_language():
    """자동 생성은 가사 오인식이 많다 — 같은 언어 수동작성이 있으면 그쪽."""
    choice = yc.select_original_track(
        {"subtitles": MANUAL_15, "automatic_captions": AUTO_WITH_ORIG}
    )
    assert (choice.lang, choice.auto, choice.language) == ("ja", False, "ja")
    assert choice.reason == "asr_orig"
    assert choice.label == "JA"


def test_select_uses_orig_auto_track_when_no_manual_in_that_language():
    choice = yc.select_original_track(
        {"subtitles": {"en": [{}], "ko": [{}]}, "automatic_captions": AUTO_WITH_ORIG}
    )
    assert (choice.lang, choice.auto, choice.language) == ("ja-orig", True, "ja")
    assert "자동 생성" in choice.label


def test_select_matches_regional_manual_variant():
    """판정 언어가 'ja'인데 트랙 키가 'ja-JP'뿐이어도 같은 언어로 붙는다."""
    choice = yc.select_original_track(
        {"subtitles": {"ja-JP": [{"name": "日本語"}], "en": [{}]},
         "automatic_captions": {"ja-orig": [{}], "en": [{}]}}
    )
    assert (choice.lang, choice.auto) == ("ja-JP", False)


def test_select_raises_when_no_captions_at_all():
    with pytest.raises(yc.CaptionUnavailable) as e:
        yc.select_original_track({"subtitles": {}, "automatic_captions": {}})
    assert e.value.code == "no_captions"
    assert e.value.http_status == 404
    assert e.value.terminal is True


def test_select_raises_when_language_undeterminable():
    with pytest.raises(yc.CaptionUnavailable) as e:
        yc.select_original_track({"subtitles": {"en": [{}], "ko": [{}]}, "automatic_captions": {}})
    assert e.value.code == "language_undetermined"


def test_live_chat_is_not_a_caption_track():
    """라이브 채팅 리플레이가 유일한 '자막'이면 자막이 없는 것과 같다."""
    with pytest.raises(yc.CaptionUnavailable) as e:
        yc.select_original_track({"subtitles": {"live_chat": [{}]}, "automatic_captions": {}})
    assert e.value.code == "no_captions"


def test_align_language_only_passed_for_engine_supported_langs():
    """CTC가 모르는 언어 코드를 그대로 넘기면 엉뚱한 어댑터가 잡힌다 — None으로 비운다."""
    ja = yc.CaptionLyrics(
        track=yc.TrackChoice("ja", False, "JA", "asr_orig", "ja"), lines=["a", "b", "c"]
    )
    gl = yc.CaptionLyrics(
        track=yc.TrackChoice("gl", False, "GL", "sole_manual", "gl"), lines=["a", "b", "c"]
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
    """extract_caption_info / download_track_lines만 갈아끼운다 — 판정·정리는 실코드."""
    calls: dict = {}

    def fake_extract(video_id):
        calls["video_id"] = video_id
        return info

    def fake_download(video_id, lang, auto):
        calls["track"] = (lang, auto)
        return lines

    orig = (yc.extract_caption_info, yc.download_track_lines)
    yc.extract_caption_info = fake_extract
    yc.download_track_lines = fake_download
    try:
        yield calls
    finally:
        yc.extract_caption_info, yc.download_track_lines = orig


def test_fetch_lyrics_uses_selected_track_and_cleans_text():
    info = {"subtitles": MANUAL_15, "automatic_captions": AUTO_WITH_ORIG}
    raw = _lines("[음악]", "隠しきれない", "隠しきれない", ">> 次の行", "三行目")
    with _ytdlp(info, raw) as calls:
        found = yc.fetch_lyrics_from_captions(VIDEO)
    assert calls["track"] == ("ja", False)  # 수동작성 ja 트랙
    assert found.lines == ["隠しきれない", "次の行", "三行目"]
    assert found.text == "隠しきれない\n次の行\n三行目"
    assert found.track.reason == "asr_orig"
    assert found.align_language == "ja"


def test_fetch_lyrics_rejects_caption_with_too_few_usable_lines():
    """자막이 안내 문구·효과음뿐이면 GPU를 태우기 전에 거절한다."""
    info = {"subtitles": {"ja": [{}]}, "automatic_captions": {"ja-orig": [{}]}}
    with _ytdlp(info, _lines("[음악]", "구독과 좋아요")):
        with pytest.raises(yc.CaptionUnavailable) as e:
            yc.fetch_lyrics_from_captions(VIDEO)
    assert e.value.code == "too_short"
    assert e.value.terminal is True


def test_fetch_lyrics_rejects_video_without_captions():
    with _ytdlp({"subtitles": {}, "automatic_captions": {}}, []):
        with pytest.raises(yc.CaptionUnavailable) as e:
            yc.fetch_lyrics_from_captions(VIDEO)
    assert e.value.code == "no_captions"


def test_fetch_lyrics_rejects_malformed_video_id():
    with pytest.raises(yc.CaptionUnavailable):
        yc.fetch_lyrics_from_captions("too-short")


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


CAPTION_INFO = {"subtitles": MANUAL_15, "automatic_captions": AUTO_WITH_ORIG}
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
            assert (resp.lang, resp.auto, resp.reason) == ("ja", False, "asr_orig")
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


def test_generate_from_caption_uses_auto_track_when_no_manual_original():
    async def body():
        async with _env(local_worker=False):
            info = {"subtitles": {"en": [{}], "ko": [{}]}, "automatic_captions": AUTO_WITH_ORIG}
            with _ytdlp(info, CAPTION_RAW) as calls:
                resp = await generate_sync_from_caption(
                    GenerateFromCaptionRequest(video_id=VIDEO), BackgroundTasks()
                )
            assert calls["track"] == ("ja-orig", True)
            assert resp.auto is True
            assert resp.lang == "ja-orig"
            assert "자동 생성" in resp.track_label

    asyncio.run(body())


@pytest.mark.parametrize(
    "info,raw,code,status",
    [
        ({"subtitles": {}, "automatic_captions": {}}, [], "no_captions", 404),
        (
            {"subtitles": {"en": [{}], "ko": [{}]}, "automatic_captions": {}},
            [],
            "language_undetermined",
            404,
        ),
        ({"subtitles": {"ja": [{}]}, "automatic_captions": {"ja-orig": [{}]}},
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
