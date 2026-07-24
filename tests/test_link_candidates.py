"""커버 온디맨드 자동 연결 — 제목 매칭·후보 선정·쿨다운·제목 백필 테스트.

기존 서버 테스트 규약을 그대로 따른다: 격리된 in-memory SQLite로 connection.async_session을
몽키패치하고 라우트 코루틴을 직접 await(asyncio.run). httpx/TestClient는 쓰지 않는다.

여기서 검증하는 계약의 핵심: **제목은 후보 발견에만 쓰이고, SyncLink는 절대 만들지 않는다.**
제목이 완벽히 일치해도 결과는 '검증 잡 제출'까지이며 링크 생성은 반주 상관 판정의 몫이다.
"""

import asyncio
import contextlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from everyric2.config.settings import get_settings
from everyric2.server import title_match
from everyric2.server import worker as worker_core
from everyric2.server.api.sync import (
    GenerateRequest,
    SyncLinkRequest,
    create_sync_link,
    find_link_candidates,
    generate_sync,
    get_sync,
)
from everyric2.server.db import connection as db_conn
from everyric2.server.db.models import Base, LinkJob
from everyric2.server.db.repository import (
    LinkJobRepository,
    SyncLinkRepository,
    SyncRepository,
)

COVER = "COVERvideo1"
SOURCE = "SOURCEvid01"
OTHER = "OTHERvid001"

SOURCE_TITLE = "熱異常 / いよわ feat.初音ミク"
COVER_TITLE = "熱異常 歌ってみた【足立レイ】"


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
    try:
        yield sm
    finally:
        db_conn.async_session = orig
        for k, v in saved.items():
            object.__setattr__(server, k, v)
        worker_core._PENDING_TITLE.clear()
        await engine.dispose()


async def _seed_sync(sm, video_id, title=None, artist=None, lyrics_hash="h1"):
    async with sm() as s:
        row = await SyncRepository(s).create(
            video_id=video_id,
            lyrics_hash=lyrics_hash,
            timestamps=[{"text": "라인", "start": 1.0, "end": 2.0}],
            engine="ctc",
            title=title,
            artist=artist,
        )
        await s.commit()
        return row.id


async def _count_link_jobs(sm) -> int:
    async with sm() as s:
        return len((await s.execute(select(LinkJob))).scalars().all())


# ── title_match: 정규화·잡토큰 제거 ───────────────────────────────


def test_normalize_title_strips_spaces_and_symbols():
    assert title_match.normalize_title("Roki ロキ!") == "rokiロキ"


def test_strip_noise_tokens_removes_upload_conventions():
    for raw, kept in [
        ("熱異常 Official MV", "熱異常"),
        ("熱異常 歌ってみた", "熱異常"),
        ("熱異常 (Instrumental)", "熱異常"),
        ("熱異常 off vocal", "熱異常"),
        ("熱異常 커버", "熱異常"),
        ("Roki Music Video", "roki"),
    ]:
        assert title_match.normalize_title(title_match.strip_noise_tokens(raw)) == kept


def test_candidate_queries_keeps_index_behaviour_without_noise_drop():
    # drop_noise 기본값 False는 곡 인덱스 매칭의 기존 동작을 그대로 보존한다
    plain = title_match.candidate_queries("熱異常 Official MV")
    assert "熱異常" not in plain
    with_noise_drop = title_match.candidate_queries("熱異常 Official MV", drop_noise=True)
    assert "熱異常" in with_noise_drop


# ── title_match: 매칭 점수 ────────────────────────────────────────


def test_match_score_exact_after_noise_strip():
    score, _ = title_match.match_score("熱異常 Official MV", "熱異常")
    assert score == 1.0


def test_match_score_matches_cover_against_original_full_title():
    score, _ = title_match.match_score(COVER_TITLE, SOURCE_TITLE)
    assert score == 1.0


def test_match_score_unrelated_titles_return_none():
    assert title_match.match_score("熱異常", "全然違う曲名です") is None


def test_match_score_artist_fragment_overlap_stays_below_default_threshold():
    """«【初音ミク】곡명» 류에서 가수명 조각이 다른 곡 제목에 포함되며 생기는 오탐 —
    점수가 기본 하한(0.6) 아래라 후보로 승격되지 않는다."""
    hit = title_match.match_score("【初音ミク】熱異常", "初音ミクの消失")
    assert hit is not None
    assert hit[0] < 0.6


