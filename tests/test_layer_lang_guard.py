"""번역 레이어 라벨-내용 언어 가드 테스트 — 실사고(OHcNQHbWrFY) 재발 방지.

사고: lineMetaLang이 빠진 생성 요청이 기본값 ko로 폴백해 영어 번역이 (ko, manual)로
저장됐고, 크로스 지문 이관이 사람 origin을 신뢰해 새 지문까지 복사했다 — ko 사용자
화면에 영어가 떴다. 가드는 upsert_layer 합류 지점에서 명백한 불일치만 거부한다.
"""

import asyncio
import contextlib

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from everyric2.server.db.models import Base
from everyric2.server.db.repository import (
    TranslationLayerRepository,
    layer_content_lang_mismatch,
)
from everyric2.server.text_fingerprint import lines_fingerprint

VIDEO = "VIDVIDVID02"
FP = lines_fingerprint(["怖くないの？", "見えない未来"])

# 실사고 데이터 형태 그대로 — 원문 일본어 + 영어 번역이 ko로 라벨링
POISON_LINES = [
    {"text": "怖くないの？", "translation": "Aren't you scared?"},
    {"text": "見えない未来", "translation": "An unseen future must be unsettling."},
]
KO_LINES = [
    {"text": "怖くないの？", "translation": "무섭지 않아?"},
    {"text": "見えない未来", "translation": "보이지 않는 미래는 불안하겠지."},
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
    try:
        yield sm
    finally:
        await engine.dispose()


# ── 판정 함수 ──────────────────────────────────────────────────────


def test_ko_label_with_english_content_is_mismatch():
    assert layer_content_lang_mismatch("ko", POISON_LINES) is True


def test_ko_label_with_korean_content_passes():
    assert layer_content_lang_mismatch("ko", KO_LINES) is False


def test_ko_with_loanwords_passes():
    # 외래어·고유명사가 섞여도 한글이 주면 정상 — 문턱(5%)이 이를 통과시켜야 한다
    lines = [{"text": "t", "translation": "나의 Mayday, 나의 SOS — 그래도 한글이 본문이다"}]
    assert layer_content_lang_mismatch("ko", lines) is False


def test_short_content_passes():
    # 판정 근거가 부족하면(20자 미만) 막지 않는다
    assert layer_content_lang_mismatch("ko", [{"text": "t", "translation": "OK go"}]) is False


def test_en_label_with_hangul_majority_is_mismatch():
    assert layer_content_lang_mismatch("en", KO_LINES) is True


def test_en_label_with_english_content_passes():
    assert layer_content_lang_mismatch("en", POISON_LINES) is False


# ── upsert 합류 지점 거부 ─────────────────────────────────────────


def test_upsert_refuses_poison_and_accepts_clean():
    async def body():
        async with _env() as sm:
            async with sm() as s:
                repo = TranslationLayerRepository(s)
                refused = await repo.upsert_layer(
                    VIDEO, FP, "ko", lines=POISON_LINES, attribution=None, origin="manual"
                )
                assert refused is None  # 오염은 저장되지 않는다
                saved = await repo.upsert_layer(
                    VIDEO, FP, "ko", lines=KO_LINES, attribution=None, origin="manual"
                )
                assert saved is not None
                await s.commit()
            async with sm() as s:
                repo = TranslationLayerRepository(s)
                layer = await repo.get_layer(VIDEO, FP, "ko")
                assert layer is not None
                assert layer.lines[0]["translation"] == "무섭지 않아?"

    asyncio.run(body())
