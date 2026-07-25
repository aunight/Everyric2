"""쿠키 엔드포인트의 어드민 게이트와 크기 상한.

왜 이 테스트가 있는가: `/api/cookies`의 쓰기·삭제에는 어드민 게이트도 크기 상한도 없었다.
전역 미들웨어(`main.py::require_api_key`)는 **일반 사용자 키도 통과**시키므로, 키를 가진
누구나 배포의 유튜브 쿠키를 지워 그 순간부터 모든 사용자의 다운로드를 실패하게 만들거나,
임의 내용을 심어 서버 IP로 남의 계정을 쓰게 할 수 있었다. 강제 재생성·초기화처럼 GPU
몇십 초를 태우는 행위에는 일일 한도가 걸려 있는데 배포 전체를 멈추는 경로가 무방비였다.

어드민 키를 설정하지 않은 배포(단일 사용자 로컬)는 그대로 통과해야 한다 — 게이트를 무조건
세우면 그런 배포는 쿠키를 넣을 방법이 사라진다. 그 두 방향을 함께 못박는다.
"""

import asyncio
from contextlib import contextmanager

import pytest
from fastapi import HTTPException

from everyric2.config.settings import get_settings
from everyric2.server.api.cookies import (
    CookiesTextRequest,
    clear_cookies,
    get_cookies_status,
    set_cookies_text,
)


@contextmanager
def _admin_key(value: str | None):
    server = get_settings().server
    orig = server.admin_api_key
    object.__setattr__(server, "admin_api_key", value)
    try:
        yield
    finally:
        object.__setattr__(server, "admin_api_key", orig)


@contextmanager
def _cookie_sandbox(tmp_path):
    """쿠키 경로를 tmp로 돌려 실제 배포 파일을 건드리지 않는다."""
    import everyric2.server.api.cookies as mod

    target = tmp_path / "cookies.txt"
    orig_write, orig_legacy = mod.cookies_write_path, mod.LEGACY_COOKIES_PATH
    settings = get_settings().audio
    orig_file, orig_browser = settings.cookie_file, settings.cookies_from_browser
    mod.cookies_write_path = lambda: target
    mod.LEGACY_COOKIES_PATH = tmp_path / "legacy.txt"
    try:
        yield target
    finally:
        mod.cookies_write_path = orig_write
        mod.LEGACY_COOKIES_PATH = orig_legacy
        object.__setattr__(settings, "cookie_file", orig_file)
        object.__setattr__(settings, "cookies_from_browser", orig_browser)


def test_non_admin_cannot_wipe_the_deployment_cookies(tmp_path):
    with _admin_key("admin-secret"), _cookie_sandbox(tmp_path) as target:
        target.write_text("# real cookies")
        for key in (None, "user-key", "wrong"):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(clear_cookies(x_api_key=key))
            assert exc.value.status_code == 403
        assert target.read_text() == "# real cookies"  # 파일이 살아 있어야 한다


def test_non_admin_cannot_overwrite_the_cookies(tmp_path):
    with _admin_key("admin-secret"), _cookie_sandbox(tmp_path) as target:
        target.write_text("# real cookies")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(set_cookies_text(
                CookiesTextRequest(content="# attacker cookies"), x_api_key="user-key"))
        assert exc.value.status_code == 403
        assert target.read_text() == "# real cookies"


def test_admin_key_may_still_manage_cookies(tmp_path):
    with _admin_key("admin-secret"), _cookie_sandbox(tmp_path) as target:
        asyncio.run(set_cookies_text(
            CookiesTextRequest(content="# admin set"), x_api_key="admin-secret"))
        assert target.read_text() == "# admin set"
        asyncio.run(clear_cookies(x_api_key="admin-secret"))
        assert not target.exists()


def test_deployment_without_an_admin_key_is_not_locked_out(tmp_path):
    # 어드민 키를 안 쓰는 배포(로컬 단일 사용자)는 게이트가 없어야 한다 —
    # 있으면 그 배포는 쿠키를 넣을 방법이 사라진다.
    with _admin_key(None), _cookie_sandbox(tmp_path) as target:
        asyncio.run(set_cookies_text(CookiesTextRequest(content="# local"), x_api_key=None))
        assert target.read_text() == "# local"
        asyncio.run(clear_cookies(x_api_key=None))
        assert not target.exists()


def test_oversized_cookie_body_is_rejected(tmp_path):
    import everyric2.server.api.cookies as mod

    with _admin_key(None), _cookie_sandbox(tmp_path) as target:
        big = "x" * (mod._MAX_COOKIE_BYTES + 1)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(set_cookies_text(CookiesTextRequest(content=big), x_api_key=None))
        assert exc.value.status_code == 413
        assert not target.exists()  # 상한 초과는 쓰기 전에 막혀야 한다


def test_status_endpoint_stays_open():
    # 조회는 게이트하지 않는다 — 설정 화면이 "쿠키가 설정됐는가"를 물어야 하고,
    # 응답에 쿠키 내용은 들어가지 않는다(configured/method/path뿐).
    with _admin_key("admin-secret"):
        status = asyncio.run(get_cookies_status())
        assert hasattr(status, "configured")
