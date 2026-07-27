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

from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from everyric2.server.api import translate as translate_api
from everyric2.server.api.sync import SyncLinkRequest, create_sync_link, get_sync
from everyric2.server.api.translate import TranslateRequest, translate_lyrics
from everyric2.server.db import connection as db_conn
from everyric2.server.db.models import Base
from everyric2.server.db.repository import SyncRepository, TranslationLayerRepository
from everyric2.server.text_fingerprint import lines_fingerprint

VIDEO = "LANGVID0001"
LINES = ["첫 줄", "둘째 줄"]
FP = lines_fingerprint(LINES)


@contextlib.asynccontextmanager
async def _env():
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
    try:
        yield sm
    finally:
        db_conn.async_session = orig
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
