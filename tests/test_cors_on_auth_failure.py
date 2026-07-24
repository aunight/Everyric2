"""인증 거절(401)에도 CORS 헤더가 붙도록 미들웨어 순서를 고정한다.

실측 사고: 인증 미들웨어가 CORS 미들웨어보다 **바깥**에 등록돼 있어, 키가 틀리면
401을 직접 반환하면서 CORS 미들웨어를 아예 거치지 않았다. 그러면 응답에
Access-Control-Allow-Origin이 없고 브라우저가 그 응답을 통째로 차단한다 — 확장의
fetch는 상태 코드 대신 TypeError("Failed to fetch")를 받는다. 화면에는 "API 키가
틀렸어요"가 아니라 "서버에 연결할 수 없어요"가 떠서 사용자가 원인을 알 수 없었다.

서버에서 curl로는 드러나지 않는 결함이다(Origin이 없으면 CORS와 무관하게 401 본문이
그대로 온다). 실제로 브라우저 확장에서만 재현됐다.

HTTP 왕복 대신 등록 순서를 검사하는 이유: TestClient는 httpx를 요구하는데 이 프로젝트
의존성에 없다. Starlette은 `add_middleware`가 `user_middleware.insert(0, ...)`이라
**인덱스 0이 가장 바깥**이고, CORS가 바깥이어야 안쪽에서 만들어진 오류 응답에도
헤더를 붙일 수 있다. 이 불변식이 곧 결함의 원인이자 수정의 핵심이다.
"""
from fastapi.middleware.cors import CORSMiddleware


def _app():
    from everyric2.server.main import app

    return app


def test_cors_middleware_is_outermost():
    """CORS가 인증 미들웨어보다 바깥이어야 401에도 헤더가 붙는다."""
    stack = _app().user_middleware
    assert stack, "미들웨어가 하나도 등록돼 있지 않다"
    assert stack[0].cls is CORSMiddleware, (
        "CORSMiddleware가 가장 바깥이 아니다 — 인증 401이 CORS를 거치지 않아 "
        f"브라우저가 응답을 차단한다. 현재 가장 바깥: {stack[0].cls}"
    )


def test_auth_middleware_is_registered_inside_cors():
    """인증 미들웨어가 여전히 존재하되 CORS 안쪽에 있어야 한다."""
    stack = _app().user_middleware
    assert len(stack) >= 2, f"인증 미들웨어가 사라졌다 (스택 {len(stack)}개)"
    inner = [m.cls.__name__ for m in stack[1:]]
    assert any("BaseHTTPMiddleware" in n for n in inner), (
        f"CORS 안쪽에 http 미들웨어가 없다 — 등록 순서를 확인하라: {inner}"
    )


def test_cors_allows_only_extension_origin():
    """일반 웹사이트 오리진에는 열지 않는다 (기존 보안 의도 유지)."""
    cors = _app().user_middleware[0]
    regex = cors.kwargs.get("allow_origin_regex")
    assert regex and "chrome-extension" in regex, f"확장 오리진 정규식이 아니다: {regex}"
    assert not cors.kwargs.get("allow_origins"), "모든 오리진을 여는 설정이 들어갔다"
