"""fetch_cached_audio_sync — 링크 검증의 캐시 우선 오디오 조달 (네트워크/ffmpeg 전부 모킹).

같은 호스트 워커가 동기 컨텍스트에서 캐시를 조달하고, 미설정/미스/추출 실패는 전부 None으로
떨어져 호출부(yt-dlp)가 이어받는 폴백 계약을 못 박는다.
"""

import pytest

from everyric2.config.settings import get_settings
from everyric2.server import media_cache


def _set_url(url: str) -> None:
    object.__setattr__(get_settings().server, "media_cache_url", url)


def test_unset_url_returns_none():
    _set_url("")
    assert media_cache.fetch_cached_audio_sync("vid00000001", "t") is None


def test_hit_extracts_and_returns_path(monkeypatch, tmp_path):
    _set_url("http://cache.test")
    try:
        src = tmp_path / "vid.mp4"
        src.write_bytes(b"x")
        monkeypatch.setattr(
            media_cache, "_lookup", lambda url, key, vid: {"found": True, "path": str(src)}
        )
        monkeypatch.setattr(media_cache, "_run_ffmpeg", lambda s, d: True)
        out = media_cache.fetch_cached_audio_sync("vid00000001", "t")
        assert out is not None
        assert out.endswith(".m4a")
    finally:
        _set_url("")


def test_miss_returns_none(monkeypatch):
    _set_url("http://cache.test")
    try:
        monkeypatch.setattr(media_cache, "_lookup", lambda url, key, vid: {"found": False})
        assert media_cache.fetch_cached_audio_sync("vid00000001", "t") is None
    finally:
        _set_url("")


def test_extract_failure_returns_none(monkeypatch, tmp_path):
    _set_url("http://cache.test")
    try:
        src = tmp_path / "vid.mp4"
        src.write_bytes(b"x")
        monkeypatch.setattr(
            media_cache, "_lookup", lambda url, key, vid: {"found": True, "path": str(src)}
        )
        monkeypatch.setattr(media_cache, "_run_ffmpeg", lambda s, d: False)
        assert media_cache.fetch_cached_audio_sync("vid00000001", "t") is None
    finally:
        _set_url("")


# ── lookup_cached — 링크 자동 제출 게이트의 경량 조회 ─────────────


def test_lookup_cached_true_on_found(monkeypatch):
    _set_url("http://cache.test")
    try:
        monkeypatch.setattr(media_cache, "_lookup", lambda url, key, vid: {"found": True})
        assert media_cache.lookup_cached("vid00000001") is True
    finally:
        _set_url("")


def test_lookup_cached_false_on_miss_error_or_unset(monkeypatch):
    _set_url("")
    assert media_cache.lookup_cached("vid00000001") is False  # 미설정
    _set_url("http://cache.test")
    try:
        monkeypatch.setattr(media_cache, "_lookup", lambda url, key, vid: {"found": False})
        assert media_cache.lookup_cached("vid00000001") is False  # 미스
        monkeypatch.setattr(
            media_cache,
            "_lookup",
            lambda url, key, vid: (_ for _ in ()).throw(RuntimeError("down")),
        )
        assert media_cache.lookup_cached("vid00000001") is False  # 오류도 조용히 False
    finally:
        _set_url("")


# ── 워커 ③ 게이트 — 캐시 미스면 다운로드 없이 판정 포기 ──────────


def test_download_audio_for_link_gives_up_on_cache_miss_when_cache_only(monkeypatch):
    """link_cache_only(기본 True)면 캐시 미스에서 yt-dlp로 폴백하지 않는다 — 유튜브 접촉 0.
    실패가 아니라 정상 종결(판정 포기)이고, 쌍은 쿨다운에 들어가 재제출이 억제된다."""
    from everyric2 import cli

    monkeypatch.setattr(media_cache, "fetch_cached_audio_sync", lambda vid, tag: None)
    server = get_settings().server
    orig = server.link_cache_only
    object.__setattr__(server, "link_cache_only", True)
    try:
        with pytest.raises(cli.LinkAudioCacheMissError):
            cli._download_audio_for_link("MISSvideo01", "t")
    finally:
        object.__setattr__(server, "link_cache_only", orig)
