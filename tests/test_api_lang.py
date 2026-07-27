"""GET /api/sync?lang= 서빙 + POST /api/translate persist= 저장 (Task 9).

실 DB를 건드리지 않도록 격리된 in-memory SQLite로 connection.async_session을 몽키패치하고
라우트 핸들러(코루틴)를 직접 호출한다 — test_sync_link.py·test_caption_line_meta.py와 같은
서버 테스트 규약이다. translate_lyrics는 동기(plain def) + BackgroundTasks 브리지라
`BackgroundTasks()`를 만들어 넘기고 `await bg()`로 큐에 쌓인 저장 작업을 직접 실행한다
(Starlette가 응답 뒤에 하는 일과 동일 — test_caption_line_meta.py의 `_run_background`
패턴). LLM은 절대 호출하지 않는다 — `LyricsTranslator`를 통째로 목으로 갈아끼운다.

4개 핵심 시나리오(계획 Task 9 Step 1) + ko 레거시 이행 가정 + SyncLink 경유 조회에도
같은 규칙이 적용되는지를 덧붙인다.
"""

import asyncio
import contextlib

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from everyric2.config.settings import get_settings
from everyric2.server.api import translate as translate_api
from everyric2.server.api.sync import (
    Attribution,
    RegenerateRequest,
    SaveTranslationLayerRequest,
    SaveTranslationLayerResponse,
    SyncLinkRequest,
    TranslationLayerLine,
    create_sync_link,
    get_sync,
    regenerate_sync,
    save_translation_layer,
)
from everyric2.server.api.translate import TranslateRequest, translate_lyrics
from everyric2.server.db import connection as db_conn
from everyric2.server.db.models import Base
from everyric2.server.db.repository import SyncRepository, TranslationLayerRepository
from everyric2.server.text_fingerprint import lines_fingerprint

VIDEO = "LANGVID0001"
LINES = ["첫 줄", "둘째 줄"]
FP = lines_fingerprint(LINES)


@contextlib.asynccontextmanager
async def _env(**server_overrides):
    """in-memory SQLite로 몽키패치 + 선택적 서버 설정 오버라이드(local_worker 등).

    test_caption_line_meta.py의 _env(**server_overrides)와 같은 패턴 — regenerate_sync는
    local_worker=True(기본값)면 백그라운드로 실 워커 파이프라인(process_job)까지 큐잉하려
    들어 유닛 테스트에서 await하면 안 된다. local_worker=False로 두면 상태만 queued로
    마킹하고 끝나 백필 태스크만 안전하게 실행할 수 있다."""
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
    try:
        yield sm
    finally:
        db_conn.async_session = orig
        for k, v in saved.items():
            object.__setattr__(server, k, v)
        await engine.dispose()


def _seed_segments(translations: list[str]) -> list[dict]:
    return [
        {"text": text, "translation": tr, "start": float(i), "end": float(i) + 1.0}
        for i, (text, tr) in enumerate(zip(LINES, translations))
    ]


async def _run_background(bg: BackgroundTasks) -> None:
    """Starlette가 응답 후에 하는 일 — 등록된 작업을 순서대로 실행한다."""
    await bg()


# ── translate_lyrics를 위한 LLM 없는 대역 (persist 테스트 전용) ──────────────


class _FakeLine:
    def __init__(self, original: str, translation: str):
        self.original = original
        self.translation = translation
        self.pronunciation = None
        self.failed = False


class _FakeResult:
    def __init__(self, lines, source_lang, target_lang, translation_skipped=False):
        self.lines = lines
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.engine = "fake"
        self.translation_skipped = translation_skipped


class _FakeInnerTranslator:
    def translate(self, text, source_lang, target_lang, context=None):
        originals = text.split("\n")
        if source_lang == target_lang:
            lines = [_FakeLine(o, "") for o in originals]
            return _FakeResult(lines, source_lang, target_lang, translation_skipped=True)
        lines = [_FakeLine(o, f"[{target_lang}] {o}") for o in originals]
        return _FakeResult(lines, source_lang, target_lang)


class _FakeLyricsTranslator:
    def __init__(self, settings=None, log_label=None):
        self._translator = _FakeInnerTranslator()

    def translate_with_pronunciation(self, text, source_lang, target_lang, context=None):
        return self._translator.translate(text, source_lang, target_lang, context)


