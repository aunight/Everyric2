from fastapi import APIRouter, Header, HTTPException, UploadFile
from pydantic import BaseModel

from everyric2.config.paths import LEGACY_COOKIES_PATH, cookies_read_path, cookies_write_path
from everyric2.config.settings import get_settings

router = APIRouter(prefix="/api/cookies", tags=["cookies"])

# 업로드 쿠키 본문 상한. Netscape 쿠키 파일은 도메인당 수백 바이트라 실사용은 수 KB이고,
# 브라우저 전체를 내보내도 100KB를 넘지 않는다. 상한이 없으면 이 엔드포인트가 임의 크기
# 쓰기 경로가 된다.
_MAX_COOKIE_BYTES = 256 * 1024


def _require_admin(x_api_key: str | None) -> None:
    """쿠키 변경·삭제는 어드민만. 배포 전체의 다운로드 가능 여부를 좌우하기 때문이다.

    쿠키를 지우면 그 순간부터 **모든 사용자**의 다운로드가 실패하고, 임의 내용을 심으면
    서버 IP로 남의 유튜브 계정을 쓰게 된다. 그런데 여기에는 어드민 게이트도 일일 한도도
    없었다 — 전역 미들웨어(main.py require_api_key)가 일반 사용자 키도 통과시키므로
    키를 가진 누구나 부를 수 있었다. 강제 재생성·초기화처럼 GPU 몇십 초를 태우는 행위에는
    한도가 걸려 있는데, 배포 전체를 멈추는 이 경로가 무방비인 것은 균형이 맞지 않는다.

    ``admin_api_key``가 설정되지 않은 배포(단일 사용자 로컬)는 그대로 통과시킨다 —
    _check_destructive_limit과 같은 규칙이다. 어드민 키를 안 쓰는 배포에 게이트를 세우면
    쿠키를 넣을 방법이 사라진다.
    """
    server = get_settings().server
    if not server.admin_api_key:
        return
    if x_api_key != server.admin_api_key:
        raise HTTPException(
            status_code=403,
            detail="쿠키 변경은 어드민 키가 필요해요 — 이 배포의 모든 사용자에게 영향을 줘요.",
        )


class CookiesStatus(BaseModel):
    configured: bool
    method: str | None = None
    path: str | None = None


class CookiesTextRequest(BaseModel):
    content: str


@router.get("", response_model=CookiesStatus)
async def get_cookies_status():
    settings = get_settings().audio

    if settings.cookies_from_browser:
        return CookiesStatus(
            configured=True,
            method="browser",
            path=settings.cookies_from_browser,
        )
    elif settings.cookie_file and settings.cookie_file.exists():
        return CookiesStatus(
            configured=True,
            method="file",
            path=str(settings.cookie_file),
        )
    elif cookies_read_path().exists():
        return CookiesStatus(
            configured=True,
            method="file",
            path=str(cookies_read_path()),
        )

    return CookiesStatus(configured=False)


@router.post("/upload")
async def upload_cookies_file(file: UploadFile, x_api_key: str | None = Header(default=None)):
    _require_admin(x_api_key)
    if not file.filename or not file.filename.endswith(".txt"):
        raise HTTPException(status_code=400, detail="File must be a .txt file")

    content = await file.read()
    if len(content) > _MAX_COOKIE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"쿠키 파일이 너무 커요 ({len(content) // 1024}KB) — "
            f"{_MAX_COOKIE_BYTES // 1024}KB까지만 받아요.",
        )

    target = cookies_write_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)

    settings = get_settings().audio
    settings.cookie_file = target
    settings.cookies_from_browser = None

    return {"status": "ok", "path": str(target)}


@router.post("/text")
async def set_cookies_text(
    request: CookiesTextRequest, x_api_key: str | None = Header(default=None)
):
    _require_admin(x_api_key)
    if not request.content.strip():
        raise HTTPException(status_code=400, detail="Content cannot be empty")
    if len(request.content.encode()) > _MAX_COOKIE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"쿠키 내용이 너무 커요 — {_MAX_COOKIE_BYTES // 1024}KB까지만 받아요.",
        )

    target = cookies_write_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(request.content)

    settings = get_settings().audio
    settings.cookie_file = target
    settings.cookies_from_browser = None

    return {"status": "ok", "path": str(target)}


@router.delete("")
async def clear_cookies(x_api_key: str | None = Header(default=None)):
    _require_admin(x_api_key)
    # 새 위치와 레거시(/tmp → Windows에선 C:\tmp) 둘 다 지워야 완전히 초기화된다
    for path in (cookies_write_path(), LEGACY_COOKIES_PATH):
        path.unlink(missing_ok=True)

    settings = get_settings().audio
    settings.cookie_file = None
    settings.cookies_from_browser = None

    return {"status": "ok"}
