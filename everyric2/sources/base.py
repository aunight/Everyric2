"""가사 위키 소스 공통 계약 — 줄 모양, HTML 셀 정리, 예의 있는 HTTP 조회.

확장(everyric2-chrome/src/lib/sources.ts)의 ``SourceLine``과 같은 계약이다: 마크업도
라이선스도 다른 위키들이 "원문 + 발음 + 번역 줄 목록"이라는 한 모양으로 나온다는 것만
공유한다. 파서(vocaro.py·miraheze.py)는 자기 HTML을 이 모양으로 어댑트해 반환한다.

**포팅 주의 — 정규식 낱말 경계**: 확장의 파서는 JS 정규식이고 ``\\b``가 ASCII 기준이다.
파이썬 ``\\b``는 유니코드 기준이라 «곡名feat.가수»처럼 CJK 글자 뒤에 붙은 ``feat``에서
동작이 갈린다(JS는 경계 인정, 파이썬은 CJK도 낱말 문자라 경계 불인정 → 매칭 실패).
보카로 제목에서 흔한 모양이라 ASCII 낱말 경계를 명시적으로 쓴다 — :data:`ASCII_LB` /
:data:`ASCII_RB` 참고.
"""

from __future__ import annotations

import html
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests

logger = logging.getLogger(__name__)

# JS ``\b``와 같은 ASCII 전용 낱말 경계(위 파일 머리말 참고). 유니코드 ``\b``를 쓰면
# CJK 제목에 붙은 라틴 토큰(feat./MV)을 놓친다.
ASCII_LB = r"(?<![A-Za-z0-9_])"
ASCII_RB = r"(?![A-Za-z0-9_])"


@dataclass(frozen=True)
class SourceLine:
    """가사 한 줄 — 원문과, 있으면 발음 표기·번역.

    ``pronunciation``의 표기와 ``translation``의 언어는 소스마다 고정이다(vocaro는
    한글 독음 + 한국어, miraheze는 로마자 + 영어) — 소비처가 소스별로 안다.
    """

    text: str
    pronunciation: str | None = None
    translation: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        """서버 ``line_meta`` 계약과 같은 dict — 그대로 API 본문에 실을 수 있다."""
        return {
            "text": self.text,
            "pronunciation": self.pronunciation,
            "translation": self.translation,
        }


# ── HTML 셀 → 텍스트 (확장 파서의 cellText 포트) ──────────────────

_RT_SPAN_RE = re.compile(r'<span class="rt">[\s\S]*?</span>')
_BR_RE = re.compile(r"<br\s*/?>")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def cell_text(cell_html: str, *, drop_ruby: bool = False) -> str:
    """표 칸 HTML에서 텍스트만 뽑아 공백을 접는다.

    ``drop_ruby``면 후리가나 읽기(``<span class="rt">``)를 먼저 걷어낸다 — vocaro의
    원문 칸은 한자에 루비가 달려 있어 그대로 두면 "光ひかり"처럼 읽기가 원문에 섞인다.
    ``<br>``은 공백으로 접는다(한 칸 = 한 줄이라는 계약을 지킨다).
    """
    text = _RT_SPAN_RE.sub("", cell_html) if drop_ruby else cell_html
    text = _BR_RE.sub(" ", text)
    text = _TAG_RE.sub("", text)
    return _WS_RE.sub(" ", html.unescape(text)).strip()


# ── 예의 있는 HTTP 조회 ────────────────────────────────────────────

# 대량 인제스트는 곡마다 위키를 두드린다 — 남의 서버에 붙는 속도를 코드가 스스로 제한한다.
DEFAULT_MIN_INTERVAL_SEC = 1.0
DEFAULT_TIMEOUT_SEC = 8.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SEC = 2.0

# 되받아 볼 가치가 있는 상태 코드 — 429(속도 제한)와 5xx(일시 장애)만.
# 404/403은 다시 물어도 같은 답이라 즉시 포기한다.
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


class WikiFetcher:
    """호출 간격을 지키고 일시 실패에 백오프하는 GET 전용 조회기.

    한 인스턴스가 한 사이트를 담당한다(간격이 인스턴스 단위) — vocaro와 miraheze는
    서로 다른 서버라 각자 자기 간격을 쓴다.

    실패는 예외가 아니라 ``None``이다. 대량 인제스트에서 위키 한 번 실패는 그 곡을
    건너뛸 사유일 뿐이고, 체인 전체를 세울 일이 아니다.

    ``sleep``을 주입할 수 있다 — 테스트가 실제로 기다리지 않게 한다.
    """

    def __init__(
        self,
        user_agent: str = "everyric2-bulk-ingest/1.0 (lyrics sync helper)",
        *,
        min_interval_sec: float = DEFAULT_MIN_INTERVAL_SEC,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_sec: float = DEFAULT_BACKOFF_SEC,
        sleep: Callable[[float], None] = time.sleep,
        session: requests.Session | None = None,
    ) -> None:
        self.min_interval_sec = min_interval_sec
        self.timeout_sec = timeout_sec
        self.max_attempts = max(1, max_attempts)
        self.backoff_sec = backoff_sec
        self._sleep = sleep
        self._session = session or requests.Session()
        self._session.headers.update({"User-Agent": user_agent})
        self._last_at: float | None = None
        #: 이 조회기가 실제로 보낸 요청 수 — 곡당 요청 예산을 셀 때 쓴다
        self.requests_sent = 0

    def get_text(self, url: str) -> str | None:
        resp = self._get(url)
        if resp is None:
            return None
        # wikidot은 Content-Type에 charset을 안 실어 requests가 ISO-8859-1로 추측한다 —
        # 그대로 두면 한국어/일본어가 깨진다 (vocaro_index._fetch와 같은 처리).
        if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text

    def get_json(self, url: str) -> dict[str, Any] | None:
        resp = self._get(url)
        if resp is None:
            return None
        try:
            data = resp.json()
        except ValueError:
            logger.warning("sources: JSON 파싱 실패 - %s", url)
            return None
        return data if isinstance(data, dict) else None

    def _get(self, url: str) -> requests.Response | None:
        for attempt in range(self.max_attempts):
            self._wait_turn()
            try:
                resp = self._session.get(url, timeout=self.timeout_sec)
            except requests.RequestException as e:
                logger.warning("sources: 요청 실패 - %s (%s)", url, e)
                if not self._backoff(attempt):
                    return None
                continue
            if resp.status_code == 200:
                return resp
            if resp.status_code in _RETRY_STATUS:
                logger.warning("sources: %d 응답, 백오프 - %s", resp.status_code, url)
                if not self._backoff(attempt):
                    return None
                continue
            logger.info("sources: %d 응답, 포기 - %s", resp.status_code, url)
            return None
        return None

    def _wait_turn(self) -> None:
        now = time.monotonic()
        if self._last_at is not None:
            gap = self.min_interval_sec - (now - self._last_at)
            if gap > 0:
                self._sleep(gap)
        self._last_at = time.monotonic()
        self.requests_sent += 1

    def _backoff(self, attempt: int) -> bool:
        """다음 시도가 남았으면 그만큼 쉬고 True. 마지막 시도였으면 False."""
        if attempt + 1 >= self.max_attempts:
            return False
        self._sleep(self.backoff_sec * (2**attempt))
        return True
