"""유튜브 자막으로 만든 싱크의 번역·독음 — 서버가 조달한 가사는 서버가 메타도 만든다.

회귀 배경: `POST /api/sync/generate-from-caption`이 line_meta도 line_meta_pending도 넘기지
않아 자막 싱크는 번역·발음이 영구히 0줄이었다 (실측 aDnGs2i_qqo: 0/35). 클라이언트는 이
경로에서 의도적으로 LLM을 부르지 않으므로 아무도 만들지 않는 상태였다.

여기서 못 박는 계약:
  ① 자막 생성은 line_meta_pending=True로 잡을 만든다 (워커가 정렬 직전까지 기다려 준다).
  ② 백그라운드 작업이 번역 결과를 line_meta로 잡에 부착한다 — 원문 라인 텍스트 그대로.
  ③ **번역이 예외를 던져도 빈 리스트를 부착한다** — 이게 핵심이다. 안 붙이면 스태시 키가
     생기지 않아 워커가 LINE_META_WAIT_SEC(120초)를 통째로 헛되게 기다린다.
  ④ status == "completed"(중복 싱크 재사용)면 부착을 아예 시도하지 않는다 — 그 job_id는
     잡이 아니라 싱크 id다.
  ⑤ 번역과 잡 처리는 겹쳐 돈다 (순차로 걸면 잡이 자기 번역을 상한까지 기다린다).

GPU도 LLM도 쓰지 않는다: yt-dlp(자막 조달)와 translate_lyrics(LLM 호출)만 목으로 갈아끼우고
그 사이 코어(잡 생성·스태시·부착 분기)는 실제 코드다. DB는 기존 서버 테스트 규약대로 격리된
in-memory SQLite로 connection.async_session을 몽키패치한다.
"""

import asyncio
import contextlib
import inspect

from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from everyric2.config.settings import get_settings
from everyric2.server import worker as worker_core
from everyric2.server.api import sync as sync_api
from everyric2.server.api.sync import (
    GenerateFromCaptionRequest,
    _expects_pronunciation,
    generate_sync_from_caption,
)
from everyric2.server.api.translate import TranslateResponse, TranslationLineResponse
from everyric2.server.db import connection as db_conn
from everyric2.server.db.models import Base
from everyric2.server.db.repository import JobRepository, SyncRepository, hash_lyrics
from everyric2.server.services import youtube_captions as yc

VIDEO = "aDnGs2i_qqo"
CAPTION_TEXTS = ["夏の匂い", "遠い花火", "君の横顔"]
CAPTION_LYRICS = "\n".join(CAPTION_TEXTS)


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
    _clear_stashes()
    try:
        yield sm
    finally:
        db_conn.async_session = orig
        for k, v in saved.items():
            object.__setattr__(server, k, v)
        _clear_stashes()
        await engine.dispose()


def _clear_stashes() -> None:
    worker_core._PENDING_LINE_META.clear()
    worker_core._PENDING_ATTRIBUTION.clear()
    worker_core._PENDING_TITLE.clear()
    worker_core._PENDING_FORCE.clear()
    worker_core._PENDING_META_WAIT.clear()
    worker_core._CANCEL_REQUESTED.clear()


@contextlib.contextmanager
def _captions(monkeypatch, texts=CAPTION_TEXTS, language="ja", translations=None):
    """자막 조달만 목으로 — 트랙 판정·정리는 건너뛰고 최종 결과를 그대로 준다."""
    track = yc.TrackChoice(
        lang=language, auto=False, label=language.upper(), reason="title_script", language=language
    )
    monkeypatch.setattr(
        yc,
        "fetch_lyrics_from_captions",
        lambda vid: yc.CaptionLyrics(track=track, lines=texts, translations=translations),
    )
    yield


def _fake_translate(calls: list, lines=None, boom: bool = False):
    """translate_lyrics 대역 — 실제 함수와 같은 동기 시그니처(스레드풀 호출)를 지킨다.

    background_tasks는 실제 함수가 POST /api/translate의 persist=true 저장에만 쓰는
    인자다 — 이 호출부(_translate_and_attach_line_meta)는 persist를 안 쓰므로 값을
    받기만 하고 버린다(실 시그니처와 위치 인자 개수를 맞추기 위해서만 필요)."""

    def fake(request, background_tasks=None):
        calls.append(request)
        if boom:
            # 실제 엔드포인트가 실패를 알리는 방식(HTTPException 500)과 동일
            raise RuntimeError("NIM upstream exploded")
        return TranslateResponse(
            lines=lines if lines is not None else _default_lines(),
            source_lang=request.source_lang,
            target_lang=request.target_lang,
            engine="fake",
        )

    return fake