def test_rank_matches_orders_exact_before_partial_and_honours_min_score():
    entries = [("v_partial", "初音ミクの消失"), ("v_exact", "熱異常")]
    ranked = title_match.rank_matches("【初音ミク】熱異常", entries, min_score=0.5)
    assert ranked[0][0] == "v_exact"
    assert ranked[0][1] == 1.0
    # 하한을 올리면 부분 겹침 후보가 탈락한다
    tight = title_match.rank_matches("【初音ミク】熱異常", entries, min_score=0.6)
    assert [key for key, _ in tight] == ["v_exact"]


# ── 후보 탐색 엔드포인트 ──────────────────────────────────────────


def test_candidates_submits_link_job_for_top_match():
    async def body():
        async with _env() as sm:
            await _seed_sync(sm, SOURCE, title=SOURCE_TITLE)
            resp = await find_link_candidates(COVER, title=COVER_TITLE)
            assert resp.status == "submitted"
            assert [c.video_id for c in resp.candidates] == [SOURCE]
            assert resp.candidates[0].score == 1.0
            assert resp.followup == "link_validate"
            assert resp.job_id
            async with sm() as s:
                lj = await LinkJobRepository(s).get_by_id(resp.job_id)
                assert lj.video_id == COVER and lj.source_video_id == SOURCE
                assert lj.status == "queued"
                # 제목이 완벽히 일치해도 링크는 만들어지지 않는다 — 판정은 반주 상관의 몫
                assert await SyncLinkRepository(s).get(COVER) is None

    asyncio.run(body())


def test_candidates_excludes_self_and_unrelated_titles():
    async def body():
        async with _env() as sm:
            # 자기 자신이 코퍼스에 제목만 있는 다른 lyrics_hash로 들어 있어도 후보가 되면 안 된다
            await _seed_sync(sm, OTHER, title="全然違う曲名です")
            resp = await find_link_candidates(COVER, title=COVER_TITLE)
            assert resp.status == "none"
            assert resp.candidates == []
            assert await _count_link_jobs(sm) == 0

    asyncio.run(body())


def test_candidates_skips_when_video_has_own_sync():
    async def body():
        async with _env() as sm:
            await _seed_sync(sm, SOURCE, title=SOURCE_TITLE)
            await _seed_sync(sm, COVER, title=None)
            resp = await find_link_candidates(COVER, title=COVER_TITLE, artist="足立レイ")
            assert resp.status == "has_sync"
            assert resp.candidates == []
            assert await _count_link_jobs(sm) == 0
            # 자기 싱크가 있는 경우 이 호출은 제목 백필로 쓰인다
            async with sm() as s:
                rows = await SyncRepository(s).get_by_video(COVER)
                assert rows[0].title == COVER_TITLE
                assert rows[0].artist == "足立レイ"

    asyncio.run(body())


def test_candidates_skips_when_link_already_exists():
    async def body():
        async with _env() as sm:
            await _seed_sync(sm, SOURCE, title=SOURCE_TITLE)
            async with sm() as s:
                await SyncLinkRepository(s).upsert(COVER, SOURCE, 1.0, verified=True)
                await s.commit()
            resp = await find_link_candidates(COVER, title=COVER_TITLE)
            assert resp.status == "linked"
            assert await _count_link_jobs(sm) == 0

    asyncio.run(body())


def test_candidates_disabled_returns_candidates_without_job():
    async def body():
        async with _env(auto_link_candidates=False) as sm:
            await _seed_sync(sm, SOURCE, title=SOURCE_TITLE)
            resp = await find_link_candidates(COVER, title=COVER_TITLE)
            assert resp.status == "disabled"
            assert [c.video_id for c in resp.candidates] == [SOURCE]
            assert resp.job_id is None
            assert resp.followup is None  # 아무 후속 작업도 내지 않았다
            assert await _count_link_jobs(sm) == 0

    asyncio.run(body())


def test_candidates_rejects_malformed_video_id():
    async def body():
        async with _env():
            with pytest.raises(HTTPException) as e:
                await find_link_candidates("too-short", title=COVER_TITLE)
            assert e.value.status_code == 422

    asyncio.run(body())