def _patch_translator(monkeypatch):
    monkeypatch.setattr(translate_api, "LyricsTranslator", _FakeLyricsTranslator)


# ── (1) lang 없이 조회 = 기존 응답 그대로 ─────────────────────────────


def test_lookup_without_lang_is_unchanged():
    async def body():
        async with _env() as sm:
            async with sm() as s:
                await SyncRepository(s).create(
                    video_id=VIDEO,
                    lyrics_hash="h1",
                    timestamps=_seed_segments(["레거시 번역 1", ""]),
                )
                await s.commit()

            resp = await get_sync(VIDEO)
            assert resp.found is True
            assert resp.translation_lang is None
            assert [seg["translation"] for seg in resp.timestamps] == ["레거시 번역 1", ""]
            assert [seg["text"] for seg in resp.timestamps] == LINES

    asyncio.run(body())


# ── (2) 레이어 upsert 후 lang=en 조회 → translation 교체 + translation_lang="en" ──


def test_lookup_with_lang_replaces_translation_from_layer():
    async def body():
        async with _env() as sm:
            async with sm() as s:
                await SyncRepository(s).create(
                    video_id=VIDEO,
                    lyrics_hash="h1",
                    timestamps=_seed_segments(["레거시 번역 1", "레거시 번역 2"]),
                )
                await TranslationLayerRepository(s).upsert_layer(
                    VIDEO,
                    FP,
                    "en",
                    lines=[
                        {"text": "첫 줄", "translation": "First line"},
                        {"text": "둘째 줄", "translation": "Second line"},
                    ],
                    attribution=None,
                    origin="llm",
                )
                await s.commit()

            resp = await get_sync(VIDEO, lang="en")
            assert resp.translation_lang == "en"
            assert [seg["translation"] for seg in resp.timestamps] == [
                "First line",
                "Second line",
            ]

            # 레이어 조회가 원본 SyncResult.timestamps를 오염시키지 않았는지 확인 —
            # lang 없이 다시 조회하면 레거시 번역이 그대로 남아 있어야 한다.
            legacy = await get_sync(VIDEO)
            assert [seg["translation"] for seg in legacy.timestamps] == [
                "레거시 번역 1",
                "레거시 번역 2",
            ]
            assert legacy.translation_lang is None

    asyncio.run(body())


# ── (3) lang=fr 레이어 없음 → translation 전부 빈 값 + translation_lang None ──


def test_lookup_with_lang_and_no_layer_blanks_out_non_ko():
    async def body():
        async with _env() as sm:
            async with sm() as s:
                await SyncRepository(s).create(
                    video_id=VIDEO,
                    lyrics_hash="h1",
                    timestamps=_seed_segments(["레거시 번역 1", "레거시 번역 2"]),
                )
                await s.commit()

            resp = await get_sync(VIDEO, lang="fr")
            assert resp.translation_lang is None
            assert all(seg["translation"] == "" for seg in resp.timestamps)

    asyncio.run(body())


# ── ko 레거시 이행 가정: 레이어 없는 lang="ko"는 저장분을 ko로 간주하고 유지한다 ──


def test_lookup_with_lang_ko_and_no_layer_keeps_legacy_translation():
    async def body():
        async with _env() as sm:
            async with sm() as s:
                await SyncRepository(s).create(
                    video_id=VIDEO,
                    lyrics_hash="h1",
                    timestamps=_seed_segments(["레거시 번역 1", ""]),
                )
                await s.commit()

            resp = await get_sync(VIDEO, lang="ko")
            assert resp.translation_lang == "ko"
            assert [seg["translation"] for seg in resp.timestamps] == ["레거시 번역 1", ""]

    asyncio.run(body())


def test_lookup_with_lang_ko_and_no_translation_anywhere_yields_none():
    async def body():
        async with _env() as sm:
            async with sm() as s:
                await SyncRepository(s).create(
                    video_id=VIDEO,
                    lyrics_hash="h1",
                    timestamps=_seed_segments(["", ""]),
                )
                await s.commit()

            resp = await get_sync(VIDEO, lang="ko")
            # 번역이 하나도 없으면 "ko"라고 우길 근거가 없다
            assert resp.translation_lang is None

    asyncio.run(body())


# ── (4) POST /api/translate persist=true → 레이어 생김 ─────────────────


