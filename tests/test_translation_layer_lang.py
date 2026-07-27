"""번역 레이어의 언어 판정 — line_meta 번역의 언어 vs 요청자 언어(Job.target_lang).

실사고 재현: 영어 설정 사용자가 vocaro(한국어 번역이 함께 실린 위키) 가사로 생성하면
세그에 붙는 번역은 **한국어**인데, 레이어 언어를 요청자 언어로 정하던 구현은 그 한국어를
en 레이어에 기록하고 legacy 슬롯에서는 벗겨 버렸다. 결과: ``lang=en`` 조회가 한국어 번역을
``translation_lang="en"``으로 내주고, 확장은 «내 언어 번역이 있다»고 보고 영어를 영영
요청하지 않았다. 한국어 번역은 legacy에서도 사라져 ko 사용자까지 잃었다.

올바른 규칙은 «담긴 것에 맞는 라벨»이다: 판정 기준은 전부 line_meta_lang이고 target_lang은
진단 로그에만 쓴다. 그래야 lang=en 조회가 비어 나가고(translation_lang=None) 확장이 영어를
요청한다.

검증은 **원격 워커 결과 수신부(submit_result)와 캐시 완결(cache_check)을 실제로 태워서**
한다 — 프로덕션 생성이 이 경로다. 격리는 tests/test_worker_pool.py와 같은 방식(인메모리
SQLite로 connection.async_session 몽키패치 + 라우트 코루틴 직접 호출).
"""
import asyncio
import contextlib

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from everyric2 import __version__
from everyric2.config.settings import get_settings
from everyric2.server import worker as worker_core
from everyric2.server.api import worker as worker_api
from everyric2.server.api.worker import (
    CacheCheckRequest,
    ClaimRequest,
    ResultRequest,
    cache_check,
    claim_job,
    submit_result,
)
from everyric2.server.db import connection as db_conn
from everyric2.server.db.models import Base
from everyric2.server.db.repository import (
    JobRepository,
    SyncRepository,
    TranslationLayerRepository,
    hash_lyrics,
)
from everyric2.server.text_fingerprint import lines_fingerprint

WKEY = "test-worker-key"
WID = "worker-A"

LYRICS = "アルバイトはネクラモード\n背負った"
# vocaro가 들고 오는 모양 — 원문 + 한국어 번역이 함께 실려 온다
KO_META = [
    {"text": "アルバイトはネクラモード", "translation": "아르바이트는 네쿠라 모드"},
    {"text": "背負った", "translation": "짊어졌다"},
]
EN_META = [
    {"text": "アルバイトはネクラモード", "translation": "Part-time job, gloomy mode"},
    {"text": "背負った", "translation": "I carried it"},
]


def _timestamps(meta):
    """워커가 돌려주는 직렬화 결과 — line_meta 번역이 세그에 이미 병합된 상태."""
    return [
        {"text": m["text"], "start": i * 2.0, "end": i * 2.0 + 1.5, "translation": m["translation"]}
        for i, m in enumerate(meta)
    ]


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

    server = get_settings().server
    prev_key = server.worker_key
    object.__setattr__(server, "worker_key", WKEY)
    _clear_globals()
    try:
        yield sm
    finally:
        db_conn.async_session = orig
        object.__setattr__(server, "worker_key", prev_key)
        _clear_globals()
        await engine.dispose()


def _clear_globals():
    worker_api._LEASES.clear()
    worker_core._CANCEL_REQUESTED.clear()
    worker_core._PENDING_LINE_META.clear()
    worker_core._PENDING_LINE_META_LANG.clear()
    worker_core._PENDING_ATTRIBUTION.clear()
    worker_core._PENDING_FORCE.clear()


async def _seed_job(sm, video_id, target_lang="ko", lyrics=LYRICS):
    async with sm() as s:
        job = await JobRepository(s).create(
            video_id=video_id, lyrics=lyrics, target_lang=target_lang
        )
        await JobRepository(s).update_status(job.id, "queued", progress=0)
        await s.commit()
        return job.id


async def _layer(sm, video_id, texts, lang):
    async with sm() as s:
        return await TranslationLayerRepository(s).get_layer(
            video_id, lines_fingerprint(texts), lang
        )


async def _stored_segments(sm, video_id):
    async with sm() as s:
        rows = await SyncRepository(s).get_by_video(video_id)
        assert len(rows) == 1
        stored = rows[0].timestamps
        return stored["segments"] if isinstance(stored, dict) else stored


# ── 원격 워커 결과 수신부 (프로덕션 생성 경로) ──────────────────────


