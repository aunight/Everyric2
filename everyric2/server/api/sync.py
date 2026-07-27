import asyncio
import copy
import logging
import re
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query
from pydantic import BaseModel, Field

from everyric2.config.settings import get_settings
from everyric2.server import title_match

# 리스 회수는 워커 API가 소유한다 (레지스트리가 거기 있다). api/worker는 api/sync를
# 임포트하지 않으므로 순환이 없다 — 요청마다 함수 내 임포트를 반복하지 않게 최상위로 둔다.
from everyric2.server.api.worker import reclaim_expired_leases
from everyric2.server.db.connection import get_session
from everyric2.server.db.repository import (
    ActionLogRepository,
    JobRepository,
    LinkJobRepository,
    SyncLinkRepository,
    SyncRepository,
    TranslationLayerRepository,
    VideoOffsetRepository,
    hash_lyrics,
)
from everyric2.server.text_fingerprint import lines_fingerprint, normalize_line

logger = logging.getLogger(__name__)


# ── GPU를 태우는 경로의 일일 상한 (action_logs 기반, 영상·행위별 24시간) ──────────
#
# 파괴적 행위(daily_destructive_limit, 기본 2회)와 **같은 기전**을 상한만 달리해 재사용한다.
# 설정으로 올릴 후보라 값의 근거를 붙여 모듈 상수로 둔다.

# POST /api/sync/generate — 이 경로에는 한도가 전혀 없었다. 가사를 한 글자만 바꾸면 매번 새
# lyrics_hash가 되어 캐시·합류를 모두 비켜 새 GPU 잡이 생긴다. 다만 이건 제품의 **주 경로**라
# 파괴적 행위와 같은 2회로 잡으면 정상 사용이 망가진다: 오탈자 수정, 다른 가사 판본 시도,
# 실패 후 재시도로 한 영상에 여러 번 생성하는 것은 흔하다. 20회/24h는 그 여유를 크게 남기면서
# (하루 20번 같은 영상에 새 가사로 생성하는 정상 사용자는 없다) 무한 반복은 잘라낸다.
# 캐시 히트·진행 중 잡 합류는 GPU를 쓰지 않으므로 세지 않는다 (검사 위치가 잡 생성 직전).
DAILY_GENERATE_LIMIT = 20

# GET /api/sync/{video_id}/link-candidates — GET 하나가 GPU 잡(영상 2개 다운로드 + demucs ×2
# + 상관)을 제출한다. 억제가 (video_id, 후보) 쌍 쿨다운뿐이라 같은 영상에서도 후보를 바꿔가며
# 반복 제출이 가능했다. 같은 영상에서 자동 후보 제출이 하루 3번을 넘을 이유가 없다 — 상위
# 후보 1건만 제출하고, 그 후보가 쿨다운에 걸려도 다음 후보로 넘어가는 경로는 없다.
DAILY_LINK_CANDIDATE_LIMIT = 3

# 가사 하한 — 이 줄 수를 못 넘기는 생성 요청은 잡을 만들지 않고 400으로 거절한다.
#
# 왜: lyrics에 최소 길이 제약이 없어 빈 가사가 무검증 통과하면 정렬 결과가 0줄이고, 그 0줄이
# completed로 저장돼 이후 같은 lyrics_hash는 **캐시 히트로 영구히 0줄**을 돌려준다
# (GET /api/sync/{id}가 found:true, timestamps:[]를 낸다 → 사용자에겐 "싱크가 있다"고 표시된
# 채 가사가 영원히 안 나오고, 같은 가사로는 다시 생성할 수도 없다).
#
# 값이 자막 경로(services.youtube_captions.MIN_LYRIC_LINES=3)와 다른 이유: 그 3줄은 «[음악]»
# 류 효과음 표기뿐인 자동자막을 걸러내는 값이고, /generate의 가사는 사용자가 의도해 붙여넣은
# 것이라 짧은 후크 한 줄도 정당한 입력이다. 영구 0줄 봉인은 1줄 하한으로 완전히 막힌다.
MIN_LYRICS_LINES = 1

# 가사 한 줄로 인정하는 조건 — 공백·구두점만 있는 줄은 세지 않는다 ("...\n---" 같은 입력이
# 하한을 통과해 0줄 정렬로 가는 것을 막는다). \w는 유니코드라 한글·가나·한자를 센다.
_LYRIC_WORD_RE = re.compile(r"\w")


def _validate_lyrics(lyrics: str) -> None:
    """생성 요청의 가사 하한 검사 — 미달이면 400 + 무엇을 하면 되는지 알리는 한국어 사유."""
    usable = sum(1 for line in lyrics.splitlines() if _LYRIC_WORD_RE.search(line))
    if usable < MIN_LYRICS_LINES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"가사에 글자가 있는 줄이 {MIN_LYRICS_LINES}줄 이상 필요해요 "
                "(지금은 비어 있거나 공백·기호뿐이에요). 가사를 붙여넣고 다시 시도해 주세요."
            ),
        )


async def _check_action_limit(
    session, action: str, video_id: str, api_key: str | None, limit: int
) -> None:
    """(action, video_id) 24시간 횟수 상한 — admin_api_key가 설정된 배포에서만.

    키가 미설정이면(로컬 사용) 제한 없음. 어드민 키 보유 요청은 통과.
    통과 시 로그를 남겨 다음 검사에 반영한다. 초과면 429.

    **한계(의도적)**: 키가 (행위, 영상)이라 같은 영상의 반복만 막고, 임의의 11자 video_id를
    바꿔 가며 새 영상으로 부르는 순환은 막지 못한다. 전역 상한으로 바꾸면 한 사용자의 남용이
    다른 모든 사용자의 정상 생성을 함께 막으므로, 이 층에서는 영상 단위가 옳다 — 순환 남용은
    상위(요청자 단위 인증·쿼터)에서 다뤄야 한다.
    """
    server = get_settings().server
    if not server.admin_api_key or limit <= 0 or api_key == server.admin_api_key:
        return
    log_repo = ActionLogRepository(session)
    if await log_repo.count_recent(action, video_id) >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"이 영상의 {action} 일일 한도({limit}회/24시간)에 도달했어요. 내일 다시 시도해 주세요.",
        )
    await log_repo.log(action, video_id)


async def _check_destructive_limit(session, action: str, video_id: str, api_key: str | None):
    """파괴적 행위(강제 재생성·초기화) 일일 한도 — daily_destructive_limit(기본 2회/24h)."""
    await _check_action_limit(
        session, action, video_id, api_key, get_settings().server.daily_destructive_limit
    )

router = APIRouter(prefix="/api/sync", tags=["sync"])

# 유튜브 video_id 형식 (captions.py와 동일 규칙) — 무제한 길이 문자열이 그대로 쿼리
# 파라미터/저장 키로 흘러드는 것을 차단한다
_VIDEO_ID_PATTERN = r"^[A-Za-z0-9_-]{11}$"
_VIDEO_ID_RE = re.compile(_VIDEO_ID_PATTERN)

# 생성 API의 check-then-act(기존 싱크/활성 잡 확인 → 잡 생성)를 직렬화한다 —
# 동시 다중 탭 요청이 근소하게 겹치면 같은 (video_id, lyrics_hash) 잡이 중복 생성됐다.
# 단일 프로세스 서버라 프로세스 내 락으로 충분하다.
_CREATE_LOCK = asyncio.Lock()


def _validate_video_id(video_id: str) -> None:
    if not _VIDEO_ID_RE.match(video_id):
        raise HTTPException(status_code=422, detail="invalid video_id")


async def _dispatch_job(
    job_id: str, background_tasks: BackgroundTasks, await_line_meta: bool = False
) -> None:
    """생성 잡을 처리 경로에 넘긴다.

    local_worker면 기존처럼 인프로세스로 처리(add_task → process_job)한다. False면 GPU
    없는 API 전용 서버로 보고, add_task 없이 status=queued로만 마킹해 원격 워커가 클레임
    하도록 둔다 (스태시 적재는 호출부가 이미 마쳤고, queue_position 표시도 그대로 동작).

    await_line_meta는 "번역·독음(line_meta)이 잡 생성 뒤에 따로 온다"는 예고다. 인프로세스
    워커는 다운로드·보컬 분리를 먼저 돌리고 정렬 진입 직전에 기다리므로 그 시간이 번역과
    겹친다. 원격 워커는 클레임 시점의 스태시만 받아 도중에 line_meta를 받을 수 없으니,
    대신 **큐 진입 자체를 line_meta 도착까지 늦춰** 조용히 원문 정렬로 떨어지는 것을 막는다
    (병렬 이득은 없고 기존과 동일한 총 소요 — 품질 회귀가 없는 쪽을 고른다)."""
    if get_settings().server.local_worker:
        from everyric2.server.worker import process_job, stash_line_meta_wait

        if await_line_meta:
            stash_line_meta_wait(job_id)
        background_tasks.add_task(process_job, job_id)
    elif await_line_meta:
        background_tasks.add_task(_queue_after_line_meta, job_id)
    else:
        async with get_session() as session:
            await JobRepository(session).update_status(job_id, "queued", progress=0)


