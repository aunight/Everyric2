"""번역 배치 병렬 실행 — 순서 보존, 동시성 스위치, 429 백오프, 배치 단위 실패 격리.

배치는 서로 의존이 없어(각자 자기 구간만 번역) 동시에 던질 수 있지만, 결합이 완료
순서를 타면 가사가 통째로 뒤섞인다. 여기서는 완료 순서를 일부러 뒤집어 결과가 여전히
입력 순서인지 확인한다.

실제 NIM API는 호출하지 않는다(requests.post를 mock). 백오프 대기도 _sleep을 가로채
기록만 하므로 테스트가 실제로 기다리는 일은 없다.
"""

import threading
from dataclasses import dataclass, field
from json import dumps

import pytest
import requests

import everyric2.translation.translator as tr
from everyric2.config.settings import TranslationSettings
from everyric2.translation.translator import NvidiaTranslator


@dataclass
class FakeResponse:
    status_code: int = 200
    payload: dict = field(default_factory=dict)
    ok: bool = True
    text: str = ""
    headers: dict = field(default_factory=dict)

    def json(self):
        return self.payload


def chat_response(content: str, finish_reason: str = "stop") -> FakeResponse:
    return FakeResponse(
        payload={"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}
    )


def rate_limited(retry_after: str | None = None) -> FakeResponse:
    return FakeResponse(
        status_code=429,
        ok=False,
        text="Too Many Requests",
        headers={"Retry-After": retry_after} if retry_after else {},
    )


def prompt_lines(payload: dict) -> list[str]:
    """요청 프롬프트에 실린 원문 라인들 — 어느 배치의 요청인지 식별한다."""
    return payload["messages"][0]["content"].split("LYRICS:\n")[-1].strip().split("\n")


def translated(lines: list[str]) -> FakeResponse:
    body = ",".join(
        '{"original":%s,"translation":%s}' % (dumps(ln), dumps(f"t-{ln}")) for ln in lines
    )
    return chat_response("[" + body + "]")


def make_translator(monkeypatch, tmp_path, **overrides) -> NvidiaTranslator:
    key_file = tmp_path / "nvapi.txt"
    key_file.write_text("dummy-key", encoding="utf-8")
    monkeypatch.setattr(NvidiaTranslator, "_KEY_FILE", key_file)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    settings = TranslationSettings(
        engine="nvidia", api_key=None, include_pronunciation=False, **overrides
    )
    return NvidiaTranslator(settings)


def small_batches(monkeypatch, size: int = 2) -> None:
    """평문(발음 없음) 경로를 size줄짜리 배치로 쪼갠다 — 6줄이면 3배치."""
    monkeypatch.setattr(tr, "_TEXT_BATCH_THRESHOLD", size)
    monkeypatch.setattr(tr, "_TEXT_BATCH_SIZE", size)


LINES = [f"line {i}" for i in range(6)]  # 2줄 배치면 [0,1] [2,3] [4,5]
SONG = "\n".join(LINES)
EXPECTED = [f"t-line {i}" for i in range(6)]


class TestBatchOrdering:
    """완료 순서가 아니라 입력(배치 인덱스) 순서로 이어붙여야 한다."""

    def test_scrambled_completion_still_yields_input_order(self, monkeypatch, tmp_path):
        small_batches(monkeypatch)
        translator = make_translator(monkeypatch, tmp_path, batch_concurrency=3)

        first_may_return = threading.Event()
        completed: list[str] = []
        lock = threading.Lock()

        def fake_post(url, json, headers, timeout):
            batch = prompt_lines(json)
            if batch[0] == "line 0":
                # 첫 배치를 일부러 가장 늦게 완료시킨다 — 완료 순서로 이으면 가사가 밀린다
                assert first_may_return.wait(timeout=10), "뒤 배치가 병렬로 돌지 않았다"
            response = translated(batch)
            with lock:
                completed.append(batch[0])
                if len(completed) == 2:  # 뒤쪽 두 배치가 먼저 끝났다
                    first_may_return.set()
            return response

        monkeypatch.setattr("everyric2.translation.translator.requests.post", fake_post)

        result = translator.translate(SONG, source_lang="ja", target_lang="ko")

        assert [line.original for line in result.lines] == LINES
        assert [line.translation for line in result.lines] == EXPECTED
        # 첫 배치가 실제로 가장 늦게 끝났는데도 결과 순서는 그대로여야 의미가 있다
        assert completed[-1] == "line 0"

    def test_batches_actually_run_at_the_same_time(self, monkeypatch, tmp_path):
        # 세 배치가 동시에 in-flight일 때만 배리어가 풀린다 — 순차 실행이면 시간 초과로
        # BrokenBarrierError가 나고 그 배치들이 failed로 마감돼 아래 단언이 깨진다
        small_batches(monkeypatch)
        translator = make_translator(monkeypatch, tmp_path, batch_concurrency=3)
        barrier = threading.Barrier(3, timeout=10)

        def fake_post(url, json, headers, timeout):
            barrier.wait()
            return translated(prompt_lines(json))

        monkeypatch.setattr("everyric2.translation.translator.requests.post", fake_post)

        result = translator.translate(SONG, source_lang="ja", target_lang="ko")

        assert [line.translation for line in result.lines] == EXPECTED
        assert all(not line.failed for line in result.lines)

    def test_concurrency_is_capped_by_the_setting(self, monkeypatch, tmp_path):
        small_batches(monkeypatch)
        translator = make_translator(monkeypatch, tmp_path, batch_concurrency=2)

        lock = threading.Lock()
        in_flight = 0
        peak = 0
        gate = threading.Event()

        def fake_post(url, json, headers, timeout):
            nonlocal in_flight, peak
            with lock:
                in_flight += 1
                peak = max(peak, in_flight)
                started = in_flight
            if started >= 2:
                gate.set()
            # 동시성 2면 앞 두 배치가 함께 뜨고 세 번째는 자리가 날 때까지 기다린다
            gate.wait(timeout=10)
            response = translated(prompt_lines(json))
            with lock:
                in_flight -= 1
            return response

        monkeypatch.setattr("everyric2.translation.translator.requests.post", fake_post)

        result = translator.translate(SONG, source_lang="ja", target_lang="ko")

        assert [line.translation for line in result.lines] == EXPECTED
        assert peak == 2


class TestSequentialSwitch:
    """batch_concurrency=1 = 기존 순차 동작 (즉시 되돌릴 수 있는 스위치)."""

    def _run(self, monkeypatch, tmp_path, concurrency):
        small_batches(monkeypatch)
        translator = make_translator(monkeypatch, tmp_path, batch_concurrency=concurrency)
        seen: list[str] = []
        threads: set[str] = set()
        lock = threading.Lock()

        def fake_post(url, json, headers, timeout):
            batch = prompt_lines(json)
            with lock:
                seen.append(batch[0])
                threads.add(threading.current_thread().name)
            return translated(batch)

        monkeypatch.setattr("everyric2.translation.translator.requests.post", fake_post)
        result = translator.translate(SONG, source_lang="ja", target_lang="ko")
        return result, seen, threads

    def test_requests_go_out_in_batch_order_on_one_thread(self, monkeypatch, tmp_path):
        result, seen, threads = self._run(monkeypatch, tmp_path, 1)

        assert seen == ["line 0", "line 2", "line 4"]
        # 스레드 풀 자체를 만들지 않는다 — 호출자 스레드에서 그대로 돈다
        assert threads == {threading.current_thread().name}
        assert [line.translation for line in result.lines] == EXPECTED

    def test_result_identical_to_the_parallel_run(self, monkeypatch, tmp_path):
        def snapshot(res):
            return [
                (ln.original, ln.translation, ln.pronunciation, ln.failed) for ln in res.lines
            ]

        with pytest.MonkeyPatch.context() as mp:
            sequential, _, _ = self._run(mp, tmp_path, 1)
        with pytest.MonkeyPatch.context() as mp:
            parallel, _, _ = self._run(mp, tmp_path, 4)

        assert snapshot(sequential) == snapshot(parallel)

    def test_zero_or_negative_concurrency_is_clamped_to_one(self, monkeypatch, tmp_path):
        translator = make_translator(monkeypatch, tmp_path, batch_concurrency=0)
        assert translator._batch_concurrency() == 1

        translator.settings.batch_concurrency = -3
        assert translator._batch_concurrency() == 1

    def test_default_concurrency_is_parallel_but_modest(self):
        # 기본이 1이면 병렬화가 꺼진 채 배포된다 / 너무 크면 미확인 RPM 한도에 부딪힌다
        assert TranslationSettings().batch_concurrency == 4


class TestRateLimitBackoff:
    """429는 지수 백오프로 재시도하고, 상한을 넘으면 기존 실패 경로를 탄다."""

    def _sequenced(self, monkeypatch, translator, responses):
        calls: list[dict] = []
        sleeps: list[float] = []
        it = iter(responses)
        lock = threading.Lock()

        def fake_post(url, json, headers, timeout):
            with lock:
                calls.append(json)
                return next(it)

        monkeypatch.setattr("everyric2.translation.translator.requests.post", fake_post)
        monkeypatch.setattr(translator, "_sleep", sleeps.append)
        return calls, sleeps

    def test_backs_off_then_succeeds(self, monkeypatch, tmp_path):
        translator = make_translator(
            monkeypatch, tmp_path, rate_limit_retries=3, rate_limit_backoff_sec=2.0
        )
        calls, sleeps = self._sequenced(
            monkeypatch,
            translator,
            [rate_limited(), rate_limited(), translated(["line 0", "line 1"])],
        )

        result = translator.translate("line 0\nline 1", source_lang="ja", target_lang="ko")

        assert [line.translation for line in result.lines] == ["t-line 0", "t-line 1"]
        assert len(calls) == 3
        # 2초 → 4초로 2배씩, 지터(최대 +25%)만 얹힌다
        assert len(sleeps) == 2
        assert 2.0 <= sleeps[0] <= 2.5
        assert 4.0 <= sleeps[1] <= 5.0

    def test_retry_after_header_is_honoured_and_capped(self, monkeypatch, tmp_path):
        translator = make_translator(
            monkeypatch, tmp_path, rate_limit_retries=2, rate_limit_backoff_sec=2.0
        )
        _, sleeps = self._sequenced(
            monkeypatch,
            translator,
            [rate_limited("7"), rate_limited("9999"), translated(["line 0"])],
        )

        translator.translate("line 0", source_lang="ja", target_lang="ko")

        assert 7.0 <= sleeps[0] <= 8.75  # 기본 2초보다 길게 요구했으면 그쪽을 따른다
        assert sleeps[1] <= tr._RATE_LIMIT_MAX_WAIT_SEC * (1 + tr._RATE_LIMIT_JITTER)

    def test_retries_are_capped_then_existing_failure_path(self, monkeypatch, tmp_path):
        translator = make_translator(
            monkeypatch, tmp_path, rate_limit_retries=2, rate_limit_backoff_sec=1.0
        )
        calls, sleeps = self._sequenced(
            monkeypatch, translator, [rate_limited(), rate_limited(), rate_limited()]
        )

        # 상한을 넘으면 429 응답을 그대로 흘려 기존 'API error' 예외 경로를 탄다
        with pytest.raises(RuntimeError, match="429"):
            translator.translate("line 0\nline 1", source_lang="ja", target_lang="ko")

        assert len(calls) == 3  # 최초 1회 + 재시도 2회
        assert len(sleeps) == 2

    def test_no_retry_when_disabled(self, monkeypatch, tmp_path):
        translator = make_translator(monkeypatch, tmp_path, rate_limit_retries=0)
        calls, sleeps = self._sequenced(monkeypatch, translator, [rate_limited()])

        with pytest.raises(RuntimeError, match="429"):
            translator.translate("line 0", source_lang="ja", target_lang="ko")

        assert len(calls) == 1
        assert sleeps == []

    def test_other_errors_are_not_retried(self, monkeypatch, tmp_path):
        # 429가 아닌 실패는 기다려도 나아지지 않는다 — 즉시 기존 경로로
        translator = make_translator(monkeypatch, tmp_path, rate_limit_retries=3)
        calls, sleeps = self._sequenced(
            monkeypatch,
            translator,
            [FakeResponse(status_code=500, ok=False, text="boom")],
        )

        with pytest.raises(RuntimeError, match="500"):
            translator.translate("line 0", source_lang="ja", target_lang="ko")

        assert len(calls) == 1
        assert sleeps == []

    def test_rate_limited_batch_does_not_block_the_others(self, monkeypatch, tmp_path):
        # 백오프로 대기하는 배치가 있어도 나머지는 계속 돌고, 상한을 넘긴 배치만
        # 부분 실패(원문 + failed)로 마감된다
        small_batches(monkeypatch)
        translator = make_translator(
            monkeypatch,
            tmp_path,
            batch_concurrency=3,
            rate_limit_retries=2,
            rate_limit_backoff_sec=1.0,
        )
        sleeps: list[float] = []
        monkeypatch.setattr(translator, "_sleep", sleeps.append)

        def fake_post(url, json, headers, timeout):
            batch = prompt_lines(json)
            if batch[0] == "line 2":
                return rate_limited()
            return translated(batch)

        monkeypatch.setattr("everyric2.translation.translator.requests.post", fake_post)

        result = translator.translate(SONG, source_lang="ja", target_lang="ko")

        assert [line.original for line in result.lines] == LINES
        assert [line.failed for line in result.lines] == [
            False, False, True, True, False, False,
        ]
        assert [line.translation for line in result.lines[:2]] == ["t-line 0", "t-line 1"]
        assert [line.translation for line in result.lines[4:]] == ["t-line 4", "t-line 5"]
        assert len(sleeps) == 2  # 그 배치만 재시도 상한까지 백오프했다


class TestBatchFailureIsolation:
    """한 배치가 예외로 죽어도 나머지 배치 결과는 살아야 한다."""

    def test_failing_batch_becomes_failed_lines_and_others_survive(self, monkeypatch, tmp_path):
        small_batches(monkeypatch)
        translator = make_translator(monkeypatch, tmp_path, batch_concurrency=3)

        def fake_post(url, json, headers, timeout):
            batch = prompt_lines(json)
            if batch[0] == "line 2":
                raise requests.exceptions.ConnectionError("batch died")
            return translated(batch)

        monkeypatch.setattr("everyric2.translation.translator.requests.post", fake_post)

        result = translator.translate(SONG, source_lang="ja", target_lang="ko")

        # 줄 수와 순서는 보존 — 죽은 배치 자리는 원문만 담고 failed로 남는다
        assert [line.original for line in result.lines] == LINES
        assert [line.failed for line in result.lines] == [
            False, False, True, True, False, False,
        ]
        assert [line.translation for line in result.lines[2:4]] == ["", ""]
        assert result.lines[2].pronunciation is None
        assert [line.translation for line in result.lines[:2]] == ["t-line 0", "t-line 1"]
        assert [line.translation for line in result.lines[4:]] == ["t-line 4", "t-line 5"]

    def test_isolation_also_applies_in_sequential_mode(self, monkeypatch, tmp_path):
        # 동시성 스위치를 1로 내려도 실패 격리 규칙은 같아야 한다
        small_batches(monkeypatch)
        translator = make_translator(monkeypatch, tmp_path, batch_concurrency=1)

        def fake_post(url, json, headers, timeout):
            batch = prompt_lines(json)
            if batch[0] == "line 0":
                raise requests.exceptions.ConnectionError("first batch died")
            return translated(batch)

        monkeypatch.setattr("everyric2.translation.translator.requests.post", fake_post)

        result = translator.translate(SONG, source_lang="ja", target_lang="ko")

        assert [line.failed for line in result.lines] == [
            True, True, False, False, False, False,
        ]
        assert [line.translation for line in result.lines[2:]] == EXPECTED[2:]

    def test_all_batches_failing_still_raises(self, monkeypatch, tmp_path):
        # 전부 실패했는데 '성공'으로 마감하면 번역이 통째로 빈 채 저장된다 — 그건 500이 낫다
        small_batches(monkeypatch)
        translator = make_translator(monkeypatch, tmp_path, batch_concurrency=3)

        def fake_post(url, json, headers, timeout):
            raise requests.exceptions.ConnectionError("upstream down")

        monkeypatch.setattr("everyric2.translation.translator.requests.post", fake_post)

        with pytest.raises(RuntimeError, match="Connection failed"):
            translator.translate(SONG, source_lang="ja", target_lang="ko")

    def test_single_batch_song_propagates_as_before(self, monkeypatch, tmp_path):
        # 배치가 하나뿐인 짧은 곡은 병렬 경로를 타지 않고 예외가 그대로 올라간다
        translator = make_translator(monkeypatch, tmp_path, batch_concurrency=4)

        def fake_post(url, json, headers, timeout):
            raise requests.exceptions.ConnectionError("upstream down")

        monkeypatch.setattr("everyric2.translation.translator.requests.post", fake_post)

        with pytest.raises(RuntimeError, match="Connection failed"):
            translator.translate("line 0\nline 1", source_lang="ja", target_lang="ko")
