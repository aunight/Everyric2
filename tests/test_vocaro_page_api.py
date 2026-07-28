"""위키 페이지 프록시(/api/vocaro/page·/index) 테스트 — 확장 1.5.5의 host 권한 제거 경로.

네트워크를 타지 않도록 sources.vocaro의 조회 함수를 몽키패치하고 라우트 코루틴을 직접
await한다(tests/test_vocaro_proxy.py와 같은 방식). 검증 축:
- 슬러그/페이지명 검증이 위키 조회 **전에** 걸린다 (임의 경로로 서버를 프록시로 쓰지 못한다)
- 실패는 4xx가 아니라 found=false (확장은 조용히 다음 소스로 넘어간다)
- 응답 매핑이 VocaroSong/SourceLine을 1:1로 옮긴다
"""

import asyncio

from everyric2.server.api import vocaro as vocaro_api
from everyric2.server.api.vocaro import index_listing, song_page
from everyric2.sources.base import SourceLine
from everyric2.sources.vocaro import VocaroSong, parse_index_entries


def _song() -> VocaroSong:
    return VocaroSong(
        slug="roki",
        page_url="http://vocaro.wikidot.com/roki",
        page_title="ロキ",
        lines=[
            SourceLine(text="ロキロキの", pronunciation="로키로키노", translation="로키로키의"),
            SourceLine(text="間奏"),
        ],
    )


# ── /page ─────────────────────────────────────────────────────────


def test_page_maps_song_one_to_one(monkeypatch):
    monkeypatch.setattr(vocaro_api.vocaro_source, "fetch_song", lambda slug: _song())
    resp = asyncio.run(song_page(slug="roki"))
    assert resp.found is True
    assert resp.slug == "roki"
    assert resp.page_url == "http://vocaro.wikidot.com/roki"
    assert resp.page_title == "ロキ"
    assert [ln.text for ln in resp.lines] == ["ロキロキの", "間奏"]
    assert resp.lines[0].pronunciation == "로키로키노"
    assert resp.lines[0].translation == "로키로키의"
    assert resp.lines[1].pronunciation is None


def test_page_rejects_bad_slug_before_fetch(monkeypatch):
    def _boom(slug):
        raise AssertionError("검증 실패 슬러그로 위키 조회가 나가면 안 된다")

    monkeypatch.setattr(vocaro_api.vocaro_source, "fetch_song", _boom)
    for bad in ("../etc/passwd", "UPPER", "a", "한글", "roki/extra", "system:join"):
        resp = asyncio.run(song_page(slug=bad))
        assert resp.found is False, bad


def test_page_fetch_failure_is_found_false(monkeypatch):
    monkeypatch.setattr(vocaro_api.vocaro_source, "fetch_song", lambda slug: None)
    resp = asyncio.run(song_page(slug="no-such-song"))
    assert resp.found is False
    assert resp.lines == []


# ── /index ────────────────────────────────────────────────────────


def test_index_maps_entries(monkeypatch):
    monkeypatch.setattr(
        vocaro_api.vocaro_source,
        "fetch_index_entries",
        lambda page: [("roki", "로키"), ("charles", "샤를")],
    )
    resp = asyncio.run(index_listing(page="allsongs-h4"))
    assert resp.found is True
    assert [(e.slug, e.title) for e in resp.entries] == [("roki", "로키"), ("charles", "샤를")]


def test_index_rejects_bad_page_before_fetch(monkeypatch):
    def _boom(page):
        raise AssertionError("검증 실패 페이지명으로 위키 조회가 나가면 안 된다")

    monkeypatch.setattr(vocaro_api.vocaro_source, "fetch_index_entries", _boom)
    for bad in ("allsongs-h15", "allsongs-", "roki", "allsongs-ab", "allsongs-h0", "start"):
        resp = asyncio.run(index_listing(page=bad))
        assert resp.found is False, bad


def test_index_accepts_all_letter_and_hangul_pages(monkeypatch):
    monkeypatch.setattr(vocaro_api.vocaro_source, "fetch_index_entries", lambda page: [])
    for ok in ("allsongs-a", "allsongs-z", "allsongs-h1", "allsongs-h14", "allsongs-num", "allsongs-symbols"):
        resp = asyncio.run(index_listing(page=ok))
        assert resp.found is True, ok
        assert resp.entries == []


def test_index_fetch_failure_is_found_false(monkeypatch):
    monkeypatch.setattr(vocaro_api.vocaro_source, "fetch_index_entries", lambda page: None)
    resp = asyncio.run(index_listing(page="allsongs-a"))
    assert resp.found is False


# ── parse_index_entries (vocaro.ts parseIndexEntries 규칙 패리티) ──


def test_parse_index_entries_extracts_pairs_and_unescapes():
    html = (
        '<ul><li><a href="/roki">로키</a></li>'
        '<li><a href="/rock-and-roll">락 &amp; 롤</a></li>'
        '<li><a href="/page#anchor">앵커 제외</a></li>'
        '<li><a href="/system:join">시스템 제외</a></li>'
        '<li><a href="/empty-title">  </a></li></ul>'
    )
    assert parse_index_entries(html) == [("roki", "로키"), ("rock-and-roll", "락 & 롤")]