async def _queue_after_line_meta(job_id: str) -> None:
    """line_meta가 도착(또는 상한 초과)한 뒤에 잡을 원격 워커 큐에 올린다.

    대기 중에는 status=processing + stage="번역 대기"로 둔다 — 확장이 무엇을 기다리는지
    보이고, queued가 아니라 워커의 get_oldest_queued에도 잡히지 않는다. 대기 중 취소되면
    큐에 올리지 않고 끝낸다. 상한은 유한하므로(LINE_META_WAIT_SEC) 확장이 아무것도 보내지
    않아도 잡은 결국 큐로 올라가 원문 정렬로 완주한다.

    **말미의 queued 쓰기는 조건부다.** 취소 확인(_consume_cancel)과 그 쓰기 사이에 취소
    요청이 들어오면 무조건 쓰기는 방금 failed가 된 잡을 queued로 되살린다 → 워커가 물어
    processing이 되고, 취소된 잡은 워커가 fail을 제출하지 않아 processing에 남고, 만료
    스윕이 다시 queued로 돌려 무한 진동한다. 아직 대기 중(processing)일 때만 쓴다."""
    from everyric2.server.worker import (
        LINE_META_WAIT_SEC,
        LINE_META_WAIT_STAGE,
        JobCancelled,
        _consume_cancel,
        await_line_meta_arrival,
    )

    async with get_session() as session:
        await JobRepository(session).update_status(
            job_id, "processing", progress=48, stage=LINE_META_WAIT_STAGE
        )
    try:
        arrived = await await_line_meta_arrival(job_id, LINE_META_WAIT_SEC)
    except JobCancelled:
        await _consume_cancel(job_id)
        return
    if await _consume_cancel(job_id):
        return
    if not arrived:
        logger.info(
            "Job %s: line_meta did not arrive within %.0fs; queueing for original-text alignment",
            job_id,
            LINE_META_WAIT_SEC,
        )
    async with get_session() as session:
        # 위 취소 확인 이후에 들어온 취소도 여기서 이긴다 — processing(대기 중)일 때만 쓴다
        queued = await JobRepository(session).update_status_if(
            job_id, "queued", expected=("processing",), progress=0
        )
    if not queued:
        # 대기 중 종결된 잡(취소·실패) — 되살리지 않는다. 되살리면 워커가 물고, 취소된 잡은
        # fail을 제출하지 않아 processing에 남고, 만료 스윕이 다시 queued로 돌려 진동한다.
        logger.info("Job %s: no longer waiting when line_meta finished; not queued", job_id)


class SyncLookupResponse(BaseModel):
    found: bool
    sync_id: str | None = None
    timestamps: list[dict[str, Any]] | None = None
    lyrics_source: str | None = None
    quality_score: float | None = None
    audio_hash: str | None = None
    language: str | None = None
    created_at: str | None = None
    # 곡 단위 진단 정보 (star 흡수 구간, VAD 발성 구간) — 확장 디버그 스트립용
    debug: dict[str, Any] | None = None
    # 가사 출처 표기 (예: 보카로 가사 위키) — 푸터 병기용
    attribution: dict[str, Any] | None = None
    # 곡 템포 {bpm, beat_offset} — 가라오케 레인 마디 창/비트 격자용
    tempo: dict[str, Any] | None = None
    # 곡 키 {tonic, mode, name, confidence} — 멜로디 분석의 K-S 추정, 레인 표시용
    key: dict[str, Any] | None = None
    # 다른 영상의 싱크를 오프셋과 함께 빌려 왔을 때만 채워진다 (자기 싱크가 있으면 None).
    # 클라이언트가 링크 상태 표시·해제 버튼을 띄우는 데 쓴다.
    linked: dict[str, Any] | None = None
    # 이 영상에 저장된 사용자 싱크 오프셋(초) — 클라이언트가 재생 시점에 적용.
    # 링크로 빌려온 싱크도 보는 영상 기준이라 영상마다 따로 저장된다.
    user_offset: float | None = None
    # 세그먼트 translation이 실제로 어느 언어인지 — lang 쿼리 파라미터를 준 요청에만
    # 의미가 있다. lang 없이 조회하면 항상 None(구버전 응답과 필드 단위 동일 유지).
    # lang="ko"인데 레이어가 없으면 레거시 저장분이 ko라는 이행 가정으로 "ko"를 낸다
    # (세그에 번역이 하나도 없으면 None). 레이어가 없는 비ko lang은 번역을 비우고 None.
    translation_lang: str | None = None
    # 이 싱크(지문 기준)로 실제 서빙 가능한 번역 언어 목록 — 레이어 테이블에 존재하는
    # target_lang + (세그에 레거시 ko 번역이 있으면 "ko" 포함), 중복 제거·정렬. lang
    # 지정/미지정 요청 모두 채운다 — 추가 필드라 구버전 클라이언트는 무시하면 그만이다.
    available_langs: list[str] | None = None


class LineMeta(BaseModel):
    """라인별 부가 정보 — 발음 표기/사람 번역 (보카로 가사 위키 등). 텍스트로 세그먼트에 매칭된다."""

    text: str
    pronunciation: str | None = None
    translation: str | None = None


class Attribution(BaseModel):
    """가사 출처 표기 (예: 보카로 가사 위키 CC BY) — 싱크에 저장돼 조회 시 그대로 반환된다."""

    name: str
    url: str | None = None
    # "CC BY-SA 4.0" 등 라이선스 문구 — miraheze(vocaloidlyrics.miraheze.org) 어댑터가 싣는다.
    license: str | None = None
    # 'vocaro' | 'miraheze' 등 — 확장이 attribution.name 정규식 대신 이 필드로 출처를 가른다
    # (구싱크에는 없다 — 그 경우 확장은 이름 문자열 폴백 판정을 유지한다).
    source_id: str | None = None


class GenerateRequest(BaseModel):
    video_id: str = Field(pattern=_VIDEO_ID_PATTERN)
    lyrics: str
    lyrics_source: str = "user_input"
    language: str | None = None
    line_meta: list[LineMeta] | None = None
    attribution: Attribution | None = None
    # 영상 제목/아티스트 — 완성된 싱크에 함께 저장돼 커버 링크 후보 탐색의 단서가 된다.
    # 선택 필드라 예전 클라이언트 요청도 그대로 동작한다(제목 없이 저장).
    title: str | None = Field(default=None, max_length=256)
    artist: str | None = Field(default=None, max_length=128)
    # "line_meta는 아직이고 나중에 POST /api/sync/jobs/{job_id}/line-meta로 붙인다"는 예고.
    # 서버는 line_meta 없이 잡을 만들어 다운로드·보컬 분리를 즉시 시작하고, 정렬 진입 직전에
    # 상한을 둔 대기를 한 번 넣는다 — 클라이언트의 번역·독음 시간과 그만큼이 겹친다.
    # line_meta를 본문에 함께 실어 보내면 이 플래그는 무시된다(기다릴 것이 없다).
    line_meta_pending: bool = False
    # 요청자의 번역 대상 언어 — Job.target_lang으로 저장돼 워커의 레이어 기록·legacy 병기
    # 판정에 쓰인다. 안 싣는 구버전 확장은 "ko"(기존 동작).
    target_lang: str = Field(default="ko", max_length=8)
    # line_meta에 실린 번역의 언어. 새 확장은 항상 target_lang과 같게 보낸다 — 서버는
    # 현재 target_lang만 소비하지만, 계약을 요청 스키마에 명시해 두면(Extra 필드 무시에
    # 기대지 않고) 두 값이 갈라지는 미래 클라이언트를 스키마 수준에서 받아들일 수 있다.
    line_meta_lang: str = Field(default="ko", max_length=8)


class GenerateResponse(BaseModel):
    job_id: str
    status: str
    estimated_time: int = 15
    # 이 잡이 정렬 진입 전에 늦은 line_meta를 기다려 주는 상한(초) — 상한이지 보장은 아니다.
    # 0이면 나중에 붙여도 정렬에는 반영되지 않는다: line_meta를 본문에 이미 실어 보냈거나,
    # 플래그를 안 켰거나, status="completed"(이 경우 job_id는 잡이 아니라 완성된 싱크의 id라
    # /jobs/{id}/line-meta를 쓸 수 없다 — 번역이 끝나면 line_meta를 실어 /generate를 다시
    # 호출하면 기존 싱크에 병합된다).
    line_meta_wait_sec: float = 0.0


class SearchByAudioRequest(BaseModel):
    audio_hash: str


class CopySyncRequest(BaseModel):
    source_video_id: str = Field(pattern=_VIDEO_ID_PATTERN)
    target_video_id: str = Field(pattern=_VIDEO_ID_PATTERN)
    lyrics: str | None = None


class RegenerateRequest(BaseModel):
    video_id: str = Field(pattern=_VIDEO_ID_PATTERN)
    lyrics: str
    language: str | None = None
    force: bool = False
    line_meta: list[LineMeta] | None = None
    attribution: Attribution | None = None
    title: str | None = Field(default=None, max_length=256)
    artist: str | None = Field(default=None, max_length=128)
    # GenerateRequest.line_meta_pending과 동일 — 재생성도 번역과 병렬로 돌릴 수 있다
    line_meta_pending: bool = False
    # GenerateRequest와 동일 계약 — 재생성 요청자의 번역 언어
    target_lang: str = Field(default="ko", max_length=8)
    line_meta_lang: str = Field(default="ko", max_length=8)


def _merge_meta_into_sync(
    sync_result,
    line_meta: list[LineMeta] | None,
    attribution: Attribution | None = None,
    line_meta_lang: str = "ko",
) -> int:
    """이미 존재하는 싱크에 발음/번역 메타·출처를 병합 (세션 커밋은 호출부의 컨텍스트가 수행).

    반환값은 메타가 붙은 세그먼트 수 — 늦게 붙이는 경로(attach_line_meta)가 얼마나 매칭됐는지
    호출자에게 알려 주는 데 쓴다. 번역은 line_meta_lang이 "ko"일 때만 legacy 슬롯에 병합한다
    (워커 완료 경로의 resolve_layer_lang 판정과 같은 규칙 — 비ko 번역을 legacy에 밀어 넣으면
    한국어 사용자가 남의 언어를 받는다). 발음(한글)은 언어 무관하게 병합한다."""
    from everyric2.server.worker import merge_line_meta

    updated = dict(sync_result.timestamps)
    changed = False
    merged = 0
    if line_meta:
        segs = [dict(s) for s in updated.get("segments", [])]
        merged = merge_line_meta(
            segs,
            [m.model_dump() for m in line_meta],
            with_translation=(line_meta_lang == "ko"),
        )
        if merged:
            updated["segments"] = segs
            changed = True
    if attribution is not None:
        updated["attribution"] = attribution.model_dump()
        changed = True
    if changed:
        # JSON 컬럼은 재할당해야 변경이 감지된다
        sync_result.timestamps = updated
    return merged


# ── 싱크 링크 (inst/커버 영상이 다른 영상의 전사를 오프셋과 함께 재사용) ───────────


