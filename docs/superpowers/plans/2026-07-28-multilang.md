# 다국어(ko/en/ja) 사용자 지원 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 확장을 곡 언어 × 사용자 언어 전 매트릭스(ja·ko·en 곡 × ko/en/ja 사용자)로 확장한다 — 정렬 경로 무변경, 골든 스냅샷 관문, 실전 테스트까지.

**Architecture:** 검증된 정렬 파이프라인이 만드는 «타이밍 코어»는 그대로 두고, (1) 발음 렌더러 주입(가나 읽기 → 표기별 환전), (2) 번역 레이어 테이블 `(video_id, fingerprint, target_lang)`, (3) 소스 어댑터·확장 i18n·디버그 시각화를 그 위에 얹는다. 스펙: `docs/superpowers/specs/2026-07-28-multilang-design.md`.

**Tech Stack:** Python 3.11 + FastAPI + SQLAlchemy(SQLite) / TypeScript Chrome MV3 확장 / fugashi+UniDic / MMS CTC.

## Global Constraints

- 테스트: `cd C:/devat/everyric2 && .venv/Scripts/python.exe -m pytest tests/ -q` (PATH python 금지 — 136개 거짓 실패 전례).
- 확장 빌드: `cd everyric2-chrome && npm run build`.
- **정렬 경로 무변경**: `worker.py`의 `_align_with_pronunciation`·`engine.align(..., language="ko")`·coverage 게이트(≥0.9)·`map_pron_alignment_to_line`은 손대지 않는다.
- **romaji는 표시 전용** — CTC 정렬 입력에 절대 넣지 않는다(라틴 정렬 붕괴 실측).
- 서버 배포·재시작·원격 접속(100.76.4.47)·커밋은 **메인 에이전트만** 한다. 서브에이전트 금지.
- API 키·토큰 값 출력 금지. 서버 테스트는 `127.0.0.1`. 모든 /api가 X-API-Key 요구.
- `everyric2.db`, `"2026-07-10 17-17-20.png"` 커밋 금지. b2NTglk9tvI·BiQs 계열 재생성 금지.
- DB는 추가 전용(새 테이블·필드만). `lang` 파라미터 없는 요청의 응답은 기존과 필드 단위로 동일해야 한다(추가 필드만 허용).
- 커밋 메시지는 기존 스타일(한국어, `type(scope): 요지 — 근거`).

## 공유 계약 (전 태스크 공통 — 이름·타입은 여기 정의가 정본)

```
script 리터럴:      "hangul" | "romaji" | "kana"
seg["pron"]:        dict[script, str]                  # 표시 문자열
seg["pron_segs"]:   dict[script, list[dict]]           # [{"text","start","end","space"?}]
                    # 기존 seg["pronunciation"]/["pron_segments"]는 한글 값 유지(레거시 폴백)
TranslationLayer:   (video_id, fingerprint, target_lang) 유니크, lines=[{"text","translation"}]
lines_fingerprint(texts: list[str]) -> str             # md5 32hex, worker._normalize_line 기반
GET sync 응답 추가:  translation_lang: str | None       # lang 파라미터 준 요청에만 의미
POST /api/translate 추가 필드: persist: bool = False    # video_id와 함께 주면 레이어 저장
Job.target_lang:    str = "ko"                          # 생성 요청자의 번역 언어
line_meta_lang:     str = "ko"                          # 생성 요청 line_meta 번역의 언어
debug.heard_spans:  list[[str, float]]                  # 글자, 초 단위 시각
Settings 추가:      pronunciationScript: 'auto'|'hangul'|'romaji'|'kana' (기본 'auto')
                    uiLanguage: 'auto'|'ko'|'en'|'ja' (기본 'auto')
script 자동 결정:    translationLanguage ko→hangul, en→romaji, ja→kana, zh→hangul
```

---

### Task 1: 골든 스냅샷 — 한글 발음 경로 박제

**Files:**
- Create: `tests/test_pron_golden.py`

리팩터링 전에 현행 출력을 박제한다. 이 파일이 이후 태스크의 회귀 관문이다.

- [ ] **Step 1: 스냅샷 테스트 작성** — 실곡에서 나온 대표 라인들로 `wiki_pronunciation`과 `pronunciation_candidates`의 **현재 출력값을 하드코딩**한다. 먼저 아래 스크립트로 현재 값을 뽑고, 그 값을 assert에 그대로 적는다:

```python
# 값 추출 (스크래치): .venv/Scripts/python.exe -c "..."
from everyric2.text.pron_style import wiki_pronunciation, pronunciation_candidates
LINES = [
    "アルバイトはネクラモード", "フラッシュバック・蝉の声・二度とは帰らぬ君",
    "二人きりこの儘 愛し合えるさ―。", "背負った", "ずっと見 てたよ",
    "Take it easy なんて言葉じゃ", "Are you ready?", "縋って 縋って",
    "何かを攫う", "左から右へと", "止められない衝動",
]
for ln in LINES:
    print(repr(ln), repr(wiki_pronunciation(ln)), repr(pronunciation_candidates(ln)))
```

