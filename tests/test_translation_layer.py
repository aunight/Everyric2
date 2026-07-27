"""번역 레이어 테이블(TranslationLayer) + fingerprint 테스트.

격리된 in-memory SQLite로 Base.metadata.create_all을 직접 태워(기존 워커 풀 테스트 규약과
동일 — test_sync_link.py, test_link_jobs.py 참조) 테이블이 추가 전용으로 생성되는지,
리포지토리 upsert가 유니크 충돌 시 값을 교체하는지 검증한다. 또한 text_fingerprint의
normalize_line이 worker.py의 원본 _normalize_line과 같은 출력을 내는지(복사본 드리프트
방지) 대표 입력으로 확인한다.
"""

import asyncio
import contextlib

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from everyric2.server import worker as worker_core
from everyric2.server.db.models import Base, TranslationLayer
from everyric2.server.db.repository import TranslationLayerRepository
from everyric2.server.text_fingerprint import lines_fingerprint, normalize_line

VIDEO = "VIDVIDVID01"
FP = lines_fingerprint(["첫 줄", "두 번째 줄"])


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
    try:
        yield sm
    finally:
        await engine.dispose()


def test_upsert_then_get_roundtrip():
    async def body():
        async with _env() as sm:
            async with sm() as s:
                repo = TranslationLayerRepository(s)
                lines = [
                    {"text": "첫 줄", "translation": "First line"},
                    {"text": "두 번째 줄", "translation": "Second line"},
                ]
                attribution = {"name": "테스트 위키", "url": "https://example.test", "license": "CC BY-SA 4.0"}
                created = await repo.upsert_layer(
                    VIDEO, FP, "en", lines=lines, attribution=attribution, origin="wiki"
                )
                await s.commit()
                assert created.id  # uuid4 PK 자동 발급

            async with sm() as s:
                repo = TranslationLayerRepository(s)
                fetched = await repo.get_layer(VIDEO, FP, "en")
                assert fetched is not None
                assert fetched.lines == lines
                assert fetched.attribution == attribution
                assert fetched.origin == "wiki"

                # 다른 target_lang·다른 video_id·다른 fingerprint는 별개 레이어(못 찾음)
                assert await repo.get_layer(VIDEO, FP, "ja") is None
                assert await repo.get_layer("OTHERVIDEO1", FP, "en") is None
                assert await repo.get_layer(VIDEO, "deadbeef" * 4, "en") is None

    asyncio.run(body())


def test_upsert_on_conflict_replaces_fields_not_duplicates_row():
    async def body():
        async with _env() as sm:
            async with sm() as s:
                repo = TranslationLayerRepository(s)
                await repo.upsert_layer(
                    VIDEO,
                    FP,
                    "en",
                    lines=[{"text": "첫 줄", "translation": "First line"}],
                    attribution=None,
                    origin="llm",
                )
                await s.commit()

            # 같은 (video_id, fingerprint, target_lang)로 다시 upsert — 교체돼야 한다
            async with sm() as s:
                repo = TranslationLayerRepository(s)
                new_lines = [{"text": "첫 줄", "translation": "The first line (revised)"}]
                new_attribution = {"name": "개정 위키", "url": "https://example.test/rev"}
                replaced = await repo.upsert_layer(
                    VIDEO, FP, "en", lines=new_lines, attribution=new_attribution, origin="wiki"
                )
                await s.commit()
                assert replaced.lines == new_lines
                assert replaced.attribution == new_attribution
                assert replaced.origin == "wiki"

            # 유니크 제약 위반으로 새 행이 추가되지 않고 여전히 한 건이어야 한다
            async with sm() as s:
                count = await s.execute(
                    select(func.count()).select_from(TranslationLayer).where(
                        TranslationLayer.video_id == VIDEO,
                        TranslationLayer.fingerprint == FP,
                        TranslationLayer.target_lang == "en",
                    )
                )
                assert count.scalar_one() == 1

    asyncio.run(body())


def test_different_target_lang_coexist():
    # 같은 (video_id, fingerprint)라도 target_lang이 다르면 별개 레이어로 공존해야 한다
    # — 한 사용자가 en, 다른 사용자가 ja를 요청해도 서로의 번역을 덮어쓰지 않는다.
    async def body():
        async with _env() as sm:
            async with sm() as s:
                repo = TranslationLayerRepository(s)
                await repo.upsert_layer(
                    VIDEO, FP, "en", lines=[{"text": "첫 줄", "translation": "First"}],
                    attribution=None, origin="llm",
                )
                await repo.upsert_layer(
                    VIDEO, FP, "ja", lines=[{"text": "첫 줄", "translation": "最初の行"}],
                    attribution=None, origin="llm",
                )
                await s.commit()

            async with sm() as s:
                repo = TranslationLayerRepository(s)
                en = await repo.get_layer(VIDEO, FP, "en")
                ja = await repo.get_layer(VIDEO, FP, "ja")
                assert en.lines[0]["translation"] == "First"
                assert ja.lines[0]["translation"] == "最初の行"

    asyncio.run(body())


# ---------------------------------------------------------------------------
# fingerprint / normalize_line
# ---------------------------------------------------------------------------

def test_lines_fingerprint_is_32_hex():
    fp = lines_fingerprint(["아무 가사 줄", "다음 줄"])
    assert len(fp) == 32
    assert all(c in "0123456789abcdef" for c in fp)


def test_lines_fingerprint_invariant_to_whitespace_and_fullwidth():
    # 공백 위치·전각/반각 차이는 같은 지문을 내야 한다(worker의 line_meta 매칭 규칙과 동형)
    a = lines_fingerprint(["Are you ready?", "こんにちは"])
    b = lines_fingerprint(["Are  you ready ?", "こん にちは"])
    c = lines_fingerprint(["Are you ready？", "こんにちは"])  # 전각 물음표
    assert a == b == c


def test_lines_fingerprint_differs_on_real_content_change():
    a = lines_fingerprint(["첫 줄", "두 번째 줄"])
    b = lines_fingerprint(["첫 줄", "완전히 다른 줄"])
    assert a != b


@pytest.mark.parametrize(
    "text",
    [
        "Are you ready ?",  # 구두점 앞 공백
        "Are you ready?",
        "！hello！",  # 전각 느낌표
        "行く。",
        "行く？",
        "   양 끝 공백   ",
        "  중간   공백   여러개  ",
        "Take It Easy",  # 대소문자 혼합 — 대소문자 접기는 하지 않는다(원형 유지 확인)
        "TAKE IT EASY",
        "ＦＵＬＬＷＩＤＴＨ　ｔｅｘｔ",  # 전각 영숫자 + 전각 공백
        "탭\t포함\t텍스트",
        "",
    ],
)
def test_normalize_line_matches_worker_implementation(text):
    """text_fingerprint.normalize_line은 worker._normalize_line의 복사본 —
    두 구현이 갈라지면 fingerprint가 worker의 line_meta 매칭과 어긋난다."""
    assert normalize_line(text) == worker_core._normalize_line(text)
