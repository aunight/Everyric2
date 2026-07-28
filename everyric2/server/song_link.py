"""커버↔원곡 관계 조회 (songlink/1 consumer, 중립).

플랫폼 코퍼스의 메타데이터·크레딧에서 파생된 관계를 다운로드 0으로 받아 온다. 이 리포는
중립 계약(GET {url}/lookup?platform=youtube&id=<id> → {found, original, song, match_status,
match_path, confidence})만 알고, 실제 외부 서비스 이름·스키마는 남기지 않는다.

⚠ 응답은 판정이 아니라 후보다 — 관계 대부분이 자동 파생(제공측 실측: 오디오 검증 정답지
대비 74.5%)이라, 이것만으로 링크를 확정하지 않는다. 확정은 캐시 쌍 게이트 + 반주 상관
(오프셋 산출)이 그대로 담당한다.
"""

import logging

logger = logging.getLogger(__name__)

_LOOKUP_TIMEOUT_SEC = 3.0


def lookup_original(video_id: str) -> dict | None:
    """커버 video_id의 원곡 관계 → songlink/1 응답 dict. 미설정/미스/오류/자기참조는 None.

    동기 함수라 async 문맥에서는 asyncio.to_thread로 감싸서 부른다.
    """
    from everyric2.config.settings import get_settings

    server = get_settings().server
    if not server.song_link_url:
        return None

    import requests

    try:
        resp = requests.get(
            f"{server.song_link_url.rstrip('/')}/lookup",
            params={"platform": "youtube", "id": video_id},
            headers=(
                {"Authorization": f"Bearer {server.song_link_key}"}
                if server.song_link_key
                else {}
            ),
            timeout=_LOOKUP_TIMEOUT_SEC,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.info("곡 관계 조회 실패 — 관계 없이 진행해요 (video %s)", video_id)
        return None

    if not data.get("found"):
        return None
    original = (data.get("original") or {}).get("id")
    if not original or original == video_id:
        return None
    return data