def test_translate_persist_creates_translation_layer(monkeypatch):
    _patch_translator(monkeypatch)

    async def body():
        async with _env() as sm:
            bg = BackgroundTasks()
            resp = translate_lyrics(
                TranslateRequest(
                    text="\n".join(LINES),
                    source_lang="ko",
                    target_lang="ja",
                    video_id=VIDEO,
                    persist=True,
                ),
                bg,
            )
            assert resp.lines[0].translation == "[ja] 첫 줄"
            await _run_background(bg)

            async with sm() as s:
                layer = await TranslationLayerRepository(s).get_layer(VIDEO, FP, "ja")
                assert layer is not None
                assert layer.origin == "llm"
                assert layer.lines == [
                    {"text": "첫 줄", "translation": "[ja] 첫 줄"},
                    {"text": "둘째 줄", "translation": "[ja] 둘째 줄"},
                ]

    asyncio.run(body())


def test_translate_persist_false_does_not_create_a_layer(monkeypatch):
    _patch_translator(monkeypatch)

    async def body():
        async with _env() as sm:
            bg = BackgroundTasks()
            translate_lyrics(
                TranslateRequest(
                    text="\n".join(LINES),
                    source_lang="ko",
                    target_lang="ja",
                    video_id=VIDEO,
                    persist=False,
                ),
                bg,
            )
            await _run_background(bg)

            async with sm() as s:
                assert await TranslationLayerRepository(s).get_layer(VIDEO, FP, "ja") is None

    asyncio.run(body())


def test_translate_persist_true_without_video_id_does_not_crash(monkeypatch):
    _patch_translator(monkeypatch)

    async def body():
        async with _env():
            bg = BackgroundTasks()
            resp = translate_lyrics(
                TranslateRequest(
                    text="\n".join(LINES), source_lang="ko", target_lang="ja", persist=True
                ),
                bg,
            )
            assert resp.lines  # 정상 응답 — video_id 없으면 저장 시도만 생략
            await _run_background(bg)  # 큐가 비어 있어야 한다(예외 없이 통과)

    asyncio.run(body())


def test_translate_persist_skips_when_translation_was_skipped(monkeypatch):
    # source == target(대각선)이면 번역 필드가 전부 빈 값이라 저장할 것이 없다 —
    # 레이어에 빈 값을 얹으면 기존 실제 번역을 덮어쓸 위험만 있으므로 저장하지 않는다.
    _patch_translator(monkeypatch)

    async def body():
        async with _env() as sm:
            bg = BackgroundTasks()
            resp = translate_lyrics(
                TranslateRequest(
                    text="\n".join(LINES),
                    source_lang="ko",
                    target_lang="ko",
                    video_id=VIDEO,
                    persist=True,
                ),
                bg,
            )
            assert resp.translation_skipped is True
            await _run_background(bg)

            async with sm() as s:
                assert await TranslationLayerRepository(s).get_layer(VIDEO, FP, "ko") is None

    asyncio.run(body())


# ── 엔드투엔드: persist로 저장한 것을 lang 조회가 실제로 되찾는다 ─────────


def test_persist_then_lookup_round_trip(monkeypatch):
    _patch_translator(monkeypatch)

    async def body():
        async with _env() as sm:
            async with sm() as s:
                await SyncRepository(s).create(
                    video_id=VIDEO, lyrics_hash="h1", timestamps=_seed_segments(["", ""])
                )
                await s.commit()

            bg = BackgroundTasks()
            translate_lyrics(
                TranslateRequest(
                    text="\n".join(LINES),
                    source_lang="ko",
                    target_lang="ja",
                    video_id=VIDEO,
                    persist=True,
                ),
                bg,
            )
            await _run_background(bg)

            resp = await get_sync(VIDEO, lang="ja")
            assert resp.translation_lang == "ja"
            assert [seg["translation"] for seg in resp.timestamps] == [
                "[ja] 첫 줄",
                "[ja] 둘째 줄",
            ]

    asyncio.run(body())


# ── 링크 경유 조회(SyncLink)도 같은 규칙이 적용되는지 ────────────────────


