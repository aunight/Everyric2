"""``video_id`` 오디오 캐시 회귀 테스트.

왜 이 캐시가 있는가(2026-07-26 밤샘 배치 실측): 기존 캐시는 ``(audio_hash, lyrics_hash)``
키라서 해시를 구하려면 파일이 있어야 하고 파일을 구하려면 다운로드를 해야 한다. 그래서 싱크
생성 182건에 유튜브 다운로드 275회가 나갔고, 같은 곡을 재처리하면 275회를 또 받는다. GPU만
아끼고 유튜브 접촉은 하나도 아끼지 못했다.

여기서 못박는 것:

- **``take``가 준 파일을 파이프라인이 지워도 캐시가 남는다.** 이게 이 모듈의 가장 중요한
  불변식이다 — 파이프라인은 입력 오디오를 네 군데서 ``unlink``한다(과길이 초과, 취소, 캐시
  완결, ``_run_alignment``의 ``finally``). 보관 원본을 그대로 넘기면 첫 잡이 캐시를 지운다.
- **``put``은 이미 있는 것을 덮지 않는다.** ``audio_hash``가 파일 바이트 해시이므로 같은
  영상을 두 경로(m4a 스트림카피 / wav 트랜스코드)로 받아 뒤엣것으로 덮으면 해시가 흔들려
  ``(audio_hash, lyrics)`` 캐시가 미스한다.
- 파일명이 되는 ``video_id``·확장자가 경로를 벗어나지 못한다.
- 상한을 넘기면 **오래 안 쓴 것부터** 지운다.
- 같은 영상의 동시 확보가 락으로 한 번으로 병합된다.
- 캐시를 끄면 아무 흔적도 남기지 않는다(디렉터리조차 만들지 않는다).
"""

import threading
import time
from pathlib import Path

import pytest

from everyric2.audio import cache as audio_cache
from everyric2.config.settings import reset_settings


@pytest.fixture
def cache_env(tmp_path, monkeypatch):
    """캐시를 tmp_path로 돌린다. 상한은 테스트마다 따로 바꾼다."""
    monkeypatch.setenv("EVERYRIC_AUDIO_CACHE_ENABLED", "true")
    monkeypatch.setenv("EVERYRIC_AUDIO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("EVERYRIC_AUDIO_CACHE_MAX_GB", "10")
    reset_settings()
    yield tmp_path
    reset_settings()


def _wav(path: Path, payload: bytes = b"RIFF....WAVEfake") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


# ── 형태 검증 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "vid",
    ["dQw4w9WgXcQ", "a", "A-b_C9", "x" * 64],
)
def test_safe_ids_pass(vid):
    assert audio_cache.is_safe_id(vid)


@pytest.mark.parametrize(
    "vid",
    ["", "..", "../etc/passwd", "a/b", "a\\b", "a.b", "x" * 65, "id with space", "id;rm"],
)
def test_unsafe_ids_rejected(vid):
    """파일명에 그대로 들어가는 값이다 — 경로 구분자와 ``..``가 통과하면 안 된다."""
    assert not audio_cache.is_safe_id(vid)


def test_unsafe_id_is_never_stored(cache_env):
    src = _wav(cache_env / "src.wav")
    assert audio_cache.put("../escape", src) is None
    assert audio_cache.find("../escape") is None


def test_disallowed_extension_is_not_stored(cache_env):
    """확장자도 파일명이 된다. 허용 목록 밖은 보관하지 않는다."""
    src = _wav(cache_env / "src.exe")
    assert audio_cache.put("vid00000001", src) is None
    assert audio_cache.find("vid00000001") is None


# ── 왕복과 핵심 불변식 ───────────────────────────────────────────────────────


def test_put_then_take_roundtrip(cache_env):
    src = _wav(cache_env / "src.wav", b"AUDIOBYTES")
    stored = audio_cache.put("vid00000002", src)
    assert stored is not None and stored.is_file()

    work = cache_env / "work"
    got = audio_cache.take("vid00000002", work, "job1234")
    assert got is not None
    assert got.read_bytes() == b"AUDIOBYTES"
    # 원본이 아니라 복사본을 받아야 한다
    assert got != stored
    assert got.parent == work


def test_pipeline_deleting_its_audio_keeps_the_cache(cache_env):
    """가장 중요한 불변식 — 파이프라인은 입력 오디오를 지운다(네 군데서)."""
    src = _wav(cache_env / "src.wav", b"KEEPME")
    audio_cache.put("vid00000003", src)

    first = audio_cache.take("vid00000003", cache_env / "w1", "jobaaaa")
    assert first is not None
    first.unlink()  # 파이프라인이 하는 일

    second = audio_cache.take("vid00000003", cache_env / "w2", "jobbbbb")
    assert second is not None, "첫 잡이 오디오를 지우면 캐시가 사라졌다"
    assert second.read_bytes() == b"KEEPME"