# ── 재제출 억제: 진행 중 병합 + 완료/실패 쿨다운 ──────────────────


def test_candidates_merges_into_active_job():
    async def body():
        async with _env() as sm:
            await _seed_sync(sm, SOURCE, title=SOURCE_TITLE)
            first = await find_link_candidates(COVER, title=COVER_TITLE)
            second = await find_link_candidates(COVER, title=COVER_TITLE)
            assert second.status == "pending"
            assert second.job_id == first.job_id
            assert await _count_link_jobs(sm) == 1

    asyncio.run(body())


@pytest.mark.parametrize("finished_status", ["done", "failed"])
def test_candidates_respect_cooldown_after_finished_attempt(finished_status):
    async def body():
        async with _env(link_retry_cooldown_days=14) as sm:
            await _seed_sync(sm, SOURCE, title=SOURCE_TITLE)
            async with sm() as s:
                lj = await LinkJobRepository(s).create(COVER, SOURCE)
                lj.status = finished_status
                await s.commit()
                finished_id = lj.id

            resp = await find_link_candidates(COVER, title=COVER_TITLE)
            assert resp.status == "cooldown"
            assert resp.job_id == finished_id
            # 새 잡을 만들지 않는다 — 같은 영상을 반복해 열어도 GPU가 다시 돌지 않는다
            assert await _count_link_jobs(sm) == 1

    asyncio.run(body())


def test_candidates_resubmit_after_cooldown_expires():
    async def body():
        async with _env(link_retry_cooldown_days=14) as sm:
            await _seed_sync(sm, SOURCE, title=SOURCE_TITLE)
            stale = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
            async with sm() as s:
                s.add(
                    LinkJob(
                        video_id=COVER,
                        source_video_id=SOURCE,
                        status="done",
                        match=False,
                        created_at=stale,
                    )
                )
                await s.commit()

            resp = await find_link_candidates(COVER, title=COVER_TITLE)
            assert resp.status == "submitted"
            assert await _count_link_jobs(sm) == 2

    asyncio.run(body())


def test_cooldown_disabled_when_days_zero():
    async def body():
        async with _env(link_retry_cooldown_days=0) as sm:
            await _seed_sync(sm, SOURCE, title=SOURCE_TITLE)
            async with sm() as s:
                lj = await LinkJobRepository(s).create(COVER, SOURCE)
                lj.status = "done"
                await s.commit()

            resp = await find_link_candidates(COVER, title=COVER_TITLE)
            assert resp.status == "submitted"
            assert await _count_link_jobs(sm) == 2

    asyncio.run(body())


def test_cooldown_only_applies_to_the_same_pair():
    async def body():
        async with _env(link_retry_cooldown_days=14) as sm:
            await _seed_sync(sm, SOURCE, title=SOURCE_TITLE)
            async with sm() as s:
                lj = await LinkJobRepository(s).create(COVER, OTHER)
                lj.status = "done"
                await s.commit()

            resp = await find_link_candidates(COVER, title=COVER_TITLE)
            assert resp.status == "submitted"

    asyncio.run(body())


# ── 제목 백필 ─────────────────────────────────────────────────────


def test_get_sync_backfills_missing_title():
    async def body():
        async with _env() as sm:
            await _seed_sync(sm, SOURCE, title=None)
            resp = await get_sync(SOURCE, title=SOURCE_TITLE, artist="いよわ")
            assert resp.found is True
            async with sm() as s:
                row = (await SyncRepository(s).get_by_video(SOURCE))[0]
                assert row.title == SOURCE_TITLE
                assert row.artist == "いよわ"

    asyncio.run(body())


def test_get_sync_never_overwrites_existing_title():
    async def body():
        async with _env() as sm:
            await _seed_sync(sm, SOURCE, title="원래 제목", artist="원래 아티스트")
            await get_sync(SOURCE, title="다른 제목", artist="다른 아티스트")
            async with sm() as s:
                row = (await SyncRepository(s).get_by_video(SOURCE))[0]
                assert row.title == "원래 제목"
                assert row.artist == "원래 아티스트"

    asyncio.run(body())


