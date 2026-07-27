"""사람이 만든 유튜브 자막을 «가사 줄이 시작할 수 없는 구간»의 앵커로 쓴다.

## 왜 필요한가 (실측 2026-07-26, zyRt-nBM3dY 시니컬 나이트 플랜)

8.7~24.9초가 크레디트 표시 구간(간주)인데 정렬이 그 자리에 가사 8줄을 밀어 넣고, 그 대가로
25~58초를 33초 비웠다. 붕괴 기제는 star 열의 점수가 ``log(1.0)=0``이고 실제 토큰은 전부
음수라는 것이다(``ctc_engine`` 참고) — **DP는 실제 토큰이 쓰는 프레임 수를 최소화하는 게
이득**이라, 합성보컬처럼 토큰 점수가 전 구간 균일 바닥이면 「글자를 앞으로 몰고 뒤를 star로
비우기」가 최적해가 된다.

기존 간주 방어 장치(``_post_interlude_windows``·``_snap_post_interlude_leak``·
``_leaked_runs``·``_clamp_stretched_lines``)는 전부 **«VAD 무음 갭»이라는 단 하나의 앵커**
위에 서 있는데, 이 곡의 VAD 첫 구간이 ``[0.6, 45.2]``로 간주를 통째로 삼켰다(``audio/vad.py``는
상대 RMS 백분위 게이트이고 절대 플로어가 없다). 즉 판정 좌표계 자체가 사고 현장을 포함하지
않았다. 분포 기반 판정도 배제됐다: 앞 12줄의 실제 char rate가 4.97/s로
``mass_leak_min_char_rate``(11.0)의 절반도 안 돼 임계값으로 정상 곡과 구별할 수 없다.

그래서 **완전히 다른 좌표계**를 하나 더 들여온다: 사람이 찍은 자막 타임스탬프. 표시
타이밍으로는 부정확하지만(그래서 ``clean_caption_lines``가 타임스탬프를 버린다) 우리에게
필요한 것은 ±0.1초가 아니라 «24.9초 전엔 이 가사 줄이 시작하지 않는다»는 ±2초짜리 구조
제약이고, 자막은 그 용도로 충분하다.

실측 매칭률: 정규화 후 부분 매칭으로 45/57줄(79%)이 매칭됐다(원문 그대로는 6/57=11% —
이 곡 자막은 ``(Furetemitai himitsu to) 触れてみたい秘密と``처럼 로마자를 괄호로 병기한다).
매칭된 앵커는 정답을 정확히 가리켰다 — 간주 앞 4줄은 ±0.2초, 간주에 밀어 넣은 8줄만
17~22초 틀렸다.

## 크레디트를 따로 걸러낼 필요가 없는 이유

우리 가사(위키·LRCLIB·사용자 입력)에는 크레디트가 없다. 그래서 «우리 가사와 매칭되는 자막
이벤트만» 앵커로 쓰면 크레디트 3줄(``･Vocal:初音ミク``, ``・Music＆Words : Ayase``,
``Ayase/シニカルナイトプラン``)이 자동으로 탈락한다(실측 확인). 자막의 번역·화자 표기·추임새도
같은 원리로 걸러진다. 이 모듈이 자막 텍스트를 **형태로** 판정하지 않는 것은 설계다 — 그 길은
이미 실패했다(``_is_credit_line``이 위 3줄을 전부 놓쳤다).

## 「매칭이 없다」 ≠ 「가사가 없다」 (2026-07-26 오폭 실측)

정상 곡 ``ba7YbGO2aq4``(numb numb)에서 오폭이 났고 원인이 둘이었다.

**① 단방향 포함 매칭.** 우리 줄이 자막보다 길면 매칭이 실패한다 —
``우리 '網膜に焼き付く影 numb numb'`` vs ``자막 '網膜に焼き付く影'``. 매칭률이 76%로 떨어지고
**가사가 가득한 구간 3개가 「가사 없는 공백」으로 잘못 잡혔다**(그 안에 우리 줄이 5·5·2개).
→ ``keys_match``가 양방향으로 본다.

**② 「그 사이에 매칭된 이벤트가 없다」는 규칙 자체.** 매칭 실패의 원인이 두 갈래인데 그 규칙은
둘을 구별하지 못한다. 진짜 공백(자막에 그 구간 이벤트가 없거나 크레디트뿐)과 매칭 실패(자막에
가사가 있는데 못 맞춤)는 정반대의 결론을 요구한다. → ``lyric_like_events``가 **순서를 무시한
전역 확인**으로 그 둘을 가른다. 개수가 아니라 내용을 묻는다: 사고 곡의 금지 구간에는 이벤트가
3개 있지만 전부 크레디트이고 우리 57줄 어디에도 없다.

## 이 기능의 실제 영향권 (실측)

수동 원어 자막이 있고 매칭률이 높은 곡만이다. 대조군 8곡 중 ``zyRt-nBM3dY``(표적, 79%)와
``ba7YbGO2aq4``(65cue, 최대 오폭 위험)·``04L-NZAObiE``(100% 매칭, 금지 구간 0개)만 앵커가
붙고, ``s5Rkv_5Sbbo``·``8JRuowZtRBc``는 수동 자막이 없으며 ``VWVtIg5cdDU``·``G5hScSFkib4``·
``OHcNQHbWrFY``는 매칭 0%로 안전장치에 걸린다.

## 이 모듈의 경계

순수 함수만 있다 — 네트워크도 torch도 없다. IO는
``server.services.youtube_captions.iter_manual_caption_events``가, emission 마스킹은
``alignment.ctc_engine``이 한다.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# 괄호 안 로마자 병기 제거 — «(Furetemitai himitsu to) 触れてみたい秘密と» 같은 자막을
# 우리 가사와 맞추려면 이것을 먼저 걷어내야 한다 (실측: 이 처리 없이는 11%만 매칭됐다)
_PAREN_RE = re.compile(r"[（(\[【][^）)\]】]*[）)\]】]")
# 구두점·장식·공백 전부 제거. 자막과 가사는 같은 노래를 다른 표기 관습으로 적으므로
# (「、」 유무, 「〜」 유무, 「♪」 장식) 문자만 남겨 비교한다.
_PUNCT_RE = re.compile(r"[\s、。，．,\.！？!?…‥・·､｡「」『』（）\(\)\[\]~〜～\-—ー♪`＆&/]+")

# 부분 매칭이라 짧은 키는 아무 데나 걸린다(「ああ」는 거의 모든 줄 안에 있다). 그래서 두 키 중
# **짧은 쪽**이 이 길이 미만이면 매칭으로 인정하지 않는다. 4자로 둔 근거는 실측 앵커의 하한이다 —
# 사고 곡에서 간주 앞 마지막 앵커가 「しようよ」(정규화 후 정확히 4자)였고, 그것을 잃으면 표적
# 구간의 앞 경계가 사라진다. 3자로 내리면 「ラララ」·「ahah」류 후렴 조각이 앵커가 된다.
MIN_KEY_LEN = 4

# 자막 키가 우리 줄의 **조각**일 때(자막이 우리보다 짧은 방향) 요구하는 최소 길이 비율.
# 방향에 따라 규칙이 다른 이유:
#   · 우리 줄 ⊂ 자막  — 자막 한 이벤트가 우리 여러 줄을 이어 붙이는 것은 정상이다
#     (실측: 「別に意味とか無いけどさ、眠い目を擦る」이 우리 4·5번 줄). 비율이 낮아도 정당하다.
#   · 자막 ⊂ 우리 줄 — 자막이 우리 줄의 일부만 적은 것이다. 정당한 경우도 있고
#     (실측 ba7YbGO2aq4: 우리 「網膜に焼き付く影 numb numb」 vs 자막 「網膜に焼き付く影」 = 0.5),
#     후렴 조각이 우연히 걸린 경우도 있다(「numb numb」만 있는 이벤트). 그래서 «상당 부분»을 요구한다.
# 실측 정당 사례가 둘이고 그 하한이 이 값을 정한다 (ba7YbGO2aq4):
#   「網膜に焼き付く影」(8) ⊂ 「網膜に焼き付く影 numb numb」(16) = 0.50
#   「ゆらゆら」(4)        ⊂ 「ゆらゆら numb numb」(12)        = 0.33
# 0.3은 그 둘보다 낮고, 20자짜리 줄에 걸리는 4자 후렴 조각(0.2)은 막는다.
# **이 비율이 후렴 오매칭을 다 막지는 못한다** — 「numb numb」(8)만 있는 이벤트는 16자 줄에서
# 0.5로 통과한다. 그 방어는 순서 매칭·인접성 게이트·구간 내 이벤트 검사가 함께 한다.
_MIN_FRAGMENT_RATIO = 0.3

# 자막 이벤트의 dDurationMs가 0/누락일 때 쓰는 최소 표시 길이. 앵커의 «끝»은 금지 구간의
# 앞 경계라 0이면 그 줄의 가창이 끝나기 전부터 금지해 버린다.
_MIN_EVENT_SEC = 0.5

# 앵커 줄이 «최소한 이만큼은 불렸을 것»으로 보는 글자당 시간. 자막이 가창보다 먼저 사라지는
# 트랙이 흔하므로, 금지 구간의 앞 경계는 자막 표시 종료와 이 추정치 중 **늦은 쪽**을 쓴다
# (둘 중 어느 쪽도 구간을 넓히지 않는다 = 항상 보수적).
# 0.4초/글자는 실측 가창 속도(사고 곡 간주 앞 블록 4.97자/초 = 0.2초/글자)의 **두 배로**
# 넉넉하게 잡은 값이다 — 이 추정이 짧으면 앞 줄의 꼬리를 밀어낼 수 있으므로 과대추정이 안전하다.
_SEC_PER_CHAR = 0.4


def anchor_key(text: str) -> str:
    """자막 이벤트와 가사 줄을 비교하기 위한 정규화 키.

    실측된 규칙 그대로다(45/57 매칭). 규칙을 손대면 매칭률이 재측정 없이 흔들리므로,
    바꿀 때는 실오디오에서 다시 재는 것을 전제로 한다.
    """
    return _PUNCT_RE.sub("", _PAREN_RE.sub("", text or "")).lower()


@dataclass(frozen=True)
class CaptionAnchor:
    """가사 줄 하나와 그것을 담은 자막 이벤트의 시각."""

    line_idx: int
    event_idx: int
    start: float
    end: float
    # 감사용 — 어떤 자막 텍스트가 이 앵커를 만들었는지 사후에 되짚을 수 있어야 한다
    text: str
    # 이 앵커가 담당하는 가사 줄의 글자 수 (정규화 키 기준) — 가창 길이 추정에 쓴다
    chars: int = 0


@dataclass(frozen=True)
class SpanCandidate:
    """인접성·간격 게이트를 통과한 금지 구간 후보와 그 채택 여부."""

    start: float
    end: float
    # 앞 앵커의 가사 줄 인덱스 — 어느 두 줄 사이인지 사후에 되짚는 키
    left_line: int
    # 두 앵커의 자막 이벤트 사이에 있던 이벤트 수와, 그 중 우리 가사와 전역 매칭된 수
    events_between: int
    lyric_like: int
    accepted: bool
    reason: str = ""

    def as_debug(self) -> list[Any]:
        return [
            self.left_line,
            round(self.start, 2),
            round(self.end, 2),
            self.events_between,
            self.lyric_like,
            self.reason or "ok",
        ]


@dataclass(frozen=True)
class AnchorPlan:
    """앵커에서 도출한 두 종류의 제약과 그 판정 근거.

    ``spans`` — **음성 제약**: 가사 줄이 놓일 수 없는 (start, end) 구간.
    ``line_starts`` — **양성 제약**: line_idx → 그 줄이 시작해야 하는 시각(자막 시각).

    둘을 나눠 두는 이유는 실패 방향이 정반대라서다. 음성 제약이 틀리면 «덜 막는다»로
    실패하지만(창이 좁아질 뿐), 양성 제약이 틀리면 맞는 줄을 틀린 곳으로 **끌고 간다**.
    그래서 양성 제약에는 더 높은 매칭률을 요구하고 스위치도 따로 둔다.
    """

    spans: list[tuple[float, float]] = field(default_factory=list)
    line_starts: dict[int, float] = field(default_factory=dict)
    # line_idx → (자막 시작, 자막 표시 종료) — line_starts와 같은 매칭·같은 게이트에서
    # 나오지만 끝 시각까지 담는다. 자막 스캐폴드(caption_scaffold)가 줄 길이 추정에 쓴다.
    line_spans: dict[int, tuple[float, float]] = field(default_factory=dict)
    debug: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.spans or self.line_starts)


def keys_match(lyric_key: str, caption_key: str) -> bool:
    """정규화된 가사 키와 자막 키가 **같은 줄을 가리키는가** — 양방향 부분 매칭.

    단방향(`가사 ⊂ 자막`)만 보면 **우리 줄이 자막보다 길 때 매칭이 실패한다.** 실측
    ba7YbGO2aq4(정상 곡): 우리 「網膜に焼き付く影 numb numb」 vs 자막 「網膜に焼き付く影」이
    매칭되지 않아 매칭률이 76%로 떨어지고, 그 결과 **가사가 가득한 구간이 「가사 없는 공백」으로
    잘못 잡혔다** — 정확히 우리가 피하려는 사고다. 그래서 양방향으로 본다.

    방향별 조건이 다른 근거는 ``_MIN_FRAGMENT_RATIO`` 주석에 있다. 이 함수는 앵커 매칭과
    「구간 안 미매칭 이벤트가 가사처럼 보이는가」 판정에 **똑같이** 쓰인다 — 두 판정이 갈라지면
    한쪽이 가사로 본 것을 다른 쪽이 아니라고 보게 된다.
    """
    if len(lyric_key) < MIN_KEY_LEN or len(caption_key) < MIN_KEY_LEN:
        return False
    if lyric_key in caption_key:
        return True
    if caption_key in lyric_key:
        return len(caption_key) >= _MIN_FRAGMENT_RATIO * len(lyric_key)
    return False


def match_anchors(lyric_texts: list[str], events: list[dict[str, Any]]) -> list[CaptionAnchor]:
    """가사 줄 ↔ 자막 이벤트를 **순서를 지켜 앞으로만** 매칭한다.

    순서 제약이 필수인 이유: 반복 후렴은 자막에도 여러 번 나오므로 자유 탐색이면 뒤쪽 등장이
    앞 줄에 붙어 시각이 수십 초 틀어진다. 자막 포인터를 유지하고 각 가사 줄을 그 포인터
    **이후**에서만 찾는다.

    포인터를 매칭한 이벤트에서 +1 하지 않는 것도 의도다 — 자막 한 이벤트가 우리 두 줄을 담을
    수 있다(실측: ``別に意味とか無いけどさ、眠い目を擦る``이 우리 4·5번 줄이다).

    매칭 판정 자체는 ``keys_match``(양방향)에 있다.
    """
    keys = [anchor_key(t) for t in lyric_texts]
    ev_keys = [anchor_key(e.get("text", "")) for e in events]
    anchors: list[CaptionAnchor] = []
    ptr = 0
    for i, key in enumerate(keys):
        if len(key) < MIN_KEY_LEN:
            continue
        for j in range(ptr, len(ev_keys)):
            if not keys_match(key, ev_keys[j]):
                continue
            ev = events[j]
            start = float(ev.get("start") or 0.0)
            end = max(float(ev.get("end") or 0.0), start + _MIN_EVENT_SEC)
            anchors.append(
                CaptionAnchor(
                    line_idx=i,
                    event_idx=j,
                    start=start,
                    end=end,
                    text=str(ev.get("text") or ""),
                    chars=len(key),
                )
            )
            ptr = j
            break
    return anchors


def lyric_like_events(lyric_texts: list[str], events: list[dict[str, Any]]) -> set[int]:
    """우리 가사의 **어느 줄과든** 매칭되는 자막 이벤트의 인덱스 (순서 무시, 전역 확인).

    「자막에 매칭이 없다」는 「그 구간에 가사가 없다」를 뜻하지 않는다. 매칭 실패의 원인이
    두 갈래이고 그 둘은 정반대의 결론을 요구한다:

      · **진짜 공백** — 자막에 그 구간 이벤트가 없거나 가사가 아닌 것뿐이다
        (실측 zyRt-nBM3dY: 8.7~24.9에 크레디트 3줄만 있고, 그 3줄은 우리 57줄 어디에도 없다)
        → 금지해도 된다
      · **매칭 실패** — 자막에 가사가 있는데 순서 매칭이 놓쳤다
        (실측 ba7YbGO2aq4: 자막이 우리보다 짧아 단방향 포함이 실패했다)
        → 금지하면 정상 배치된 줄을 밀어낸다

    이 함수가 그 둘을 가른다. **순서 매칭(``match_anchors``)이 놓친 것과 정말 우리 가사에 없는
    것을 구별하려면 순서를 무시한 전역 확인이 필요하다** — 순서 매칭에서 탈락한 이벤트가
    우리 어느 줄과든 맞으면 그것은 「가사인데 못 맞춘 것」이다.

    크레디트를 **형태로** 판정하지 않는 것이 중요하다. 그 길은 이미 실패했다
    (``_is_credit_line``이 이 곡의 크레디트 3줄을 전부 놓쳤다 — ``･Vocal:初音ミク``의 구분자와
    ``Ayase/シニカルナイトプラン``의 형태가 규칙 밖이다). 여기서는 형태를 묻지 않고
    «우리 가사에 있는 말인가»만 묻는다.
    """
    keys = [k for k in (anchor_key(t) for t in lyric_texts) if len(k) >= MIN_KEY_LEN]
    out: set[int] = set()
    for j, ev in enumerate(events):
        ek = anchor_key(ev.get("text", ""))
        if len(ek) < MIN_KEY_LEN:
            continue
        if any(keys_match(k, ek) for k in keys):
            out.add(j)
    return out


def span_candidates(
    anchors: list[CaptionAnchor],
    events: list[dict[str, Any]],
    lyric_texts: list[str],
    min_gap_sec: float,
    margin_sec: float,
) -> list[SpanCandidate]:
    """앵커에서 «가사 줄이 시작할 수 없는 구간» 후보를 뽑고 각각을 채택/기각한다.

    게이트가 셋이고 전부 보수적이다.

    ① **두 앵커가 가사에서도 이웃일 때만** 본다 (``line_idx``가 연속). 사이에 매칭 안 된 우리
       줄이 하나라도 있으면 그 줄이 정말 거기서 불릴 수 있어 금지할 근거가 없다. 실측
       ba7YbGO2aq4의 오폭 후보 3개는 사이에 우리 줄이 5·5·2개 있었고, 이 게이트가 셋 다
       기각한다.

    ② **간격이 간주라고 부를 만큼 길 때만** 본다 (``min_gap_sec``). 짧은 간격은 노래의 쉼이고,
       다음 줄을 미리 띄우지 않는 자막 습관만으로도 몇 초는 벌어진다.

    ③ **그 사이의 자막 이벤트가 가사처럼 보이면 기각한다** (``lyric_like_events``). ①이
       「우리 가사 쪽에 그 구간에 놓일 것이 없다」를 보장하지만, 그것은 **매칭이 옳았을 때만**
       참이다. 매칭이 실패해 우연히 인접해 보일 수 있으므로, 자막 쪽에서도 확인한다 — 그
       구간의 이벤트가 우리 가사의 어느 줄과든 맞으면 그것은 가사이고, 금지해서는 안 된다.
       실측 사고 구간의 크레디트 3줄은 우리 57줄 어디에도 없어 이 게이트를 통과한다. 즉
       「이벤트 수 0」이 조건일 수 없다 — 물어야 할 것은 개수가 아니라 **내용**이다.

    앞 경계: 앞 앵커의 가창이 **어디까지 갔는지** 두 근거 중 늦은 쪽을 쓴다 — 자막 표시
    종료(사람이 찍은 «이 줄은 끝났다»)와 그 줄의 글자 수로 본 가창 길이 추정. 자막이 가창보다
    먼저 사라지는 트랙이 흔하고, 반대로 다음 줄까지 계속 띄워 두는 트랙도 흔하다. 늦은 쪽을
    고르면 어느 습관에서도 앞 줄의 꼬리를 밀어내지 않는다. 한 이벤트가 우리 여러 줄을 담고
    있으면 그 줄들의 글자를 모두 합쳐 추정한다.

    경계 여유(margin): 자막 시각은 수백 ms 어긋난다. 앞뒤 모두 margin만큼 안으로 물려
    금지 구간을 **줄이는** 방향으로만 작용한다 — 보수적 실패가 기본이다.
    """
    # 한 자막 이벤트에 붙은 우리 줄들의 글자 수 합 — 그 이벤트의 가창 길이 추정 기준
    chars_per_event: dict[int, int] = {}
    for a in anchors:
        chars_per_event[a.event_idx] = chars_per_event.get(a.event_idx, 0) + a.chars
    lyricish = lyric_like_events(lyric_texts, events)

    out: list[SpanCandidate] = []
    for a, b in zip(anchors, anchors[1:]):
        if b.line_idx != a.line_idx + 1:
            continue  # ① 사이에 매칭 안 된 가사 줄이 있다 — 그 줄이 여기서 불릴 수 있다
        sung = max(_MIN_EVENT_SEC, chars_per_event[a.event_idx] * _SEC_PER_CHAR)
        occupied_until = max(a.end, a.start + sung)
        if b.start - occupied_until < min_gap_sec:
            continue  # ② 간주라고 부를 길이가 아니다
        lo, hi = occupied_until + margin_sec, b.start - margin_sec
        if hi - lo <= 0:
            continue  # 여유가 간격을 다 먹었다 (보수적 실패)
        # ③ 두 앵커의 이벤트 **사이**에 있는 이벤트들 — 시간순이므로 인덱스 구간이 곧 시간 구간
        between = list(range(a.event_idx + 1, b.event_idx))
        lyricish_between = [j for j in between if j in lyricish]
        out.append(
            SpanCandidate(
                start=round(lo, 3),
                end=round(hi, 3),
                left_line=a.line_idx,
                events_between=len(between),
                lyric_like=len(lyricish_between),
                accepted=not lyricish_between,
                reason="lyric_events" if lyricish_between else "",
            )
        )
    return out


def forbidden_spans(
    anchors: list[CaptionAnchor],
    events: list[dict[str, Any]],
    lyric_texts: list[str],
    min_gap_sec: float,
    margin_sec: float,
) -> list[tuple[float, float]]:
    """채택된 금지 구간만 (start, end) 목록으로. 판정 근거는 ``span_candidates`` 참고."""
    return [
        (c.start, c.end)
        for c in span_candidates(anchors, events, lyric_texts, min_gap_sec, margin_sec)
        if c.accepted
    ]


def derive_anchor_plan(
    lyric_texts: list[str],
    tracks: Iterable[tuple[str, list[dict[str, Any]]]],
    *,
    min_match: float = 0.5,
    min_gap_sec: float = 8.0,
    margin_sec: float = 1.0,
    audio_sec: float = 0.0,
    max_forbidden_ratio: float = 0.35,
    positive_min_match: float = 0.85,
) -> AnchorPlan:
    """가장 그럴듯한 트랙을 **먼저 맞혀 보고**, 기준을 넘으면 그것으로 제약을 만든다.

    트랙 선택은 «모든 트랙을 재서 최적을 고른다»가 아니다. 트랙마다 yt-dlp 호출 1회라
    그 방식은 곡당 요청이 두 자리로 간다. 대신 ``tracks``가 사전 정보로 정렬된 게으른
    이터러블(``youtube_captions.order_manual_tracks``: 우리 가사의 문자 체계 → 제목·채널명의
    문자 체계 → 알파벳)이라는 전제 아래, **``min_match``를 넘는 첫 트랙에서 멈춘다.**

    이 전제가 왜 필요한지가 실측으로 드러났다: ``zyRt-nBM3dY``의 수동 트랙은
    ``[ar, zh-TW, en, fil, id, ja, ko, ms, es, th, tr, vi]``로 **``ja``가 알파벳 6번째**다.
    상한 5개로 자르고 최적을 고르는 방식이면 ``zh-TW``가 뽑혀 매칭률 11%로 앵커가 버려진다
    (``ja``는 79%). 문자 체계 힌트가 ``ja``를 첫 후보로 올리면 요청 1회로 끝난다.
    상한은 이제 «최적 탐색 예산»이 아니라 «포기 지점»이다.

    금지 구간(음성 제약) 안전장치 — 어느 하나라도 걸리면 금지 구간 없이 돌아간다:
      ① 매칭률 하한 — 우리 가사와 자막이 애초에 다른 곡/다른 버전이면 제약이 거짓말이 된다
      ② 금지 구간 0개 — 자막이 말하는 간주가 없다(구간 안 이벤트가 가사처럼 보여 기각된 경우 포함)
      ③ 총 금지 길이 상한 — 반복 후렴 오매칭으로 앵커가 크게 밀리면 금지 구간이 비정상적으로
         커진다. 4분 곡에서 간주가 곡의 3분의 1을 넘는 구성은 드물어, 넘으면 매칭을 의심한다.

    ``line_starts``(양성 제약)는 **더 높은 매칭률**(``positive_min_match``)을 요구한다.
    실패 방향이 반대이기 때문이다 — 금지 구간이 틀리면 덜 막고 끝나지만, 양성 제약이 틀리면
    맞는 줄을 틀린 곳으로 끌고 간다. 금지 구간이 하나도 없어도 양성 제약은 유효하므로
    (간주가 없는데 배치가 붕괴한 곡) 둘의 판정을 독립적으로 낸다.
    """
    matchable = sum(1 for t in lyric_texts if len(anchor_key(t)) >= MIN_KEY_LEN)
    debug: dict[str, Any] = {"lines": len(lyric_texts), "matchable": matchable}
    if not matchable:
        return AnchorPlan(debug={**debug, "skipped": "no_matchable_lines"})

    chosen: tuple[str, list[CaptionAnchor], list[dict[str, Any]], float] | None = None
    tried: list[list[Any]] = []
    for lang, events in tracks:
        anchors = match_anchors(lyric_texts, events)
        rate = len(anchors) / matchable
        tried.append([lang, len(events), len(anchors), round(rate, 3)])
        if rate >= min_match:
            chosen = (lang, anchors, events, rate)
            break
    debug["tracks"] = tried
    if chosen is None:
        # 후보를 다 봤는데 기준을 넘는 트랙이 없다. 트랙이 아예 없는 것과 구별해 남긴다 —
        # 전자는 «이 영상엔 수동 자막이 없다», 후자는 «자막이 우리 가사와 다르다»다.
        return AnchorPlan(debug={**debug, "skipped": "low_match" if tried else "no_manual_track"})

    lang, anchors, events, rate = chosen
    debug.update({"track": lang, "matched": len(anchors), "rate": round(rate, 3)})
    # 어느 자막 줄이 어느 시각을 주장하는지 — 두 제약의 유일한 근거이고 감사의 출발점이다
    debug["anchors"] = [
        [a.line_idx, round(a.start, 2), round(a.end, 2), a.text[:40]] for a in anchors
    ]

    # ── 양성 제약: 앵커 줄의 시각 그대로 ──
    line_starts: dict[int, float] = {}
    line_spans: dict[int, tuple[float, float]] = {}
    if rate >= positive_min_match:
        # 같은 자막 이벤트에 우리 두 줄이 붙으면 둘의 시각이 같다. 그대로 둔다 — 창 정렬이
        # 순서를 지켜 순차 배치하므로 같은 시각이 모순을 만들지 않는다.
        # (자막 스캐폴드는 공유 이벤트의 후속 줄을 자체적으로 보간으로 돌린다)
        line_starts = {a.line_idx: a.start for a in anchors}
        line_spans = {a.line_idx: (a.start, a.end) for a in anchors}
    else:
        debug["positive_skipped"] = "low_match"

    # ── 음성 제약: 금지 구간 ──
    candidates = span_candidates(anchors, events, lyric_texts, min_gap_sec, margin_sec)
    # 채택/기각을 모두 남긴다 — 기각된 후보와 그 사유가 오폭을 막았다는 증거다
    debug["candidates"] = [c.as_debug() for c in candidates]
    spans = [(c.start, c.end) for c in candidates if c.accepted]
    total = sum(e - s for s, e in spans)
    if not spans:
        debug["skipped"] = "no_gap"
    elif audio_sec > 0 and total > audio_sec * max_forbidden_ratio:
        debug["skipped"] = "too_much_forbidden"
        debug["forbidden_sec"] = round(total, 2)
        spans = []
    else:
        debug["forbidden_sec"] = round(total, 2)
        debug["spans"] = [[s, e] for s, e in spans]
    return AnchorPlan(spans=spans, line_starts=line_starts, line_spans=line_spans, debug=debug)


def script_counts(text: str) -> dict[str, int]:
    """문자 체계별 글자 수 (kana / hangul / han / latin / total).

    ``ctc_engine``의 스크립트 분류(``_char_script``)와 판정이 겹치지만 그 모듈은 torch를
    최상위 import한다 — API 프로세스에 torch를 끌고 들어가지 않기로 한 계약
    (``server/main.py``의 지연 임포트)을 지키려면 여기서 문자만 세는 편이 맞다. 이 모듈은
    표준 라이브러리만 쓰므로 API 경로에서도 안전하게 부를 수 있다.

    쓰는 곳이 둘이다: 앵커 트랙 순서(``script_lang_hint``)와 자막 가사 경로의 문자 구성
    게이트(``youtube_captions.has_cjk_script``). 「이 텍스트가 CJK 문자를 쓰는가」의 정의가
    하나여야 두 판정이 어긋나지 않는다.
    """
    counts = {"kana": 0, "hangul": 0, "han": 0, "latin": 0, "total": 0}
    for ch in text or "":
        if ch.isspace():
            continue
        o = ord(ch)
        counts["total"] += 1
        if 0x3040 <= o <= 0x30FF or 0xFF66 <= o <= 0xFF9D:
            counts["kana"] += 1
        elif 0xAC00 <= o <= 0xD7A3 or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F:
            counts["hangul"] += 1
        elif 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0xF900 <= o <= 0xFAFF:
            counts["han"] += 1
        elif ("a" <= ch.lower() <= "z") or 0x00C0 <= o <= 0x024F:
            counts["latin"] += 1
    return counts


def script_lang_hint(text: str) -> str | None:
    """텍스트의 문자 체계에서 언어를 추정한다 — 앵커 트랙을 받아 볼 순서를 정하는 데만 쓴다.

    정확도는 중요하지 않다: 틀려도 트랙 순서만 바뀐다.
    """
    c = script_counts(text)
    if c["kana"]:
        return "ja"
    if c["hangul"]:
        return "ko"
    if c["han"]:
        return "zh"
    return None