def _default_lines() -> list[TranslationLineResponse]:
    return [
        TranslationLineResponse(
            original="夏の匂い", translation="여름의 냄새", pronunciation="나츠노 니오이"
        ),
        TranslationLineResponse(
            original="遠い花火", translation="먼 불꽃놀이", pronunciation="토오이 하나비"
        ),
        TranslationLineResponse(
            original="君の横顔", translation="너의 옆모습", pronunciation="키미노 요코가오"
        ),
    ]


async def _run_background(background_tasks: BackgroundTasks) -> None:
    """Starlette가 응답 후에 하는 일 — 등록된 작업을 순서대로 실행한다."""
    await background_tasks()


# ── ① 자막 생성은 line_meta_pending으로 잡을 만든다 ────────────────


def test_caption_generate_marks_line_meta_pending(monkeypatch):
    """워커가 정렬 진입 직전에 번역을 기다려 주도록 예고를 남긴다 — 안 하면 원문 정렬이 된다."""

    async def body():
        async with _env(local_worker=True) as sm:
            with _captions(monkeypatch):
                resp = await generate_sync_from_caption(
                    GenerateFromCaptionRequest(video_id=VIDEO, title="夏の匂い"),
                    BackgroundTasks(),
                )
            assert resp.status == "processing"
            # 상한이 응답에 실려 나간다 = 서버가 실제로 기다려 준다는 뜻
            assert resp.line_meta_wait_sec == worker_core.LINE_META_WAIT_SEC
            # 인프로세스 워커는 "정렬 직전에 기다려라" 예고를 스태시로 받는다
            assert resp.job_id in worker_core._PENDING_META_WAIT
            # 아직 값은 없다 (백그라운드 번역이 붙일 것) — 있으면 기다리지 않는다
            assert resp.job_id not in worker_core._PENDING_LINE_META
            # 공개 응답 스키마는 그대로다
            assert (resp.lang, resp.auto, resp.reason, resp.line_count) == (
                "ja",
                False,
                "title_script",
                3,
            )
            async with sm() as s:
                job = await JobRepository(s).get_by_id(resp.job_id)
                assert job.lyrics == CAPTION_LYRICS

    asyncio.run(body())


# ── ② 백그라운드 작업이 번역 결과를 부착한다 ──────────────────────


def test_background_attaches_translation_as_line_meta(monkeypatch):
    """번역·독음이 잡 스태시에 붙고, text는 LLM echo가 아니라 넘긴 원문이다."""

    async def body():
        async with _env(local_worker=False):
            calls: list = []
            monkeypatch.setattr(sync_api, "_dispatch_job", _noop_dispatch())
            monkeypatch.setattr(
                "everyric2.server.api.translate.translate_lyrics", _fake_translate(calls)
            )
            bg = BackgroundTasks()
            with _captions(monkeypatch):
                resp = await generate_sync_from_caption(
                    GenerateFromCaptionRequest(video_id=VIDEO, title="夏", artist="누군가"),
                    bg,
                )
            await _run_background(bg)

            assert worker_core._PENDING_LINE_META[resp.job_id] == [
                {
                    "text": "夏の匂い",
                    "pronunciation": "나츠노 니오이",
                    "translation": "여름의 냄새",
                },
                {
                    "text": "遠い花火",
                    "pronunciation": "토오이 하나비",
                    "translation": "먼 불꽃놀이",
                },
                {
                    "text": "君の横顔",
                    "pronunciation": "키미노 요코가오",
                    "translation": "너의 옆모습",
                },
            ]
            # 정렬에 쓰이는 라인 분할 그대로 번역에 넘어갔다 (분할이 어긋나면 한 줄도 안 붙는다)
            assert len(calls) == 1
            assert calls[0].text == CAPTION_LYRICS
            # 원어를 알려주고, CJK 원문이라 독음을 요청했다
            assert calls[0].source_lang == "ja"
            assert calls[0].include_pronunciation is True
            # 진단용 곡 컨텍스트도 함께 (서버 로그 상관 + 번역 품질)
            assert (calls[0].video_id, calls[0].title, calls[0].artist) == (
                VIDEO,
                "夏",
                "누군가",
            )

    asyncio.run(body())