class SyncLinkRequest(BaseModel):
    video_id: str = Field(pattern=_VIDEO_ID_PATTERN)
    source_video_id: str = Field(pattern=_VIDEO_ID_PATTERN)
    offset_sec: float = 0.0
    # 원곡 대비 재생 배속 (nightcore 1.25 등) — 고정 오프셋만으로는 배속이 다른 커버에서
    # 곡이 진행될수록 가사가 밀린다. 소스 시간 t → t/rate + offset으로 사상.
    rate: float = Field(default=1.0, ge=0.25, le=4.0)


class SyncLinkResponse(BaseModel):
    video_id: str
    source_video_id: str
    offset_sec: float
    rate: float = 1.0
    # 반주 상관 검증(link-jobs)을 통과한 링크인지 — 이 수동 API로 만든 링크는 항상 False
    verified: bool = False
    created_at: str | None = None


def _shift_time(value: Any, offset: float, rate: float = 1.0) -> Any:
    """숫자면 t/rate + offset 사상(과한 부동소수 잡음 방지로 반올림), 아니면 그대로.

    rate는 원곡 대비 재생 배속 — nightcore(1.25)처럼 시간축이 압축된 커버는 고정
    오프셋만으로는 뒤로 갈수록 밀린다. rate=1.0이면 기존과 동일한 순수 시프트."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(value / rate + offset, 4)
    return value


def _shift_sync_timestamps(
    timestamps: dict[str, Any], offset: float, rate: float = 1.0
) -> dict[str, Any]:
    """소스 싱크의 모든 시간 필드를 t/rate + offset으로 사상한 깊은 복사본을 만든다.

    세그먼트 start/end·words·notes·pron_segments, extra.debug의 vad_regions/star_spans/
    f0_curve.t0, tempo.beat_offset, 세그먼트 debug.orig까지 함께 옮긴다. attribution 등
    시간이 아닌 필드는 그대로 둔다. offset은 음수(소스가 링크 영상보다 늦게 시작)도 된다.
    rate≠1이면 BPM(rate배)과 f0_curve 샘플 간격(dt/rate)도 함께 보정한다."""
    data = copy.deepcopy(timestamps)
    rate = rate if rate and rate > 0 else 1.0

    def sh(value: Any) -> Any:
        return _shift_time(value, offset, rate)

    for seg in data.get("segments", []) or []:
        if seg.get("start") is not None:
            seg["start"] = sh(seg["start"])
        if seg.get("end") is not None:
            seg["end"] = sh(seg["end"])
        for w in seg.get("words") or []:
            if w.get("start") is not None:
                w["start"] = sh(w["start"])
            if w.get("end") is not None:
                w["end"] = sh(w["end"])
        for n in seg.get("notes") or []:
            if n.get("start") is not None:
                n["start"] = sh(n["start"])
            if n.get("end") is not None:
                n["end"] = sh(n["end"])
        for p in seg.get("pron_segments") or []:
            if p.get("start") is not None:
                p["start"] = sh(p["start"])
            if p.get("end") is not None:
                p["end"] = sh(p["end"])
        dbg = seg.get("debug")
        if isinstance(dbg, dict) and isinstance(dbg.get("orig"), list) and len(dbg["orig"]) == 2:
            dbg["orig"] = [sh(dbg["orig"][0]), sh(dbg["orig"][1])]

    debug = data.get("debug")
    if isinstance(debug, dict):
        for key in ("vad_regions", "star_spans"):
            arr = debug.get(key)
            if isinstance(arr, list):
                debug[key] = [
                    [sh(span[0]), sh(span[1]), *span[2:]]
                    for span in arr
                    if isinstance(span, (list, tuple)) and len(span) >= 2
                ]
        f0 = debug.get("f0_curve")
        if isinstance(f0, dict) and f0.get("t0") is not None:
            f0["t0"] = sh(f0["t0"])
            if rate != 1.0 and isinstance(f0.get("dt"), (int, float)):
                f0["dt"] = round(f0["dt"] / rate, 6)

    tempo = data.get("tempo")
    if isinstance(tempo, dict):
        if tempo.get("beat_offset") is not None:
            tempo["beat_offset"] = sh(tempo["beat_offset"])
        if rate != 1.0 and isinstance(tempo.get("bpm"), (int, float)):
            tempo["bpm"] = round(tempo["bpm"] * rate, 2)

    return data


def _build_sync_response(
    result, timestamps: dict[str, Any], linked: dict[str, Any] | None = None
) -> "SyncLookupResponse":
    """SyncResult + (원본 또는 시프트된) timestamps dict → 조회 응답. linked면 빌린 싱크."""
    return SyncLookupResponse(
        found=True,
        sync_id=result.id,
        timestamps=timestamps.get("segments", []),
        lyrics_source=result.engine,
        quality_score=result.quality_score,
        audio_hash=result.audio_hash,
        language=result.language,
        created_at=result.created_at.isoformat() if result.created_at else None,
        debug=timestamps.get("debug"),
        attribution=timestamps.get("attribution"),
        tempo=timestamps.get("tempo"),
        key=timestamps.get("key"),
        linked=linked,
    )


async def _persist_legacy_ko_layer(
    video_id: str,
    fingerprint: str,
    lines: list[dict[str, Any]],
    attribution: dict[str, Any] | None,
) -> None:
    """세그에 박혀 있던 레거시 ko 번역을 TranslationLayer(origin="legacy")로 백필한다.

    배포 이전(레이어 테이블이 생기기 전) 생성분은 ko 번역이 SyncResult.timestamps의
    세그먼트에만 있고 레이어가 없다. lang=en 같은 비ko 조회는 레이어가 없으면 세그
    translation을 비우므로(TranslationLayer.origin 주석 참고), 그 상태에서 재생성이 한 번
    이라도 일어나면 원래 있던 ko 번역(위키 사람 번역 포함)을 되살릴 방법이 사라진다 —
    두 호출부(`_apply_translation_lang`의 lang=ko 조회, `regenerate_sync`의 잡 생성 직전)가
    이 함수로 "레이어가 아직 없으면 지금 채운다"를 수행한다.

    BackgroundTasks로 스케줄되므로 요청을 처리하던 세션이 아니라 독립된 세션을 새로
    연다 — translate.py의 `_persist_translation_layer`와 같은 동기↔비동기 브리지 뒤
    저장 패턴이다. 실패해도 이미 나간 응답에는 영향이 없으므로 로그만 남기고 삼킨다.
    """
    try:
        async with get_session() as session:
            await TranslationLayerRepository(session).upsert_layer(
                video_id, fingerprint, "ko", lines=lines, attribution=attribution, origin="legacy"
            )
    except Exception:
        logger.exception("Failed to backfill legacy ko translation layer for video %s", video_id)


async def _schedule_ko_backfill_if_needed(
    session,
    background_tasks: BackgroundTasks,
    video_id: str,
    segments: list[dict[str, Any]],
    attribution: dict[str, Any] | None,
) -> None:
    """세그에 레거시 ko 번역이 있고 그 지문의 ko 레이어가 아직 없으면 백그라운드로 채운다.

    응답(조회든 재생성이든)을 늦추지 않으려고 upsert 자체는 `_persist_legacy_ko_layer`로
    미룬다 — 여기서는 "채울 필요가 있는가"만 판정하고 스케줄만 건다."""
    has_translation = any((seg.get("translation") or "").strip() for seg in segments)
    if not has_translation:
        return
    fingerprint = lines_fingerprint([seg.get("text", "") or "" for seg in segments])
    if await TranslationLayerRepository(session).get_layer(video_id, fingerprint, "ko") is not None:
        return
    lines = [
        {"text": seg.get("text", "") or "", "translation": seg.get("translation", "") or ""}
        for seg in segments
    ]
    background_tasks.add_task(_persist_legacy_ko_layer, video_id, fingerprint, lines, attribution)


async def _apply_translation_lang(
    session,
    video_id: str,
    resp: "SyncLookupResponse",
    lang: str | None,
    background_tasks: BackgroundTasks,
) -> "SyncLookupResponse":
    """조회 응답에 available_langs를 채우고, lang이 있으면 세그먼트 translation을 그
    언어로 맞춘다.

    **available_langs는 lang 유무와 무관하게 항상 채운다** — 추가 필드라 구버전 클라이언트
    호환에는 영향이 없다. 번역 치환은 **lang이 없으면 하지 않는다** — 구버전 클라이언트의
    응답이 필드 단위로 기존과 동일해야 한다는 전역 제약을 여기서 지킨다. video_id는 항상
    URL 경로의 값(자기 싱크든 링크로 빌린 싱크든)을 쓴다 — TranslationLayer는 (video_id,
    fingerprint, target_lang) 키라 보는 영상 기준으로 일관되게 조회해야 POST
    /api/translate의 persist=true 저장과 같은 키로 맞아떨어진다.

    세그먼트는 원본 result.timestamps의 리스트를 직접 건드리지 않도록 얕은 복사본을
    만들어 교체한다 — JSON 컬럼은 재할당해야 변경이 감지되므로(다른 곳의 동일 주석 참고)
    이 자체가 SyncResult를 오염시키진 않지만, 세션 수명 동안 같은 ORM 객체가 재사용될
    가능성을 원천 차단하는 편이 안전하다.
    """
    if not resp.found:
        return resp
    segments = [dict(seg) for seg in (resp.timestamps or [])]
    fingerprint = lines_fingerprint([seg.get("text", "") or "" for seg in segments])
    has_legacy_translation = any((seg.get("translation") or "").strip() for seg in segments)

    layer_langs = await TranslationLayerRepository(session).list_layer_langs(video_id, fingerprint)
    available = set(layer_langs)
    if has_legacy_translation:
        available.add("ko")
    resp.available_langs = sorted(available)

    if not lang:
        resp.timestamps = segments
        return resp

    layer = await TranslationLayerRepository(session).get_layer(video_id, fingerprint, lang)
    if layer is not None:
        # merge_line_meta(worker.py)와 같은 색인 규칙 — 값이 있는 첫 항목을 채택한다
        by_text: dict[str, str] = {}
        for item in layer.lines or []:
            key = normalize_line(item.get("text", "") or "")
            if not key:
                continue
            value = item.get("translation", "") or ""
            if key not in by_text or (not by_text[key] and value):
                by_text[key] = value
        for seg in segments:
            seg["translation"] = by_text.get(normalize_line(seg.get("text", "") or ""), "")
        resp.translation_lang = lang
    elif lang == "ko":
        # 레이어가 없으면 저장된 레거시 번역이 ko라는 이행 가정 — 그대로 둔다.
        # 세그에 번역이 하나도 없으면 "ko"라고 우길 근거가 없으므로 None.
        resp.translation_lang = "ko" if has_legacy_translation else None
        if has_legacy_translation:
            # 이번 조회로 레거시 ko가 노출되는 김에 레이어에 옮겨 백필한다 — 다음 번
            # lang=en 등 비ko 조회·재생성에서도 이 번역이 살아남게 한다.
            await _schedule_ko_backfill_if_needed(
                session, background_tasks, video_id, segments, resp.attribution
            )
    else:
        # 비ko이고 레이어가 없으면 레거시 값이 어느 언어인지 알 수 없다 — 비운다
        for seg in segments:
            seg["translation"] = ""
        resp.translation_lang = None
    resp.timestamps = segments
    return resp


@router.post("/link", response_model=SyncLinkResponse)
async def create_sync_link(request: SyncLinkRequest, x_api_key: str | None = Header(default=None)):
    """영상 video_id가 source_video_id의 싱크를 offset과 함께 빌려 쓰도록 링크(upsert).

    자기 자신 링크는 거부. source에 실제 싱크가 있어야 한다 — source가 그 자체로 링크만
    있고 자기 싱크가 없으면(링크의 링크) 거부한다(단순화: 1단계 링크만 허용).

    **이 경로는 검증이 없다** — 호출자가 준 오프셋(0 포함)을 그대로 박으므로 틀린 링크가
    코퍼스에 남을 수 있다(실제 사례 있음). 두 겹으로 완화한다: ① 만들어진 링크는 항상
    verified=False로 기록돼 자동 검증 링크(link-jobs 통과)와 조회 응답에서 구분되고,
    ② manual_link_requires_admin을 켠 배포에서는 어드민 키를 요구한다. 검증된 링크를
    원하면 POST /api/link-jobs(반주 상관 판정)를 쓴다."""
    if request.video_id == request.source_video_id:
        raise HTTPException(status_code=400, detail="Cannot link a video to itself")

    server = get_settings().server
    if server.manual_link_requires_admin:
        if not server.admin_api_key or x_api_key != server.admin_api_key:
            raise HTTPException(
                status_code=403,
                detail="검증 없는 수동 링크는 어드민 키가 필요해요. "
                "자동 검증 링크는 /api/link-jobs로 요청해 주세요.",
            )

    async with get_session() as session:
        sync_repo = SyncRepository(session)
        source_syncs = await sync_repo.get_by_video(request.source_video_id)
        if not source_syncs:
            raise HTTPException(
                status_code=400,
                detail=f"Source video {request.source_video_id} has no sync to link",
            )
        link_repo = SyncLinkRepository(session)
        link = await link_repo.upsert(
            request.video_id,
            request.source_video_id,
            request.offset_sec,
            request.rate,
            verified=False,
        )
        return SyncLinkResponse(
            video_id=link.video_id,
            source_video_id=link.source_video_id,
            offset_sec=link.offset_sec,
            rate=link.rate,
            verified=link.verified,
            created_at=link.created_at.isoformat() if link.created_at else None,
        )


@router.delete("/link/{video_id}")
async def delete_sync_link(video_id: str):
    _validate_video_id(video_id)
    async with get_session() as session:
        removed = await SyncLinkRepository(session).delete(video_id)
        return {"video_id": video_id, "removed": removed}


@router.get("/list")
async def list_available_syncs(limit: int = Query(50, ge=1, le=200)):
    """조회 가능한 싱크 목록 (확장의 링크 후보 선택용) — 영상별 1개, 최신순."""
    async with get_session() as session:
        results = await SyncRepository(session).get_all_unique_videos(limit=limit)
        items = []
        for r in results:
            ts = r.timestamps or {}
            segments = ts.get("segments", []) or []
            debug = ts.get("debug") or {}
            attribution = ts.get("attribution") or {}
            items.append(
                {
                    "video_id": r.video_id,
                    "first_line": segments[0].get("text", "") if segments else "",
                    "line_count": len(segments),
                    "attribution_name": attribution.get("name"),
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "alignment_text": debug.get("alignment_text"),
                }
            )
        # 확장 클라이언트(listSyncs)가 SyncListItem[] bare 배열을 기대 → 래핑하지 않는다
        return items


class UserOffsetRequest(BaseModel):
    offset_sec: float


@router.put("/offset/{video_id}")
async def save_user_offset(video_id: str, request: UserOffsetRequest):
    """이 영상에서 사용자가 조정한 싱크 오프셋(초)을 저장 — 다음 조회부터 함께 내려간다."""
    _validate_video_id(video_id)
    offset = max(-60.0, min(60.0, request.offset_sec))
    async with get_session() as session:
        await VideoOffsetRepository(session).upsert(video_id, offset)
    return {"video_id": video_id, "offset_sec": offset}


class LinkCandidate(BaseModel):
    video_id: str
    title: str | None = None
    artist: str | None = None
    # 제목 유사도 (1.0 = 정규화 정확 일치). 같은 곡인지의 판정값이 아니라 후보 순위일 뿐이다
    score: float


class LinkCandidatesResponse(BaseModel):
    video_id: str
    # has_sync | linked | disabled | none | submitted | pending | cooldown
    status: str
    candidates: list[LinkCandidate] = []
    # 낸 후속 작업의 종류 — 클라이언트가 진행 상태를 어느 API로 폴링할지 가른다.
    # 오늘은 "link_validate"(반주 상관 검증) 하나뿐이다. _dispatch_candidate_followup 참고.
    followup: str | None = None
    # submitted/pending/cooldown일 때 해당 후속 작업의 id
    job_id: str | None = None


# ── 후보 확정 이후: 후속 작업 디스패치 (교체 지점) ────────────────

# 반주 상관 검증 잡 — 커버가 원곡과 같은 반주를 쓰는지 판정해 SyncLink를 만든다
FOLLOWUP_LINK_VALIDATE = "link_validate"


async def _dispatch_candidate_followup(
    session, video_id: str, candidate_video_id: str, api_key: str | None = None
) -> tuple[str, str, str | None]:
    """후보를 확정한 뒤 **무엇을 제출할지** 결정하는 단일 교체 지점. (kind, status, job_id) 반환.

    status는 submitted | pending | cooldown.

    오늘의 구현은 반주 상관 검증 잡 하나다. 앞으로 "원곡의 가사·번역·독음을 재사용해
    이 영상 자체를 새로 정렬"처럼 다른 후속 작업으로 갈아끼울 수 있도록 제출 로직을 여기
    한 곳에 가둬 두었다 — 후보 탐색·제목 정규화·재제출 억제 정책은 이 함수를 바꿔도
    재작성할 필요가 없다. 두 경로를 조건부로 함께 쓰거나(예: 커버 음질이 나쁘면 링크,
    아니면 재정렬) 순차 폴백으로 확장하는 것도 이 함수 안에서 끝난다.

    교체 구현이 지켜야 할 계약:
      - 같은 (영상, 후보) 쌍의 재제출 억제를 반드시 자체적으로 유지할 것 — 진행 중이면
        pending, 최근에 끝난 이력이 있으면 cooldown. 이게 없으면 사용자가 같은 영상을
        열 때마다 GPU가 다시 돈다(현재 쿨다운 기준: link_retry_cooldown_days).
      - 실제 제출 직전에 영상 단위 일일 상한(_check_action_limit)을 통과할 것 — 쌍 쿨다운은
        후보를 바꾸면 비켜 가므로, GET 하나가 GPU 잡을 만드는 이 경로에는 쌍과 무관한
        상한이 한 겹 더 필요하다. 초과는 429다(확장은 이 조회의 실패를 조용히 무시한다 —
        content.ts probeLinkCandidates: `if (!data) return;`).
      - kind는 클라이언트가 진행 상태를 어느 API로 폴링할지 가르는 값이므로, 새 종류를
        도입하면 그 종류의 조회 경로도 함께 알려야 한다.
    """
    server = get_settings().server
    repo = LinkJobRepository(session)

    active = await repo.get_active_pair(video_id, candidate_video_id)
    if active:
        return FOLLOWUP_LINK_VALIDATE, "pending", active.id
    recent = await repo.get_recent_attempt(
        video_id, candidate_video_id, server.link_retry_cooldown_days
    )
    if recent:
        return FOLLOWUP_LINK_VALIDATE, "cooldown", recent.id
    # 억제(pending/cooldown)를 모두 통과해 실제로 GPU 잡을 만드는 지점 — 여기서만 센다
    await _check_action_limit(
        session, "link_candidates", video_id, api_key, DAILY_LINK_CANDIDATE_LIMIT
    )
    link_job = await repo.create(video_id, candidate_video_id)
    return FOLLOWUP_LINK_VALIDATE, "submitted", link_job.id


@router.get("/{video_id}/link-candidates", response_model=LinkCandidatesResponse)
async def find_link_candidates(
    video_id: str,
    title: Annotated[str, Query(min_length=1, max_length=256)],
    artist: Annotated[str | None, Query(max_length=128)] = None,
    x_api_key: str | None = Header(default=None),
):
    """이 영상과 같은 곡일 만한 코퍼스 영상을 제목으로 찾고, 최상위 후보 1건에 대해
    후속 작업을 자동 제출한다 (무엇을 제출할지는 _dispatch_candidate_followup이 정한다 —
    오늘은 반주 상관 검증 잡). 응답의 followup이 그 종류를 알려준다.

    **제목 매칭은 후보 발견에만 쓴다.** 같은 곡인지의 최종 판정은 기존 반주 상관 게이트
    (link_match_threshold·link_min_offset_margin)가 그대로 담당하며, 이 엔드포인트가
    SyncLink를 직접 만드는 경로는 없다 — 제목이 맞았다는 이유만으로 링크가 생기지 않는다.
    그래서 매칭이 헐거워도 안전하다(오탐의 대가는 후속 작업 한 번).

    자기 싱크가 있거나 이미 링크가 있으면 후보 없이 즉시 반환한다. 같은 쌍을 최근
    link_retry_cooldown_days 안에 이미 시도했으면 재제출하지 않는다 — 사용자가 같은 영상을
    반복해 열 때마다 GPU를 다시 태우는 남용 경로를 막는다. 쌍 쿨다운은 후보를 바꾸면 비켜
    가므로 실제 제출에는 영상 단위 일일 상한(DAILY_LINK_CANDIDATE_LIMIT)이 한 겹 더 걸린다
    (초과 시 429)."""
    _validate_video_id(video_id)
    server = get_settings().server

    async with get_session() as session:
        sync_repo = SyncRepository(session)

        # (a) 자기 싱크가 있으면 링크가 필요 없다 — 대신 비어 있던 제목을 이 기회에 채운다
        own = await sync_repo.get_by_video(video_id)
        if own:
            await sync_repo.set_title_if_missing(own[0], title, artist)
            return LinkCandidatesResponse(video_id=video_id, status="has_sync")
        if await SyncLinkRepository(session).get(video_id):
            return LinkCandidatesResponse(video_id=video_id, status="linked")

        # (b) 제목이 채워진 코퍼스를 전수 스캔해 상위 후보를 뽑는다 (자기 자신 제외)
        rows = await sync_repo.list_titled(limit=server.link_candidate_scan_limit)
        entries = [(r.video_id, r.title or "") for r in rows if r.video_id != video_id]
        ranked = title_match.rank_matches(
            title, entries, min_score=server.link_candidate_min_title_score, limit=5
        )
        by_video = {r.video_id: r for r in rows}
        candidates = [
            LinkCandidate(
                video_id=vid,
                title=by_video[vid].title,
                artist=by_video[vid].artist,
                score=score,
            )
            for vid, score in ranked
        ]
        if not candidates:
            return LinkCandidatesResponse(video_id=video_id, status="none")
        if not server.auto_link_candidates:
            return LinkCandidatesResponse(
                video_id=video_id, status="disabled", candidates=candidates
            )

        # (c) 최상위 후보 1건만 인프로세스로 제출한다 (여러 후보 순차 재시도는 넣지 않는다).
        # 무엇을 제출할지는 _dispatch_candidate_followup 한 곳에서만 정해진다
        kind, status, job_id = await _dispatch_candidate_followup(
            session, video_id, candidates[0].video_id, x_api_key
        )
        return LinkCandidatesResponse(
            video_id=video_id,
            status=status,
            candidates=candidates,
            followup=kind,
            job_id=job_id,
        )


@router.get("/{video_id}", response_model=SyncLookupResponse)
async def get_sync(
    video_id: str,
    lyrics_hash: str | None = None,
    title: Annotated[str | None, Query(max_length=256)] = None,
    artist: Annotated[str | None, Query(max_length=128)] = None,
    lang: Annotated[str | None, Query(max_length=8)] = None,
    # 기본값을 둔 이유: `BackgroundTasks | None = None`은 FastAPI가 이 타입을 더 이상
    # "시스템이 주입하는 특수 의존성"으로 인식하지 못하게 만들어 라우트 등록 자체가
    # FastAPIError로 깨진다(POST /api/translate에서 실측). 그렇다고 필수 인자로 두면
    # 이 함수를 직접 호출하는 기존 테스트 20여 곳(test_sync_link.py 등 — 이 작업의
    # 수정 허용 범위 밖)이 전부 깨진다. `BackgroundTasks()` 기본값은 타입 주석 자체는
    # Optional이 아니라서 FastAPI가 여전히 실제 요청마다 **새 인스턴스를 주입**하고
    # (기본값은 무시된다 — ASGI 요청으로 직접 검증함) 응답 후 정상적으로 실행해 준다.
    # 기본값 인스턴스는 이 함수를 직접 호출하는 기존 테스트가 인자를 생략했을 때만
    # 쓰이는 껍데기이고, 아무도 실행해 주지 않아 그 호출들의 백필 스케줄은 조용히
    # 버려진다(그 테스트들은 애초에 백필을 검증하지 않는다).
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """이 영상의 싱크를 조회한다. 자기 싱크 > 링크로 빌려온 싱크 순.

    title/artist는 선택적 기회적 백필용이다 — 이 영상 '자기' 싱크의 title이 비어 있을 때만
    조용히 채운다(기존 값은 절대 덮어쓰지 않는다). 재생성 없이 기존 코퍼스에 제목이 쌓여
    커버 링크 후보 탐색이 동작하게 만드는 경로다. 링크로 빌려온 싱크는 소유자가 다른 영상
    (원곡)이라 커버의 제목이 원곡 행에 새겨지지 않도록 백필하지 않는다.

    lang은 선택이다 — 주면 세그먼트 translation을 그 언어의 TranslationLayer로 맞춰
    치환하고 응답의 translation_lang에 실제 반영된 언어를 담는다(규칙은
    _apply_translation_lang 참고). 안 주면 기존 필드 그대로다(구버전 클라이언트 호환).
    available_langs는 lang 유무와 무관하게 항상 채워진다."""
    _validate_video_id(video_id)
    async with get_session() as session:
        repo = SyncRepository(session)
        user_offset = await VideoOffsetRepository(session).get(video_id)

        # 자기 싱크가 있으면 링크보다 우선한다
        if lyrics_hash:
            result = await repo.get_by_video_and_hash(video_id, lyrics_hash)
            if result:
                await repo.set_title_if_missing(result, title, artist)
                resp = _build_sync_response(result, result.timestamps)
                resp.user_offset = user_offset
                return await _apply_translation_lang(session, video_id, resp, lang, background_tasks)
        else:
            results = await repo.get_by_video(video_id)
            if results:
                await repo.set_title_if_missing(results[0], title, artist)
                resp = _build_sync_response(results[0], results[0].timestamps)
                resp.user_offset = user_offset
                return await _apply_translation_lang(session, video_id, resp, lang, background_tasks)

        # 자기 싱크가 없고 링크가 있으면 source 싱크를 offset 적용해 빌려 온다
        link = await SyncLinkRepository(session).get(video_id)
        if link:
            source_syncs = await repo.get_by_video(link.source_video_id)
            if source_syncs:
                src = source_syncs[0]
                link_rate = getattr(link, "rate", 1.0) or 1.0
                shifted = _shift_sync_timestamps(src.timestamps, link.offset_sec, link_rate)
                resp = _build_sync_response(
                    src,
                    shifted,
                    linked={
                        "source_video_id": link.source_video_id,
                        "offset_sec": link.offset_sec,
                        "rate": link_rate,
                        # 반주 상관 검증을 통과한 링크인지 — 수동 링크(검증 없이 오프셋 지정)와
                        # 구분해 클라이언트가 신뢰도를 표시할 수 있게 한다
                        "verified": bool(getattr(link, "verified", False)),
                    },
                )
                resp.user_offset = user_offset
                # lang 레이어는 보는 영상(video_id) 기준으로 조회한다 — source_video_id가
                # 아니다. POST /api/translate persist=true도 항상 요청받은 video_id로
                # 저장하므로, 조회도 같은 키를 써야 서로 맞아떨어진다.
                return await _apply_translation_lang(session, video_id, resp, lang, background_tasks)

        return SyncLookupResponse(found=False, user_offset=user_offset)


@router.delete("/{video_id}")
async def reset_video_syncs(video_id: str, x_api_key: str | None = Header(default=None)):
    """이 영상의 서버 싱크를 전부 삭제(초기화) — 잘못 붙여넣은 가사 등에서 새로 시작.

    이 영상이 소유자이거나 소스인 링크도 함께 제거한다 ("/link/{video_id}"가 먼저
    선언돼 있어 링크 삭제 경로와 충돌하지 않는다). 공개 배포에선 일일 한도 적용."""
    _validate_video_id(video_id)
    async with get_session() as session:
        await _check_destructive_limit(session, "reset", video_id, x_api_key)
        removed_syncs = await SyncRepository(session).delete_by_video(video_id)
        removed_links = await SyncLinkRepository(session).delete_involving(video_id)
        return {
            "video_id": video_id,
            "removed_syncs": removed_syncs,
            "removed_links": removed_links,
        }


@router.post("/generate", response_model=GenerateResponse)
async def generate_sync(
    request: GenerateRequest,
    background_tasks: BackgroundTasks,
    x_api_key: str | None = Header(default=None),
):
    """가사로 싱크 생성 잡을 만든다 (기존 싱크가 있으면 즉시 completed).

    line_meta(발음/번역)는 **선택**이다. 본문에 실어 보내는 기존 경로가 그대로 동작하고,
    아직 번역이 안 끝났으면 line_meta_pending=true로 잡을 먼저 만들어 다운로드·보컬 분리를
    선행시킨 뒤 POST /api/sync/jobs/{job_id}/line-meta로 붙일 수 있다 (응답의
    line_meta_wait_sec이 서버가 실제로 기다려 주는 상한).

    공개 배포(admin_api_key 설정)에서는 새 잡을 만드는 요청에 DAILY_GENERATE_LIMIT이
    걸린다 — 캐시 히트·진행 중 잡 합류는 GPU를 쓰지 않으므로 세지 않는다."""
    from everyric2.server.worker import LINE_META_WAIT_SEC

    _validate_lyrics(request.lyrics)
    lyrics_hash_value = hash_lyrics(request.lyrics)
    # 본문에 line_meta가 이미 있으면 기다릴 것이 없다 — 플래그보다 실제 값이 우선
    await_line_meta = request.line_meta_pending and not request.line_meta
    wait_sec = LINE_META_WAIT_SEC if await_line_meta else 0.0

    # 활성 잡을 보기 **전에** 만료 리스를 회수한다 (락 밖 — 중첩 세션 금지).
    # 죽은 워커가 물고 있던 잡은 processing에 남아 get_active_by_video에 활성으로 잡히고,
    # 그러면 이 요청이 죽은 잡에 합류해 그 (영상, 가사)는 재기동까지 재생성 불가가 된다.
    # 주기 스윕이 이미 그 일을 하지만 여기서 한 번 더 하는 것이 **이벤트 루프에 의존하지
    # 않는 방어선**이다 — 주기 태스크가 아직 안 돌았거나(간격 이내) 죽어 있어도 봉인이 풀린다.
    await reclaim_expired_leases()

    # 확인(기존 싱크/활성 잡)→생성 사이에 다른 요청이 끼면 중복 잡이 생긴다 — 직렬화
    async with _CREATE_LOCK, get_session() as session:
        sync_repo = SyncRepository(session)
        existing = await sync_repo.get_by_video_and_hash(request.video_id, lyrics_hash_value)
        if existing:
            # 정렬은 재사용하되 새로 들어온 발음/번역 메타·출처는 반영한다
            if request.line_meta or request.attribution:
                _merge_meta_into_sync(existing, request.line_meta, request.attribution)
            # 제목이 비어 있던 기존 싱크는 이 기회에 채운다 (기존 값은 덮어쓰지 않는다)
            await sync_repo.set_title_if_missing(existing, request.title, request.artist)
            return GenerateResponse(
                job_id=existing.id,
                status="completed",
                estimated_time=0,
            )

        job_repo = JobRepository(session)
        # 같은 영상·가사로 이미 돌고 있는 잡이 있으면 새 잡을 만들지 않고 합류한다 —
        # 버튼 연타로 동일 잡이 중복 생성되면 같은 임시 오디오 파일을 두 작업이 잡아
        # Windows에서 WinError 32(파일 사용 중)로 다운로드가 깨진다
        active = await job_repo.get_active_by_video(request.video_id, lyrics_hash_value)
        if active:
            from everyric2.server.worker import (
                stash_attribution,
                stash_line_meta,
                stash_title,
            )

            if request.line_meta:
                stash_line_meta(
                    active.id,
                    [m.model_dump() for m in request.line_meta],
                    request.line_meta_lang,
                )
            if request.attribution:
                stash_attribution(active.id, request.attribution.model_dump())
            stash_title(active.id, request.title, request.artist)
            # 합류한 잡이 이미 정렬에 들어갔을 수도 있으니 상한은 어디까지나 상한이다 —
            # line-meta 붙이기는 받아 주고(늦으면 완성된 싱크에 병합된다) 값은 그대로 알린다
            return GenerateResponse(
                job_id=active.id,
                status="processing",
                estimated_time=15,
                line_meta_wait_sec=wait_sec,
            )
        # 여기부터가 실제로 GPU를 태우는 유일한 분기 — 한도 검사는 이 지점이어야 한다
        # (위의 캐시 히트·합류에서 예산을 먹으면 정상 사용이 헛되게 소모된다)
        await _check_action_limit(
            session, "generate", request.video_id, x_api_key, DAILY_GENERATE_LIMIT
        )
        job = await job_repo.create(
            video_id=request.video_id,
            lyrics=request.lyrics,
            language=request.language,
            target_lang=request.target_lang,
        )
        job_id = job.id

    from everyric2.server.worker import stash_attribution, stash_line_meta, stash_title

    if request.line_meta:
        stash_line_meta(
            job_id, [m.model_dump() for m in request.line_meta], request.line_meta_lang
        )
    if request.attribution:
        stash_attribution(job_id, request.attribution.model_dump())
    stash_title(job_id, request.title, request.artist)
    await _dispatch_job(job_id, background_tasks, await_line_meta=await_line_meta)

    return GenerateResponse(
        job_id=job_id,
        status="processing",
        estimated_time=15,
        line_meta_wait_sec=wait_sec,
    )


class LineMetaAttachRequest(BaseModel):
    """진행 중인 생성 잡에 나중에 붙이는 번역·독음.

    **line_meta를 빈 배열로 보내면 "붙일 것이 없음"이 확정**돼 워커가 즉시 원문 정렬로
    진행한다 — 클라이언트가 번역에 실패했을 때 반드시 이걸 한 번 보내야 잡이 대기 상한까지
    헛되게 서 있지 않는다.
    """

    line_meta: list[LineMeta] = Field(default_factory=list)
    attribution: Attribution | None = None
    title: str | None = Field(default=None, max_length=256)
    artist: str | None = Field(default=None, max_length=128)
    # line_meta에 실린 번역의 언어 — GenerateRequest.line_meta_lang과 같은 계약.
    # 워커의 resolve_layer_lang이 레이어 언어·legacy 병기 판정에 쓴다.
    line_meta_lang: str = Field(default="ko", max_length=8)


class LineMetaAttachResponse(BaseModel):
    job_id: str
    # 잡의 현재 상태 (pending | queued | processing | completed | failed)
    status: str
    # stashed = 잡이 아직 진행 중 → 정렬(또는 최소한 결과 저장)에 반영된다
    # merged  = 잡이 이미 완료돼 완성된 싱크에 직접 병합했다
    # dropped = 잡이 실패/취소됐거나 완료 싱크를 찾지 못해 아무것도 하지 않았다
    applied: str
    # merged일 때 메타가 붙은 세그먼트 수
    merged_segments: int = 0


async def _merge_meta_into_completed_job(
    session,
    job,
    line_meta: list[LineMeta],
    attribution: Attribution | None,
    title: str | None,
    artist: str | None,
    line_meta_lang: str = "ko",
) -> LineMetaAttachResponse:
    """완료된 잡의 싱크에 메타를 직접 병합 — merged, 싱크를 못 찾으면 dropped.

    정렬은 다시 하지 않는다 (캐시 히트로 몇 초 만에 끝난 잡이 대표적).
    번역은 워커 완료 경로와 같은 규칙으로 언어 레이어에도 기록한다 — 이 경로만 빠지면
    늦게 붙인 번역이 legacy(ko 한정)에만 남고 언어별 조회가 영영 못 찾는다.
    """
    from everyric2.server.worker import (
        layer_origin,
        record_translation_layer,
        translation_layer_lines,
    )

    sync_repo = SyncRepository(session)
    existing = await sync_repo.get_by_video_and_hash(job.video_id, job.lyrics_hash)
    if existing is None:
        return LineMetaAttachResponse(job_id=job.id, status=job.status, applied="dropped")
    merged = _merge_meta_into_sync(existing, line_meta, attribution, line_meta_lang)
    attr_dump = attribution.model_dump() if attribution else None
    await record_translation_layer(
        session,
        job.video_id,
        [s.get("text") or "" for s in existing.timestamps.get("segments", [])],
        translation_layer_lines([m.model_dump() for m in line_meta]),
        line_meta_lang,
        origin=layer_origin(attr_dump),
        attribution=attr_dump,
    )
    await sync_repo.set_title_if_missing(existing, title, artist)
    return LineMetaAttachResponse(
        job_id=job.id, status=job.status, applied="merged", merged_segments=merged
    )


async def _attach_line_meta_to_job(
    job_id: str,
    line_meta: list[LineMeta],
    attribution: Attribution | None = None,
    title: str | None = None,
    artist: str | None = None,
    line_meta_lang: str = "ko",
) -> LineMetaAttachResponse | None:
    """번역·독음을 잡에 붙이는 실제 동작 — HTTP 엔드포인트와 서버 내부 생성 경로의 공용 몸통.

    잡을 찾지 못하면 None. HTTP 계약(404)으로 바꾸는 것은 엔드포인트의 몫이고, 내부
    호출자에게는 예외가 아니라 값으로 알려야 한다(백그라운드에서 올린 예외는 아무도 안 본다).
    """
    from everyric2.server.worker import stash_attribution, stash_line_meta, stash_title

    async with get_session() as session:
        job = await JobRepository(session).get_by_id(job_id)
        if not job:
            return None

        if job.status == "completed":
            return await _merge_meta_into_completed_job(
                session, job, line_meta, attribution, title, artist, line_meta_lang
            )

        if job.status == "failed":
            # 취소·실패한 잡 — 스태시를 남기면 정리 지점 없이 새므로 아무것도 하지 않는다
            return LineMetaAttachResponse(job_id=job_id, status=job.status, applied="dropped")

        job_status = job.status

    # 빈 배열도 그대로 넣는다 — 스태시 키의 존재 자체가 워커에게 "도착 확정" 신호다
    stash_line_meta(job_id, [m.model_dump() for m in line_meta], line_meta_lang)
    if attribution:
        stash_attribution(job_id, attribution.model_dump())
    stash_title(job_id, title, artist)

    # 스태시를 쓴 **뒤에 상태를 다시 읽는다**. 위 읽기와 이 쓰기 사이에 잡이 종결되면(캐시
    # 히트 완료·취소·실패) 스태시를 거둘 주체가 사라져 프로세스 수명 동안 영구 잔류하고
    # (누수), 메타는 싱크에 병합되지도 않는데 응답은 applied="stashed"라 사실과 다르다.
    # 종결됐으면 스태시를 회수하고 완료 싱크에 직접 병합(merged)하거나 버린다(dropped) —
    # 실제로 일어난 일을 응답에 담는다.
    #
    # 재확인 이후에 종결되는 창은 남는다(터미널 처리는 워커 쪽 코드가 소유해 여기서 같은 락을
    # 걸 수 없다). 다만 그 경우 스태시는 워커의 터미널 정리(_pop_stashes)가 거두므로 누수는
    # 되지 않고, 남는 것은 "stashed로 답했는데 반영되지 못했다"는 좁은 창뿐이다.
    async with get_session() as session:
        job = await JobRepository(session).get_by_id(job_id)
        if job is not None and job.status in ("completed", "failed"):
            from everyric2.server.api.worker import _pop_stashes

            _pop_stashes(job_id)
            if job.status == "failed":
                return LineMetaAttachResponse(
                    job_id=job_id, status=job.status, applied="dropped"
                )
            return await _merge_meta_into_completed_job(
                session, job, line_meta, attribution, title, artist, line_meta_lang
            )

    return LineMetaAttachResponse(job_id=job_id, status=job_status, applied="stashed")


@router.post("/jobs/{job_id}/line-meta", response_model=LineMetaAttachResponse)
async def attach_line_meta(job_id: str, request: LineMetaAttachRequest):
    """생성 잡에 번역·독음(line_meta)을 나중에 붙인다 — 번역을 다운로드·분리와 겹치는 경로.

    호출 순서: POST /api/sync/generate (line_meta_pending=true, line_meta 없이) → job_id 확보
    → 클라이언트가 번역·독음을 만드는 동안 서버는 다운로드·보컬 분리를 진행 → 이 엔드포인트
    → GET /api/job/{job_id} 폴링.

    잡이 아직 정렬 전이면 그 발음 텍스트로 정렬이 이뤄지고(독음 정렬), 대기 상한을 넘겨 이미
    원문으로 정렬됐거나 잡이 끝났으면 발음·번역 텍스트만 결과에 병합된다. 어느 쪽이든 호출은
    성공하며 applied가 무엇이 일어났는지 알린다 — 클라이언트는 분기할 필요가 없다.
    """
    from everyric2.server.api.job import _validate_job_id

    _validate_job_id(job_id)
    applied = await _attach_line_meta_to_job(
        job_id,
        request.line_meta,
        request.attribution,
        request.title,
        request.artist,
        request.line_meta_lang,
    )
    if applied is None:
        raise HTTPException(status_code=404, detail="잡을 찾을 수 없어요")
    return applied


class GenerateFromCaptionRequest(BaseModel):
    """video_id만으로 싱크를 만든다 — 가사는 서버가 유튜브 자막에서 조달한다.

    title/artist는 선택이다(커버 링크 후보 탐색의 단서로 함께 저장될 뿐, 자막 판정에는
    쓰이지 않는다). **자막 트랙을 고르는 필드는 일부러 두지 않았다** — 원어 판정은
    전적으로 서버 몫이고, 사용자가 고르는 단계를 없애는 것이 이 엔드포인트의 목적이다.
    """

    video_id: str = Field(pattern=_VIDEO_ID_PATTERN)
    title: str | None = Field(default=None, max_length=256)
    artist: str | None = Field(default=None, max_length=128)


class CaptionGenerateResponse(GenerateResponse):
    """생성 응답 + 실제로 어떤 자막을 왜 썼는지 — 클라이언트 표시·로그 확인용."""

    lang: str
    auto: bool
    track_label: str
    # 원어 판정 근거 (asr_orig | asr_only | video_language | sole_manual)
    reason: str
    line_count: int


# 가나(U+3040–U+30FF)와 CJK 한자(U+3400–U+9FFF) — 한글은 포함되지 않는다
_CJK_RE = re.compile(r"[぀-ヿ㐀-鿿]")


def _expects_pronunciation(lines: list[str]) -> bool:
    """이 원문에 발음표기(한글 독음)가 의미가 있는가.

    확장의 expectsPronunciation(content.ts)과 같은 규칙·같은 임계(5자)를 쓴다 — 한국어 곡에
    한글 독음을 붙이는 건 무의미하고 LLM 시간만 늘리며, 임계를 두면 제목의 한자 한두 자
    같은 잡음으로는 켜지지 않는다. 두 경로가 다른 규칙을 쓰면 같은 곡이 어디서 생성됐는지에
    따라 독음이 있다가 없어진다.
    """
    return len(_CJK_RE.findall("".join(lines))) >= 5


async def _translate_and_attach_line_meta(
    job_id: str,
    lines: list[str],
    source_lang: str | None,
    video_id: str,
    title: str | None,
    artist: str | None,
    human_translations: list[str] | None = None,
) -> None:
    """자막 가사의 번역·독음을 만들어 잡에 붙인다 (서버가 가사를 조달한 경로 전용).

    **어떤 이유로 실패해도 반드시 한 번은 붙인다** — 빈 리스트가 "붙일 것 없음" 확정 신호라
    (worker._PENDING_LINE_META 규약) 아무것도 안 붙이면 워커가 정렬 진입 직전에 대기 상한
    (LINE_META_WAIT_SEC)을 통째로 헛되게 태운다. 그래서 예외는 여기서 끝내고 로그로만 남긴다.

    `human_translations`는 같은 영상의 한국어 수동 자막에서 온 사람 번역이다(`lines`와 같은
    길이, 빈 문자열은 «그 줄에는 없음»). 있으면 **그 줄의 기계 번역을 덮는다** — 사람이 옮긴
    번역이 더 낫고, 같은 영상 자막이라 맥락도 맞다. 독음은 사람 자막에 없으므로 여전히 만든다.
    """
    from starlette.concurrency import run_in_threadpool

    from everyric2.server.api.translate import TranslateRequest, translate_lyrics

    human = human_translations if human_translations and any(human_translations) else None
    wants_pron = _expects_pronunciation(lines)

    # 독음이 필요 없고 번역은 사람 것이 있으면 LLM을 부를 이유가 없다 — 한국어 원문 곡에
    # 한국어 자막이 붙어 있는 경우가 아니라(그때는 번역 자체를 안 만든다), 원문이 라틴 문자인
    # 곡에 한국어 팬 자막이 있는 경우가 여기 걸린다.
    if human and not wants_pron:
        meta = [
            LineMeta(text=src, pronunciation=None, translation=tr)
            for src, tr in zip(lines, human)
            if tr
        ]
        applied = await _attach_line_meta_to_job(job_id, meta)
        logger.info(
            "Job %s: caption line_meta from human captions only (%d/%d lines, applied=%s)",
            job_id, len(meta), len(lines), getattr(applied, "applied", None),
        )
        return

    meta: list[LineMeta] = []
    try:
        # 엔진 선택(EVERYRIC_TRANSLATE_ENGINE)·톤·가나 오염 재시도까지 /api/translate와 완전히
        # 같은 경로를 쓴다 — 별도 호출을 만들면 두 경로의 번역 품질이 조용히 갈린다.
        # 동기 LLM 호출(수십 초)이라 이벤트 루프 밖으로 내보낸다: 같은 루프에서 이 잡의
        # 다운로드·보컬 분리가 돌고 있다.
        from fastapi import BackgroundTasks

        result = await run_in_threadpool(
            translate_lyrics,
            TranslateRequest(
                text="\n".join(lines),
                source_lang=source_lang or "auto",
                include_pronunciation=wants_pron,
                title=title,
                artist=artist,
                video_id=video_id,
            ),
            # persist를 안 쓰므로(기본 False) 실행될 일 없는 껍데기 — translate_lyrics가
            # BackgroundTasks를 필수로 받게 되어(POST /api/translate의 persist 브리지)
            # 직접 호출하는 이 경로도 인스턴스를 함께 넘겨야 한다.
            BackgroundTasks(),
        )
        # LLM이 echo한 original이 아니라 넘긴 원문으로 text를 채운다 — 병합(merge_line_meta)은
        # 정규화 텍스트 매칭이라 한 글자만 달라도 그 줄은 붙지 않는다. 줄 수가 같아 인덱스로
        # 대응시킬 수 있고, 짧은 응답이 와도 zip이 남는 줄을 조용히 버린다.
        for i, (src, line) in enumerate(zip(lines, result.lines)):
            pron = (line.pronunciation or "").strip() or None
            trans = (line.translation or "").strip() or None
            # 사람 번역이 그 줄에 있으면 기계 번역을 덮는다
            if human and i < len(human) and human[i]:
                trans = human[i]
            if pron or trans:
                meta.append(LineMeta(text=src, pronunciation=pron, translation=trans))
    except Exception:
        logger.exception("Job %s: caption line_meta translation failed", job_id)
        # LLM이 죽어도 사람 번역은 살아 있다 — 그것만이라도 붙인다
        if human:
            meta = [
                LineMeta(text=src, pronunciation=None, translation=tr)
                for src, tr in zip(lines, human)
                if tr
            ]

    applied = await _attach_line_meta_to_job(job_id, meta)
    if applied is None:
        logger.warning("Job %s: vanished before caption line_meta could be attached", job_id)
    else:
        logger.info(
            "Job %s: caption line_meta attached (%d/%d lines, applied=%s)",
            job_id,
            len(meta),
            len(lines),
            applied.applied,
        )


async def _process_caption_job(
    job_id: str,
    lines: list[str],
    source_lang: str | None,
    video_id: str,
    title: str | None,
    artist: str | None,
    pipeline: BackgroundTasks,
    human_translations: list[str] | None = None,
) -> None:
    """번역·독음 생성과 잡 처리(인프로세스 파이프라인 또는 원격 큐 진입)를 **동시에** 돌린다.

    둘을 각각 add_task로 걸면 안 된다: Starlette의 BackgroundTasks는 등록 순서대로 하나씩
    await하므로 먼저 걸린 잡 처리가 아직 시작조차 안 한 번역을 대기 상한까지 기다리고
    (원격 경로에선 그사이 큐에 올라가 번역을 통째로 놓친다), line_meta_pending이 노리는
    "다운로드·보컬 분리와 번역이 겹친다"가 성립하지 않는다.
    """
    await asyncio.gather(
        _translate_and_attach_line_meta(
            job_id, lines, source_lang, video_id, title, artist, human_translations
        ),
        pipeline(),
    )


@router.post("/generate-from-caption", response_model=CaptionGenerateResponse)
async def generate_sync_from_caption(
    request: GenerateFromCaptionRequest,
    background_tasks: BackgroundTasks,
    x_api_key: str | None = Header(default=None),
):
    """video_id만으로 유튜브 자막을 조달해 싱크 생성 잡을 만든다.

    자막 사용 가능 여부 판정 → 원어 트랙 자동 선택 → 본문 취득 → 가사 텍스트 구성까지
    서버가 하고, 그 뒤는 **/generate와 완전히 같은 경로**로 넘긴다(중복 싱크 재사용,
    활성 잡 합류, 큐 적재가 그대로 적용된다 — 여기서 복제하지 않는다).

    자막 타임스탬프는 버린다. 자막 타이밍은 가사 표시용이라 발성 시점과 어긋나고,
    정렬은 어차피 CTC가 오디오에서 새로 잡는다.

    **번역·독음도 이 경로에서는 서버가 만든다.** 클라이언트는 자기가 본 자막 라인 분할만
    알지, 정렬에 실제로 쓰이는 분할(clean_caption_lines·merge_rolling)은 서버 쪽이다.
    line_meta 병합은 정규화 텍스트 매칭이라 분할이 어긋나면 한 줄도 붙지 않는다 — 가사를
    조달한 쪽이 메타도 소유해야 번역·독음이 실제로 싱크에 남는다.

    실패는 detail={code, message}로 나간다. 4xx는 이 영상이 자막으로는 불가능하다는
    확정 판정이므로 클라이언트는 가사 직접 붙여넣기로 안내하면 된다. 5xx는 조달 실패라
    재시도 가치가 있다.
    """
    from starlette.concurrency import run_in_threadpool

    from everyric2.server.services.youtube_captions import (
        CaptionUnavailable,
        fetch_lyrics_from_captions,
    )

    try:
        # yt-dlp는 블로킹 IO라 이벤트 루프 밖으로 내보낸다 (extract + 트랙 다운로드 2회)
        found = await run_in_threadpool(fetch_lyrics_from_captions, request.video_id)
    except CaptionUnavailable as e:
        message = e.message
        if e.terminal:
            message = f"{message} — 가사를 직접 붙여넣어 주세요"
        raise HTTPException(
            status_code=e.http_status, detail={"code": e.code, "message": message}
        ) from e

    track = found.track
    # /generate가 등록하는 잡 처리 작업을 별도 컨테이너로 받는다 — background_tasks에 그대로
    # 얹으면 번역 작업과 순차 실행돼 겹치지 않는다 (_process_caption_job 참고)
    pipeline = BackgroundTasks()
    base = await generate_sync(
        GenerateRequest(
            video_id=request.video_id,
            lyrics=found.text,
            lyrics_source="youtube_caption",
            # CTC가 다루는 언어일 때만 지정한다 — 그 밖(gl/fil 등)은 엔진의 텍스트
            # 기반 자동 판정이 더 낫다 (services.youtube_captions.ALIGNABLE_LANGS)
            language=found.align_language,
            attribution=Attribution(
                name=f"유튜브 자막 · {track.label}",
                url=f"https://www.youtube.com/watch?v={request.video_id}",
            ),
            title=request.title,
            artist=request.artist,
            # 번역·독음은 아래 백그라운드 작업이 만들어 붙인다 — 정렬 진입 직전까지 기다려
            # 주므로 다운로드·보컬 분리와 겹치고, 독음이 붙으면 독음 정렬 경로를 탄다
            line_meta_pending=True,
        ),
        pipeline,
        # 어드민 키는 이 경로에서도 일일 생성 상한을 면제받아야 한다 (같은 검사를 재사용)
        x_api_key,
    )
    if base.status != "completed":
        # completed는 같은 자막 가사의 싱크를 그대로 재사용한 경우다 — job_id가 잡이 아니라
        # 싱크 id라 붙일 잡이 없고, generate_sync도 처리 작업을 등록하지 않는다(pipeline 비어
        # 있음). line_meta_pending도 그 경로에서 이미 무시된다(응답의 wait_sec=0).
        background_tasks.add_task(
            _process_caption_job,
            base.job_id,
            found.lines,
            track.language,
            request.video_id,
            request.title,
            request.artist,
            pipeline,
            found.translations,
        )
    return CaptionGenerateResponse(
        **base.model_dump(),
        lang=track.lang,
        auto=track.auto,
        track_label=track.label,
        reason=track.reason,
        line_count=len(found.lines),
    )


@router.post("/search-by-audio", response_model=SyncLookupResponse)
async def search_by_audio_hash(request: SearchByAudioRequest):
    async with get_session() as session:
        repo = SyncRepository(session)
        result = await repo.get_by_audio_hash(request.audio_hash)
        if result:
            return SyncLookupResponse(
                found=True,
                sync_id=result.id,
                timestamps=result.timestamps.get("segments", []),
                lyrics_source=result.engine,
                quality_score=result.quality_score,
                audio_hash=result.audio_hash,
                language=result.language,
                created_at=result.created_at.isoformat() if result.created_at else None,
            )
        return SyncLookupResponse(found=False)


@router.get("/list/{video_id}")
async def list_syncs_for_video(video_id: str):
    async with get_session() as session:
        repo = SyncRepository(session)
        results = await repo.get_by_video(video_id)
        return {
            "video_id": video_id,
            "syncs": [
                {
                    "sync_id": r.id,
                    "lyrics_hash": r.lyrics_hash,
                    "audio_hash": r.audio_hash,
                    "quality_score": r.quality_score,
                    "language": r.language,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in results
            ],
        }


class SearchSyncRequest(BaseModel):
    title: str | None = None
    artist: str | None = None
    limit: int = 10


@router.post("/search")
async def search_available_syncs(request: SearchSyncRequest):
    async with get_session() as session:
        repo = SyncRepository(session)
        results = await repo.get_all_unique_videos(limit=request.limit * 3)
        return {
            "syncs": [
                {
                    "video_id": r.video_id,
                    "audio_hash": r.audio_hash,
                    "quality_score": r.quality_score,
                    "language": r.language,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "lyrics_preview": _get_lyrics_preview(r.timestamps),
                }
                for r in results
            ]
        }


def _get_lyrics_preview(timestamps: dict) -> str:
    segments = timestamps.get("segments", [])
    if not segments:
        return ""
    texts = [s.get("text", "") for s in segments[:3]]
    return " / ".join(texts)[:100]


@router.post("/regenerate", response_model=GenerateResponse)
async def regenerate_sync(
    request: RegenerateRequest,
    background_tasks: BackgroundTasks,
    x_api_key: str | None = Header(default=None),
):
    from everyric2.server.worker import LINE_META_WAIT_SEC

    _validate_lyrics(request.lyrics)
    lyrics_hash_value = hash_lyrics(request.lyrics)
    await_line_meta = request.line_meta_pending and not request.line_meta
    wait_sec = LINE_META_WAIT_SEC if await_line_meta else 0.0

    # 재생성도 죽은 잡에 합류해 봉인될 수 있다 — 생성 경로와 같은 회수를 먼저 한다
    await reclaim_expired_leases()

    # 확인(기존 싱크/활성 잡)→생성 사이에 다른 요청이 끼면 중복 잡이 생긴다 — 직렬화
    async with _CREATE_LOCK, get_session() as session:
        sync_repo = SyncRepository(session)

        # 잡 생성 전 — 이 영상의 **최신** 싱크(새 lyrics_hash와 무관하게)에 레거시(세그
        # 전용) ko 번역이 남아 있고 아직 레이어가 없으면 지금 백필한다. 안 하면 이번
        # 재생성(특히 force)으로 그 세그 자체가 새 싱크로 갈아끼워지면서 옛 ko 번역
        # (위키 사람 번역 포함)을 되살릴 방법이 사라진다 — TranslationLayer.origin="legacy"
        # 주석 참고.
        latest_syncs = await sync_repo.get_by_video(request.video_id)
        if latest_syncs:
            latest_timestamps = latest_syncs[0].timestamps or {}
            await _schedule_ko_backfill_if_needed(
                session,
                background_tasks,
                request.video_id,
                latest_timestamps.get("segments", []) or [],
                latest_timestamps.get("attribution"),
            )

        if request.force:
            # 강제 재생성은 GPU 수십 초를 태우는 파괴적 행위 — 공개 배포에선 일일 한도 적용
            await _check_destructive_limit(session, "regenerate", request.video_id, x_api_key)
        if not request.force:
            existing = await sync_repo.get_by_video_and_hash(request.video_id, lyrics_hash_value)
            if existing:
                if request.line_meta or request.attribution:
                    _merge_meta_into_sync(existing, request.line_meta, request.attribution)
                await sync_repo.set_title_if_missing(existing, request.title, request.artist)
                return GenerateResponse(
                    job_id=existing.id,
                    status="completed",
                    estimated_time=0,
                )

        job_repo = JobRepository(session)
        # 재생성도 같은 잡 진행 중이면 합류 — 연타가 동시 다운로드(WinError 32)를 만들지 않게
        active = await job_repo.get_active_by_video(request.video_id, lyrics_hash_value)
        if active:
            return GenerateResponse(
                job_id=active.id,
                status="processing",
                estimated_time=15,
                line_meta_wait_sec=wait_sec,
            )
        if not request.force:
            # force는 위에서 이미 훨씬 엄격한 파괴적 한도(기본 2회/24h)를 통과했다 —
            # 여기서 또 세면 한 번의 재생성이 두 예산을 먹는다. 비force 재생성은 GPU
            # 소비가 /generate와 같으므로 같은 상한을 쓴다.
            await _check_action_limit(
                session, "generate", request.video_id, x_api_key, DAILY_GENERATE_LIMIT
            )
        job = await job_repo.create(
            video_id=request.video_id,
            lyrics=request.lyrics,
            language=request.language,
            target_lang=request.target_lang,
        )
        job_id = job.id

    from everyric2.server.worker import (
        stash_attribution,
        stash_force,
        stash_line_meta,
        stash_title,
    )

    if request.force:
        # 워커의 (audio_hash, lyrics_hash) 재사용 검사까지 건너뛰어야 진짜 재생성이 된다
        stash_force(job_id)
    if request.line_meta:
        stash_line_meta(
            job_id, [m.model_dump() for m in request.line_meta], request.line_meta_lang
        )
    if request.attribution:
        stash_attribution(job_id, request.attribution.model_dump())
    stash_title(job_id, request.title, request.artist)
    await _dispatch_job(job_id, background_tasks, await_line_meta=await_line_meta)

    return GenerateResponse(
        job_id=job_id,
        status="processing",
        estimated_time=15,
        line_meta_wait_sec=wait_sec,
    )
