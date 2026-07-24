import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from everyric2.server.db.models import (
    ActionLog,
    Job,
    LinkJob,
    SyncLink,
    SyncResult,
    VideoOffset,
)


def hash_lyrics(lyrics: str) -> str:
    return hashlib.sha256(lyrics.strip().encode()).hexdigest()[:16]


class SyncRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_video_and_hash(self, video_id: str, lyrics_hash: str) -> SyncResult | None:
        # force 재생성은 같은 (video_id, lyrics_hash) 행을 여러 개 만들 수 있다 — 최신 우선
        result = await self.session.execute(
            select(SyncResult)
            .where(
                SyncResult.video_id == video_id,
                SyncResult.lyrics_hash == lyrics_hash,
            )
            .order_by(SyncResult.created_at.desc())
        )
        return result.scalars().first()

    async def get_by_audio_hash(self, audio_hash: str) -> SyncResult | None:
        result = await self.session.execute(
            select(SyncResult)
            .where(SyncResult.audio_hash == audio_hash)
            .order_by(SyncResult.created_at.desc())
        )
        return result.scalar_one_or_none()

    async def get_by_audio_and_lyrics_hash(
        self, audio_hash: str, lyrics_hash: str
    ) -> SyncResult | None:
        # force 재생성으로 동일 해시 행이 복수 존재할 수 있다 — 최신 우선
        result = await self.session.execute(
            select(SyncResult)
            .where(
                SyncResult.audio_hash == audio_hash,
                SyncResult.lyrics_hash == lyrics_hash,
            )
            .order_by(SyncResult.created_at.desc())
        )
        return result.scalars().first()

    async def get_by_video(self, video_id: str) -> list[SyncResult]:
        result = await self.session.execute(
            select(SyncResult)
            .where(SyncResult.video_id == video_id)
            .order_by(SyncResult.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_all_unique_videos(self, limit: int = 50) -> list[SyncResult]:
        """Get one sync result per unique video_id, ordered by most recent."""
        from sqlalchemy import func

        # Subquery to get max created_at for each video_id
        subquery = (
            select(SyncResult.video_id, func.max(SyncResult.created_at).label("max_created"))
            .group_by(SyncResult.video_id)
            .subquery()
        )

        # Join to get full SyncResult rows
        result = await self.session.execute(
            select(SyncResult)
            .join(
                subquery,
                (SyncResult.video_id == subquery.c.video_id)
                & (SyncResult.created_at == subquery.c.max_created),
            )
            .order_by(SyncResult.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_titled(self, limit: int = 500) -> list[SyncResult]:
        """title이 채워진 싱크를 영상별 1건(최신)으로 — 링크 후보 전수 스캔용.

        created_at이 초 단위 문자열이라 같은 초에 만들어진 동일 영상 행이 둘 다 걸릴 수
        있어 파이썬에서 한 번 더 dedupe한다."""
        subquery = (
            select(SyncResult.video_id, func.max(SyncResult.created_at).label("max_created"))
            .where(SyncResult.title.is_not(None))
            .group_by(SyncResult.video_id)
            .subquery()
        )
        result = await self.session.execute(
            select(SyncResult)
            .join(
                subquery,
                (SyncResult.video_id == subquery.c.video_id)
                & (SyncResult.created_at == subquery.c.max_created),
            )
            .where(SyncResult.title.is_not(None))
            .order_by(SyncResult.created_at.desc())
            .limit(limit * 2)
        )
        seen: set[str] = set()
        rows: list[SyncResult] = []
        for row in result.scalars().all():
            if row.video_id in seen:
                continue
            seen.add(row.video_id)
            rows.append(row)
            if len(rows) >= limit:
                break
        return rows

    async def set_title_if_missing(
        self, sync_result: SyncResult, title: str | None, artist: str | None = None
    ) -> bool:
        """title이 비어 있을 때만 조용히 채운다 (기존 값은 절대 덮어쓰지 않는다).

        기회적 백필용 — 조회 요청이 제목을 실어 보내면 재생성 없이 기존 코퍼스에 제목이
        쌓인다. 채웠으면 True."""
        if not title or sync_result.title:
            return False
        sync_result.title = title.strip()[:256]
        if artist and not sync_result.artist:
            sync_result.artist = artist.strip()[:128]
        await self.session.flush()
        return True

    async def delete_by_video(self, video_id: str) -> int:
        """이 영상의 모든 싱크 삭제(초기화) — 잘못 붙여넣은 가사 등에서 완전히 새로 시작.
        삭제된 행 수를 반환."""
        result = await self.session.execute(
            delete(SyncResult).where(SyncResult.video_id == video_id)
        )
        return result.rowcount or 0

    async def create(
        self,
        video_id: str,
        lyrics_hash: str,
        timestamps: list[dict[str, Any]],
        language: str | None = None,
        engine: str = "ctc",
        quality_score: float | None = None,
        audio_hash: str | None = None,
        extra: dict[str, Any] | None = None,
        title: str | None = None,
        artist: str | None = None,
    ) -> SyncResult:
        sync_result = SyncResult(
            video_id=video_id,
            lyrics_hash=lyrics_hash,
            audio_hash=audio_hash,
            # extra: segments 밖의 곡 단위 부가정보 (예: {"debug": {...}})
            timestamps={"segments": timestamps, **(extra or {})},
            language=language,
            engine=engine,
            quality_score=quality_score,
            title=(title.strip()[:256] if title else None),
            artist=(artist.strip()[:128] if artist else None),
        )
        self.session.add(sync_result)
        await self.session.flush()
        return sync_result


class JobRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, job_id: str) -> Job | None:
        result = await self.session.execute(select(Job).where(Job.id == job_id))
        return result.scalar_one_or_none()

    async def get_pending(self, limit: int = 10) -> list[Job]:
        result = await self.session.execute(
            select(Job).where(Job.status == "pending").order_by(Job.created_at).limit(limit)
        )
        return list(result.scalars().all())

    async def get_oldest_queued(self) -> Job | None:
        """가장 오래 대기(queued)한 잡 — 원격 워커 claim이 FIFO로 하나씩 가져간다."""
        result = await self.session.execute(
            select(Job).where(Job.status == "queued").order_by(Job.created_at).limit(1)
        )
        return result.scalar_one_or_none()

    async def get_active_by_video(self, video_id: str, lyrics_hash: str) -> Job | None:
        """같은 영상·같은 가사로 이미 진행 중(pending/processing)인 잡 — 중복 생성 차단용.

        같은 잡이 2개 돌면 같은 임시 오디오 파일을 두 프로세스가 잡아 Windows에서
        WinError 32(파일 잠금)로 다운로드가 깨진다 — 생성 요청은 진행 중 잡에 합류시킨다.
        """
        result = await self.session.execute(
            select(Job)
            .where(
                Job.video_id == video_id,
                Job.lyrics_hash == lyrics_hash,
                Job.status.in_(["pending", "queued", "processing"]),
            )
            .order_by(Job.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def count_queued_before(self, created_at, exclude_id: str | None = None) -> int:
        """대기열 순번 계산 — 나보다 먼저 등록된 대기(queued) 잡 수.

        created_at은 server_default=func.now()라 SQLite에 초 단위 문자열로 저장되는데,
        파이썬 datetime 바인딩은 마이크로초까지 붙어 문자열 비교에서 자기 자신이
        "나보다 먼저"로 세어졌다 (첫 대기 잡이 대기열 2번으로 표시). `<=` + 자기 id
        제외로 바로잡는다 — 같은 초의 다른 잡끼리 순번을 공유하는 건 허용."""
        conditions = [Job.status == "queued", Job.created_at <= created_at]
        if exclude_id is not None:
            conditions.append(Job.id != exclude_id)
        result = await self.session.execute(
            select(func.count()).select_from(Job).where(*conditions)
        )
        return int(result.scalar_one())

    async def create(
        self,
        video_id: str,
        lyrics: str,
        language: str | None = None,
    ) -> Job:
        lyrics_hash = hash_lyrics(lyrics)
        job = Job(
            video_id=video_id,
            lyrics=lyrics,
            lyrics_hash=lyrics_hash,
            language=language,
        )
        self.session.add(job)
        await self.session.flush()
        return job

    async def update_status(
        self,
        job_id: str,
        status: str,
        progress: int | None = None,
        result_id: str | None = None,
        error: str | None = None,
        stage: str | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": status}
        if progress is not None:
            values["progress"] = progress
        if result_id is not None:
            values["result_id"] = result_id
        if error is not None:
            values["error"] = error
        if stage is not None:
            values["stage"] = stage

        await self.session.execute(update(Job).where(Job.id == job_id).values(**values))


class VideoOffsetRepository:
    """영상별 사용자 싱크 오프셋 upsert/조회."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, video_id: str) -> float | None:
        result = await self.session.execute(
            select(VideoOffset).where(VideoOffset.video_id == video_id)
        )
        row = result.scalar_one_or_none()
        return row.offset_sec if row else None

    async def upsert(self, video_id: str, offset_sec: float) -> None:
        result = await self.session.execute(
            select(VideoOffset).where(VideoOffset.video_id == video_id)
        )
        row = result.scalar_one_or_none()
        if row:
            row.offset_sec = offset_sec
        else:
            self.session.add(VideoOffset(video_id=video_id, offset_sec=offset_sec))
        await self.session.flush()


class ActionLogRepository:
    """파괴적 행위 로그 — 영상·행위별 최근 24시간 횟수로 일일 한도를 검사한다."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def log(self, action: str, video_id: str) -> None:
        self.session.add(ActionLog(action=action, video_id=video_id))
        await self.session.flush()

    async def count_recent(self, action: str, video_id: str, hours: int = 24) -> int:
        since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)
        result = await self.session.execute(
            select(func.count())
            .select_from(ActionLog)
            .where(
                ActionLog.action == action,
                ActionLog.video_id == video_id,
                ActionLog.created_at >= since,
            )
        )
        return int(result.scalar_one())


class SyncLinkRepository:
    """싱크 링크 CRUD (video_id 고유 → PK 기반 upsert)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, video_id: str) -> SyncLink | None:
        result = await self.session.execute(
            select(SyncLink).where(SyncLink.video_id == video_id)
        )
        return result.scalar_one_or_none()

    async def delete_involving(self, video_id: str) -> int:
        """이 영상이 소유자이거나 소스인 링크 전부 삭제 — 싱크 초기화 시 정합성 유지
        (소스 싱크가 사라진 링크를 남겨두면 빌려 쓰던 영상의 조회가 깨진다)."""
        result = await self.session.execute(
            delete(SyncLink).where(
                or_(SyncLink.video_id == video_id, SyncLink.source_video_id == video_id)
            )
        )
        return result.rowcount or 0

    async def upsert(
        self,
        video_id: str,
        source_video_id: str,
        offset_sec: float,
        rate: float = 1.0,
        verified: bool = False,
    ) -> SyncLink:
        """verified=True는 반주 상관 검증(link-jobs)을 통과한 자동 링크만 쓴다 —
        수동 링크 API는 검증 없이 오프셋을 박으므로 항상 False로 남는다."""
        existing = await self.get(video_id)
        if existing:
            existing.source_video_id = source_video_id
            existing.offset_sec = offset_sec
            # 신규 삽입 시 rate가 누락돼 배속 링크가 1.0으로 저장되던 버그를 함께 바로잡는다
            existing.rate = rate
            existing.verified = verified
            await self.session.flush()
            return existing
        link = SyncLink(
            video_id=video_id,
            source_video_id=source_video_id,
            offset_sec=offset_sec,
            rate=rate,
            verified=verified,
        )
        self.session.add(link)
        await self.session.flush()
        return link

    async def delete(self, video_id: str) -> bool:
        existing = await self.get(video_id)
        if not existing:
            return False
        await self.session.delete(existing)
        await self.session.flush()
        return True


class LinkJobRepository:
    """링크 검증 잡 CRUD — 중복 쌍 병합·FIFO claim·결과 마감."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, link_job_id: str) -> LinkJob | None:
        result = await self.session.execute(select(LinkJob).where(LinkJob.id == link_job_id))
        return result.scalar_one_or_none()

    async def get_active_pair(self, video_id: str, source_video_id: str) -> LinkJob | None:
        """같은 (video_id, source_video_id)로 이미 진행 중(queued/processing)인 잡 — 중복 방지."""
        result = await self.session.execute(
            select(LinkJob)
            .where(
                LinkJob.video_id == video_id,
                LinkJob.source_video_id == source_video_id,
                LinkJob.status.in_(["queued", "processing"]),
            )
            .order_by(LinkJob.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_recent_attempt(
        self, video_id: str, source_video_id: str, days: int
    ) -> LinkJob | None:
        """최근 N일 안에 끝난(done/failed) 같은 쌍의 잡 — 자동 재제출 쿨다운용.

        get_active_pair는 진행 중(queued/processing) 중복만 막는다. 그래서 완료·실패한
        쌍은 사용자가 그 영상을 열 때마다 다시 제출돼 GPU를 반복해 태울 수 있다 (온디맨드
        자동 제출 경로가 생기며 실제 남용 경로가 됐다). 이력이 있으면 그 잡을 돌려준다.
        days<=0이면 쿨다운 비활성으로 보고 항상 None."""
        if days <= 0:
            return None
        since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        result = await self.session.execute(
            select(LinkJob)
            .where(
                LinkJob.video_id == video_id,
                LinkJob.source_video_id == source_video_id,
                LinkJob.status.in_(["done", "failed"]),
                LinkJob.created_at >= since,
            )
            .order_by(LinkJob.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def get_oldest_queued(self) -> LinkJob | None:
        """가장 오래 대기(queued)한 링크 잡 — 워커 claim이 sync 잡 다음으로 FIFO 소비한다."""
        result = await self.session.execute(
            select(LinkJob).where(LinkJob.status == "queued").order_by(LinkJob.created_at).limit(1)
        )
        return result.scalar_one_or_none()

    async def create(self, video_id: str, source_video_id: str) -> LinkJob:
        link_job = LinkJob(video_id=video_id, source_video_id=source_video_id, status="queued")
        self.session.add(link_job)
        await self.session.flush()
        return link_job

    async def update_status(self, link_job_id: str, status: str) -> None:
        await self.session.execute(
            update(LinkJob).where(LinkJob.id == link_job_id).values(status=status)
        )

    async def mark_done(
        self, link_job_id: str, match: bool, offset_sec: float, confidence: float
    ) -> None:
        await self.session.execute(
            update(LinkJob)
            .where(LinkJob.id == link_job_id)
            .values(
                status="done", match=match, offset_sec=offset_sec, confidence=confidence, error=None
            )
        )

    async def mark_failed(self, link_job_id: str, error: str) -> None:
        await self.session.execute(
            update(LinkJob).where(LinkJob.id == link_job_id).values(status="failed", error=error)
        )
