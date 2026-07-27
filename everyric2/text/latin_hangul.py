"""라틴 문자 → 한글 **조밀(tight) 음차** (결정론).

## 왜 음차하는가

``pron_style.wiki_pronunciation``은 오랫동안 라틴을 원문 그대로 남겼다("규칙화 불가").
그 방침이 정렬을 깨뜨린다는 것이 실측으로 확인됐다.

1. kor/jpn 어댑터에서 **라틴 글자는 정렬되지 않는다.** 코퍼스 20곡에서 라틴이 많은 줄의
   라인 conf가 라틴 없는 줄의 1/10이었다(XKZIQlqVjjk 0.0003 vs 0.0057). 라틴 글자의
   conf<0.01이 90~99%다. vocab에 a–z 토큰이 있어도 모델은 그 소리에 대응시키지 못한다.
2. 같은 emission에 표기만 바꿔 라인별 창 안에서 직접 채점하면
   **원문(라틴) < 관습 음차 < 조밀 음차** 순이다(원문 vs 관습 5/6, 관습 vs 조밀 7/7):

   ===============  ========  ==============  =============
   낱말             원문      관습 음차       조밀 음차
   ===============  ========  ==============  =============
   ``Approved``x4    -1.254   어프루브드 -0.848  어프룹 **-0.562**
   ``Revoke``x4      -3.083   리보크 -1.383      리복 **-0.743**
   ``need``              -    니드 -0.434        닏 **-0.306**
   ``want``              -    원트 -0.671        원 **-0.522**
   ``take``              -    테이크 -0.877      테익 **-0.805**
   ``with``              -    위드 -1.128        윋 **-1.047**
   ===============  ========  ==============  =============
3. 종단 증거 — 사람이 만든 일본어 자막(SRT) 88 cue 대조(XKZIQlqVjjk, 40줄 짝지음, 전역
   오프셋 제거 후 절대 잔차): 라틴 유지 중앙값 0.085s / p90 0.629 / ±0.3s 80.0% →
   조밀 음차 중앙값 **0.056s** / p90 **0.170s** / ±0.3s **95.0%** / ±1.0s **100%**.

## 왜 "조밀"인가

한글 표기 관습은 자음군에 **노래에 없는 모음**을 끼워 넣는다(approved → 어프루브드).
정렬기는 존재하지 않는 음절 2개를 찾게 되고 그만큼 어긋난다. 종성으로 닫으면(어프룹)
실제 가창 음절 수와 맞는다. 그래서 이 모듈의 출력은 관습 표기가 **아니다** — 규칙이 만든
관습형을 ``tighten``이 어말의 삽입 자음 음절(그·드·브·프·스·크·트…)만 받침으로 접거나
버린다. 표시도 이 값을 쓴다: 3단 표시에서 원문은 이미 첫 줄에 있으니 발음 줄에 같은
라틴을 적는 것은 정보가 0이고, 표시=정렬이어야 발음 음절 스팬(``pron_segments``)이 표시
발음과 일치해 라틴 줄에서도 가라오케 음절 채움이 동작한다.

## 구성

- ``tighten``: 관습형 → 조밀형. **이 함수만이 조밀 규칙을 안다.** 규칙 엔진 출력과 (나중에
  붙일 수 있는) LLM 음차가 같은 마감을 통과하도록 공개해 둔다.
- ``latin_word_to_hangul``: 낱말 1개. 못박은 표 → 글자 이름 → 규칙 엔진(+``tighten``).
- ``transliterate_latin``: 렌더된 발음 문자열의 라틴 구간만 치환 (``pron_style`` 진입점).

영어 정서법은 규칙만으로 완전히 풀리지 않는다. 그래서 **자주 나오는 낱말은 표로 못박고**
나머지는 규칙 엔진에 맡긴다. 규칙 엔진의 목표는 "옳은 영어 발음"이 아니라 위 실측이
말하는 두 가지다: (a) 한글로 나온다, (b) 음절 수가 가창과 맞는다. 실제로 위 표의
need·take·fine·keep·drip·give·blue·all·it·hey·loppi·want은 표 없이 규칙만으로 재현된다
(``tests/test_latin_hangul.py``가 그것을 못박는다).
"""
from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------------------
# 한글 조립/분해
# ---------------------------------------------------------------------------

_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_JONG = "ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"
_HANGUL_BASE = 0xAC00


def _compose(cho: str, jung: str, jong: str = "") -> str:
    jong_idx = _JONG.index(jong) + 1 if jong else 0
    return chr(_HANGUL_BASE + (_CHO.index(cho) * 21 + _JUNG.index(jung)) * 28 + jong_idx)


