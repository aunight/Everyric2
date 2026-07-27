"""보카로 가사 위키(vocaro.wikidot.com) 곡 페이지 파서 — 서버측 포트.

확장 ``everyric2-chrome/src/lib/vocaro.ts``의 ``parseSongPage``/``fetchSongPage``를 그대로
옮긴 것이다. 규칙을 두 곳에 적는 대신 **동작을 같게** 유지한다 — 확장이 붙인 발음/번역과
서버가 대량으로 붙인 발음/번역이 같은 줄 나눔이어야 line_meta 텍스트 매칭이 맞는다.

- 사이트가 HTTPS를 지원하지 않아 평문 http로 접근한다(https는 301로 되돌린다).
- 가사 표는 공식 가이드상 **원문/발음/번역 3행 1세트**다. 2행 1세트(발음 없이 번역만)인
  페이지도 있어 행 수로 판별한다.
- 라이선스: 위키 편집 콘텐츠는 CC BY 4.0(출처 표기 필요), 인용된 원문 가사의 저작권은
  원저작자에게 있다 — 저장·표시 경로에서 출처 링크를 항상 함께 싣는다.

슬러그 → 곡 매칭은 여기서 하지 않는다. 원제(일본어) 매칭은 서버가 이미 크롤해 둔
:mod:`everyric2.server.vocaro_index`(``models/vocaro_index.json``)가 유일한 안정 경로다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from everyric2.sources.base import SourceLine, WikiFetcher, cell_text

BASE_URL = "http://vocaro.wikidot.com"
LICENSE = "CC BY 4.0"
#: 확장이 출처 배지에 쓰는 이름과 같게 맞춘다 (everyric2-chrome/src/content.ts)
ATTRIBUTION_NAME = "보카로 가사 위키"
SOURCE_ID = "vocaro"


@dataclass(frozen=True)
class VocaroSong:
    slug: str
    page_url: str
    page_title: str
    lines: list[SourceLine]

    def attribution(self) -> dict[str, str]:
        """서버 ``attribution`` 계약(name/url/license/source_id)."""
        return {
            "name": ATTRIBUTION_NAME,
            "url": self.page_url,
            "license": LICENSE,
            "source_id": SOURCE_ID,
        }

    def original_text(self) -> str:
        """원문(일본어) 가사 본문 — 빈 줄은 빼고 줄바꿈으로 잇는다."""
        return "\n".join(ln.text for ln in self.lines if ln.text)

    def line_meta(self) -> list[dict[str, str | None]]:
        """발음이나 번역이 실제로 있는 줄만 골라 ``line_meta``로."""
        return [ln.as_dict() for ln in self.lines if ln.pronunciation or ln.translation]


def page_url(slug: str) -> str:
    return f"{BASE_URL}/{slug}"


# ── 파싱 (vocaro.ts parseSongPage와 동일 규칙) ─────────────────────

_TABLE_RE = re.compile(r'<table class="wiki-content-table">([\s\S]*?)</table>')
_ROW_RE = re.compile(r"<tr[^>]*>([\s\S]*?)</tr>")
_TITLE_CELL_RE = re.compile(r'<th[^>]*class="[^"]*title-cell[^"]*"[^>]*>([\s\S]*?)</th>')


def parse_song_page(page_html: str) -> tuple[str, list[SourceLine]] | None:
    """곡 페이지 HTML → (원제, 가사 줄 목록). 가사 표가 없으면 None.

    행 수로 세트 크기를 판별한다 — 3의 배수면 원문/발음/번역, 아니고 2의 배수면
    원문/번역, 둘 다 아니면 전부 원문으로 본다(어긋난 표를 조용히 버리지 않는다).

    6행처럼 3과 2의 배수를 겸하는 표는 3행 세트로 읽는다 — vocaro.ts와 같은 판정
    순서다. 발음 행을 번역으로 오인하는 쪽보다 발음이 있는데 못 읽는 쪽이 드물다.
    """
    table = _TABLE_RE.search(page_html)
    if not table:
        return None

    rows = [cell_text(m.group(1), drop_ruby=True) for m in _ROW_RE.finditer(table.group(1))]

    lines: list[SourceLine]
    if rows and len(rows) % 3 == 0:
        lines = [
            SourceLine(
                text=rows[i],
                pronunciation=rows[i + 1] or None,
                translation=rows[i + 2] or None,
            )
            for i in range(0, len(rows), 3)
        ]
    elif rows and len(rows) % 2 == 0:
        lines = [
            SourceLine(text=rows[i], translation=rows[i + 1] or None)
            for i in range(0, len(rows), 2)
        ]
    else:
        lines = [SourceLine(text=r) for r in rows]

    lines = [ln for ln in lines if ln.text]
    title_match = _TITLE_CELL_RE.search(page_html)
    title = cell_text(title_match.group(1), drop_ruby=True) if title_match else ""
    return title, lines


# ── 조회 ──────────────────────────────────────────────────────────

_default_fetcher: WikiFetcher | None = None


def _fetcher() -> WikiFetcher:
    global _default_fetcher
    if _default_fetcher is None:
        _default_fetcher = WikiFetcher()
    return _default_fetcher


def fetch_song(slug: str, fetcher: WikiFetcher | None = None) -> VocaroSong | None:
    """슬러그로 곡 페이지를 받아 파싱. 요청 실패·가사 표 없음이면 None (요청 1회)."""
    url = page_url(slug)
    page_html = (fetcher or _fetcher()).get_text(url)
    if not page_html:
        return None
    parsed = parse_song_page(page_html)
    if parsed is None:
        return None
    title, lines = parsed
    if not lines:
        return None
    return VocaroSong(slug=slug, page_url=url, page_title=title or slug, lines=lines)


def fetch_song_page(slug: str, fetcher: WikiFetcher | None = None) -> list[SourceLine]:
    """가사 줄만 필요할 때. 못 받으면 빈 목록 — 출처 표기가 필요하면 :func:`fetch_song`."""
    song = fetch_song(slug, fetcher)
    return song.lines if song else []