def test_korean_line_meta_for_an_english_requester_records_the_ko_layer():
    """이 파일의 존재 이유 — 한국어 번역이 en 레이어에 박히면 안 된다."""

    async def body():
        async with _env() as sm:
            job_id = await _seed_job(sm, "VIDKOENxx01", target_lang="en")
            worker_core.stash_line_meta(job_id, KO_META, "ko")
            await claim_job(ClaimRequest(worker_id=WID, version=__version__), x_worker_key=WKEY)
            await submit_result(
                job_id,
                ResultRequest(timestamps=_timestamps(KO_META), audio_hash="h1"),
                x_worker_key=WKEY, x_worker_id=WID,
            )

            texts = [m["text"] for m in KO_META]
            ko = await _layer(sm, "VIDKOENxx01", texts, "ko")
            assert ko is not None, "한국어 번역은 ko 레이어에 있어야 한다"
            assert ko.lines[0]["translation"] == "아르바이트는 네쿠라 모드"

            # en 레이어는 생기지 않는다 → lang=en 조회가 비어 나가 확장이 영어를 요청한다
            assert await _layer(sm, "VIDKOENxx01", texts, "en") is None

            # legacy 슬롯은 유지 — ko 번역은 구버전 확장과 ko 사용자에게 그대로 유효하다
            segs = await _stored_segments(sm, "VIDKOENxx01")
            assert [s.get("translation") for s in segs] == [
                "아르바이트는 네쿠라 모드",
                "짊어졌다",
            ]

    asyncio.run(body())


def test_english_line_meta_records_the_en_layer_and_strips_legacy():
    async def body():
        async with _env() as sm:
            job_id = await _seed_job(sm, "VIDENENxx01", target_lang="en")
            worker_core.stash_line_meta(job_id, EN_META, "en")
            await claim_job(ClaimRequest(worker_id=WID, version=__version__), x_worker_key=WKEY)
            await submit_result(
                job_id,
                ResultRequest(timestamps=_timestamps(EN_META), audio_hash="h2"),
                x_worker_key=WKEY, x_worker_id=WID,
            )

            texts = [m["text"] for m in EN_META]
            en = await _layer(sm, "VIDENENxx01", texts, "en")
            assert en is not None
            assert en.lines[0]["translation"] == "Part-time job, gloomy mode"
            assert await _layer(sm, "VIDENENxx01", texts, "ko") is None

            # 비ko 번역은 legacy에서 비운다 — lang 없이 조회한 ko 사용자가 영어를 받지 않게
            segs = await _stored_segments(sm, "VIDENENxx01")
            assert all("translation" not in s for s in segs)

    asyncio.run(body())


def test_legacy_request_without_a_stashed_lang_behaves_exactly_as_before():
    """lang을 안 싣는 구버전 생성 요청 — ko 레이어 + legacy 유지(기존 동작)."""

    async def body():
        async with _env() as sm:
            job_id = await _seed_job(sm, "VIDOLDxxx01")
            worker_core.stash_line_meta(job_id, KO_META)  # lang 인자 없음
            await claim_job(ClaimRequest(worker_id=WID, version=__version__), x_worker_key=WKEY)
            await submit_result(
                job_id,
                ResultRequest(timestamps=_timestamps(KO_META), audio_hash="h3"),
                x_worker_key=WKEY, x_worker_id=WID,
            )

            texts = [m["text"] for m in KO_META]
            assert await _layer(sm, "VIDOLDxxx01", texts, "ko") is not None
            segs = await _stored_segments(sm, "VIDOLDxxx01")
            assert segs[0]["translation"] == "아르바이트는 네쿠라 모드"

    asyncio.run(body())


def test_target_lang_alone_never_strips_the_legacy_slot():
    """스태시가 아예 없는 잡(원문만 정렬) — target_lang=en이어도 세그를 건드리지 않는다.

    구버전 규칙은 여기서 legacy를 벗겨, line_meta 없이 만들어진 싱크의 번역까지 지웠다.
    """

    async def body():
        async with _env() as sm:
            job_id = await _seed_job(sm, "VIDNOMETA01", target_lang="en")
            await claim_job(ClaimRequest(worker_id=WID, version=__version__), x_worker_key=WKEY)
            await submit_result(
                job_id,
                ResultRequest(timestamps=_timestamps(KO_META), audio_hash="h4"),
                x_worker_key=WKEY, x_worker_id=WID,
            )

            segs = await _stored_segments(sm, "VIDNOMETA01")
            assert segs[0]["translation"] == "아르바이트는 네쿠라 모드"

    asyncio.run(body())


# ── 캐시 완결 경로 ────────────────────────────────────────────────