def test_korean_captions_do_not_request_pronunciation(monkeypatch):
    """한국어 곡에 한글 독음은 무의미하다 — 확장과 같은 규칙으로 서버도 끈다."""

    async def body():
        async with _env(local_worker=False):
            calls: list = []
            monkeypatch.setattr(sync_api, "_dispatch_job", _noop_dispatch())
            monkeypatch.setattr(
                "everyric2.server.api.translate.translate_lyrics",
                _fake_translate(
                    calls,
                    lines=[
                        TranslationLineResponse(original=t, translation="", pronunciation=None)
                        for t in ("여름의 냄새", "먼 불꽃놀이", "너의 옆모습")
                    ],
                ),
            )
            bg = BackgroundTasks()
            with _captions(monkeypatch, texts=["여름의 냄새", "먼 불꽃놀이", "너의 옆모습"], language="ko"):
                resp = await generate_sync_from_caption(
                    GenerateFromCaptionRequest(video_id=VIDEO), bg
                )
            await _run_background(bg)

            assert calls[0].include_pronunciation is False
            # 번역도 독음도 없으면 빈 리스트가 붙는다 — 워커는 기다리지 않고 원문 정렬로 간다
            assert worker_core._PENDING_LINE_META[resp.job_id] == []

    asyncio.run(body())


def test_human_caption_translation_overrides_the_machine_one(monkeypatch):
    """같은 영상의 한국어 수동 자막이 있으면 그 줄의 기계 번역을 덮는다.

    독음은 사람 자막에 없으므로 여전히 LLM이 만든다 — 그래서 호출은 그대로 일어나고
    번역만 갈린다. 실측(어젯밤 300곡): 원문이 한국어가 아닌데 한국어 수동 자막이 있는 곡이
    93곡(31%)이다.
    """

    async def body():
        async with _env(local_worker=False):
            calls: list = []
            monkeypatch.setattr(sync_api, "_dispatch_job", _noop_dispatch())
            monkeypatch.setattr(
                "everyric2.server.api.translate.translate_lyrics", _fake_translate(calls)
            )
            bg = BackgroundTasks()
            # 가운데 줄만 사람 번역이 있다 — 빈 자리는 기계 번역이 남아야 한다
            with _captions(monkeypatch, translations=["", "사람이 옮긴 불꽃놀이", ""]):
                resp = await generate_sync_from_caption(
                    GenerateFromCaptionRequest(video_id=VIDEO), bg
                )
            await _run_background(bg)

            meta = worker_core._PENDING_LINE_META[resp.job_id]
            assert [m["translation"] for m in meta] == [
                "여름의 냄새", "사람이 옮긴 불꽃놀이", "너의 옆모습",
            ]
            # 독음은 그대로 LLM이 만든 것이 붙는다
            assert [m["pronunciation"] for m in meta] == [
                "나츠노 니오이", "토오이 하나비", "키미노 요코가오",
            ]
            assert len(calls) == 1, "독음이 필요하므로 LLM은 여전히 부른다"

    asyncio.run(body())


def test_human_translation_skips_the_llm_when_no_pronunciation_is_needed(monkeypatch):
    """독음이 필요 없고 번역은 사람 것이 있으면 LLM을 부를 이유가 없다."""

    async def body():
        async with _env(local_worker=False):
            calls: list = []
            monkeypatch.setattr(sync_api, "_dispatch_job", _noop_dispatch())
            monkeypatch.setattr(
                "everyric2.server.api.translate.translate_lyrics", _fake_translate(calls)
            )
            bg = BackgroundTasks()
            latin = ["Wow oh yeah", "Come on baby", "One more time"]
            with _captions(
                monkeypatch, texts=latin, language="en",
                translations=["와 예", "이리 와", "한 번 더"],
            ):
                resp = await generate_sync_from_caption(
                    GenerateFromCaptionRequest(video_id=VIDEO), bg
                )
            await _run_background(bg)

            assert calls == [], "LLM을 불렀다"
            meta = worker_core._PENDING_LINE_META[resp.job_id]
            assert [m["translation"] for m in meta] == ["와 예", "이리 와", "한 번 더"]
            assert all(m["pronunciation"] is None for m in meta)

    asyncio.run(body())


def test_human_translation_survives_an_llm_failure(monkeypatch):
    """LLM이 죽어도 사람 번역은 살아 있다 — 그것만이라도 붙여야 한다."""

    async def body():
        async with _env(local_worker=False):
            monkeypatch.setattr(sync_api, "_dispatch_job", _noop_dispatch())
            monkeypatch.setattr(
                "everyric2.server.api.translate.translate_lyrics",
                _fake_translate([], boom=True),
            )
            bg = BackgroundTasks()
            with _captions(monkeypatch, translations=["첫 줄", "", "셋째 줄"]):
                resp = await generate_sync_from_caption(
                    GenerateFromCaptionRequest(video_id=VIDEO), bg
                )
            await _run_background(bg)

            meta = worker_core._PENDING_LINE_META[resp.job_id]
            assert [(m["text"], m["translation"]) for m in meta] == [
                ("夏の匂い", "첫 줄"), ("君の横顔", "셋째 줄"),
            ]

    asyncio.run(body())


