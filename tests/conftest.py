"""Pytest configuration and fixtures."""

import os

import pytest

from everyric2.config.settings import reset_settings

# 테스트에서 지워 두는 환경변수 접두사 — 이게 없으면 배포 환경에서만 테스트가 깨진다.
#
# 왜 필요한가: `everyric2/translation/translator.py`가 임포트 시점에 `load_dotenv()`를
# 부른다. 그래서 그 모듈을 임포트하는 테스트가 하나라도 수집되면 리포 루트의 `.env`가
# **프로세스 전역 환경변수로 주입**된다. 배포 서버에는 `.env`가 있으니 그 뒤로
# `ServerSettings()` 같은 기본값 단언이 실제 배포값을 읽어 실패한다(실측: 서버에서
# test_settings.py::test_default_values와 test_vocaro_proxy.py가 깨졌고, `.env`가 없는
# 개발 머신에서는 전부 통과해 여태 보이지 않았다).
#
# 프로덕션 로딩을 건드리지 않는 이유: systemd 유닛은 `EnvironmentFile=.env`로 이미
# 환경을 주입하므로 배포에서는 `load_dotenv()`가 없어도 되지만, systemd 없이 직접
# 실행하는 로컬 개발에서는 그 호출이 실제로 설정을 싣는다(중첩 설정 모델은 부모의
# `env_file`을 물려받지 않는다). 즉 그 호출은 로컬에서 부하를 지고 있어 함부로 뺄 수
# 없다. 대신 테스트를 환경에 독립적으로 만든다 — 검증이 실행 환경마다 다른 결과를
# 내면 배포 전 검증으로서 의미가 없다.
_CLEARED_PREFIXES = ("EVERYRIC_",)


@pytest.fixture(autouse=True)
def hermetic_env():
    """테스트가 배포 `.env`를 읽지 않도록 관련 환경변수를 비우고, 끝나면 되돌린다.

    개별 테스트가 `monkeypatch.setenv`로 값을 넣는 것은 그대로 동작한다 — 이 픽스처는
    테스트 본문이 시작되기 전에 지우고, 본문에서 설정한 값은 monkeypatch가 되돌린다.
    """
    saved = {k: v for k, v in os.environ.items() if k.startswith(_CLEARED_PREFIXES)}
    for k in saved:
        del os.environ[k]
    try:
        yield
    finally:
        for k, v in saved.items():
            os.environ[k] = v


@pytest.fixture(autouse=True)
def no_audio_cache(hermetic_env):
    """오디오 캐시를 기본으로 꺼 둔다 — 켜려는 테스트는 스스로 켠다.

    끄지 않으면 두 가지가 깨진다. ① `audio.cache_dir` 기본값이 `~/.cache/everyric2/audio`라
    테스트를 돌리는 것만으로 **사용자 홈에 오디오가 쌓인다**. ② 캐시는 프로세스 수명보다
    오래 살므로 `_download_and_hash`를 세는 테스트끼리 서로의 캐시를 히트해
    **실행 순서에 따라 결과가 갈린다**. 검증이 실행 환경·순서마다 다른 결과를 내면 배포 전
    검증으로서 의미가 없다(위 `hermetic_env`와 같은 이유다).
    """
    os.environ["EVERYRIC_AUDIO_CACHE_ENABLED"] = "false"
    try:
        yield
    finally:
        # hermetic_env가 되돌리기 전에 우리가 넣은 값을 치운다
        os.environ.pop("EVERYRIC_AUDIO_CACHE_ENABLED", None)


@pytest.fixture(autouse=True)
def reset_settings_before_test(no_audio_cache):
    """Reset settings before each test.

    `no_audio_cache`(→`hermetic_env`)를 인자로 받아 **환경을 비우고 캐시를 끈 뒤에** 설정
    캐시를 지운다 — 순서가 뒤바뀌면 캐시가 오염된 환경으로 다시 채워진다.
    """
    reset_settings()
    yield
    reset_settings()