def test_get_sync_backfill_targets_hash_matched_row():
    async def body():
        async with _env() as sm:
            await _seed_sync(sm, SOURCE, title=None, lyrics_hash="hh")
            await get_sync(SOURCE, lyrics_hash="hh", title=SOURCE_TITLE)
            async with sm() as s:
                row = (await SyncRepository(s).get_by_video(SOURCE))[0]
                assert row.title == SOURCE_TITLE

    asyncio.run(body())


def test_get_sync_does_not_stamp_cover_title_onto_linked_source():
    async def body():
        async with _env() as sm:
            await _seed_sync(sm, SOURCE, title=None)
            async with sm() as s:
                await SyncLinkRepository(s).upsert(COVER, SOURCE, 2.0, verified=True)
                await s.commit()
            resp = await get_sync(COVER, title=COVER_TITLE)
            assert resp.linked["source_video_id"] == SOURCE
            async with sm() as s:
                row = (await SyncRepository(s).get_by_video(SOURCE))[0]
                assert row.title is None  # 커버 제목이 원곡 행에 새겨지면 안 된다

    asyncio.run(body())


def test_generate_backfills_title_on_existing_sync():
    async def body():
        async with _env() as sm:
            from everyric2.server.db.repository import hash_lyrics

            lyrics = "가사 한 줄"
            await _seed_sync(sm, SOURCE, title=None, lyrics_hash=hash_lyrics(lyrics))
            resp = await generate_sync(
                GenerateRequest(
                    video_id=SOURCE, lyrics=lyrics, title=SOURCE_TITLE, artist="いよわ"
                ),
                BackgroundTasks(),
            )
            assert resp.status == "completed"
            async with sm() as s:
                row = (await SyncRepository(s).get_by_video(SOURCE))[0]
                assert row.title == SOURCE_TITLE

    asyncio.run(body())


def test_generate_stashes_title_for_new_job():
    async def body():
        async with _env(local_worker=False) as sm:
            resp = await generate_sync(
                GenerateRequest(
                    video_id=COVER, lyrics="새 가사", title=COVER_TITLE, artist="足立レイ"
                ),
                BackgroundTasks(),
            )
            assert resp.status == "processing"
            assert worker_core.peek_title(resp.job_id) == (COVER_TITLE, "足立レイ")
            _ = sm

    asyncio.run(body())


# ── 수동 링크 안전장치 ────────────────────────────────────────────


def test_manual_link_is_recorded_unverified():
    async def body():
        async with _env() as sm:
            await _seed_sync(sm, SOURCE, title=SOURCE_TITLE)
            link = await create_sync_link(
                SyncLinkRequest(video_id=COVER, source_video_id=SOURCE, offset_sec=0.0)
            )
            assert link.verified is False
            resp = await get_sync(COVER)
            assert resp.linked["verified"] is False

    asyncio.run(body())


def test_manual_link_admin_gate_rejects_without_key():
    async def body():
        async with _env(manual_link_requires_admin=True, admin_api_key="adminkey") as sm:
            await _seed_sync(sm, SOURCE, title=SOURCE_TITLE)
            with pytest.raises(HTTPException) as e:
                await create_sync_link(
                    SyncLinkRequest(video_id=COVER, source_video_id=SOURCE, offset_sec=0.0)
                )
            assert e.value.status_code == 403
            # 어드민 키를 제시하면 통과한다
            link = await create_sync_link(
                SyncLinkRequest(video_id=COVER, source_video_id=SOURCE, offset_sec=0.0),
                x_api_key="adminkey",
            )
            assert link.verified is False

    asyncio.run(body())


def test_manual_link_gate_off_by_default():
    async def body():
        async with _env() as sm:
            await _seed_sync(sm, SOURCE, title=SOURCE_TITLE)
            link = await create_sync_link(
                SyncLinkRequest(video_id=COVER, source_video_id=SOURCE, offset_sec=0.0)
            )
            assert link.source_video_id == SOURCE

    asyncio.run(body())


def test_synclink_upsert_persists_rate_on_insert():
    """신규 삽입 시 rate가 누락돼 배속 링크가 1.0으로 저장되던 회귀 방지."""

    async def body():
        async with _env() as sm:
            async with sm() as s:
                link = await SyncLinkRepository(s).upsert(COVER, SOURCE, 1.5, rate=1.25)
                assert link.rate == 1.25
                await s.commit()
            async with sm() as s:
                assert (await SyncLinkRepository(s).get(COVER)).rate == 1.25

    asyncio.run(body())