def test_expects_pronunciation_rule_matches_the_client():
    """content.ts expectsPronunciation과 같은 판정 — 임계 미달 잡음으로는 켜지지 않는다."""
    assert _expects_pronunciation(["夏の匂い", "遠い花火"]) is True
    assert _expects_pronunciation(["여름의 냄새", "먼 불꽃놀이"]) is False
    assert _expects_pronunciation(["Summer smell", "Distant fireworks"]) is False
    # CJK가 4자면 아직 아니고 5자부터 켜진다 (제목의 한자 한두 자로 켜지지 않게)
    assert _expects_pronunciation(["한글 夏遠花火"]) is False
    assert _expects_pronunciation(["한글 夏遠花火君"]) is True


# ── ③ 번역 실패에도 빈 리스트를 부착한다 (회귀의 핵심) ────────────


def test_translation_failure_still_attaches_empty_list(monkeypatch):
    """번역이 예외를 던져도 스태시 키를 만든다 — 안 만들면 워커가 120초를 헛되게 기다린다."""

    async def body():
        async with _env(local_worker=False):
            calls: list = []
            monkeypatch.setattr(sync_api, "_dispatch_job", _noop_dispatch())
            monkeypatch.setattr(
                "everyric2.server.api.translate.translate_lyrics",
                _fake_translate(calls, boom=True),
            )
            bg = BackgroundTasks()
            with _captions(monkeypatch):
                resp = await generate_sync_from_caption(
                    GenerateFromCaptionRequest(video_id=VIDEO), bg
                )
            # 백그라운드 작업이 예외를 밖으로 흘리지 않는다 (흘리면 부착이 건너뛰어진다)
            await _run_background(bg)

            assert calls, "번역을 시도조차 하지 않았다"
            # 키가 **존재**하는 것이 "붙일 것 없음" 확정 신호다 — 없음(KeyError)과 다르다
            assert resp.job_id in worker_core._PENDING_LINE_META
            assert worker_core._PENDING_LINE_META[resp.job_id] == []

    asyncio.run(body())


def test_empty_attach_releases_the_waiting_worker(monkeypatch):
    """빈 부착이 실제로 대기를 즉시 푼다 — 규약이 워커 쪽과 이어져 있음을 확인."""

    async def body():
        async with _env(local_worker=False):
            monkeypatch.setattr(sync_api, "_dispatch_job", _noop_dispatch())
            monkeypatch.setattr(
                "everyric2.server.api.translate.translate_lyrics",
                _fake_translate([], boom=True),
            )
            bg = BackgroundTasks()
            with _captions(monkeypatch):
                resp = await generate_sync_from_caption(
                    GenerateFromCaptionRequest(video_id=VIDEO), bg
                )
            await _run_background(bg)
            # 워커의 대기 함수가 상한을 소모하지 않고 즉시 "없음"으로 진행한다
            assert worker_core._wait_for_line_meta(resp.job_id, timeout=30.0) is None

    asyncio.run(body())


# ── ④ 중복 싱크 재사용(completed)이면 부착을 시도하지 않는다 ───────


def test_completed_response_schedules_nothing(monkeypatch):
    """job_id가 잡이 아니라 싱크 id인 경우 — 붙일 잡이 없으니 번역도 부르지 않는다."""

    async def body():
        async with _env(local_worker=False) as sm:
            async with sm() as s:
                await SyncRepository(s).create(
                    video_id=VIDEO,
                    lyrics_hash=hash_lyrics(CAPTION_LYRICS),
                    timestamps=[{"text": "夏の匂い", "start": 0.0, "end": 1.0}],
                    engine="ctc",
                )
                await s.commit()

            calls: list = []
            monkeypatch.setattr(
                "everyric2.server.api.translate.translate_lyrics", _fake_translate(calls)
            )
            bg = BackgroundTasks()
            with _captions(monkeypatch):
                resp = await generate_sync_from_caption(
                    GenerateFromCaptionRequest(video_id=VIDEO), bg
                )
            assert (resp.status, resp.estimated_time) == ("completed", 0)
            # 백그라운드에 아무것도 걸리지 않았다
            assert bg.tasks == []
            await _run_background(bg)
            assert calls == []
            # 싱크 id를 잡으로 오인해 스태시를 남기지도 않았다 (정리 지점이 없어 샌다)
            assert worker_core._PENDING_LINE_META == {}

    asyncio.run(body())


