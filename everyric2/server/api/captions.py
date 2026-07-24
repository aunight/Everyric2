"""유튜브 자막 조회 API — **폐기 예정**.

이 라우터의 두 GET 라우트는 확장이 "자막 트랙 목록을 받아 사용자가 하나를 고르는" UI를
띄우던 시절의 것이다. 사용자가 자막을 고르는 단계는 없애기로 했고, 대체 경로는
`POST /api/sync/generate-from-caption`(video_id만으로 원어 트랙 자동 판정)이다.
확장이 아직 이 라우트를 호출하고 있어 지금 지우면 구버전 확장이 깨지므로 당분간 남긴다.
제거 시 확장에서 함께 지워야 하는 것: `listCaptionTracks`/`fetchCaptionLines`
(everyric-api.ts), `YT_CAPTION_TRACKS`/`YT_CAPTION_TEXT` 메시지 핸들러(background.ts),
`handleCaptionTracks`/`handleCaptionPick`(content.ts), `renderCaptionTracks`(panels.ts),
`showCaptionTracks`(overlay.ts·pip.ts), `CaptionTrack` 타입(types.ts).

조달 로직 자체는 `everyric2.server.services.youtube_captions`가 소유한다 — 라우트는
얇은 껍데기다. 워치 페이지에서 긁은 timedtext URL이 POT 강제로 브라우저 밖에서는
빈 본문을 주기 때문에(2026-07 실측) 서버의 yt-dlp 경로는 계속 필요하다.
"""

from fastapi import APIRouter, HTTPException

from everyric2.server.services.youtube_captions import (
    LANG_RE,
    VIDEO_ID_RE,
    CaptionUnavailable,
    download_track_lines,
    extract_caption_info,
    json3_events_to_lines,  # noqa: F401 — 기존 임포트 경로 유지 (tests/test_captions.py)
    list_public_tracks,
)

router = APIRouter(prefix="/api/captions", tags=["captions"])


def _validate(video_id: str, lang: str | None = None) -> None:
    """경로 파라미터를 URL·파일 glob에 그대로 쓰므로 형식을 강제한다 (쿼리 인젝션 차단)."""
    if not VIDEO_ID_RE.match(video_id):
        raise HTTPException(status_code=422, detail="invalid video_id")
    if lang is not None and not LANG_RE.match(lang):
        raise HTTPException(status_code=422, detail="invalid lang")


def _http(exc: CaptionUnavailable) -> HTTPException:
    return HTTPException(status_code=exc.http_status, detail=exc.message)


@router.get("/{video_id}")
def list_caption_tracks(video_id: str):
    """이 영상의 자막 트랙 목록. **폐기 예정** — 모듈 상단 주석 참고."""
    _validate(video_id)
    try:
        info = extract_caption_info(video_id)
    except CaptionUnavailable as e:
        raise _http(e) from e
    return {"video_id": video_id, "tracks": list_public_tracks(info)}


@router.get("/{video_id}/{lang}")
def get_caption_lines(video_id: str, lang: str, auto: bool = False):
    """선택한 트랙의 자막을 타이밍 포함 라인으로 반환. **폐기 예정** — 상단 주석 참고."""
    _validate(video_id, lang)
    try:
        return {
            "video_id": video_id,
            "lang": lang,
            "auto": auto,
            "lines": download_track_lines(video_id, lang, auto),
        }
    except CaptionUnavailable as e:
        raise _http(e) from e


__all__ = ["router", "json3_events_to_lines", "list_caption_tracks", "get_caption_lines"]