```python
# tests/test_pron_golden.py 형태 (기대값은 추출 결과로 채운다 — 빈 칸 금지)
import pytest
from everyric2.text.pron_style import pronunciation_candidates, wiki_pronunciation

GOLDEN = {
    "アルバイトはネクラモード": "아루바이토와 네쿠라 모오도",  # ← 실제 추출값으로 교체
    # ... LINES 전체
}

@pytest.mark.parametrize("text,expected", sorted(GOLDEN.items()))
def test_wiki_pronunciation_golden(text, expected):
    assert wiki_pronunciation(text) == expected

GOLDEN_CANDIDATES = {  # 후보 목록까지 통째로 (순서 포함)
    "ずっと見 てたよ": ["즛토 미테 타요", "..."],  # ← 실제 추출값
}

@pytest.mark.parametrize("text,expected", sorted(GOLDEN_CANDIDATES.items()))
def test_candidates_golden(text, expected):
    assert pronunciation_candidates(text) == expected
```

- [ ] **Step 2: 실행 확인** — `.venv/Scripts/python.exe -m pytest tests/test_pron_golden.py -q` → 전부 PASS (현재 코드 그대로니 당연히 통과해야 한다. 실패하면 추출값 복사 실수).
- [ ] **Step 3: 커밋** — `test(pron): 다국어화 전 한글 발음 경로 골든 스냅샷 — 렌더러 주입 리팩터링의 회귀 관문`

### Task 2: 렌더러 주입 — `script` 파라미터

**Files:**
- Modify: `everyric2/text/pron_style.py` (`_render_pronunciation:205`, `_render:190-202`, `wiki_pronunciation:293`)
- Test: `tests/test_pron_golden.py` (기존 통과 확인만)

**Interfaces:**
- Produces: `_render_pronunciation(text, tokens, *, script="hangul", latin_tight=True)`, `wiki_pronunciation(text, *, script="hangul")`. `script="hangul"` 경로는 **코드 경로까지 기존과 동일**해야 한다.

- [ ] **Step 1:** `_render`와 `_render_pronunciation`에 `script` 파라미터를 추가한다. 가나 런 변환(`_reading_to_hangul`)과 라틴 음차(`transliterate_latin`) 호출을 script 분기로 감싼다. `script="romaji"`일 때: 가나 런 → `kana_romaji.kana_to_romaji`(Task 3에서 생성 — 이 태스크에서는 `raise NotImplementedError` 스텁 대신 **임시로 가나 그대로 통과**시키고 Task 4에서 연결), 라틴 → 그대로 통과. `_WIKI_DIGRAPHS`(휘/훼)와 `_restore_ei`는 가나 조작이므로 script 무관 공통 유지.
- [ ] **Step 2:** `.venv/Scripts/python.exe -m pytest tests/test_pron_golden.py tests/test_pron_style.py tests/test_pron_candidates.py -q` → 전부 PASS (바이트 동일 증명).
- [ ] **Step 3:** 전체 테스트 `.venv/Scripts/python.exe -m pytest tests/ -q` → 1563+ 전부 PASS.
- [ ] **Step 4: 커밋** — `refactor(pron): 렌더러 주입 — 표기(script)를 파라미터로, hangul 경로는 바이트 동일`

### Task 3: `kana_romaji.py` — 가나→romaji 렌더러

**Files:**
- Create: `everyric2/text/kana_romaji.py`
- Test: `tests/test_kana_romaji.py`

**Interfaces:**
- Produces: `kana_to_romaji(text: str) -> str` (가나 외 통과), `moras_to_romaji(moras: list[str]) -> list[str]` (입력과 같은 길이, 모라별 토큰).