def test_put_does_not_overwrite_an_existing_entry(cache_env):
    """확보 경로가 갈리면 같은 영상도 바이트가 다르다. 먼저 들어온 하나를 계속 써야
    ``audio_hash``가 흔들리지 않는다."""
    audio_cache.put("vid00000004", _wav(cache_env / "a.wav", b"FIRST"))
    audio_cache.put("vid00000004", _wav(cache_env / "b.wav", b"SECOND-DIFFERENT"))

    got = audio_cache.take("vid00000004", cache_env / "w", "job")
    assert got is not None
    assert got.read_bytes() == b"FIRST"


def test_empty_file_is_not_stored(cache_env):
    """0바이트를 보관하면 그 곡이 영구히 «캐시 히트인데 정렬 불가»가 된다."""
    empty = _wav(cache_env / "empty.wav", b"")
    assert audio_cache.put("vid00000005", empty) is None
    assert audio_cache.find("vid00000005") is None


def test_take_miss_returns_none(cache_env):
    assert audio_cache.take("vid00000006", cache_env / "w", "job") is None


def test_partial_files_are_not_visible(cache_env):
    """보관은 임시 이름으로 쓰고 원자적으로 바꾼다 — 반쪽 파일이 히트하면 정렬이 잘린
    오디오로 돈다. 점으로 시작하는 잔여물은 조회·집계에서 제외한다."""
    d = audio_cache.cache_dir()
    (d / ".vid00000007.123.part").write_bytes(b"HALF")
    assert audio_cache.find("vid00000007") is None
    assert audio_cache.stats()["files"] == 0


# ── 상한과 LRU ───────────────────────────────────────────────────────────────


def test_prune_evicts_least_recently_used(cache_env):
    payload = b"x" * 1000
    for i, vid in enumerate(["old0000001", "mid0000002", "new0000003"]):
        audio_cache.put(vid, _wav(cache_env / f"s{i}.wav", payload))
        # mtime을 벌려 LRU 순서를 확정한다 (파일시스템 해상도에 기대지 않는다)
        stored = audio_cache.find(vid)
        assert stored is not None
        import os

        os.utime(stored, (1_700_000_000 + i * 100, 1_700_000_000 + i * 100))

    # 2개만 남을 상한
    freed = audio_cache.prune(max_bytes=2100)
    assert freed >= 1000
    assert audio_cache.find("old0000001") is None, "가장 오래된 것이 남았다"
    assert audio_cache.find("new0000003") is not None, "가장 최근 것이 지워졌다"


def test_take_refreshes_recency(cache_env):
    """조회한 것은 «최근 쓴 것»이 되어야 한다 — 안 그러면 인기곡이 먼저 지워진다."""
    import os

    payload = b"y" * 1000
    for i, vid in enumerate(["aaa0000001", "bbb0000002"]):
        audio_cache.put(vid, _wav(cache_env / f"t{i}.wav", payload))
        stored = audio_cache.find(vid)
        os.utime(stored, (1_700_000_000 + i * 100, 1_700_000_000 + i * 100))

    audio_cache.take("aaa0000001", cache_env / "w", "job")  # 오래된 쪽을 쓴다
    audio_cache.prune(max_bytes=1100)

    assert audio_cache.find("aaa0000001") is not None, "방금 쓴 것이 지워졌다"
    assert audio_cache.find("bbb0000002") is None


def test_zero_ceiling_means_unlimited(cache_env):
    audio_cache.put("vid00000008", _wav(cache_env / "u.wav", b"z" * 5000))
    assert audio_cache.prune(max_bytes=0) == 0
    assert audio_cache.find("vid00000008") is not None


# ── single-flight ────────────────────────────────────────────────────────────


def test_video_lock_serialises_the_same_video(cache_env):
    """같은 영상의 동시 확보가 한 번으로 병합되는가 — 겹침이 관측되면 실패다."""
    overlap = []
    inside = threading.Event()
    holder_done = threading.Event()

    def holder():
        with audio_cache.video_lock("same000001"):
            inside.set()
            time.sleep(0.15)
            overlap.append("holder-exit")
        holder_done.set()

    def waiter():
        inside.wait(timeout=2)
        with audio_cache.video_lock("same000001"):
            overlap.append("waiter-enter")

    t1, t2 = threading.Thread(target=holder), threading.Thread(target=waiter)
    t1.start(); t2.start(); t1.join(timeout=3); t2.join(timeout=3)

    assert overlap == ["holder-exit", "waiter-enter"], f"락이 겹쳤다: {overlap}"


