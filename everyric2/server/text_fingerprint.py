"""번역 레이어의 지문(fingerprint) — 가사 원문이 같으면 재생성에도 번역이 살아남게 하는 키.

`normalize_line`은 worker.py의 동명 내부 함수(`_normalize_line`)를 그대로 복사한 것이다
(worker는 아직 이 모듈을 임포트하지 않는다 — 순환 임포트 없이 worker를 이 모듈로 전환하는
것은 후속 작업). 두 구현이 갈라지면 TranslationLayer.fingerprint가 worker의 line_meta
매칭 키와 어긋나 저장된 번역을 못 찾는 사고가 난다 — 고칠 때는 반드시 양쪽을 함께 고친다.
"""

import hashlib
import re
import unicodedata


def normalize_line(s: str) -> str:
    """라인 매칭용 정규화 키 — 유니코드 호환 정규화(NFKC) + 서식문자 제거 + 공백 전면 제거.

    라인 메타(발음/번역)는 가사 원문과 별도 경로로 들어와 표기가 미세하게 어긋난다.
    공백만 접던 예전 규칙은 구두점 앞뒤 띄어쓰기 차이(``Are you ready ?`` vs
    ``Are you ready?``)나 전각/반각 차이(``！`` vs ``!``)를 다른 라인으로 취급해
    실측 6줄이 메타를 못 받았다. NFKC가 전각/반각·호환 문자를 접고, 공백을 전부
    지워 띄어쓰기 위치 차이를 흡수한다.
    **구두점 자체는 지우지 않는다** — 지우면 ``行く。``와 ``行く？``처럼 부호만 다른
    별개 라인이 같은 키로 뭉쳐 엉뚱한 메타가 붙는다(과잉 정규화 위험).
    """
    t = unicodedata.normalize("NFKC", s)
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Cf")  # ZWSP·BOM 등 서식문자
    return re.sub(r"\s+", "", t)


def lines_fingerprint(texts: list[str]) -> str:
    """세그먼트 원문 텍스트 목록의 md5 32hex — 정규화 라인들을 "\\n"으로 이어 해시한다.

    normalize_line과 같은 정규화를 쓰므로, worker의 line_meta 매칭에서 같은 라인으로
    취급되는 표기 차이(공백·전각/반각)는 지문도 바꾸지 않는다.
    """
    joined = "\n".join(normalize_line(t) for t in texts)
    return hashlib.md5(joined.encode("utf-8")).hexdigest()