표기 규칙 (miraheze 관례 — 헵번 자음 + 장음은 가나 표기 그대로):
- 헵번: し→shi, ち→chi, つ→tsu, ふ→fu, じ→ji, を→o(조사 읽기는 이미 phonetic), ん→n (모음·y 앞은 n'), っ→다음 자음 중복(っち→tchi), ー→직전 모음 반복, 요음 きゃ→kya·しゅ→shu·ちょ→cho.
- 장음 매크론 안 씀: こう→kou, とお→too (wapuro — miraheze 대다수 표기와 일치).

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_kana_romaji.py
import pytest
from everyric2.text.kana_romaji import kana_to_romaji, moras_to_romaji

@pytest.mark.parametrize("kana,expected", [
    ("ずっと", "zutto"), ("しゅんかん", "shunkan"), ("ちょっとまって", "chottomatte"),
    ("スーパー", "suupaa"), ("こんや", "kon'ya"), ("せんえん", "sen'en"),
    ("フラッシュバック", "furasshubakku"), ("っち", "tchi"),
    ("あいしあえる", "aishiaeru"), ("わ たし", "wa tashi"),  # 공백 통과
    ("abc123", "abc123"),  # 비가나 통과
])
def test_kana_to_romaji(kana, expected):
    assert kana_to_romaji(kana) == expected

def test_moras_to_romaji_same_length_and_sokuon():
    # ずっと → [zu, t, to]: っ은 다음 자음 하나를 받는다
    assert moras_to_romaji(["ず", "っ", "と"]) == ["zu", "t", "to"]
    # っち는 tchi — っ 토큰이 t, ち가 chi
    assert moras_to_romaji(["ま", "っ", "ち"]) == ["ma", "t", "chi"]
    # 장음 ー는 직전 모음
    assert moras_to_romaji(["す", "ー"]) == ["su", "u"]
    # ん 단독 모라, 다음이 모음이면 n'
    assert moras_to_romaji(["せ", "ん", "え", "ん"]) == ["se", "n'", "e", "n"]
    # 요음 결합 모라 1개
    assert moras_to_romaji(["しゅ", "ん"]) == ["shu", "n"]
```

- [ ] **Step 2:** 실행 → FAIL (모듈 없음) 확인.
- [ ] **Step 3: 구현** — `_MORA_ROMAJI` 표(오십음 전체 + 요음 digraph + 외래어 조합 ふぁ→fa·てぃ→ti·うぃ→wi 등, `kana_hangul._DIGRAPHS`와 같은 키 집합을 romaji 값으로), `moras_to_romaji`가 정본이고 `kana_to_romaji`는 내부에서 문자열을 모라로 쪼개(요음 소문자 결합, `reading.py`의 `_hira_to_kana_moras`류 로직 재사용 가능) `moras_to_romaji`를 부른 join. っ의 «다음 자음»은 다음 토큰의 첫 자음(ch→t 특례). 가타카나 입력은 `kana_hangul._to_hiragana` 재사용해 정규화.
- [ ] **Step 4:** `.venv/Scripts/python.exe -m pytest tests/test_kana_romaji.py -q` → PASS.
- [ ] **Step 5: 커밋** — `feat(pron): 가나→romaji 렌더러 — 헵번 자음 + wapuro 장음(miraheze 관례)`

### Task 4: romaji 라인 API + 심판 판정 공유

**Files:**
- Modify: `everyric2/text/pron_style.py` (script="romaji" 연결, 신규 함수 2개), `everyric2/text/reading.py` (`text_to_moras`에 tokens 인자)
- Test: `tests/test_pron_style.py`에 추가

**Interfaces:**
- Produces:
  - `romaji_line(text: str, tokens: list[ReadingToken] | None = None) -> tuple[str, list[str], list[bool]] | None` — (표시 문자열, 모라 토큰, space_after 불리언 — 세 값은 `display == "".join(tok + (" " if sp else "") for tok, sp in zip(...)).strip()` 관계를 보장).
  - `candidate_token_sets(text) -> tuple[list[str], list[list[ReadingToken]]]` — `pronunciation_candidates`와 같은 순서로 (렌더 문자열, 토큰 열) 병렬 반환. `[0]`이 기본값.
  - `text_to_moras(text, tokens=None)` — tokens를 주면 재토크나이즈 없이 그 읽기로 모라를 만든다(심판이 바꾼 읽기의 모라 수 반영).

- [ ] **Step 1: 실패 테스트**

```python
def test_romaji_line_basic():
    from everyric2.text.pron_style import romaji_line
    display, moras, spaces = romaji_line("アルバイトはネクラモード")
    assert display == "arubaito wa nekura moodo"
    assert len(moras) == 12  # text_to_moras와 같은 모라 수
    assert "".join(m + (" " if s else "") for m, s in zip(moras, spaces)).strip() == display

def test_romaji_line_latin_passthrough():
    from everyric2.text.pron_style import romaji_line
    display, _, _ = romaji_line("Take it easy なんて言葉じゃ")
    assert display.startswith("Take it easy")  # 라틴 원형 유지

def test_candidate_token_sets_parallel():
    from everyric2.text.pron_style import candidate_token_sets, pronunciation_candidates
    text = "ずっと見てたよ"
    rendered, tokens = candidate_token_sets(text)
    assert rendered == pronunciation_candidates(text)
    assert len(rendered) == len(tokens)
```

- [ ] **Step 2:** FAIL 확인.
- [ ] **Step 3: 구현** — `romaji_line`: `tokenize_reading(phonetic=True, adopt_ruby=True)`(또는 인자 tokens) → 모라는 `text_to_moras(text, tokens=tokens)` → `moras_to_romaji` → space_after는 다음 모라의 소스 토큰이 새 토큰이고 그 토큰 pos가 助詞/助動詞/接尾辞가 아니면 True + 원문 공백·구두점 위치. 표시 문자열은 그 세 값에서 합성(단일 소스 — «표시=세그» 불변식). 라틴/ASCII 모라는 원형 통과. `candidate_token_sets`: 기존 `pronunciation_candidates` 내부 루프를 공유하도록 리팩터링(렌더 문자열 목록은 기존 함수가 그대로 반환 — 골든 유지).
- [ ] **Step 4:** 골든 포함 전체 → `.venv/Scripts/python.exe -m pytest tests/ -q` PASS.
- [ ] **Step 5: 커밋** — `feat(pron): romaji 라인 렌더(표시=모라 토큰 단일 소스) + 심판 후보 토큰 노출`

### Task 5: `heard_spans` — CTC가 들은 것의 글자별 시각

**Files:**
- Modify: `everyric2/alignment/ctc_engine.py` (`_heard_lines:769` 인근), `everyric2/server/worker.py` (debug 직렬화 `:3575-3581`)
- Test: `tests/test_ctc_heard.py` (신규, greedy 스팬 단위 함수만 — GPU 불필요)

**Interfaces:**
- Produces: `_greedy_spans(frames: list[int], blank_id, id_to_token, frame_sec: float, t0: float) -> list[tuple[str, float]]` (모듈 함수, 단위 테스트 가능), `get_last_heard_spans() -> dict[int, list[tuple[str, float]]]`, `seg["debug"]["heard_spans"] = [[str, float], ...]`.

- [ ] **Step 1: 실패 테스트** — 합성 greedy 열로 스팬 추출 검증:

```python
def test_greedy_spans_collapse_and_time():
    from everyric2.alignment.ctc_engine import _greedy_spans
    # blank=0, frames: [0,5,5,0,7,0] → 토큰 5@frame1, 7@frame4
    spans = _greedy_spans([0, 5, 5, 0, 7, 0], 0, {5: "あ", 7: "い"}, frame_sec=0.02, t0=10.0)
    assert spans == [("あ", 10.02), ("い", 10.08)]
```

- [ ] **Step 2:** FAIL 확인.
- [ ] **Step 3: 구현** — `_heard_lines` 옆에 `_greedy_spans` 추가(연속 중복 붕괴 + 첫 프레임 시각), `_heard_lines` 호출부에서 라인 창별로 함께 계산해 `self._last_heard_spans`에 저장, 접근자 추가. worker: `heard` 싣는 자리(`:3577-3579`)에서 `heard_spans`도 함께 debug에 싣는다(값은 `[[ch, round(t, 2)], ...]`). ko 독음 정렬 경로는 heard가 **독음(한글)** 전사임을 주의 — 그대로 싣는다(디버그 라벨이 이미 «전사텍스트=독음» 구분 표시 중).
- [ ] **Step 4:** `.venv/Scripts/python.exe -m pytest tests/test_ctc_heard.py -q` PASS + 전체 PASS.
- [ ] **Step 5: 커밋** — `feat(debug): heard 글자별 시각(heard_spans) — CTC가 들은 것의 타임라인 재료`

### Task 6: 확장 디버그 «라인 집중 뷰»

**Files:**
- Modify: `everyric2-chrome/src/types.ts:121` (debug 타입), `everyric2-chrome/src/ui/pip.ts` (`renderDebugOverlay:2116-2163`, `renderTimingLanes:2103-2113`)

**Interfaces:**
- Consumes: `seg.debug.heard: string`, `seg.debug.heard_spans: [string, number][]`, `seg.debug.referee: {default, chosen, margin, gain, scores}` (Task 5 산출 + 기존 서버 필드).

- [ ] **Step 1:** types.ts debug에 `heard?: string; heard_spans?: [string, number][]; referee?: { default?: string; chosen?: string; margin?: number; gain?: number; scores?: [string, number][] }` 추가.
- [ ] **Step 2:** pip 디버그 오버레이 재작성 — 재생 중 라인에 대해: (1) 기존 원문 글자 타이밍 바(conf 색) 유지, (2) 바로 아래 heard 레인: `heard_spans`의 각 글자를 시각 위치에 배치(없으면 `heard` 문자열을 라인 구간에 균등 배치 폴백), (3) 심판 개입 라인이면 `⚖ {default}→{chosen} ({gain>=0?'+':''}{gain})` 뱃지. 기존 «보정 전 고스트(orig 점선)»는 fixes 라벨 텍스트로 축소(고스트 드로잉 제거 — 헷갈림의 주범).
- [ ] **Step 3:** `cd everyric2-chrome && npm run build` → 성공.
- [ ] **Step 4: 커밋** — `feat(ext-debug): 라인 집중 뷰 — 원문 타이밍 바 아래 heard 타임라인 + 심판 뱃지, 고스트 제거`

### Task 7: 번역 레이어 테이블 + fingerprint

**Files:**
- Modify: `everyric2/server/db/models.py`
- Create: `everyric2/server/text_fingerprint.py`
- Modify: `everyric2/server/db/repository.py` (Layer CRUD — 기존 리포지토리 패턴 확인 후 동형 추가)
- Test: `tests/test_translation_layer.py`

**Interfaces:**
- Produces:

```python
# models.py
class TranslationLayer(Base):
    __tablename__ = "translation_layers"
    id: Mapped[str]  # uuid4 PK (SyncResult와 동형)
    video_id: Mapped[str]      # String(32), index
    fingerprint: Mapped[str]   # String(32), index — lines_fingerprint 값
    target_lang: Mapped[str]   # String(8)
    lines: Mapped[list]        # JSON: [{"text": str, "translation": str}]
    attribution: Mapped[dict | None]  # JSON: {"name","url","license","source_id"}
    origin: Mapped[str]        # String(16): "llm"|"wiki"|"manual"|"caption"
    created_at: Mapped[datetime]
    __table_args__ = (UniqueConstraint("video_id", "fingerprint", "target_lang"),)

# text_fingerprint.py
def lines_fingerprint(texts: list[str]) -> str:
    """세그먼트 원문 텍스트 목록의 md5 32hex — worker._normalize_line과 같은 정규화."""
```

- 리포지토리: `get_layer(video_id, fingerprint, target_lang)`, `upsert_layer(...)` (유니크 충돌 시 교체).

- [ ] **Step 1: 실패 테스트** — 인메모리 SQLite로 upsert→get→덮어쓰기, fingerprint가 공백·전각 차이에 불변(정규화 공유)임을 검증.
- [ ] **Step 2:** FAIL 확인. **Step 3:** 구현 — 테이블 생성 방식은 기존 `Base.metadata.create_all` 경로를 확인해 그대로 탄다(추가 전용). `_normalize_line`은 worker에서 `text_fingerprint.py`로 **이동**하고 worker가 임포트(순환 방지). **Step 4:** 전체 테스트 PASS. **Step 5: 커밋** — `feat(db): 번역 레이어 테이블 (video_id, fingerprint, target_lang) — 언어별 번역의 정석 저장`

### Task 8: worker — pron dict 저장 + 생성 시 번역 분리

**Files:**
- Modify: `everyric2/server/worker.py` (직렬화 `:3549-3583`, `merge_line_meta:249`, 생성 시 번역 호출부 — `translate_with_pronunciation` 검색으로 위치 특정)
- Test: `tests/test_worker_pron_dict.py` (직렬화 단위 — 실오디오 불필요, 세그먼트 dict 조작 함수 단위로)

**Interfaces:**
- Consumes: `romaji_line`, `candidate_token_sets` (Task 4), `TranslationLayer`/`lines_fingerprint` (Task 7).
- Produces: 직렬화 후 각 ja 세그먼트에 `pron["hangul"]`(기존 pronunciation과 동일 문자열)·`pron["romaji"]`·`pron_segs["romaji"]`; Job에 `target_lang`(String(8), default "ko") 칼럼; 생성 시 번역이 레이어에 기록되고 legacy 슬롯은 ko일 때만.

- [ ] **Step 1:** 순수 함수 `attach_pron_variants(seg: dict, *, referee_tokens=None) -> None`을 worker에 추가하는 실패 테스트: words(char_spans)와 text가 있는 세그먼트에 romaji pron/segs가 붙고, `pron["hangul"] == seg["pronunciation"]`, romaji segs의 start가 단조인지. referee_tokens를 주면 romaji가 그 읽기(모라 수 변화)를 따르는지.
- [ ] **Step 2:** FAIL 확인. **Step 3:** 구현 — 직렬화 루프(`:3549` 이후)에서 세그먼트 완성 직후 호출. 심판이 바꾼 라인은 `_referee_candidates` 경로에서 chosen 인덱스를 알 수 있으므로 `candidate_token_sets`의 해당 토큰 열을 `pron_data[i]["tokens"]`로 실어 보내고 여기서 소비. `merge_line_meta`(캐시 재사용·늦은 병합)에서도 병합 후 `attach_pron_variants` 호출(멱등 — 이미 있으면 스킵, `_attach_pron_segments`의 기존 가드 패턴).
- [ ] **Step 4:** 생성 시 번역 분리 — worker의 번역 호출부에서: 발음(결정론)은 현행 그대로 ko로, 번역 target은 `job.target_lang`. 결과 번역은 (a) `TranslationLayer(origin="llm")` upsert, (b) `target_lang=="ko"`일 때만 legacy 슬롯(`seg["translation"]`) 병기. line_meta로 들어온 번역(`line_meta_lang` — Task 9에서 API에 추가)도 같은 규칙: 레이어 기록 + ko만 legacy.
- [ ] **Step 5:** 전체 테스트 PASS. **Step 6: 커밋** — `feat(worker): 발음 전 표기 동시 생성(pron dict, 심판 판정 공유) + 생성 번역의 레이어 기록`

### Task 9: API — `lang` 파라미터 + persist

**Files:**
- Modify: `everyric2/server/api/sync.py` (lookup 핸들러 + `SyncLookupResponse`, 생성 요청 모델), `everyric2/server/api/translate.py`
- Test: `tests/test_api_lang.py` (FastAPI TestClient — 기존 API 테스트 파일 패턴 확인 후 동형)

**Interfaces:**
- Produces: `GET /api/sync?...&lang=<code>` — lang 지정 시: 해당 언어 레이어 있으면 세그 translation 교체+`translation_lang=<code>`; 없으면 `lang=="ko"`는 legacy 유지(`translation_lang="ko"` — 저장분은 ko라는 이행 가정), 비ko는 translation 비움+`translation_lang=None`. lang 미지정 → **기존 응답 그대로**(translation_lang 필드는 None으로 존재 — pydantic 추가 필드는 허용 범위). 생성 요청에 `target_lang: str = "ko"`, `line_meta_lang: str = "ko"` 필드. `POST /api/translate`에 `persist: bool = False` — true이고 video_id 있으면 결과를 `lines_fingerprint(원문 lines)`로 레이어 upsert(origin="llm").

- [ ] **Step 1: 실패 테스트** — TestClient로: (1) lang 없이 조회 = 기존 필드 동일, (2) 레이어 upsert 후 `lang=en` 조회 → translation 교체 + translation_lang=="en", (3) `lang=en` 레이어 없음 → translation 전부 빈 값 + translation_lang None, (4) translate persist=true → 레이어 생김.
- [ ] **Step 2:** FAIL 확인. **Step 3:** 구현 (translation 교체는 `_normalize_line` 매칭 — merge_line_meta와 같은 색인 방식 재사용). **Step 4:** 전체 PASS. **Step 5: 커밋** — `feat(api): 언어별 번역 서빙(lang)·저장(persist) — 구버전 요청은 기존 응답 그대로`

### Task 10: 대각선 생략 — 같은 언어 무처리

**Files:**
- Modify: `everyric2/translation/translator.py` (`_should_skip_pronunciation:495`), `everyric2/server/api/translate.py` (가나 오염 가드 `:14-49`)
- Test: `tests/test_translator_matrix.py`

**Interfaces:**
- Produces: `_should_skip_pronunciation(text, source_lang, target_lang)` — 매트릭스 규칙: 곡 언어==target_lang이면 True(ko곡×ko유저, en곡×en유저, ja곡×ja유저); 기존 «en/ko 원문이면 스킵» 규칙은 **target=ko일 때만** 유지(가나 예외 포함). 가나 오염 가드는 `target_lang=="ko"`일 때만 발동(ja 타깃의 정상 가나 발음 파괴 방지 — 지금은 ja 타깃 발음 경로가 없지만 가드가 미래 경로를 죽이지 않게 선행 수정).

- [ ] **Step 1: 실패 테스트** — (ja곡, target=ja) → 발음 스킵 True·translation_skipped True, (ko곡, target=ko) → 스킵 True, (ja곡, target=en) → 발음 스킵 False. 기존 «라틴 많은 일본어 곡 en 오판» 테스트 계약 유지 확인.
- [ ] **Step 2:** FAIL. **Step 3:** 구현(호출부 시그니처 전파). **Step 4:** 전체 PASS. **Step 5: 커밋** — `fix(translate): 곡 언어==사용자 언어면 번역·발음 생략 — 매트릭스 대각선`

### Task 11: 확장 — 다국어 수신·요청

**Files:**
- Modify: `everyric2-chrome/src/types.ts` (LyricLine·Settings), `src/lib/settings.ts` (DEFAULT_SETTINGS), `src/lib/everyric-api.ts` (lang 파라미터·persist), `src/content.ts` (loadTranslations 가드 `:1015-1075`, expectsPronunciation `:1120`, humanTranslated 가드 `:1009,1019`, handleGenerate — target_lang 전달), `src/ui/overlay.ts` (발음 표기 선택 — 자동이면 UI 추가 없음)

**Interfaces:**
- Consumes: 서버 `pron`/`pron_segs` dict, `translation_lang` (Task 8·9).
- Produces: `Settings.pronunciationScript`(기본 'auto')·`Settings.uiLanguage`(기본 'auto'); script 해석 헬퍼 `resolveScript(settings): 'hangul'|'romaji'|'kana'`; 표시 경로는 `line.pron?.[script] ?? line.pronunciation` 폴백; `pron_segs[script] ?? pron_segments` 폴백.

- [ ] **Step 1:** types/settings에 필드 추가 + `resolveScript` 구현(공유 계약의 자동 결정표).
- [ ] **Step 2:** 조회에 `lang=translationLanguage` 부여. loadTranslations 가드를 «응답 translation_lang === 내 언어»로 교체(기존 `every(l => l.translation)` 검사는 그 안쪽 조건으로 유지). 번역 완료 시 `persist: true`로 재호출 없이 최초 호출에 persist를 실어 서버에 저장. `expectsPronunciation`을 매트릭스로: 곡 스크립트(가나/한자→ja, 한글→ko, 라틴→en 추정)이 내 언어와 다르면 발음 기대. vocaro/humanTranslated 스킵 가드는 `translationLanguage==='ko'`일 때만.
- [ ] **Step 3:** 렌더 지점(overlay·pip의 pronunciation/pron_segments 소비처 전부 — grep으로 특정)을 폴백 헬퍼 경유로 교체.
- [ ] **Step 4:** `npm run build` 성공. **Step 5: 커밋** — `feat(ext): 사용자 언어별 번역 요청·발음 표기 선택 — 남의 언어 수신 구멍 봉쇄`

### Task 12: 소스 Protocol + miraheze 어댑터

**Files:**
- Create: `everyric2-chrome/src/lib/sources.ts` (인터페이스), `everyric2-chrome/src/lib/miraheze.ts`
- Modify: `src/lib/vocaro.ts` (인터페이스 준수 — 반환에 pronLang/translationLang/license 부여), `src/background.ts` (MIRAHEZE_LOOKUP 메시지), `src/types.ts` (RuntimeMessage·Attribution 확장), `src/content.ts` (소스 체인 `:1305-1320`, adoptVocaroResult `:1347` 일반화, `/위키/` 판별 `:1876` 제거), `manifest.json` (host_permissions `https://vocaloidlyrics.miraheze.org/*`)

**Interfaces:**
- Produces:

```typescript
// sources.ts
export interface SourceLine { text: string; pronunciation?: string; translation?: string }
export interface SourceResult {
  sourceId: 'vocaro' | 'miraheze' | 'lrclib' | 'manual';
  pageUrl: string; pageTitle: string;
  lines: SourceLine[];
  pronLang?: 'hangul' | 'romaji';      // 위키 발음의 표기
  translationLang?: 'ko' | 'en';       // 위키 번역의 언어
  license?: string;                     // "CC BY-SA 4.0" 등
}
```

- miraheze lookup: `GET https://vocaloidlyrics.miraheze.org/w/api.php?action=query&list=search&srsearch=<title>&format=json&origin=*` → 최상위 후보 → `action=parse&page=<title>&prop=text` → HTML 파싱: 가사 표는 `<table>` 안 행들이 «일본어 | Romaji | English» 3열(2열이면 번역 없음 — 하꼬곡 romaji-only). 셀 안 `<br>`가 줄 구분.
- attribution: `{ name: pageTitle + ' — VocaloidLyrics Wiki', url, license: 'CC BY-SA 4.0', source_id: 'miraheze' }` 형태로 생성 요청에 실림 (Attribution 타입에 `license?`, `source_id?` 추가 — 서버 `sync.py:236` Attribution 모델에도 같은 optional 필드 추가).

- [ ] **Step 1:** sources.ts 작성, vocaro.ts가 `SourceResult`(sourceId 'vocaro', pronLang 'hangul', translationLang 'ko')를 반환하게 어댑트(기존 필드 유지 — 호출부 점진 전환).
- [ ] **Step 2:** miraheze.ts 구현 + background 메시지 + manifest 권한.
- [ ] **Step 3:** content.ts: 소스 체인에 miraheze 추가 — 우선순위는 `translationLanguage==='ko'`면 vocaro→miraheze, 아니면 miraheze→vocaro. adopt 경로 일반화: `SourceResult.translationLang`을 생성 요청 `line_meta_lang`으로 전달, `pronLang!=='hangul'`이면 line_meta의 pronunciation은 비운다(정렬은 한글 독음만 — 서버가 결정론 생성). `data.source==='vocaro'` 하드코딩 2곳(`:1009,1019`)을 attribution.source_id 기반으로.
- [ ] **Step 4:** `npm run build` 성공 + 수동 확인은 Task 16(실전)에서. **Step 5: 커밋** — `feat(sources): 소스 Protocol + vocaloidlyrics.miraheze.org 어댑터(CC BY-SA 4.0 표기)`

### Task 13: 확장 i18n + 서버 오류 코드

**Files:**
- Create: `everyric2-chrome/_locales/ko/messages.json`, `_locales/en/messages.json`, `_locales/ja/messages.json`, `src/lib/i18n.ts`
- Modify: `manifest.json` (`default_locale: "ko"`), 한국어 리터럴 소비처 전부(content.ts, overlay.ts, panels.ts, pip.ts, options.html), `everyric2/server/api/translate.py`·`worker.py`·`services/youtube_captions.py` (오류 코드)

**Interfaces:**
- Produces: `t(key: string, subs?: string[]): string` — `chrome.i18n.getMessage` 래퍼, `uiLanguage!=='auto'`면 번들된 messages 사전에서 직접 조회(크롬 i18n은 브라우저 로케일 고정이라 오버라이드용 사전을 `i18n.ts`가 임포트). 서버: 오류 응답 detail을 `{code: str, message: str}` dict로 — 단, **확장의 tolerant 파싱(‏`typeof detail === 'string' ? detail : detail.message`)을 먼저 배포**하고 서버 코드화는 그 다음 커밋(구버전 호환 순서).

- [ ] **Step 1:** i18n.ts + _locales 3언어(리터럴 전수는 grep `[가-힣]` in src로 뽑아 키화 — 대략 30~50개). 기계적 대치는 executor 위임 가능.
- [ ] **Step 2:** `npm run build` 성공, 확장 UI 문자열이 t() 경유인지 grep으로 잔존 한글 리터럴 0 확인(주석 제외).
- [ ] **Step 3: 커밋** — `feat(ext-i18n): _locales ko/en/ja + t() — UI 문자열 전수 키화`
- [ ] **Step 4:** 확장 오류 파싱 tolerant화 → 커밋 → 서버 detail `{code, message}` 전환(translate 422·워커 영상 길이·자막 문자 검사 3곳부터) + 확장 code→로컬라이즈 매핑 → 커밋 `feat(api): 오류 코드 — 확장이 자기 언어로 렌더`.

### Task 14: `ko_reading.py` + 라틴→가나 체인

**Files:**
- Create: `everyric2/text/ko_reading.py`
- Modify: `everyric2/server/worker.py` (`attach_pron_variants` — ko 곡·라틴 곡 분기 추가)
- Test: `tests/test_ko_reading.py`

**Interfaces:**
- Produces:

```python
def hangul_to_kana(text: str) -> str          # 한국어 → 가타카나 (비한글 통과)
def hangul_to_romaja(text: str) -> str        # RR 로마자
def hangul_line_moras(text: str) -> list[tuple[str, int, int]]  # (모라 토큰, char_start, char_end)
def latin_to_kana(text: str) -> str           # 라틴 → (latin_hangul 느슨) → 가타카나 체인
```

규칙: 자모 분해는 `reading.py._decompose_hangul` 재사용(공유 유틸로 이동 금지 — 임포트만). 초성+중성 → 가타카나 표(카→カ, 사→サ, ...), 받침: ㄴ/ㅇ→ン, ㄱ/ㅋ→ック류 촉음(ッ+무성), ㄹ→ル, ㅁ→ム, ㅂ/ㅍ→プ, ㅅ/ㅆ/ㄷ계→ッ. 연음: 받침+다음 초성 ㅇ → 받침을 다음 음절 초성으로(먹어→머거→モゴ). RR: 국립국어원 표준표 + 같은 연음 적용. 모라 산출: 한글 1글자 = 1모라, 받침이 ン/ッ/ル 등 독립 가나가 되면 그 글자에 2모라 귀속(타이밍은 글자 스팬 내부 균등 분할 — `pron_segs` 생성 시).

- [ ] **Step 1: 실패 테스트**

```python
@pytest.mark.parametrize("ko,kana", [
    ("사랑해", "サランヘ"), ("먹어", "モゴ"), ("있잖아", "イッチャナ"),
    ("좋아", "チョア"), ("한국", "ハングク"),
])
def test_hangul_to_kana(ko, kana): ...

@pytest.mark.parametrize("ko,rr", [
    ("사랑해", "saranghae"), ("먹어", "meogeo"), ("한국", "hanguk"),
])
def test_hangul_to_romaja(ko, rr): ...

def test_latin_to_kana_chain():
    assert latin_to_kana("take") == "テイク"  # latin_hangul 느슨(테이크) 경유
```

(기대값은 구현 전에 표준표로 손계산해 확정 — 구현에 맞추지 말 것.)
- [ ] **Step 2:** FAIL. **Step 3:** 구현. **Step 4:** `attach_pron_variants`에 ko 곡 분기: `pron["kana"]`·`pron["romaja"]`(script 키는 "kana"/"romaji" 재사용 — romaja도 "romaji" 키에 싣는다: 클라이언트는 script 하나만 고른다) + `pron_segs`(원문 정렬 words = 한글 글자 스팬에서 직접). 라틴 곡: `pron["kana"] = latin_to_kana(...)`. **Step 5:** 전체 PASS. **Step 6: 커밋** — `feat(pron): 한글 읽기 엔진(가타카나·RR) + 라틴→가나 체인 — ko·en 곡의 ja/en 사용자 발음`

### Task 15: 디버그 곡 전체 패널

**Files:**
- Modify: `everyric2-chrome/src/ui/overlay.ts` (디버그 스트립에 «전체 보기» 토글), `src/ui/panels.ts` 또는 신규 `src/ui/debug-panel.ts`

- [ ] **Step 1:** 패널: 전 라인 세로 나열 — 각 행에 [시각] 원문 / heard(있으면) / conf 등급 칩 / fixes·scaffold 라벨(debugMeta.caption_scaffold 존재 시 라인 소스 표기). 클릭 시 해당 구간으로 시크(`seekToVideoTime` 재사용 — 시크 가드가 이미 있다).
- [ ] **Step 2:** `npm run build` 성공. **Step 3: 커밋** — `feat(ext-debug): 곡 전체 디버그 패널 — 원문 vs heard 전수 대비 + 클릭 시크`

### Task 16: 실전 테스트·배포·검증 (메인 에이전트 전용)

- [ ] **Step 1:** 로컬 전체: `.venv/Scripts/python.exe -m pytest tests/ -q` 전부 PASS + `ruff check` + `npm run build`.
- [ ] **Step 2:** 서버 배포 — ssh로 pull + 유저 유닛 재시작(포트 8300). health 확인(admin 키, 127.0.0.1 아님 주의 — 원격은 100.76.4.47:8300).
- [ ] **Step 3: 호환 관문** — 기존 곡(JW3N-HvU0MA)을 lang 없이 조회 → 필드 구성이 배포 전 응답과 동일(+추가 필드만)임을 diff로 확인. **b2NT·BiQs 금지.**
- [ ] **Step 4: 쓰기 경로 (릴리스 검증 기준)** — 테스트 곡 1곡을 admin 키로 재생성(3090 워커) → (a) ko 조회: pronunciation·번역 현행과 동등 + `pron.hangul`/`pron.romaji` 존재, (b) `lang=en` 조회: translation 빈 값 확인 → `/api/translate persist=true target=en` → 재조회에 en 번역+`translation_lang=en`, (c) debug에 heard_spans 존재.
- [ ] **Step 5:** 확장 실사용 확인 요청 — 사용자에게 dist 새로고침 후 (1) 한국어 설정에서 기존과 동일한지, (2) 번역 언어 en으로 바꿔 romaji+영어가 뜨는지, (3) 디버그 라인 뷰 확인을 부탁한다.
- [ ] **Step 6:** 결과를 커밋 로그·메모리에 기록.

## Self-Review 결과

- 스펙 커버리지: 스펙 8단계 → Task 1-2(§1), 5-6(§2), 3·4·7·8·9(§3), 10·11(§4), 12(§5), 13(§6), 14(§7), 15(§8), 16(실전). 대각선·가드·attribution license·수동 입력 유지(기존 handleGenerate 경로 무변경) 모두 태스크에 있음.
- 타입 일관성: script 리터럴·`pron`/`pron_segs`·`translation_lang`·`lines_fingerprint`·`romaji_line` 시그니처를 공유 계약 절에 단일 정의.
- 알려진 미결(의도적): miraheze의 사람 romaji는 v1에서 무시(결정론 romaji 사용), zh 사용자는 번역만(발음은 hangul 폴백), 다중 발음 동시 표시 범위 외.
