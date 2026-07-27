import logging
import re

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from everyric2.config.settings import get_settings
from everyric2.translation.translator import LyricsTranslator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/translate", tags=["translate"])

# 한글 독음에 가나가 섞이면 LLM 출력 실수 ("카에데노 타네가 오츠 에코오 체ン바" 실측)
_KANA_RE = re.compile(r"[぀-ゟ゠-ヿ]")


def bad_pron_indices(lines) -> list[int]:
    """발음표기에 가나 문자가 섞인 라인 인덱스 (LLM 전사 실수 감지)."""
    return [
        i for i, line in enumerate(lines)
        if line.pronunciation and _KANA_RE.search(line.pronunciation)
    ]


def merge_pron_retry(lines, retry_lines, bad: list[int]) -> int:
    """오염 라인의 발음만 재시도 결과로 교체 (번역은 1차 결과 유지 — 이미 정상).

    재시도도 오염이면 가나 문자만 제거하고, 제거로 내용이 크게 유실되면(>20%)
    발음을 비워 클라이언트가 발음 없이 표시하게 한다. 교체/정리한 라인 수 반환.
    """
    fixed = 0
    for i in bad:
        retry_pron = (
            retry_lines[i].pronunciation
            if retry_lines is not None and i < len(retry_lines)
            else None
        )
        if retry_pron and not _KANA_RE.search(retry_pron):
            lines[i].pronunciation = retry_pron
            fixed += 1
            continue
        original = lines[i].pronunciation or ""
        stripped = " ".join(_KANA_RE.sub("", original).split())
        lines[i].pronunciation = (
            stripped if len(stripped) >= len(original) * 0.8 else None
        )
        fixed += 1
    return fixed


class TranslateRequest(BaseModel):
    text: str = Field(..., description="Text to translate (newline-separated lines)")
    source_lang: str = Field(default="auto", description="Source language code")
    target_lang: str = Field(default="ko", description="Target language code")
    tone: str = Field(default="natural", description="Translation tone")
    include_pronunciation: bool = Field(
        default=False, description="Include pronunciation of the original text"
    )
    # 곡 컨텍스트 (선택) — LLM이 가사 맥락(제목/아티스트)을 알고 번역하게 한다
    title: str | None = Field(default=None, description="Song title for context")
    artist: str | None = Field(default=None, description="Artist name for context")
    # 로그 상관용 (선택) — 지금까지 서버 로그에 어떤 곡인지 안 남아 품질 사고의 사후
    # 추적이 불가능했다. 선택 필드라 기존 호출자는 그대로 동작한다.
    video_id: str | None = Field(default=None, description="Video id, logged for diagnostics")
    # true이고 video_id가 있으면 성공한 번역 결과를 TranslationLayer(origin="llm")로 저장한다
    # — 언어별 번역 레이어(video_id, fingerprint, target_lang)에 실려 GET /api/sync?lang=
    # 조회가 재사용한다. video_id가 없으면 조용히 무시(저장할 키가 없다).
    persist: bool = Field(default=False, description="Persist result to the translation layer")


class TranslationLineResponse(BaseModel):
    original: str
    translation: str
    pronunciation: str | None = None
    # NIM 응답 잘림으로 이 라인만 복구하지 못한 경우 True(원문만 채워짐). 확장은 무시해도
    # 되지만, 부분 실패를 표시하거나 재시도 판단에 쓸 수 있게 결과에 남긴다.
    failed: bool = False


class TranslateResponse(BaseModel):
    lines: list[TranslationLineResponse]
    source_lang: str
    target_lang: str
    engine: str
    # 원문 언어가 대상 언어와 같아 번역을 건너뛴 경우 True — translation이 전부 빈 문자열로
    # 온다. 클라이언트가 '번역 실패'와 '번역할 것이 없음'을 구분하는 데 쓴다.
    translation_skipped: bool = False


async def _persist_translation_layer(
    video_id: str, texts: list[str], target_lang: str, translations: list[str]
) -> None:
    """번역 결과를 TranslationLayer(origin="llm")로 저장 — BackgroundTasks로 응답 후 실행된다.

    translate_lyrics는 동기(plain def) 핸들러라 요청 스레드에서 바로 await할 async 세션을
    쓸 수 없다. 이 리포의 기존 브리지 패턴(sync.py의 background_tasks.add_task)을 그대로
    따른다 — 핸들러는 결과를 즉시 반환하고, 저장은 응답 이후 이벤트 루프에서 처리된다.
    실패해도 번역 응답 자체는 이미 나갔으므로 로그만 남기고 삼킨다.
    """
    from everyric2.server.db.connection import get_session
    from everyric2.server.db.repository import TranslationLayerRepository
    from everyric2.server.text_fingerprint import lines_fingerprint

    try:
        fingerprint = lines_fingerprint(texts)
        lines = [{"text": t, "translation": tr} for t, tr in zip(texts, translations)]
        async with get_session() as session:
            await TranslationLayerRepository(session).upsert_layer(
                video_id, fingerprint, target_lang, lines=lines, attribution=None, origin="llm"
            )
    except Exception:
        logger.exception("Failed to persist translation layer for video %s", video_id)