def test_video_lock_does_not_block_different_videos(cache_env):
    """다른 영상끼리는 기다리지 않아야 한다 — 그러면 동시 처리가 직렬화된다."""
    entered = threading.Event()

    def holder():
        with audio_cache.video_lock("alpha00001"):
            entered.set()
            time.sleep(0.3)

    t = threading.Thread(target=holder)
    t.start()
    entered.wait(timeout=2)
    start = time.monotonic()
    with audio_cache.video_lock("beta000001"):
        pass
    elapsed = time.monotonic() - start
    t.join(timeout=3)
    assert elapsed < 0.2, f"다른 영상이 락을 기다렸다 ({elapsed:.2f}s)"


def test_unsafe_id_lock_is_a_passthrough(cache_env):
    """형태가 이상한 ID는 캐시가 거부하므로 병합할 것도 없다 — 막히면 안 된다."""
    with audio_cache.video_lock("../nope"):
        with audio_cache.video_lock("../nope"):
            pass


# ── 꺼진 캐시 ────────────────────────────────────────────────────────────────


class TestAcquireWiring:
    """``_acquire_audio``가 실제로 유튜브 접촉을 줄이는가 — 캐시 모듈이 아니라 배선을 본다.

    러너 실측이 문제 삼은 것이 정확히 이 지점이다: 캐시가 다운로드 **뒤에** 있어서 생성
    182건에 다운로드 275회가 나갔다. 여기서 세는 것은 ``_download_and_hash`` 호출 횟수다.
    """

    @staticmethod
    def _wire(monkeypatch, tmp_path, payload=b"DOWNLOADED-WAV"):
        """다운로더를 세는 가짜로 갈아끼우고 (worker 모듈, 호출 기록)을 돌려준다."""
        from everyric2.server import worker as worker_mod

        calls: list[str] = []

        def fake_download(video_id: str, job_id: str) -> dict:
            calls.append(video_id)
            p = tmp_path / "dl" / f"{video_id}-{job_id[:8]}.wav"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(payload)
            return {"audio_path": str(p), "audio_hash": worker_mod.compute_audio_hash(p)}

        monkeypatch.setattr(worker_mod, "_download_and_hash", fake_download)
        return worker_mod, calls

    @staticmethod
    def _job(worker_mod, vid="wiredvid001", job_id="job-0001", **kw):
        return worker_mod.JobInput(job_id=job_id, video_id=vid, lyrics="가사", **kw)

    def test_second_job_does_not_touch_youtube(self, cache_env, monkeypatch):
        monkeypatch.setenv("EVERYRIC_AUDIO_TEMP_DIR", str(cache_env / "tmp"))
        reset_settings()
        worker_mod, calls = self._wire(monkeypatch, cache_env)

        first = worker_mod._acquire_audio(self._job(worker_mod, job_id="job-0001"))
        second = worker_mod._acquire_audio(self._job(worker_mod, job_id="job-0002"))

        assert calls == ["wiredvid001"], f"두 번째 잡이 또 받았다: {calls}"
        # 같은 파일에서 왔으므로 해시가 흔들리지 않는다 — (audio_hash, lyrics) 캐시가 이때 적중한다
        assert first["audio_hash"] == second["audio_hash"]
        # 잡마다 자기 복사본을 받는다 (한쪽이 지워도 다른 쪽이 멀쩡해야 한다)
        assert first["audio_path"] != second["audio_path"]

    def test_deleting_the_handed_file_does_not_break_the_next_job(self, cache_env, monkeypatch):
        monkeypatch.setenv("EVERYRIC_AUDIO_TEMP_DIR", str(cache_env / "tmp"))
        reset_settings()
        worker_mod, calls = self._wire(monkeypatch, cache_env)

        first = worker_mod._acquire_audio(self._job(worker_mod, job_id="job-000a"))
        Path(first["audio_path"]).unlink()  # 파이프라인이 하는 일

        second = worker_mod._acquire_audio(self._job(worker_mod, job_id="job-000b"))
        assert calls == ["wiredvid001"]
        assert Path(second["audio_path"]).read_bytes() == b"DOWNLOADED-WAV"

    def test_force_reuses_the_cached_audio(self, cache_env, monkeypatch):
        """force는 «정렬을 다시 돌려라»는 뜻이다. 오디오를 다시 받아 올 이유가 아니다 —
        회수 대조군을 재다운로드 0으로 돌리는 근거가 이것이다."""
        monkeypatch.setenv("EVERYRIC_AUDIO_TEMP_DIR", str(cache_env / "tmp"))
        reset_settings()
        worker_mod, calls = self._wire(monkeypatch, cache_env)

        worker_mod._acquire_audio(self._job(worker_mod, job_id="job-000c"))
        worker_mod._acquire_audio(self._job(worker_mod, job_id="job-000d", force=True))
        assert calls == ["wiredvid001"]

    def test_media_cache_path_is_also_stored(self, cache_env, monkeypatch):
        """미디어 캐시에서 추출된 파일도 보관해야 한다 — 그러면 다음 잡은 ffmpeg 추출조차
        하지 않고, 확보 경로가 갈려 해시가 흔들리는 문제도 같이 사라진다."""
        monkeypatch.setenv("EVERYRIC_AUDIO_TEMP_DIR", str(cache_env / "tmp"))
        reset_settings()
        worker_mod, calls = self._wire(monkeypatch, cache_env)

        extracted = _wav(cache_env / "extracted.m4a", b"FROM-MEDIA-CACHE")
        first = worker_mod._acquire_audio(
            self._job(worker_mod, job_id="job-000e", audio_path=str(extracted))
        )
        assert calls == [], "미디어 캐시 경로인데 다운로드가 돌았다"
        assert first["audio_path"] == str(extracted)

        # 두 번째 잡은 audio_path 없이 와도 캐시에서 받는다
        second = worker_mod._acquire_audio(self._job(worker_mod, job_id="job-000f"))
        assert calls == []
        assert Path(second["audio_path"]).read_bytes() == b"FROM-MEDIA-CACHE"
        assert first["audio_hash"] == second["audio_hash"]

    def test_concurrent_jobs_for_one_video_download_once(self, cache_env, monkeypatch):
        """공개 트래픽은 인기곡에 동시에 몰린다. 캐시는 두 번째 요청부터 듣지만 락은 첫
        순간부터 들어야 한다."""
        monkeypatch.setenv("EVERYRIC_AUDIO_TEMP_DIR", str(cache_env / "tmp"))
        reset_settings()
        from everyric2.server import worker as worker_mod

        calls: list[str] = []
        started = threading.Event()

        def slow_download(video_id: str, job_id: str) -> dict:
            calls.append(video_id)
            started.set()
            time.sleep(0.2)  # 다운로드가 도는 동안 두 번째 잡이 들어온다
            p = cache_env / "dl" / f"{video_id}-{job_id[:8]}.wav"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"SLOW")
            return {"audio_path": str(p), "audio_hash": worker_mod.compute_audio_hash(p)}

        monkeypatch.setattr(worker_mod, "_download_and_hash", slow_download)

        results: dict[str, dict] = {}

        def run(job_id: str):
            results[job_id] = worker_mod._acquire_audio(
                worker_mod.JobInput(job_id=job_id, video_id="hotvid00001", lyrics="가사")
            )

        t1 = threading.Thread(target=run, args=("job-aaaa",))
        t2 = threading.Thread(target=run, args=("job-bbbb",))
        t1.start()
        started.wait(timeout=2)
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert calls == ["hotvid00001"], f"동시 요청이 각자 받았다: {calls}"
        assert len(results) == 2
        assert results["job-aaaa"]["audio_hash"] == results["job-bbbb"]["audio_hash"]

    def test_disabled_cache_keeps_the_old_behaviour(self, tmp_path, monkeypatch):
        """캐시를 끄면 예전 그대로 매번 받는다 — 끄는 스위치가 실제로 끄는지 본다."""
        monkeypatch.setenv("EVERYRIC_AUDIO_CACHE_ENABLED", "false")
        monkeypatch.setenv("EVERYRIC_AUDIO_CACHE_DIR", str(tmp_path / "nope"))
        monkeypatch.setenv("EVERYRIC_AUDIO_TEMP_DIR", str(tmp_path / "tmp"))
        reset_settings()
        try:
            worker_mod, calls = self._wire(monkeypatch, tmp_path)
            worker_mod._acquire_audio(self._job(worker_mod, job_id="job-1111"))
            worker_mod._acquire_audio(self._job(worker_mod, job_id="job-2222"))
            assert calls == ["wiredvid001", "wiredvid001"]
            assert not (tmp_path / "nope").exists()
        finally:
            reset_settings()


def test_disabled_cache_leaves_no_trace(tmp_path, monkeypatch):
    target = tmp_path / "never-created"
    monkeypatch.setenv("EVERYRIC_AUDIO_CACHE_ENABLED", "false")
    monkeypatch.setenv("EVERYRIC_AUDIO_CACHE_DIR", str(target))
    reset_settings()
    try:
        assert audio_cache.find("vid00000009") is None
        assert audio_cache.take("vid00000009", tmp_path / "w", "job") is None
        assert audio_cache.put("vid00000009", _wav(tmp_path / "s.wav")) is None
        assert audio_cache.prune() == 0
        assert audio_cache.stats() == {"files": 0, "bytes": 0}
        assert not target.exists(), "꺼진 캐시가 디렉터리를 만들었다"
        with pytest.raises(audio_cache.AudioCacheDisabled):
            audio_cache.cache_dir()
    finally:
        reset_settings()