def test_linked_sync_lookup_applies_lang_keyed_by_requested_video_id():
    """링크로 빌려온 싱크도 lang 규칙이 적용된다 — 레이어 키는 source가 아니라
    **요청받은 video_id**(링크를 단 영상)를 쓴다. POST /api/translate persist=true도
    항상 요청 video_id로 저장하므로, 조회도 같은 키라야 서로 맞아떨어진다."""

    async def body():
        async with _env() as sm:
            async with sm() as s:
                await SyncRepository(s).create(
                    video_id="SRCSRCSRC01",
                    lyrics_hash="h1",
                    timestamps=_seed_segments(["소스 레거시", "소스 레거시2"]),
                )
                await s.commit()

            await create_sync_link(
                SyncLinkRequest(
                    video_id="DSTDSTDST01", source_video_id="SRCSRCSRC01", offset_sec=5.0
                )
            )

            async with sm() as s:
                # 레이어는 링크를 단 영상(DST) 키로 저장 — SRC 키로는 안 만든다
                await TranslationLayerRepository(s).upsert_layer(
                    "DSTDSTDST01",
                    FP,
                    "en",
                    lines=[
                        {"text": "첫 줄", "translation": "Linked first"},
                        {"text": "둘째 줄", "translation": "Linked second"},
                    ],
                    attribution=None,
                    origin="llm",
                )
                await s.commit()

            resp = await get_sync("DSTDSTDST01", lang="en")
            assert resp.linked is not None  # 여전히 링크 응답이다(시간이 시프트됨)
            assert resp.translation_lang == "en"
            assert [seg["translation"] for seg in resp.timestamps] == [
                "Linked first",
                "Linked second",
            ]
            # 시간은 여전히 시프트된 값(오프셋 5.0) — lang 처리가 시간 필드를 안 건드렸다
            assert resp.timestamps[0]["start"] == 5.0

            # SRC 자체를 lang=en으로 조회하면 그 레이어가 없으므로(DST 키로만 있다) 비워진다
            src_resp = await get_sync("SRCSRCSRC01", lang="en")
            assert src_resp.translation_lang is None
            assert all(seg["translation"] == "" for seg in src_resp.timestamps)

    asyncio.run(body())


# ── ko 자동 백필: 실사고 시나리오 — 배포 이전 생성분의 ko 번역(위키 사람 번역 포함)이
# en 유저의 재생성으로 서빙에서 사라지는 것을 막는다 ────────────────────────


def test_ko_lookup_backfills_layer_and_survives_regeneration_without_legacy_segments():
    """레이어 없는 옛 싱크를 lang=ko로 읽으면 (a) 레거시 그대로 서빙되고 (b) 백그라운드로
    레이어에 옮겨 백필된다. 그 뒤 "en 유저가 재생성해 새 싱크의 세그엔 ko 번역이 아예
    없는" 상황을 흉내 내도, 미리 백필된 레이어 덕분에 lang=ko 조회가 여전히 그 번역을
    돌려준다 — 백필이 없었다면 이 지점에서 translation_lang이 None이 됐을 것이다."""

    async def body():
        async with _env() as sm:
            async with sm() as s:
                await SyncRepository(s).create(
                    video_id=VIDEO,
                    lyrics_hash="h1",
                    timestamps=_seed_segments(["레거시 번역 1", "레거시 번역 2"]),
                )
                await s.commit()

            # (1) lang=ko 조회 — 레거시 그대로 서빙 + 백그라운드 백필 스케줄
            bg = BackgroundTasks()
            resp = await get_sync(VIDEO, lang="ko", background_tasks=bg)
            assert resp.translation_lang == "ko"
            assert [seg["translation"] for seg in resp.timestamps] == [
                "레거시 번역 1",
                "레거시 번역 2",
            ]
            await _run_background(bg)

            async with sm() as s:
                layer = await TranslationLayerRepository(s).get_layer(VIDEO, FP, "ko")
                assert layer is not None
                assert layer.origin == "legacy"
                assert layer.lines == [
                    {"text": "첫 줄", "translation": "레거시 번역 1"},
                    {"text": "둘째 줄", "translation": "레거시 번역 2"},
                ]

            # (2) en 유저의 재생성을 흉내 — 같은 가사지만 새 싱크의 세그엔 ko 번역이 없다
            async with sm() as s:
                repo = SyncRepository(s)
                await repo.delete_by_video(VIDEO)
                await repo.create(
                    video_id=VIDEO, lyrics_hash="h1", timestamps=_seed_segments(["", ""])
                )
                await s.commit()

            # (3) 백필된 레이어가 살아 있어 lang=ko가 여전히 복원된다
            recovered = await get_sync(VIDEO, lang="ko")
            assert recovered.translation_lang == "ko"
            assert [seg["translation"] for seg in recovered.timestamps] == [
                "레거시 번역 1",
                "레거시 번역 2",
            ]

    asyncio.run(body())