@router.post("", response_model=TranslateResponse)
def translate_lyrics(request: TranslateRequest, background_tasks: BackgroundTasks):
    # background_tasks는 `BackgroundTasks | None` 같은 optional 주석을 쓸 수 없다 —
    # FastAPI가 그러면 이 타입을 더 이상 "시스템이 주입하는 특수 의존성"으로 인식하지
    # 않고 일반 Pydantic 필드로 취급해 라우트 등록 자체가 FastAPIError로 깨진다(실측).
    # 그래서 필수 인자로 두고, 이 함수를 직접 호출하는 기존 경로들(sync.py의 자막 자동
    # 번역, 일부 테스트)이 전부 BackgroundTasks() 인스턴스를 함께 넘기도록 맞춘다.
    # async def가 아닌 plain def — 내부의 동기 LLM 호출(requests.post, 수십 초)이
    # 이벤트 루프를 세우면 /health까지 밀려 확장이 서버가 죽은 줄 알게 된다.
    # FastAPI는 plain def 엔드포인트를 스레드풀에서 돌린다.

    # 입력 상한 — 대형(122B) 모델 특성상 초장문은 분 단위로 걸려(5000자 실측 90초+)
    # 확장 UI가 죽은 것처럼 보인다. 가사 규모를 한참 넘는 입력은 422로 즉시 거절한다.
    # 주의: 아래 try 안에서 올리면 except Exception이 500으로 삼킨다 — 반드시 밖에서.
    lines_in = request.text.split("\n")
    if len(request.text) > 15000 or len(lines_in) > 400 or any(len(ln) > 1000 for ln in lines_in):
        raise HTTPException(
            status_code=422,
            detail="가사가 너무 길어요 — 최대 400줄, 줄당 1000자, 전체 15000자까지 지원해요.",
        )
    valid_tones = ("literal", "natural", "poetic", "casual", "formal")
    if request.tone not in valid_tones:
        raise HTTPException(
            status_code=422, detail=f"unknown tone '{request.tone}' — one of {valid_tones}"
        )

    try:
        # 전역 싱글턴을 직접 변조하면 스레드풀 동시 요청끼리 tone/발음 플래그가 새어
        # 나간다 — 요청마다 깊은 복사본을 만들어 격리한다
        settings = get_settings().translation.model_copy(deep=True)
        object.__setattr__(settings, "tone", request.tone)
        settings.include_pronunciation = request.include_pronunciation

        translator = LyricsTranslator(settings=settings, log_label=request.video_id)
        log_prefix = f"[{request.video_id}] " if request.video_id else ""

        context = None
        if request.title:
            context = f'"{request.title.strip()}"'
            if request.artist:
                context += f" by {request.artist.strip()}"

        if request.include_pronunciation:
            result = translator.translate_with_pronunciation(
                request.text,
                source_lang=request.source_lang,
                target_lang=request.target_lang,
                context=context,
            )
            # 가나 오염 가드는 target=ko 전용이다 — 발음 계약이 "가나 없는 한글"인 건 ko
            # 타깃뿐이고, ja 타깃(가나 곡의 정상 가나 발음)에서 돌면 멀쩡한 발음을 파괴한다.
            bad = bad_pron_indices(result.lines) if request.target_lang == "ko" else []
            if bad:
                logger.warning(
                    "%sKana leaked into %d pronunciation lines; retrying once",
                    log_prefix,
                    len(bad),
                )
                retry_lines = None
                try:
                    retry = translator.translate_with_pronunciation(
                        request.text,
                        source_lang=request.source_lang,
                        target_lang=request.target_lang,
                        context=context,
                    )
                    retry_lines = retry.lines
                except Exception:
                    logger.exception("Pronunciation retry failed; sanitizing in place")
                merge_pron_retry(result.lines, retry_lines, bad)
        else:
            result = translator._translator.translate(
                request.text,
                source_lang=request.source_lang,
                target_lang=request.target_lang,
                context=context,
            )

        skipped = getattr(result, "translation_skipped", False)
        failed = [i for i, line in enumerate(result.lines) if getattr(line, "failed", False)]
        if failed:
            # 부분 실패 — 전체 500 대신 실패 라인만 원문으로 반환됐음을 서버 로그에 남긴다
            logger.warning(
                "%sTranslation partially failed: %d/%d lines unrecovered (indices %s)",
                log_prefix,
                len(failed),
                len(result.lines),
                failed,
            )
        elif not skipped:
            # 절단도 아니고 스킵도 아닌데 비어 있는 라인 — 저품질 재요청 후에도 남은 경우다
            blank = [
                i
                for i, line in enumerate(result.lines)
                if len(line.original.strip()) >= 2 and not (line.translation or "").strip()
            ]
            if blank:
                logger.warning(
                    "%sTranslation left %d/%d lines empty after retry (indices %s)",
                    log_prefix,
                    len(blank),
                    len(result.lines),
                    blank,
                )

        # 스킵 결과(translation_skipped)는 번역 필드가 전부 빈 값이라 저장할 것이 없다 —
        # 레이어에 빈 값을 얹으면 기존에 저장된 실제 번역을 덮어쓸 위험만 있다.
        if request.persist and request.video_id and not skipped:
            background_tasks.add_task(
                _persist_translation_layer,
                request.video_id,
                [line.original for line in result.lines],
                request.target_lang,
                [line.translation for line in result.lines],
            )

        return TranslateResponse(
            lines=[
                TranslationLineResponse(
                    original=line.original,
                    translation=line.translation,
                    pronunciation=line.pronunciation,
                    failed=getattr(line, "failed", False),
                )
                for line in result.lines
            ],
            source_lang=result.source_lang,
            target_lang=result.target_lang,
            engine=result.engine,
            translation_skipped=skipped,
        )
    except Exception:
        # 내부 예외 문자열엔 API URL(키 포함 가능)·경로가 실릴 수 있다 — 클라엔 일반
        # 메시지만, 상세는 서버 로그로
        logger.exception("Translation request failed")
        raise HTTPException(status_code=500, detail="translation failed (see server log)")
