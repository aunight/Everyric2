"""제목 정규화·후보 매칭 유틸 (곡 인덱스 매칭과 커버 링크 후보 탐색이 공유).

원래 vocaro_index에만 있던 정규화/후보 생성 규칙을 여기로 옮겼다. 규칙 자체는
everyric2-chrome/src/lib/vocaro.ts의 findMatch와 동일 기준을 유지한다:
정규화 정확 일치 → 상호 포함 + 길이비 0.5 이상.

**이 모듈의 결과는 "후보 발견"에만 쓴다.** 커버/원곡이 실제로 같은 곡인지의 최종 판정은
반주 상관 검증(link-jobs)이 담당한다 — 제목만으로 SyncLink를 만들지 않는다. 그래서
매칭 규칙이 다소 헐거워도 안전하다(오탐의 대가는 검증 잡 한 번).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from typing import TypeVar

# 유튜브 곡 제목 관례("곡명 / 아티스트", "곡명 - 아티스트 feat.가수", "【가수】곡명" 등)의
# 구분자 — 하이픈은 양옆 공백이 있을 때만 분리해 합성어를 보존한다
_TITLE_SPLIT_RE = re.compile(r"\s*[/／|｜–—―~〜]\s*|\s+-\s+|\s*[「」『』【】\[\]]\s*")
_FEAT_RE = re.compile(r"(?:^|\s)(?:feat|ft)\.?\s*\S.*$", re.IGNORECASE)

# 괄호로 묶인 가수/독음 병기 («【初音ミク】곡명», «곡명 (아쿠노)») — 통째로 걷어낸 변형도 후보에 넣는다
_BRACKETED_RE = re.compile(r"【[^】]*】|「[^」]*」|『[^』]*』|\[[^\]]*\]|\([^)]*\)|（[^）]*）")

# 업로드 관례 잡토큰 — 커버/인스트/공식MV가 같은 곡을 서로 다른 제목으로 올리는 주범.
# 곡명 자체에 들어갈 일이 거의 없는 표현만 넣는다(제거가 곡명을 깎으면 후보를 놓친다).
_NOISE_TOKEN_RE = re.compile(
    r"official(?:\s*(?:music|lyric)s?)?(?:\s*video|\s*audio|\s*mv)?"
    r"|music\s*video|lyrics?\s*video|audio\s*only"
    r"|\bmv\b|\bpv\b|\bhd\b|\bhq\b|\b4k\b|\b1080p\b|\b720p\b"
    r"|full\s*ver(?:sion)?\.?|short\s*ver(?:sion)?\.?|tv\s*size"
    r"|off\s*vocal|instrumental|\binst\b|karaoke|covered\s*by|\bcover\b"
    r"|カラオケ|歌ってみた|唄ってみた|弾いてみた|叩いてみた|踊ってみた"
    r"|オリジナル曲?|本家|ボカロ"
    r"|커버|불러봤다|한글\s*자막|한국어\s*자막|번역\s*자막",
    re.IGNORECASE,
)

T = TypeVar("T")


def normalize_title(title: str) -> str:
    """NFKC 정규화 + 소문자 + 영숫자/한글/가나/한자만 남기기 (공백·기호 전부 제거)."""
    t = unicodedata.normalize("NFKC", title.lower())
    return "".join(ch for ch in t if ch.isalnum())


def strip_noise_tokens(title: str) -> str:
    """MV/Official/Cover/歌ってみた 류 업로드 잡토큰 제거 (곡명 자체는 건드리지 않는다)."""
    return _NOISE_TOKEN_RE.sub(" ", title)


def candidate_queries(title: str, drop_noise: bool = False) -> list[str]:
    """풀 제목에서 곡명 후보를 정규화 형태로 생성.

    순서: 원문 → feat 제거 → 괄호 세그먼트 제거 → (drop_noise면 잡토큰 제거 변형) →
    각 변형의 구분자 조각(왼쪽 우선). 괄호 제거 변형을 조각보다 먼저 두어
    «【가수】곡명» 류에서 가수명이 곡명보다 먼저 매칭되는 오탐을 막는다.

    반환 순서가 곧 우선순위다 — 호출부는 앞쪽 후보의 매칭을 더 신뢰한다.
    drop_noise 기본값 False는 곡 인덱스 매칭의 기존 동작을 그대로 보존한다.
    """
    seen: set[str] = set()
    out: list[str] = []

    def add(raw: str) -> None:
        q = normalize_title(raw)
        if len(q) >= 2 and q not in seen:
            seen.add(q)
            out.append(q)

    stripped = _BRACKETED_RE.sub(" ", title)
    variants = [title, _FEAT_RE.sub("", title), stripped, _FEAT_RE.sub("", stripped)]
    if drop_noise:
        variants = variants + [strip_noise_tokens(v) for v in variants]
    for v in variants:
        add(v)
    for v in variants:
        for part in _TITLE_SPLIT_RE.split(v):
            add(part)
            add(_FEAT_RE.sub("", part))
    return out


def match_score(a: str, b: str, drop_noise: bool = True) -> tuple[float, int] | None:
    """두 제목의 유사도 (score, priority). 매칭이 없으면 None.

    score: 1.0 = 어떤 후보 변형끼리 정규화 정확 일치, 0.5~1.0 = 상호 포함 시 길이비.
    priority: 매칭된 두 후보의 우선순위 인덱스 합 (작을수록 원제에 가까운 변형끼리 맞음).
    동점 정렬의 tie-break용 — 가수명 조각끼리 우연히 겹친 매칭을 뒤로 민다.
    """
    qa = candidate_queries(a, drop_noise=drop_noise)
    qb = candidate_queries(b, drop_noise=drop_noise)
    best: tuple[float, int] | None = None
    for i, x in enumerate(qa):
        for j, y in enumerate(qb):
            if x == y:
                cand = (1.0, i + j)
            elif x in y or y in x:
                ratio = min(len(x), len(y)) / max(len(x), len(y))
                if ratio < 0.5:
                    continue
                cand = (ratio, i + j)
            else:
                continue
            if best is None or (-cand[0], cand[1]) < (-best[0], best[1]):
                best = cand
    return best


def rank_matches(
    query_title: str,
    entries: Sequence[tuple[T, str]],
    min_score: float = 0.6,
    limit: int = 5,
) -> list[tuple[T, float]]:
    """(키, 제목) 목록을 질의 제목과의 유사도로 정렬해 상위 limit개 반환.

    코퍼스가 작아(수십~수백 곡) 전수 스캔으로 충분하다 — 인덱스/근사 검색은 넣지 않았다.
    """
    scored: list[tuple[float, int, int, T]] = []
    for order, (key, title) in enumerate(entries):
        if not title:
            continue
        hit = match_score(query_title, title)
        if hit is None or hit[0] < min_score:
            continue
        score, priority = hit
        # 정렬 키: 점수 내림차순 → 우선순위 오름차순 → 입력 순서(최신 우선) 유지
        scored.append((-score, priority, order, key))
    scored.sort(key=lambda row: row[:3])
    return [(key, round(-neg_score, 4)) for neg_score, _, _, key in scored[:limit]]