def test_ko_lookup_without_legacy_translation_schedules_nothing():
    # 세그에 번역이 하나도 없으면 백필할 것도 없다 — 빈 레이어를 만들지 않는다
    async def body():
        async with _env() as sm:
            async with sm() as s:
                await SyncRepository(s).create(
                    video_id=VIDEO, lyrics_hash="h1", timestamps=_seed_segments(["", ""])
                )
                await s.commit()

            bg = BackgroundTasks()
            resp = await get_sync(VIDEO, lang="ko", background_tasks=bg)
            assert resp.translation_lang is None
            await _run_background(bg)

            async with sm() as s:
                assert await TranslationLayerRepository(s).get_layer(VIDEO, FP, "ko") is None

    asyncio.run(body())


# ── 재생성 직전 ko 백필 ───────────────────────────────────────────────


def test_regenerate_backfills_ko_layer_before_creating_job():
    """재생성 잡을 만들기 전에, 이 영상의 최신 싱크에 남아 있는 레거시 ko 번역을 레이어로
    옮긴다 — 재생성(특히 force)이 그 세그 자체를 갈아끼우기 전에 선수를 친다.

    local_worker=False로 둔다 — 기본값(True)이면 _dispatch_job이 실 워커 파이프라인
    (process_job)까지 background_tasks에 얹어, 백필만 확인하려는 이 테스트가 await bg()
    할 때 실제 다운로드·정렬을 돌리려 든다."""

    async def body():
        async with _env(local_worker=False) as sm:
            async with sm() as s:
                await SyncRepository(s).create(
                    video_id=VIDEO,
                    lyrics_hash="oldhash",
                    timestamps=_seed_segments(["레거시 번역 1", "레거시 번역 2"]),
                )
                await s.commit()

            bg = BackgroundTasks()
            await regenerate_sync(
                RegenerateRequest(video_id=VIDEO, lyrics="완전히 다른 새 가사", force=True),
                bg,
                x_api_key=None,
            )
            await _run_background(bg)

            async with sm() as s:
                layer = await TranslationLayerRepository(s).get_layer(VIDEO, FP, "ko")
                assert layer is not None
                assert layer.origin == "legacy"
                assert layer.lines == [
                    {"text": "첫 줄", "translation": "레거시 번역 1"},
                    {"text": "둘째 줄", "translation": "레거시 번역 2"},
                ]

    asyncio.run(body())


def test_regenerate_does_not_backfill_when_ko_layer_already_exists():
    # 이미 레이어가 있으면(예: 이전 요청이 이미 백필함) 다시 만들 필요가 없다 — origin이
    # "llm"이던 것을 "legacy"로 잘못 덮어쓰지 않는지 확인한다.
    async def body():
        async with _env(local_worker=False) as sm:
            async with sm() as s:
                await SyncRepository(s).create(
                    video_id=VIDEO,
                    lyrics_hash="oldhash",
                    timestamps=_seed_segments(["레거시 번역 1", "레거시 번역 2"]),
                )
                await TranslationLayerRepository(s).upsert_layer(
                    VIDEO,
                    FP,
                    "ko",
                    lines=[{"text": "첫 줄", "translation": "이미 있던 정식 번역"}],
                    attribution=None,
                    origin="llm",
                )
                await s.commit()

            bg = BackgroundTasks()
            await regenerate_sync(
                RegenerateRequest(video_id=VIDEO, lyrics="완전히 다른 새 가사", force=True),
                bg,
                x_api_key=None,
            )
            await _run_background(bg)

            async with sm() as s:
                layer = await TranslationLayerRepository(s).get_layer(VIDEO, FP, "ko")
                assert layer.origin == "llm"  # 그대로 — legacy로 덮어써지지 않았다

    asyncio.run(body())


# ── available_langs ────────────────────────────────────────────────