# ── ⑤ 번역과 잡 처리가 겹쳐 돈다 ──────────────────────────────────


def test_translation_overlaps_job_processing(monkeypatch):
    """잡 처리가 번역을 기다리지 않고 함께 시작한다.

    순차로 걸면 잡 처리가 먼저 돌아 아직 시작도 안 한 번역을 대기 상한까지 기다리고,
    원격 경로에선 그사이 큐에 올라가 번역을 통째로 놓친다."""

    async def body():
        async with _env(local_worker=False):
            order: list[str] = []
            gate = asyncio.Event()

            async def slow_translate(request, background_tasks=None):
                order.append("translate_start")
                await gate.wait()  # 잡 처리가 먼저 돌 기회를 준다
                order.append("translate_end")
                return TranslateResponse(
                    lines=_default_lines(),
                    source_lang="ja",
                    target_lang="ko",
                    engine="fake",
                )

            async def dispatch(job_id, background_tasks, await_line_meta=False):
                async def pipeline_step():
                    order.append("job_start")
                    gate.set()

                background_tasks.add_task(pipeline_step)

            # translate_lyrics는 동기 함수라 run_in_threadpool로 감싸져 있다 — 여기서는
            # 이벤트 루프 안에서 순서를 관찰하려고 async 대역을 쓴다(await 지점이 같다).
            # 자막 조달도 같은 함수를 타므로 동기 반환값도 그대로 통과시킨다.
            async def inline(fn, *a, **kw):
                out = fn(*a, **kw)
                return await out if inspect.isawaitable(out) else out

            monkeypatch.setattr(sync_api, "_dispatch_job", dispatch)
            monkeypatch.setattr("starlette.concurrency.run_in_threadpool", inline)
            monkeypatch.setattr(
                "everyric2.server.api.translate.translate_lyrics", slow_translate
            )
            bg = BackgroundTasks()
            with _captions(monkeypatch):
                resp = await generate_sync_from_caption(
                    GenerateFromCaptionRequest(video_id=VIDEO), bg
                )
            await asyncio.wait_for(_run_background(bg), timeout=5.0)

            # 잡 처리가 번역이 끝나기 전에 시작했다
            assert order == ["translate_start", "job_start", "translate_end"]
            assert len(worker_core._PENDING_LINE_META[resp.job_id]) == 3

    asyncio.run(body())


def test_remote_deployment_queues_after_its_own_translation(monkeypatch):
    """원격 배포(local_worker=False)의 큐 진입이 line_meta 뒤로 밀리는 것을 못 박는다.

    line_meta_pending을 켰으므로 응답 시점의 잡은 아직 queued가 아니다(pending) — 큐 진입은
    _queue_after_line_meta가 백그라운드에서 처리한다. 여기서 확인할 것은 그 지연이 잡을
    세워 두지 않는다는 것이다: 서버 자신의 번역이 도착하면 곧바로 queued로 올라가고, 원격
    워커는 클레임 시점에 이미 번역을 손에 쥔다(클레임 페이로드에 스태시가 실려 나간다).
    """

    async def body():
        async with _env(local_worker=False) as sm:
            monkeypatch.setattr(
                "everyric2.server.api.translate.translate_lyrics", _fake_translate([])
            )
            bg = BackgroundTasks()
            with _captions(monkeypatch):
                resp = await generate_sync_from_caption(
                    GenerateFromCaptionRequest(video_id=VIDEO), bg
                )
            async with sm() as s:
                # 응답 시점에는 아직 큐에 없다 — 워커가 번역 없이 물어 가면 원문 정렬이 된다
                assert (await JobRepository(s).get_oldest_queued()) is None

            await asyncio.wait_for(_run_background(bg), timeout=10.0)

            async with sm() as s:
                job = await JobRepository(s).get_by_id(resp.job_id)
                assert job.status == "queued"
                assert (await JobRepository(s).get_oldest_queued()).id == resp.job_id
            assert len(worker_core._PENDING_LINE_META[resp.job_id]) == 3

    asyncio.run(body())


def _noop_dispatch():
    """_dispatch_job 대역 — 잡을 만들되 어떤 처리도 걸지 않는다 (부착만 관찰)."""

    async def dispatch(job_id, background_tasks, await_line_meta=False):
        return None

    return dispatch