def _decompose(ch: str) -> tuple[str, str, str] | None:
    code = ord(ch) - _HANGUL_BASE
    if not 0 <= code <= 11171:
        return None
    return _CHO[code // 588], _JUNG[(code % 588) // 28], (_JONG[code % 28 - 1] if code % 28 else "")


# ---------------------------------------------------------------------------
# 조밀화 (tighten)
# ---------------------------------------------------------------------------

# 삽입 자음 음절(자음 + ㅡ, 받침 없음)의 초성 → 어말 받침.
# ㅌ→ㅅ / ㄷ→ㄷ는 실측 표기를 글자 출처대로 보존한 것이다: t에서 온 것은 잇·올라잇,
# d에서 온 것은 닏·윋로 적혀 있었다(굿만 d에서 ㅅ으로 적혀 표로 못박는다). 셋 다 음가는
# [t̚]라 정렬에는 차이가 없다.
# ㅅ·ㅈ·ㅊ가 여기 없는 것은 의도다 — 치찰음 음절은 접지 않는다(_SUNG_ALONE 참조).
_BARE_CODA = {
    "ㄱ": "ㄱ", "ㄴ": "ㄴ", "ㄷ": "ㄷ", "ㄹ": "ㄹ", "ㅁ": "ㅁ", "ㅂ": "ㅂ",
    "ㅋ": "ㄱ", "ㅌ": "ㅅ", "ㅍ": "ㅂ",
    "ㅎ": "",  # h는 받침이 없다 — 버린다
}

# 접지도 버리지도 **않는** 초성 — 치찰음(스·즈·츠)은 홀로도 부르는 진짜 음절이다.
# 실측의 it's가 잇이 아니라 잇츠(2음절)인 것이 근거이고, 한때 「붙일 곳이 있으면 접는다」로
# 두었다가 kiss→킷, ice→아잇, miss→밋이 나오는 것을 사용자 청취로 잡았다(vg6pnvn1u10,
# 2026-07-28 — 가수는 키스로 부른다). 관습 표기(키스·아이스·미스)도 스를 살리는 쪽이고,
# 표로 못박았던 yes→예스도 이제 규칙만으로 같은 값이 나온다. 반대로 파열음은 앞 음절이
# 받침으로 차 있으면 아예 들리지 않는다(want 원트 → 원, approved 어프루브드 → 어프룹).
_SUNG_ALONE = frozenset("ㅅㅈㅊ")


def _is_bare(syl: str) -> bool:
    """자음 + ㅡ, 받침 없음 — 한글 표기가 자음군을 적으려고 **끼워 넣은** 음절인가.

    ㅡ만 보는 것은 의도된 보수성이다. 시/지/치(ㅅ·ㅈ·ㅊ + ㅣ)도 삽입 음절일 수 있지만
    (위시·브릿지) 진짜 가창 음절(바지·가지)과 구별할 수 없으므로 건드리지 않는다.
    """
    d = _decompose(syl)
    return d is not None and d[1] == "ㅡ" and not d[2] and d[0] != "ㅇ"


def tighten(word: str) -> str:
    """관습 한글 음차의 **어말** 삽입 자음 음절을 받침으로 접거나 버린다.

    어말만 손대는 이유: 실측이 이긴 형태가 정확히 그 모양이다(어프루브드→어프룹,
    리보크→리복, 니드→닏, 원트→원, 테이크→테익, 위드→윋). 낱말 가운데의 ㅡ 음절
    (스트롱의 스·트)은 한국어 화자가 실제로 부르므로 남긴다.

    - 앞 음절에 받침이 없으면 받침으로 접는다: 니드 → 닏.
    - 앞 음절에 이미 받침이 있으면 **버린다**: 원트 → 원 (t는 노래에 없다).
    - 앞 음절도 삽입 음절이면 붙일 곳이 없으므로 버린다: 어프루브드 → (드 버림) →
      어프루브 → 어프룹.
    - 단 치찰음(스·즈·츠)은 붙일 곳이 없어도 남긴다: 킵스, 텍스 (실측의 잇츠와 같은 꼴).

    일본어 독음에는 **절대 쓰지 마라.** します→시마스, デス→데스처럼 す/つ에서 온 스·츠는
    끼워 넣은 자음이 아니라 진짜 모라다(시맛/뎃이 되면 곡 전체가 망가진다). 그래서 이
    함수는 라틴 낱말 하나에만 적용되고, 호출부는 ``latin_word_to_hangul`` 뿐이다.
    """
    syls = list(word)
    while len(syls) >= 2 and _is_bare(syls[-1]):
        onset = _decompose(syls[-1])[0]
        # 치찰음은 접지도 버리지도 않는다 — 근거는 _SUNG_ALONE 주석 (kiss→킷 사고)
        if onset in _SUNG_ALONE:
            break
        prev = _decompose(syls[-2])
        if prev is None:
            break
        coda = _BARE_CODA.get(onset)
        if coda and not prev[2] and not _is_bare(syls[-2]):
            syls[-2] = _compose(prev[0], prev[1], coda)
            syls.pop()
            continue
        syls.pop()
    return "".join(syls)


# ---------------------------------------------------------------------------
# 못박은 낱말 (표)
# ---------------------------------------------------------------------------

# 실측에서 나온 낱말 중 **규칙 엔진이 재현하지 못하는 것들**. 규칙이 이미 맞히는
# need·take·fine·keep·drip·give·blue·all·it·hey·loppi·so·numb·in은 일부러 비워 뒀다 —
# 표에 넣으면 규칙이 나중에 망가져도 표가 가려서 모른다(테스트가 규칙 쪽을 못박는다).
# 값은 **그때 이긴 표기 그대로**이며 ``tighten``을 다시 통과시키지 않는다 — it's가
# 잇츠(2음절)로 남아야 하는 것이 그 예다(조밀 규칙을 한 번 더 걸면 잇이 되는데, 사람
# 자막도 오디오도 2음절로 부른다).
_MEASURED_WORDS = {
    "approved": "어프룹",  # -0.562 (원문 -1.254 / 어프루브드 -0.848)
    "revoke": "리복",  # -0.743 (원문 -3.083 / 리보크 -1.383)
    "with": "윋",  # -1.047 (위드 -1.128)
    "want": "원",  # -0.522 (원트 -0.671). 규칙은 완 — 음절 수는 같고 모음만 다르다
    "good": "굿",  # d를 ㅅ으로 적은 실측값 — 규칙(굳)보다 이쪽을 쓴다
    "you": "유",
    "me": "미",
    "it's": "잇츠",
    "i'm": "아임",
    "i'll": "아일",
    "yeah": "예",
    "ok": "오케이",
    "color": "컬러",
    "cover": "커버",
    "cue": "큐",
    "give": "깁",  # -ive를 예외로 빼지 않기로 했으므로(drive·five가 길다) 표에서 잡는다
    "alright": "올라잇",
    "mm": "음",
}

# 실측 목록에는 없지만 가사에 흔하고 한글 표기가 관습으로 굳은 낱말들. 규칙 엔진이 크게
# 틀리는 것만 넣었다(규칙이 이미 맞히는 need·take·all·it 등은 일부러 비워 뒀다 — 표를
# 늘리면 규칙의 회귀를 표가 가려 버린다). 발음이 갈리는 낱말은 **넣지 않고** 규칙에 맡긴다.
_CONVENTIONAL_WORDS = {
    "a": "어",  # 관사. 글자 이름 에이는 부르지 않는 음절을 1개 더 만든다
    "the": "더",
    "be": "비", "he": "히", "we": "위", "she": "시",
    "they": "데이", "this": "디스", "that": "댓", "them": "뎀", "then": "덴",
    "there": "데어", "these": "디즈", "those": "도즈",
    "no": "노", "oh": "오", "go": "고",
    "now": "나우", "how": "하우",  # 어말 ow의 예외 — 규칙은 /oʊ/(노·쇼)로 읽는다
    "young": "영",
    "to": "투", "do": "두", "who": "후", "too": "투",
    # to·do 복합어. 규칙 엔진은 어말 -o를 항상 장모음 오로 읽는다(그게 맞다 —
    # photo 포토, auto 오토, zero 제로, piano 피애노, potato 포태토는 모두 실제로 /oʊ/다).
    # 그런데 into·onto·undo·redo·outdo는 "in/on/un/re/out"에 함수어 to·do가 그대로
    # 붙은 낱말이라 그 to·do처럼 /uː/로 읽는다 — 철자로는 photo류와 구별할 수 없는
    # 닫힌 예외라서 규칙이 아니라 표에서 잡는다(사용자가 실제 곡에서 "fade into blue"의
    # into가 인토가 아니라 인투로 들린다고 확인했다). undo·redo·outdo는 같은 원인이라
    # 함께 넣지만 실제 곡에서 청취 확인은 하지 못했다.
    "into": "인투", "onto": "온투", "undo": "언두", "redo": "리두", "outdo": "아웃두",
    # ou 자소가 낱말마다 다른 홑모음/이중모음으로 갈린다(사전 근거 + 스크래치패드의
    # JP-mixed 라인·LRCLIB 가사에서 실제 등장을 확인). 철자만으로는 구별 불가능해
    # 표에서 잡는다 — 규칙(_VOWEL_GRAPHS의 ou→ㅏㅜ)은 now·how·out·found·ground·
    # around·sound·about·without·loud·thousand·our처럼 실제 /aʊ/인 낱말에는 이미
    # 맞으므로 그쪽은 손대지 않는다.
    #
    # /ʊər/·/ɔːr/ — tour·your(·you're는 동음이의). 규칙은 ou를 ㅏㅜ로 먼저 먹어버려
    # 후치 r 흡수(_R_VOWELS)까지 가지 못한다(타우가 나온다). 실측: JP-mixed
    # 코퍼스의 "Enjoy tour and travel"·"make your smile". 음절 수는 there(데어)·
    # our(아워)와 같은 관습(어말 er/or 계열을 두 음절로 적는다)을 따랐다 — 이 음절
    # 수는 오디오로 측정하지 않았고 기존 표기와의 일관성에만 근거한다.
    "tour": "투어", "your": "유어", "you're": "유어",
    "you'll": "율", "you've": "윱",
    # /oʊ/ — soul. 규칙은 ㅏㅜ(사울)로 읽는데 실제로는 노·고와 같은 장모음 오다.
    # LRCLIB 가사("Reggae, Rap, Pop and Soul")에서 확인.
    "soul": "솔",
    # /ʌ/ — country·touching·enough·tough. 이미 표에 있는 young과 같은 부류다.
    # LRCLIB 가사("tractor pulls, country fairs" / "we'll be touching" /
    # "This is not enough" / "Standing tough under stars")에서 확인. touch(원형)는
    # touching만 코퍼스에 있고 원형 자체는 0회라 넣지 않았다 — 나오면 그때 넣는다.
    "country": "컨트리", "touching": "터칭",
    "enough": "이넙", "tough": "텁",
    # /ʊ/(book과 같은 단모음) — should·could와 그 축약형. 규칙은 ㅏㅜ로 읽는데
    # 실제로는 good(굿, 이미 표에 있음)의 모음과 같다. LRCLIB 가사("we should back
    # it up" / "could only bring the rain" / "I couldn't find a day" / "I wouldn't
    # let you haunt")에서 확인.
    "should": "슏", "could": "쿧", "couldn't": "쿠든", "wouldn't": "우든",
    # 여기부터는 다른 원인이다 — -ous/-ious 접미사(nervous·ambitious·glorious)는
    # ou가 모음이 아니라 무강세 접미사 /əs/의 일부라 위의 ou 규칙과는 무관하다. ou
    # 버그를 찾다가 함께 발견했다. nervous는 JP-mixed 코퍼스("止まぬNervousに")에서,
    # ambitious·glorious는 실제 싱크 데이터·LRCLIB 가사에서 확인했다.
    "nervous": "너버스", "ambitious": "앰비셔스", "glorious": "글로리어스",
    "one": "원", "two": "투", "once": "원스",
    "love": "럽",  # 러브 → 조밀
    "yes": "예스",
    "don't": "돈", "can't": "캔", "won't": "원",
    "come": "컴", "some": "섬", "done": "던", "gone": "곤",
    # are — 아로 적었다가 사용자 청취 교정(vg6pnvn1u10 「Are you ready」, 2026-07-28):
    # 가창은 r가 들리는 얼이다. 음절 수는 동일해 정렬에는 중립이고 표시가 맞아진다.
    "are": "얼", "our": "아워", "were": "워",
    # 규칙 엔진이 철자 모음을 그대로 읽어 틀리는 상용어 — 사용자 청취로 잡았다(위와 같은 곡).
    # god은 o를 ㅗ로(곧), ready는 ea를 이로(리디) 읽었다. 둘 다 닫힌 예외라 표에서 잡는다.
    "god": "갓", "ready": "레디",
    "eye": "아이", "eyes": "아이즈",
    "i'd": "아읻", "i've": "아입",  # 실측 I'm 아임 · I'll 아일과 같은 꼴(아이 + 조밀 종성)
    # heart·hearth만 ear를 /ɑːr/로 읽는다(닫힌 예외 2개). 관습형 하트를 조밀화한 값이다.
    "heart": "핫", "hearts": "핫츠",
    # 일본어 가사에 섞인 라틴이 **영어가 아니라 가타카나로 불리는** 부류. VOCALOID는
    # ボーカロイド(보오카로이도)로 부르며 규칙의 영어 읽기(보캘로읻)와 음절 수가 4:6으로
    # 어긋난다. 로컬 코퍼스에서 33회/4곡으로 가장 흔한 대문자 토큰이라 표에 넣었다.
    # 이 부류를 일반 규칙으로 가릴 방법은 없다(같은 철자가 곡에 따라 영어로도 불린다) —
    # 사람 표기를 확인한 것만 넣는다. 값은 kana_hangul.kana_to_hangul('ボーカロイド')다.
    "vocaloid": "보오카로이도",
}

_WORDS = {**_CONVENTIONAL_WORDS, **_MEASURED_WORDS}

# 모음이 있어서 자모 구성만으로는 낱말과 구별되지 않는 두문자어. 이 표가 필요한 이유와
# 다른 판별 축을 쓰지 않은 이유:
#
# · **길이 축을 쓰지 않았다**(2~3자 대문자 → 글자 이름). 로컬 코퍼스 4662줄의 전부 대문자
#   토큰을 세어 보면 낱말이 압도적이다: ``VOCALOID`` 33회(4곡) · ``BOY`` 4회 · ``VOX`` 2회
#   대 두문자어 ``NG`` 2회 · ``AC`` 2회. ``BOY``(3자)와 ``AT``(2자)가 직접 반례다 —
#   길이로 자르면 「逃げる気か BOY」가 「비오와이」가 된다. 지금 고치는 오류(ATM→앳)를
#   반대 방향으로 다시 만드는 것이다.
# · **음운 가능성 축도 쓰지 않았다.** ``ATM``은 /tm/ 종성이 영어에 불법이라 잡히지만
#   ``VIP``는 CVC로 완벽히 합법적인 낱말 꼴이다(rip·tip과 같다). 즉 어떤 자모 검사로도
#   VIP를 낱말과 가를 수 없어 결국 목록이 필요하고, 그러면 음운 표의 비용을 회수하지 못한다.
# · 그래서 **명시 목록**이다. 대가는 분명하다: **목록 밖의 두문자어는 여전히 낱말로 읽힌다.**
#   대문자 원문일 때만 적용된다(소문자 id·am은 낱말이다). 발음이 갈리는 것은 넣지 않았다 —
#   ``AI``는 기술(에이아이)일 수도 「愛」의 로마자(아이)일 수도 있어 규칙에 맡겼다.
_ACRONYMS = frozenset({"atm", "vip", "id", "usb", "dvd", "dna", "iq"})

# 알파벳 이름. 낱글자 나열은 낱말이 아니라 글자로 읽는다 — 실측(H7PR6K7xff0)의
# ``L-O-P-P-I'm``이 사람 자막에서 「엘-오-피-피-아임」이고, ``NG!``가 「엔지이」다.
_LETTER_NAMES = {
    "a": "에이", "b": "비", "c": "시", "d": "디", "e": "이", "f": "에프", "g": "지",
    "h": "에이치", "i": "아이", "j": "제이", "k": "케이", "l": "엘", "m": "엠",
    "n": "엔", "o": "오", "p": "피", "q": "큐", "r": "아르", "s": "에스", "t": "티",
    "u": "유", "v": "브이", "w": "더블유", "x": "엑스", "y": "와이", "z": "지",
}

# ---------------------------------------------------------------------------
# 규칙 엔진 — 영어 정서법 → 관습 한글
# ---------------------------------------------------------------------------

_VOWEL_LETTERS = frozenset("aeiouy")

# 모음 자소 → 중성 열(2개면 2음절로 퍼진다: take → 테 + 익). 긴 것부터 맞춘다.
_VOWEL_GRAPHS: tuple[tuple[str, str], ...] = (
    ("eigh", "ㅔㅣ"), ("igh", "ㅏㅣ"),
    # ``aw``는 law 로·saw 소지만 **어두에서는 활음일 수 있다** — ``_initial_aw``가 먼저 가른다.
    ("ai", "ㅔㅣ"), ("ay", "ㅔㅣ"), ("au", "ㅗ"), ("aw", "ㅗ"),
    # ``ear`` + 자음은 /ɜːr/다 (earth 엇, learn 런, heard 헏, search 서치). ``ea``가 먼저
    # 먹으면 히/린이 되어 모음이 틀린다. 어말 ``ear``(hear·near·dear·year)는 /ɪər/이라
    # 아래 _FINAL_VOWEL_GRAPHS가 따로 본다. /ɑːr/로 읽는 heart·hearth는 **낱말이 2개뿐인
    # 닫힌 예외**라 규칙이 아니라 표에서 잡는다.
    ("ear", "ㅓ"),
    ("ea", "ㅣ"), ("ee", "ㅣ"), ("ei", "ㅔㅣ"), ("ey", "ㅔㅣ"), ("ew", "ㅠ"),
    ("ie", "ㅣ"),  # 어말이 아니면 /iː/ (believe 벨립, field 필드) — 어말은 아래 표가 본다
    ("oa", "ㅗ"), ("oo", "ㅜ"), ("ou", "ㅏㅜ"), ("ow", "ㅏㅜ"),
    ("oi", "ㅗㅣ"), ("oy", "ㅗㅣ"),
    ("ue", "ㅜ"), ("ui", "ㅜ"),
)

# 어말에서만 다르게 읽는 자소 → (단음절일 때, 다음절일 때). 어말 ow는 대개 /oʊ/다
# (know·show·low·slow·snow·grow·blow) — /aʊ/인 now·how는 표에 못박아 뒀다.
_FINAL_VOWEL_GRAPHS = {
    "y": ("ㅏㅣ", "ㅣ"), "ey": ("ㅔㅣ", "ㅣ"), "ie": ("ㅏㅣ", "ㅣ"), "ow": ("ㅗ", "ㅗ"),
    "ear": ("ㅣ", "ㅣ"),  # 어말 ear는 /ɪər/ (hear 히, year 이) — 자음이 뒤따르면 /ɜːr/다
}

# 후치 r은 모음에 흡수된다 (car 카, her 허, for 포) — 뒤에 모음이 없을 때만 적용한다.
_R_VOWELS: tuple[tuple[str, str], ...] = (
    ("ar", "ㅏ"), ("er", "ㅓ"), ("ir", "ㅓ"), ("or", "ㅗ"), ("ur", "ㅓ"), ("yr", "ㅓ"),
)

_SHORT_VOWELS = {"a": "ㅐ", "e": "ㅔ", "i": "ㅣ", "o": "ㅗ", "u": "ㅓ", "y": "ㅣ"}
# 어말 묵음 e가 앞 모음을 늘린다 (take 테익, fine 파인, revoke 리복). o는 오우가 아니라
# 오다 — 실측이 이긴 리복이 그렇다.
_LONG_VOWELS = {"a": "ㅔㅣ", "e": "ㅣ", "i": "ㅏㅣ", "o": "ㅗ", "u": "ㅠ", "y": "ㅏㅣ"}

# 자음 자소 → (초성, 받침 또는 None, 홀로 설 때의 중성, 뒤 모음에 얹는 활음)
_CONS: dict[str, tuple[str, str | None, str, str | None]] = {
    "p": ("ㅍ", "ㅂ", "ㅡ", None),
    "b": ("ㅂ", "ㅂ", "ㅡ", None),
    "t": ("ㅌ", "ㅅ", "ㅡ", None),
    "d": ("ㄷ", "ㄷ", "ㅡ", None),
    "k": ("ㅋ", "ㄱ", "ㅡ", None),
    "g": ("ㄱ", "ㄱ", "ㅡ", None),
    "f": ("ㅍ", "ㅂ", "ㅡ", None),
    "v": ("ㅂ", "ㅂ", "ㅡ", None),
    "s": ("ㅅ", "ㅅ", "ㅡ", None),
    "z": ("ㅈ", "ㅅ", "ㅡ", None),
    "th": ("ㅅ", "ㅅ", "ㅡ", None),
    "dh": ("ㄷ", "ㄷ", "ㅡ", None),  # 모음 사이의 유성 th (together 토게더, mother 마더)
    "m": ("ㅁ", "ㅁ", "ㅡ", None),
    "n": ("ㄴ", "ㄴ", "ㅡ", None),
    "ng": ("ㅇ", "ㅇ", "ㅡ", None),
    "l": ("ㄹ", "ㄹ", "ㅡ", None),
    "r": ("ㄹ", None, "ㅡ", None),  # 받침이 없다 — 후치 r은 _R_VOWELS가 먹는다
    "h": ("ㅎ", None, "ㅡ", None),
    "sh": ("ㅅ", None, "ㅣ", "y"),  # 샤·셰·쇼·슈, 홀로면 시(위시)
    "ch": ("ㅊ", None, "ㅣ", None),
    "j": ("ㅈ", None, "ㅣ", None),
    "kw": ("ㅋ", None, "ㅡ", "w"),  # qu — 활음을 초성과 한 음절에 넣는다 (quick 퀵)
}

# 자음 2글자 자소. 묵음/치환 규칙은 스캐너에서 위치를 보고 결정한다.
_CONS_DIGRAPHS = frozenset({"th", "sh", "ch", "ph", "gh", "ck", "ng"})

# 앞 자음이 받침이 되지 못하는 뒤 자음(유음·비음) — 한글은 여기서 ㅡ를 쓴다
# (approved 어프…, blue 블…, drip 드…, cream 크…). 뒤 자음이 장애음이면 받침으로 닫는다
# (backdrop 백드롭, text 텍스…).
_FORCES_BARE = frozenset({"l", "r", "m", "n", "ng"})


def _prepare(w: str) -> tuple[set[int], int | None]:
    """(묵음 위치 집합, 장모음화할 모음 위치).

    어말 e는 대개 묵음이고(take/fine/revoke/give), 그 e가 「모음 + 자음 1개 + e」 꼴이면
    앞 모음을 늘린다(magic e). ``-ed``/``-es``의 e도 같다 — approved의 e는 안 부른다.
    """
    silent: set[int] = set()
    ei: int | None = None
    if w.endswith("e") and len(w) >= 3:
        ei = len(w) - 1
    elif len(w) >= 4 and w.endswith(("ed", "es")):
        prev = w[-3]
        # -ed는 t/d 뒤에서, -es는 치찰음 뒤에서 실제로 /ɪ/를 부른다 (needed, wishes)
        if w.endswith("ed") and prev not in "td":
            ei = len(w) - 2
        elif w.endswith("es") and prev not in "szxh":
            ei = len(w) - 2
    if ei is None or w[ei - 1] in _VOWEL_LETTERS:
        return silent, None
    if not any(c in _VOWEL_LETTERS for c in w[: ei - 1]):
        # 그 e가 낱말의 유일한 모음이면 묵음이 아니다 (me, the)
        return silent, None
    silent.add(ei)
    clen = 2 if w[ei - 2 : ei] in _CONS_DIGRAPHS else 1
    vi = ei - clen - 1
    if vi < 0 or w[vi] not in "aeiou" or (vi > 0 and w[vi - 1] in _VOWEL_LETTERS):
        return silent, None
    if w[vi] == "a" and w[vi + 1] == "w":
        # 「자음 1개」 자리에 온 이 w는 자음이 아니라 ``aw`` 자소의 일부다. 늘리면 ㅔㅣ가 되어
        # **늘** 틀린다: sawed 세읻 · clawed 클레읻 · flawed 플레읻 · gnawed 그네읻.
        # e는 묵음으로 두고 늘리지만 않으면 aw 자소가 그대로 ㅗ를 준다(솓·클롣·플롣·그놋).
        # ``ow``는 같은 꼴이지만 건드리지 않는다 — 장모음 o가 마침 ㅗ여서 지금 값이 이미
        # 맞고(showed 숃 · bowed 볻 · allowed 올롣), 자소 ow(ㅏㅜ)로 읽으면 오히려 틀린다.
        return silent, None
    # ``-ive``를 예외로 빼지 않는다: give·live는 짧지만 drive·five·alive·survive는 길고,
    # 가사에 나오는 빈도가 비슷하다. give는 실측값(깁)으로 표에 못박혀 있고, live는 영어
    # 자체가 갈리는 낱말이라(형용사 /laɪv/ · 동사 /lɪv/) 표에 넣지 않고 규칙에 맡긴다.
    return silent, vi


def _graphemes(w: str, silent: set[int], magic: int | None) -> list[tuple[str, str]]:
    """낱말을 자소 단위로 쪼갠다. ("V", 중성열) / ("C", 자음키) / ("G", 활음)."""
    units: list[tuple[str, str]] = []
    i, n = 0, len(w)
    while i < n:
        if i in silent:
            i += 1
            continue
        ch = w[i]

        # --- 모음 ---
        if ch in _VOWEL_LETTERS and not (ch == "y" and _starts_syllable(w, i)):
            if i == 0 and w[:2] == "aw":
                # 아래 magic e 검사보다 먼저 본다 — awe·awed·awes에서는 magic이 이 a를
                # 잡아 ㅔㅣ로 늘려 버린다(에이·에읻).
                aw = _initial_aw(w)
                if aw is not None:
                    units.append(("V", aw[0]))
                    i += aw[1]
                    continue
            if i == magic:
                units.append(("V", _LONG_VOWELS[ch]))
                i += 1
                continue
            final = next(
                (
                    g
                    for g in _FINAL_VOWEL_GRAPHS
                    # 복수·3인칭의 s는 어말 판정을 막지 않는다 (years·cries·knows)
                    if w.startswith(g, i)
                    and (i + len(g) == n or (i + len(g) + 1 == n and w.endswith("s")))
                ),
                None,
            )
            if final:
                # 어말 y·ey·ie는 낱말이 단음절일 때만 길다 — my 마이·hey 헤이·die 다이 대
                # baby 배비·honey 호니·movie 모비. 영어에서 이 갈림은 음절 수를 따라간다.
                long_here = not any(c in _VOWEL_LETTERS for c in w[:i])
                units.append(("V", _FINAL_VOWEL_GRAPHS[final][0 if long_here else 1]))
                i += len(final)
                continue
            hit = next((g for g in _VOWEL_GRAPHS if w.startswith(g[0], i)), None)
            if hit and i + len(hit[0]) - 1 not in silent:
                units.append(("V", hit[1]))
                i += len(hit[0])
                continue
            rhit = next((g for g in _R_VOWELS if w.startswith(g[0], i)), None)
            if rhit and (i + 2 >= n or w[i + 2] not in _VOWEL_LETTERS):
                units.append(("V", rhit[1]))
                i += 2
                continue
            if ch == "a" and w[i + 1 : i + 2] == "l" and w[i + 2 : i + 3] not in ("e", "i", "o", "u"):
                # 어두운 l 앞의 a는 낮아진다 — all 올, always 올웨잇, alright 올라잇.
                # l 뒤에 모음이 오면 어둡지 않다(alone 애론) — 그때는 손대지 않는다.
                units.append(("V", "ㅗ"))
                i += 1
                continue
            units.append(("V", _SHORT_VOWELS[ch]))
            i += 1
            continue

        # --- 자음 ---
        if w.startswith("tion", i):
            # -tion은 션이다 (nation 네이션) — t+i를 따로 읽으면 티온이 된다
            units += [("C", "sh"), ("V", "ㅓ"), ("C", "n")]
            i += 4
            continue
        key, ln = _read_consonant(w, i, n, silent)
        i += ln
        if key is not None:
            units.append(("G" if key in ("w", "y") else "C", key))
    return units


def _starts_syllable(w: str, i: int) -> bool:
    """이 위치의 y가 활음(자음)인가 — 어두이거나 뒤에 모음이 온다 (yes, yeah)."""
    return i == 0 or w[i + 1 : i + 2] in ("a", "e", "i", "o", "u")


# 어두 ``aw``에서 ``w``는 자소의 일부가 아니라 **다음 음절의 활음**일 수 있다. 그때 ``a``는
# 접두사 ``a-``이고, ``aw``를 한 자소로 먹으면 w가 사라져 음절이 하나 준다(away 오에이).
# 갈림은 뒤따르는 글자가 정한다 — ``w``(또는 /w/를 적는 ``wh``) 뒤에 모음이 오면 자음이다.
#
#   away 어웨이 · awake 어웨익 · aware 어웨이 · award 어왇 · await 어웨잇 · awash 어와시
#   awoke 어웍 · awoken 어워켄 · aweigh 어웨이 · awhile 어와일   ← 접두사 a- + 활음 w
#   awe 오 · awed 옫 · awesome 오솜 · awestruck 옷트럭            ← /ɔː/ (awe가 어근)
#   awful 오펄 · awkward 옥왇 · awning 오닝 · awl 올              ← 자음이 뒤따름, 규칙 무관
#
# ``e``만 예외인 이유: ``awe``(경외) 자체가 /ɔː/의 표기라 어두 ``awe`` + 자음은 그 어근이다
# (awe·awed·awes·awesome·awestruck). 반대로 ``awe`` 뒤에 모음이 오면 어근 ``awe``일 수
# 없어 다시 활음이다 — aweigh(a- + weigh, /əˈweɪ/)가 그 낱말이고, 이 갈림이 있어야 어근
# 쪽과 함께 맞는다. 발음은 Wiktionary로 확인했다. **오디오로 측정한 값은 없다.**
#
# 이 규칙이 고치는 것은 **w가 사라지는 것**뿐이다. 모음이 더 틀리는 것은 남는다 — award는
# /əˈwɔːrd/라 어웓이어야 하는데 어왇이 나온다(``ar``→ㅏ. war 와도 같은 값이니 w 뒤 ar의
# 별개 결함이다). aware(/əˈwɛər/ 어웨어 → 어웨이)는 어말 -are를 ㅔㅣ로 읽는 결함이 원인이고
# care 케이도 같다. 둘 다 음절 수는 맞으므로 이 파일의 목표(음절 수)에는 어긋나지 않는다.
#
# 어두로 제한한 이유는 반례가 어두 밖에만 있기 때문이다: **줄기가 aw로 끝나고 모음 접미사가
# 붙은** 낱말은 모두 /ɔː/다(drawing 드로잉 · sawed 솓 · clawed 클롣 · drawer 드로어 ·
# lawyer 로여). 대가는 접두사가 더 앞에 붙은 낱말(unaware 어노에이)과 로마자(kawaii
# 코에이이)를 놓치는 것인데, 둘 다 지금도 틀린 값이라 회귀는 아니다.
# 규칙이 못 잡는 것도 적어 둔다: awry(a- + wry)는 ``wr``의 묵음 w 규칙이 어두에서만 돌아
# 오리로 남는다. awing은 사전이 /əˈwɪŋ/(a- + wing)과 /ˈɔː.ɪŋ/(awe + -ing) 둘로 갈리는
# 낱말이라 규칙이 주는 어윙은 그중 하나다 — 갈리는 낱말은 표에 넣지 않는 이 파일의 방침대로 둔다.
def _initial_aw(w: str) -> tuple[str, int] | None:
    """어두 ``aw`` → (중성열, 소비 글자 수). None이면 ``aw`` 자소를 그대로 쓴다."""
    rest = w[2:]
    if rest[:1] == "e" and rest[1:2] not in _VOWEL_LETTERS:
        return "ㅗ", 3  # awe·awesome — aw가 /ɔː/이고 그 e는 묵음이다
    tail = rest[1:] if rest[:1] == "h" else rest  # ``wh``는 /w/ 한 소리다 (awhile)
    if tail[:1] in _VOWEL_LETTERS:
        return "ㅓ", 1  # 무강세 접두사 a-는 슈와다 (관사 a 어 · approved 어프룹과 같다)
    return None


def _read_consonant(w: str, i: int, n: int, silent: set[int]) -> tuple[str | None, int]:
    """위치 i의 자음 자소를 읽어 (키 또는 None=묵음, 소비 글자 수)를 준다."""
    two = w[i : i + 2]
    if two == "qu":
        return "kw", 2
    if two in ("ph",):
        return "f", 2
    if two == "ck":
        return "k", 2
    if two == "gh":
        # 모음 뒤 gh는 묵음(though) — igh는 이미 모음 자소로 먹혔다
        return (None, 2) if i > 0 and w[i - 1] in _VOWEL_LETTERS else ("g", 2)
    if two == "kn" and i == 0:
        return "n", 2
    if two == "wr" and i == 0:
        return "r", 2
    if two == "wh":
        return "w", 2
    if two == "mb" and i + 2 == n:
        return "m", 2  # numb 넘, climb 클라임
    if w.startswith("tch", i):
        return "ch", 3  # catch 캐치 (t가 따로 받침이 되면 캣치가 된다)
    if two == "ng" and (i + 2 >= n or w[i + 2] not in _VOWEL_LETTERS):
        # 뒤에 모음이 오는 ng는 /ŋ/이 아니다 (angel·danger) — n과 g를 따로 읽는다
        return "ng", 2
    if two == "th":
        voiced = i > 0 and w[i - 1] in _VOWEL_LETTERS and w[i + 2 : i + 3] in ("a", "e", "i", "o", "u")
        return ("dh" if voiced else "th"), 2
    if two in ("sh", "ch"):
        return two, 2
    ch = w[i]
    if ch == "r" and i > 0 and w[i - 1] in _VOWEL_LETTERS:
        # 후치 r은 모음에 흡수된다 (heart 힛, fire 파이) — ar/er/or 등은 이미 모음 자소가
        # 먹었고, 여기 오는 것은 그 표에 없는 조합뿐이다. 르로 적으면 없는 음절이 생긴다.
        j = i
        while j < n and w[j] == "r":
            j += 1
        end = j
        while end < n and end in silent:  # 묵음 e는 모음이 아니다 (fire 파이, more 모)
            end += 1
        if end >= n or w[end] not in _VOWEL_LETTERS:
            return None, j - i
    if ch == w[i + 1 : i + 2]:
        return _single_consonant(w, i), 2  # 겹자음은 하나로 (loppi 로피, all 올)
    return _single_consonant(w, i), 1


def _single_consonant(w: str, i: int) -> str | None:
    ch = w[i]
    if ch == "c":
        return "s" if w[i + 1 : i + 2] in ("e", "i", "y") else "k"
    if ch == "x":
        return "x"
    if ch in ("w", "y"):
        return ch
    return ch if ch in _CONS else None


_W_GLIDE = {
    "ㅏ": "ㅘ", "ㅐ": "ㅘ", "ㅓ": "ㅝ", "ㅔ": "ㅞ", "ㅗ": "ㅝ",
    "ㅜ": "ㅜ", "ㅡ": "ㅜ", "ㅣ": "ㅟ", "ㅠ": "ㅠ",
}
_Y_GLIDE = {
    "ㅏ": "ㅑ", "ㅐ": "ㅒ", "ㅓ": "ㅕ", "ㅔ": "ㅖ", "ㅗ": "ㅛ",
    "ㅜ": "ㅠ", "ㅡ": "ㅣ", "ㅣ": "ㅣ",
}


def _glide(kind: str | None, jungs: str) -> str:
    if not kind:
        return jungs
    table = _W_GLIDE if kind == "w" else _Y_GLIDE
    return table.get(jungs[0], jungs[0]) + jungs[1:]


def _push_vowel(syls: list[list], jungs: str, onset: str = "ㅇ") -> None:
    for k, jung in enumerate(jungs):
        syls.append([onset if k == 0 else "ㅇ", jung, "", False])


def _add_coda(syls: list[list], jong: str, *, on_bare: bool = False) -> bool:
    """직전 음절에 받침을 붙인다. 붙일 수 없으면 False.

    삽입 음절(ㅡ)에는 ㄹ만 붙인다 — 블루·플라워·클래스는 한글 표기지만 슷·븓은 아니다.
    """
    if not syls:
        return False
    last = syls[-1]
    if last[2] or (last[3] and not on_bare):
        return False
    last[2] = jong
    return True


def _assemble(units: list[tuple[str, str]], *, final_bare: bool = False) -> str:
    """``final_bare=True``는 **어말 자음을 받침으로 닫지 않고 개음절로** 둔다(take 테익→
    테이크, need 닏→니드) — 관습형에 가까운 «다 부르는» 표기로, 오디오 심판의 음절 증가
    후보 축 전용이다(latin_word_to_hangul의 tight=False 참조). 어말만 여는 이유: 사용자
    청취 사례(테이크·아윌 테이크 잇)가 전부 어말 접힘이 실제로 불린 경우였고, 낱말
    중간의 받침(원트의 ㄴ, 백드롭의 ㄱ)은 관습 표기도 받침으로 적는다."""
    syls: list[list] = []
    i, n = 0, len(units)
    while i < n:
        kind, val = units[i]
        nxt = units[i + 1] if i + 1 < n else None

        if kind == "V":
            _push_vowel(syls, val)
            i += 1
            continue

        if kind == "G":
            # 활음은 늘 새 음절을 연다 — swim 스윔 (앞 자음은 이미 스로 확정됐다)
            if nxt and nxt[0] == "V":
                _push_vowel(syls, _glide(val, nxt[1]))
                i += 2
            else:
                i += 1  # 뒤에 모음이 없는 w/y는 부르지 않는다
            continue

        if val == "x":
            # ks — 받침 ㄱ + ㅅ (text 텍스, mix 믹스)
            if not _add_coda(syls, "ㄱ"):
                syls.append(["ㅋ", "ㅡ", "", True])
            units = units[: i + 1] + [("C", "s")] + units[i + 1 :]
            n += 1
            i += 1
            continue

        onset, coda, bare_jung, cglide = _CONS[val]
        if nxt and nxt[0] == "V":
            if val == "l":
                # 모음 사이/자음 뒤의 l은 앞 음절에 ㄹ을 남긴다 — color 컬러, hello 헬로,
                # blue 블루 (어두 l은 앞 음절이 없어 그냥 초성이다: loppi 로피)
                _add_coda(syls, "ㄹ", on_bare=True)
            _push_vowel(syls, _glide(cglide, nxt[1]), onset)
            i += 2
            continue

        forced_bare = nxt is not None and nxt[0] == "C" and nxt[1] in _FORCES_BARE
        # 어말 치찰음(s·z·th)은 받침으로 닫지 않고 음절(스·즈)로 살린다 — 치찰음 릴리스는
        # 실제로 한 음절로 불린다(실측 it's→잇츠, 표의 eyes→아이즈와 같은 원리). 받침으로
        # 닫았더니 kiss→킷·ice→아잇·miss→밋이 나왔고 가수는 키스로 불렀다(사용자 청취
        # vg6pnvn1u10, 2026-07-28). 낱말 중간의 받침화(best→벳, text→텍스)는 그대로다.
        final_sibilant = nxt is None and onset in "ㅅㅈㅊ"
        # final_bare 모드: 어말 자음을 받침으로 닫지 않는다 (l은 예외 — 어말 l을 개음절
        # 르로 열면 관습에도 없는 표기가 된다: all 올, feel 필)
        final_open = final_bare and nxt is None and val != "l"
        if val == "l":
            if _add_coda(syls, "ㄹ", on_bare=True):
                i += 1
                continue
        elif (
            coda
            and not final_sibilant
            and not final_open
            and not (forced_bare and val not in _FORCES_BARE)
            and _add_coda(syls, coda)
        ):
            i += 1
            continue
        syls.append([onset, bare_jung, "", True])
        i += 1
    return "".join(_compose(s[0], s[1], s[2]) for s in syls)


def _rules(word: str, *, final_bare: bool = False) -> str:
    silent, magic = _prepare(word)
    return _assemble(_graphemes(word, silent, magic), final_bare=final_bare)


# ---------------------------------------------------------------------------
# 낱말 1개
# ---------------------------------------------------------------------------

_APOSTROPHES = "'’ʼ"


def _normalize(word: str) -> str:
    """악센트를 벗기고 소문자화, 아포스트로피를 ASCII로 통일, 렌더가 넣은 공백 제거."""
    decomposed = unicodedata.normalize("NFKD", word)
    kept = [
        "'" if ch in _APOSTROPHES else ch
        for ch in decomposed
        if not unicodedata.combining(ch) and not ch.isspace()
    ]
    return "".join(kept).lower()


def latin_word_to_hangul(word: str, *, tight: bool = True) -> str:
    """라틴 낱말 1개를 조밀 한글 음차로. 음차할 수 없으면 원문을 그대로 돌려준다.

    ``tight=False``면 규칙 엔진 출력을 조밀화(tighten) 없이 돌려준다 — 관습형에 가까운
    «음절을 다 부르는» 표기다(take 테익→테이크). 오디오 심판의 음절 증가 방향 후보
    전용이다: 조밀이 실측 기본값이지만 가수가 접힌 음절을 실제로 부르는 곡이 있다
    (사용자 청취 vg6pnvn1u10 「Take kiss me」 — 테이크로 부른다). 표·글자 이름 경로는
    tight와 무관하다(실측으로 못박은 값이라 느슨한 변형이 없다 — 후보도 안 생긴다)."""
    core = _normalize(word)
    letters = core.replace("'", "")
    if not letters:
        return word
    if set(letters) == {"w"}:
        # w/ww/www는 일본 넷 슬랭의 웃음 표기다 — 부르지 않는다. 사람 자막도 「信じらんねw」를
        # 「신지란네」로만 적는다(referee_truth 2줄). 더블유를 넣으면 없는 음절 3개가 생긴다.
        return ""
    pinned = _WORDS.get(core)
    if pinned is not None:
        return pinned
    if len(letters) == 1 or (
        word.isupper() and (core in _ACRONYMS or not (_VOWEL_LETTERS & set(letters)))
    ):
        # 낱글자 · 모음 없는 대문자 약어(NG·DJ·TV) · 목록에 있는 두문자어(ATM·VIP)
        # → 글자 이름으로 읽는다. 그 밖의 대문자는 낱말이다(BOY·VOCALOID·LOVE·STOP).
        return "".join(_LETTER_NAMES.get(c, c) for c in letters)
    if tight:
        out = _rules(letters)
        return tighten(out) if out else word
    out = _rules(letters, final_bare=True)
    return out or word


# ---------------------------------------------------------------------------
# 렌더된 발음 문자열의 라틴 구간 치환
# ---------------------------------------------------------------------------

# 라틴 낱말. 아포스트로피로 이어진 조각을 한 낱말로 본다 — 형태소 분석기가 it's를
# it / ' / s로 쪼개고 문절 렌더가 그 사이에 공백을 넣어 "it' s"가 되기 때문이다.
_LATIN_WORD_RE = re.compile(r"[A-Za-z]+(?:\s*[" + _APOSTROPHES + r"]\s*[A-Za-z]+)*")


# 숫자 뒤에서 뜻이 갈리는 낱말. mm은 「0.1mmの距離」에서 사람이 「레이텐 이치미리노」로
# 읽었고(ミリ), 「ひらひら mm mm」에서는 그냥 감탄사(음)다. 앞이 숫자인지로 가른다.
# 여기에 mm만 있는 이유: 사람 표기를 실제로 본 단위가 그것뿐이다. cm·kg 등을 지어 넣지 않았다.
_DIGIT_UNITS = {"mm": "미리"}


def transliterate_latin(text: str, *, tight: bool = True) -> str:
    """문자열의 라틴 낱말만 조밀 한글 음차로 바꾼다. 라틴이 없으면 원본 그대로.

    ``tight=False``는 낱말 단위로 ``latin_word_to_hangul``에 그대로 전달된다(근거는 그쪽).

    숫자 자체는 **손대지 않는다.** 1秒는 사람이 「이치뵤오」로 읽지만 우리는 「1뵤오」를 내고
    있고, 숫자를 일본어 수사로 읽는 것은 라틴 음차와 별개 문제(읽기 선택이 문맥·조수사에
    달렸다)라 여기서 흉내면 틀린 값을 박게 된다.
    """
    if not text:
        return text

    def replace(m: re.Match[str]) -> str:
        unit = _DIGIT_UNITS.get(m.group().lower())
        if unit is not None:
            head = text[: m.start()].rstrip()
            if head and head[-1].isdigit():
                return unit
        return latin_word_to_hangul(m.group(), tight=tight)

    return _LATIN_WORD_RE.sub(replace, text)