def test_available_langs_includes_stored_layers_and_legacy_ko():
    async def body():
        async with _env() as sm:
            async with sm() as s:
                await SyncRepository(s).create(
                    video_id=VIDEO,
                    lyrics_hash="h1",
                    timestamps=_seed_segments(["레거시 번역 1", "레거시 번역 2"]),
                )
                await TranslationLayerRepository(s).upsert_layer(
                    VIDEO,
                    FP,
                    "en",
                    lines=[{"text": "첫 줄", "translation": "First"}],
                    attribution=None,
                    origin="llm",
                )
                await TranslationLayerRepository(s).upsert_layer(
                    VIDEO,
                    FP,
                    "ja",
                    lines=[{"text": "첫 줄", "translation": "最初"}],
                    attribution=None,
                    origin="llm",
                )
                await s.commit()

            # lang 미지정 조회도 available_langs를 채운다 — 정렬 + 레거시 ko 포함 + 중복 제거
            resp = await get_sync(VIDEO)
            assert resp.available_langs == ["en", "ja", "ko"]

            # lang 지정 조회도 마찬가지로 채운다
            resp_en = await get_sync(VIDEO, lang="en")
            assert resp_en.available_langs == ["en", "ja", "ko"]

    asyncio.run(body())


def test_available_langs_excludes_ko_without_legacy_translation():
    async def body():
        async with _env() as sm:
            async with sm() as s:
                await SyncRepository(s).create(
                    video_id=VIDEO, lyrics_hash="h1", timestamps=_seed_segments(["", ""])
                )
                await TranslationLayerRepository(s).upsert_layer(
                    VIDEO,
                    FP,
                    "en",
                    lines=[{"text": "첫 줄", "translation": "First"}],
                    attribution=None,
                    origin="llm",
                )
                await s.commit()

            resp = await get_sync(VIDEO)
            assert resp.available_langs == ["en"]

    asyncio.run(body())


def test_available_langs_is_none_when_sync_not_found():
    async def body():
        async with _env():
            resp = await get_sync("NOSYNCNOS01")
            assert resp.found is False
            assert resp.available_langs is None

    asyncio.run(body())


# ── POST /{video_id}/translations — 완성된 싱크에 이미 확보한 번역 직접 저장 ──────


def _layer_lines(pairs: list[tuple[str, str]]) -> list[TranslationLayerLine]:
    return [TranslationLayerLine(text=t, translation=tr) for t, tr in pairs]


def test_save_translation_layer_then_lang_lookup_reflects_it():
    """정상 저장 → lang 조회에 그대로 반영된다."""

    async def body():
        async with _env() as sm:
            async with sm() as s:
                await SyncRepository(s).create(
                    video_id=VIDEO, lyrics_hash="h1", timestamps=_seed_segments(["", ""])
                )
                await s.commit()

            resp = await save_translation_layer(
                VIDEO,
                SaveTranslationLayerRequest(
                    target_lang="en",
                    lines=_layer_lines([("첫 줄", "First line"), ("둘째 줄", "Second line")]),
                    origin="caption",
                ),
            )
            assert resp == SaveTranslationLayerResponse(
                saved=True, matched=2, total=2, target_lang="en"
            )

            async with sm() as s:
                layer = await TranslationLayerRepository(s).get_layer(VIDEO, FP, "en")
                assert layer is not None
                assert layer.origin == "caption"
                assert layer.lines == [
                    {"text": "첫 줄", "translation": "First line"},
                    {"text": "둘째 줄", "translation": "Second line"},
                ]

            lookup = await get_sync(VIDEO, lang="en")
            assert lookup.translation_lang == "en"
            assert [seg["translation"] for seg in lookup.timestamps] == [
                "First line",
                "Second line",
            ]

    asyncio.run(body())


def test_save_translation_layer_stores_attribution():
    async def body():
        async with _env() as sm:
            async with sm() as s:
                await SyncRepository(s).create(
                    video_id=VIDEO, lyrics_hash="h1", timestamps=_seed_segments(["", ""])
                )
                await s.commit()

            await save_translation_layer(
                VIDEO,
                SaveTranslationLayerRequest(
                    target_lang="en",
                    lines=_layer_lines([("첫 줄", "First line"), ("둘째 줄", "Second line")]),
                    origin="wiki",
                    attribution=Attribution(
                        name="테스트 위키", url="https://example.test", license="CC BY-SA 4.0"
                    ),
                ),
            )

            async with sm() as s:
                layer = await TranslationLayerRepository(s).get_layer(VIDEO, FP, "en")
                assert layer.attribution == {
                    "name": "테스트 위키",
                    "url": "https://example.test",
                    "license": "CC BY-SA 4.0",
                    "source_id": None,
                }

    asyncio.run(body())