def test_cache_reuse_keeps_korean_meta_for_an_english_requester():
    """캐시 재사용도 같은 기준 — ko 번역이면 legacy에 병합하고 ko 레이어에 남긴다."""

    async def body():
        async with _env() as sm:
            async with sm() as s:
                await SyncRepository(s).create(
                    video_id="VIDCACHE001",
                    lyrics_hash=hash_lyrics(LYRICS),
                    timestamps=[
                        {"text": m["text"], "start": 0.0, "end": 1.0} for m in KO_META
                    ],
                    audio_hash="cachedhash",
                )
                await s.commit()
            job_id = await _seed_job(sm, "VIDCACHE001", target_lang="en")
            worker_core.stash_line_meta(job_id, KO_META, "ko")
            await claim_job(ClaimRequest(worker_id=WID, version=__version__), x_worker_key=WKEY)

            resp = await cache_check(
                job_id, CacheCheckRequest(audio_hash="cachedhash"),
                x_worker_key=WKEY, x_worker_id=WID,
            )
            assert resp.completed is True

            texts = [m["text"] for m in KO_META]
            assert await _layer(sm, "VIDCACHE001", texts, "ko") is not None
            assert await _layer(sm, "VIDCACHE001", texts, "en") is None
            segs = await _stored_segments(sm, "VIDCACHE001")
            assert segs[0]["translation"] == "아르바이트는 네쿠라 모드"

    asyncio.run(body())


def test_cache_reuse_does_not_merge_english_meta_into_the_shared_row():
    """비ko 번역은 legacy에 안 넣는다 — 이 행은 다른 언어 사용자와 공유된다."""

    async def body():
        async with _env() as sm:
            async with sm() as s:
                await SyncRepository(s).create(
                    video_id="VIDCACHE002",
                    lyrics_hash=hash_lyrics(LYRICS),
                    timestamps=[
                        {"text": m["text"], "start": 0.0, "end": 1.0} for m in KO_META
                    ],
                    audio_hash="cachedhash2",
                )
                await s.commit()
            job_id = await _seed_job(sm, "VIDCACHE002", target_lang="en")
            worker_core.stash_line_meta(job_id, EN_META, "en")
            await claim_job(ClaimRequest(worker_id=WID, version=__version__), x_worker_key=WKEY)

            await cache_check(
                job_id, CacheCheckRequest(audio_hash="cachedhash2"),
                x_worker_key=WKEY, x_worker_id=WID,
            )

            texts = [m["text"] for m in EN_META]
            assert await _layer(sm, "VIDCACHE002", texts, "en") is not None
            segs = await _stored_segments(sm, "VIDCACHE002")
            assert all("translation" not in s for s in segs)

    asyncio.run(body())


# ── 스태시 계약 ──────────────────────────────────────────────────


def test_stash_lang_defaults_to_ko_and_round_trips():
    _clear_globals()
    try:
        worker_core.stash_line_meta("job-a", KO_META)
        assert worker_core.peek_line_meta_lang("job-a") == "ko"

        worker_core.stash_line_meta("job-b", EN_META, "en")
        assert worker_core.peek_line_meta_lang("job-b") == "en"

        # 공백·빈 문자열은 ko로 정규화 (구버전 호출부·빈 필드 방어)
        worker_core.stash_line_meta("job-c", EN_META, "  ")
        assert worker_core.peek_line_meta_lang("job-c") == "ko"

        # 스태시가 없는 잡은 ko
        assert worker_core.peek_line_meta_lang("job-none") == "ko"

        # 기본값은 dict에 남기지 않는다 (lang을 안 넘기는 원격 워커 프로세스에 잔여물 0)
        assert "job-a" not in worker_core._PENDING_LINE_META_LANG
        # 비ko였다가 ko로 되쓰면 이전 언어가 지워진다
        worker_core.stash_line_meta("job-b", KO_META, "ko")
        assert worker_core.peek_line_meta_lang("job-b") == "ko"
    finally:
        _clear_globals()


def test_ignored_empty_resend_keeps_both_the_meta_and_its_lang():
    # 빈 재전송을 무시하는 기존 계약이 언어에도 그대로 적용돼야 한다 —
    # 언어만 "ko"로 되돌아가면 지켜 낸 en 메타가 ko 레이어로 잘못 기록된다.
    _clear_globals()
    try:
        worker_core.stash_line_meta("job-d", EN_META, "en")
        worker_core.stash_line_meta("job-d", [])

        assert worker_core._PENDING_LINE_META["job-d"] == EN_META
        assert worker_core.peek_line_meta_lang("job-d") == "en"
    finally:
        _clear_globals()


def test_terminal_cleanup_clears_the_lang_stash():
    _clear_globals()
    try:
        worker_core.stash_line_meta("job-e", EN_META, "en")
        worker_api._pop_stashes("job-e")

        assert "job-e" not in worker_core._PENDING_LINE_META
        assert "job-e" not in worker_core._PENDING_LINE_META_LANG
    finally:
        _clear_globals()
