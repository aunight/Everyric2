import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from everyric2.server.db.models import Base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./everyric2.db")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+asyncpg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

# SQLite: 잦은 잡 진행률 update + 다중 탭 폴링이 겹치면 기본 설정으로는
# "database is locked"가 난다 — busy timeout과 WAL로 동시성을 확보한다
_connect_args = {"timeout": 30} if DATABASE_URL.startswith("sqlite") else {}
engine = create_async_engine(DATABASE_URL, echo=False, connect_args=_connect_args)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all은 기존 테이블에 새 컬럼을 추가하지 않는다 — 가벼운 수동 보강 (SQLite 전용)
        if DATABASE_URL.startswith("sqlite"):
            from sqlalchemy import text

            await conn.execute(text("PRAGMA journal_mode=WAL"))
            cols = {row[1] for row in await conn.execute(text("PRAGMA table_info(jobs)"))}
            if "stage" not in cols:
                await conn.execute(text("ALTER TABLE jobs ADD COLUMN stage VARCHAR(24)"))
            link_cols = {
                row[1] for row in await conn.execute(text("PRAGMA table_info(sync_links)"))
            }
            if link_cols and "rate" not in link_cols:
                await conn.execute(
                    text("ALTER TABLE sync_links ADD COLUMN rate FLOAT DEFAULT 1.0")
                )
            if link_cols and "verified" not in link_cols:
                await conn.execute(
                    text("ALTER TABLE sync_links ADD COLUMN verified BOOLEAN DEFAULT 0")
                )
                # 일회성 백필: 같은 쌍으로 match=true 검증 잡이 끝난 링크는 자동 생성분이다.
                # 나머지(수동 링크 API로 박힌 것)는 0으로 남겨 미검증으로 표시한다.
                await conn.execute(
                    text(
                        "UPDATE sync_links SET verified=1 WHERE EXISTS ("
                        "  SELECT 1 FROM link_jobs lj"
                        "  WHERE lj.video_id = sync_links.video_id"
                        "    AND lj.source_video_id = sync_links.source_video_id"
                        "    AND lj.status = 'done' AND lj.match = 1)"
                    )
                )
            sync_cols = {
                row[1] for row in await conn.execute(text("PRAGMA table_info(sync_results)"))
            }
            # 제목/아티스트: 커버 링크 후보 탐색용. 기존 행은 NULL로 남고 조회 시 백필된다
            if sync_cols and "title" not in sync_cols:
                await conn.execute(text("ALTER TABLE sync_results ADD COLUMN title VARCHAR(256)"))
            if sync_cols and "artist" not in sync_cols:
                await conn.execute(text("ALTER TABLE sync_results ADD COLUMN artist VARCHAR(128)"))
        # 서버가 죽으며 남긴 좀비 잡(pending/processing) 정리 — 방치하면 같은 영상의
        # 생성 요청이 죽은 잡에 합류해 영구 "전사 중"에 갇힌다
        from sqlalchemy import text as _text

        result = await conn.execute(
            _text(
                "UPDATE jobs SET status='failed', "
                "error='서버 재시작으로 중단된 작업이에요. 다시 생성해 주세요.' "
                "WHERE status IN ('pending', 'processing', 'queued')"
            )
        )
        if result.rowcount:
            import logging

            logging.getLogger(__name__).warning(
                f"Reset {result.rowcount} zombie jobs left from a previous run"
            )


async def close_db():
    await engine.dispose()


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