def test_save_translation_layer_rejects_low_match_rate():
    """lines의 text가 세그 원문과 절반 미만 일치하면 422 — 엉뚱한 가사 방지."""

    async def body():
        async with _env() as sm:
            async with sm() as s:
                await SyncRepository(s).create(
                    video_id=VIDEO, lyrics_hash="h1", timestamps=_seed_segments(["", ""])
                )
                await s.commit()

            with pytest.raises(HTTPException) as exc:
                await save_translation_layer(
                    VIDEO,
                    SaveTranslationLayerRequest(
                        target_lang="en",
                        # 2줄 중 1줄만 이 영상의 가사와 일치 — 50% 미만이 아니라 정확히
                        # 50%면 통과해야 하므로, 3줄 중 1줄(약 33%)로 명백히 미달시킨다
                        lines=_layer_lines(
                            [
                                ("첫 줄", "First line"),
                                ("전혀 다른 곡의 가사", "Wrong song"),
                                ("이것도 다른 곡", "Also wrong"),
                            ]
                        ),
                        origin="caption",
                    ),
                )
            assert exc.value.status_code == 422

            async with sm() as s:
                assert await TranslationLayerRepository(s).get_layer(VIDEO, FP, "en") is None

    asyncio.run(body())


def test_save_translation_layer_accepts_exactly_half_match_rate():
    # "절반 이상"은 정확히 50%도 포함한다(경계값)
    async def body():
        async with _env() as sm:
            async with sm() as s:
                await SyncRepository(s).create(
                    video_id=VIDEO, lyrics_hash="h1", timestamps=_seed_segments(["", ""])
                )
                await s.commit()

            resp = await save_translation_layer(
                VIDEO,
                SaveTranslationLayerRequest(
                    target_lang="en",
                    lines=_layer_lines([("첫 줄", "First line"), ("전혀 다른 가사", "Wrong")]),
                    origin="caption",
                ),
            )
            assert resp.saved is True
            assert (resp.matched, resp.total) == (1, 2)

    asyncio.run(body())


def test_save_translation_layer_rejects_unknown_origin():
    async def body():
        async with _env() as sm:
            async with sm() as s:
                await SyncRepository(s).create(
                    video_id=VIDEO, lyrics_hash="h1", timestamps=_seed_segments(["", ""])
                )
                await s.commit()

            with pytest.raises(HTTPException) as exc:
                await save_translation_layer(
                    VIDEO,
                    SaveTranslationLayerRequest(
                        target_lang="en",
                        lines=_layer_lines([("첫 줄", "First line"), ("둘째 줄", "Second")]),
                        origin="llm",  # 화이트리스트 밖 — 이 엔드포인트는 llm을 못 만든다
                    ),
                )
            assert exc.value.status_code == 422

    asyncio.run(body())


def test_save_translation_layer_overwrites_llm_layer_with_caption():
    """기존 레이어가 origin="llm"이면 caption이 교체할 수 있다."""

    async def body():
        async with _env() as sm:
            async with sm() as s:
                await SyncRepository(s).create(
                    video_id=VIDEO, lyrics_hash="h1", timestamps=_seed_segments(["", ""])
                )
                await TranslationLayerRepository(s).upsert_layer(
                    VIDEO,
                    FP,
                    "en",
                    lines=[{"text": "첫 줄", "translation": "machine translated"}],
                    attribution=None,
                    origin="llm",
                )
                await s.commit()

            resp = await save_translation_layer(
                VIDEO,
                SaveTranslationLayerRequest(
                    target_lang="en",
                    lines=_layer_lines([("첫 줄", "human caption"), ("둘째 줄", "second line")]),
                    origin="caption",
                ),
            )
            assert resp.saved is True

            async with sm() as s:
                layer = await TranslationLayerRepository(s).get_layer(VIDEO, FP, "en")
                assert layer.origin == "caption"
                assert layer.lines[0]["translation"] == "human caption"

    asyncio.run(body())


def test_save_translation_layer_re_updates_same_origin():
    """기존이 caption이고 새 요청도 caption이면(재수집·정정 등) 교체된다."""

    async def body():
        async with _env() as sm:
            async with sm() as s:
                await SyncRepository(s).create(
                    video_id=VIDEO, lyrics_hash="h1", timestamps=_seed_segments(["", ""])
                )
                await TranslationLayerRepository(s).upsert_layer(
                    VIDEO,
                    FP,
                    "en",
                    lines=[{"text": "첫 줄", "translation": "old caption text"}],
                    attribution=None,
                    origin="caption",
                )
                await s.commit()

            resp = await save_translation_layer(
                VIDEO,
                SaveTranslationLayerRequest(
                    target_lang="en",
                    lines=_layer_lines(
                        [("첫 줄", "corrected caption text"), ("둘째 줄", "second line")]
                    ),
                    origin="caption",
                ),
            )
            assert resp.saved is True

            async with sm() as s:
                layer = await TranslationLayerRepository(s).get_layer(VIDEO, FP, "en")
                assert layer.lines[0]["translation"] == "corrected caption text"

    asyncio.run(body())


def test_save_translation_layer_refuses_to_overwrite_a_different_human_origin():
    """기존이 "wiki"인데 "caption"으로 요청 — 사람이 확인한 위키 번역을 자동 자막이
    덮어쓰면 안 된다. 에러가 아니라 saved=false로 조용히 거절한다."""

    async def body():
        async with _env() as sm:
            async with sm() as s:
                await SyncRepository(s).create(
                    video_id=VIDEO, lyrics_hash="h1", timestamps=_seed_segments(["", ""])
                )
                await TranslationLayerRepository(s).upsert_layer(
                    VIDEO,
                    FP,
                    "en",
                    lines=[{"text": "첫 줄", "translation": "trusted wiki translation"}],
                    attribution={"name": "위키", "url": None, "license": None, "source_id": "wiki"},
                    origin="wiki",
                )
                await s.commit()

            resp = await save_translation_layer(
                VIDEO,
                SaveTranslationLayerRequest(
                    target_lang="en",
                    lines=_layer_lines([("첫 줄", "auto caption text"), ("둘째 줄", "second")]),
                    origin="caption",
                ),
            )
            assert resp.saved is False
            assert (resp.matched, resp.total) == (2, 2)  # 매칭률 계산은 그대로 응답에 실린다

            async with sm() as s:
                layer = await TranslationLayerRepository(s).get_layer(VIDEO, FP, "en")
                assert layer.origin == "wiki"  # 안 바뀜
                assert layer.lines[0]["translation"] == "trusted wiki translation"

    asyncio.run(body())


def test_save_translation_layer_uses_requested_video_id_not_source_for_linked_video():
    """링크로 빌려온 영상도 요청받은 video_id를 레이어 키로 쓴다 — source_video_id가 아니다."""

    async def body():
        async with _env() as sm:
            async with sm() as s:
                await SyncRepository(s).create(
                    video_id="SRCSRCSRC01",
                    lyrics_hash="h1",
                    timestamps=_seed_segments(["", ""]),
                )
                await s.commit()

            await create_sync_link(
                SyncLinkRequest(
                    video_id="DSTDSTDST01", source_video_id="SRCSRCSRC01", offset_sec=5.0
                )
            )

            resp = await save_translation_layer(
                "DSTDSTDST01",
                SaveTranslationLayerRequest(
                    target_lang="en",
                    lines=_layer_lines([("첫 줄", "First"), ("둘째 줄", "Second")]),
                    origin="caption",
                ),
            )
            assert resp.saved is True

            async with sm() as s:
                repo = TranslationLayerRepository(s)
                assert await repo.get_layer("DSTDSTDST01", FP, "en") is not None
                assert await repo.get_layer("SRCSRCSRC01", FP, "en") is None

            lookup = await get_sync("DSTDSTDST01", lang="en")
            assert lookup.linked is not None
            assert lookup.translation_lang == "en"
            assert [seg["translation"] for seg in lookup.timestamps] == ["First", "Second"]

    asyncio.run(body())


def test_save_translation_layer_404_when_no_sync():
    async def body():
        async with _env():
            with pytest.raises(HTTPException) as exc:
                await save_translation_layer(
                    "NOSYNCNOS01",
                    SaveTranslationLayerRequest(
                        target_lang="en",
                        lines=_layer_lines([("첫 줄", "First"), ("둘째 줄", "Second")]),
                        origin="caption",
                    ),
                )
            assert exc.value.status_code == 404

    asyncio.run(body())

    asyncio.run(body())
