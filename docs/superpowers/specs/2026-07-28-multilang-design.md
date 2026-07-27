# 다국어(ko/en/ja) 사용자 지원 재설계

작성: 2026-07-28 · 상태: 사용자 승인된 설계 (구현 계획 수립 전)

## 목표

확장을 한국어 사용자 전용에서 **곡 언어 × 사용자 언어 전 매트릭스**로 확장한다.
동시에 지금 존재하는 두 버그를 수정한다:

- 한국어 곡을 한국어 사용자가 요청해도 한국어 번역·한글 발음을 만들어 버린다 (대각선 미생략).
- 세그먼트에 번역 언어가 기록되지 않아, 한국인이 만든 싱크를 일본인이 열면
  한국어 번역을 그대로 받고 확장 가드가 재요청하지 않는다 (`models.py:22-33`,
  `content.ts:1024-1039`에 문서화된 문제).

부수 목표: vocaloidlyrics.miraheze.org(CC BY-SA 4.0) 소스 확장, 수동 입력 유지,
CTC 디버그 표시 강화(원문인식 타이밍의 직관적 시각화).

## 확정된 결정 (사용자 승인)

| 축 | 결정 |
|---|---|
| 범위 | 전 매트릭스 (ja·ko 곡 × ko/en/ja 사용자) 한 번에 설계, 구현은 단계 분리 |
| 생성 깊이 | **하이브리드**: 타이밍 1회 + 발음 전 표기 즉시 생성(결정론, LLM 비용 0) + 번역은 언어별 첫 요청 시 생성·저장 |
| 저장·서빙 | **접근 1: 레이어 분리** — 언어 독립 코어 + 발음 dict + 번역 `(video_id, lyrics_hash, target_lang)` 테이블 |
| 디버그 UX | 둘 다, **라인 집중 뷰 먼저** — 구현 순서도 앞당김(1번 직후) |

## 표기 매트릭스

발음 표기(script)와 번역 언어(target_lang)는 분리된 개념이다.

| 곡 언어 | ko 사용자 | en 사용자 | ja 사용자 |
|---|---|---|---|
| ja | 한글 독음 (현행) | romaji | 발음 없음 |
| ko | 발음 없음 | romaja(RR) | 가타카나 |
| en/라틴 | 한글 음차 (현행) | 발음 없음 | 가타카나 음차 |

대각선(같은 언어)은 발음·번역 모두 생략한다.

## 아키텍처

**핵심 원칙: 정렬 경로는 한 글자도 바꾸지 않는다.** 검증된 파이프라인
(ja 곡 → 한글 독음 정렬 `worker.py:2860`, ko 곡 → 원문 정렬)이 그대로
«타이밍 코어»를 만들고, 다국어화는 렌더·저장·서빙 층에서만 일어난다.

### 데이터 모델 — 코어 + 두 레이어

1. **코어** (기존 `timestamps`, 무변경): 원문, `words` 글자 타이밍, conf, debug.
2. **발음 레이어** (세그먼트에 추가):
   - `seg["pron"] = {"hangul": str, "romaji": str, ...}` — 그 곡에 유효한 전 표기.
   - `seg["pron_segs"] = {script: [{text, start, end, resolved}]}`.
   - 기존 `pronunciation`/`pron_segments` 단일 필드는 한글 값으로 계속 채운다
     (구버전 확장·기존 싱크 폴백).
3. **번역 레이어** (신규 테이블): 키 `(video_id, lyrics_hash, target_lang)`.
   줄별 번역 + attribution(name, url, **license**) + 출처 종류(wiki/llm/manual/caption).
   가사가 같으면 재생성해도 번역이 살아남는다 — «regenerate하면 날아감» 부류가
   번역에선 구조적으로 소멸.

### API

- `GET /api/sync?...&lang=ja`: 코어 + 발음 dict(전 표기 전달, 클라이언트 선택)
  + 해당 언어 번역 조합. 응답에 `translation_lang` 명시.
  **`lang` 없으면 현행과 완전 동일 응답** (구버전 자연 게이트).
- `POST /api/translate`: `target_lang` 파라미터는 이미 존재(`translate.py:55`).
  쓰기 대상을 번역 레이어 테이블로 변경. `target_lang=ko`는 호환 위해 기존 슬롯 병기.
- 서버 오류 메시지는 오류 코드로 내려주고 확장이 자기 언어로 렌더
  (현행 한국어 문자열: `translate.py:100`, `worker.py:496-499` 등).

## 발음 생성 스택 — «가나 읽기가 화폐, 표기는 환전»

- `_render_pronunciation(tokens, script=...)`: 문절 분해·애매어휘 처리(중립층,
  `pron_style.py:32-84, 341-412`)는 공유, 마지막 환전만 렌더러 주입:
  - `hangul`: 현행 (`kana_hangul` + `latin_hangul`) — 코드 무변경.
  - `romaji`: 신규 `kana_romaji.py` — 헵번식, 표기 관례는 miraheze를 따름
    (위키 발음과 눈 비교 가능해야 함).
  - `kana`: ko 곡·라틴 곡용 가타카나.
- **음절 타이밍은 생성 시 공짜**: 렌더러가 모라별 토큰으로 뱉고 join —
  `pron_segs`가 표기마다 자동 파생. 사후 syllabifier 불필요,
  «표시=정렬» 불변식(`pron_style.py:298-303`) 유지.
- **심판(referee)은 1회 판정, 전 표기 공유**: 정렬 표기(한글)에서 가나 대립
  판정 후 선택된 **가나**를 전 렌더러가 받는다. romaji 사용자도 観て→miete
  교정을 공짜로 받는다.
- **romaji는 표시 전용, 정렬 금지**: 라틴 글자 CTC 정렬 붕괴 실측
  (`latin_hangul.py` 헤더, conf<0.01이 90~99%). 정렬은 현행 표기 유지.
- 신규 `ko_reading.py` (한글 읽기 엔진): 한글→자모 분해(재료는 `reading.py`)
  → 대표 음운 규칙(연음·비음화 등 소수) → 가타카나/RR. 한글은 1글자=1음절이라
  매핑 자명. 받침은 ッ/ン·말음 자음 처리.

## 소스 추상화

- `LyricsSource` 인터페이스:
  `lookup(title) → {url, lines[{text, pron?, translation?}], pronLang, translationLang, attribution{name, url, license}}`.
  위키 발음·번역이 언어 태그를 달고 번역 레이어로 들어간다.
- 어댑터: vocaro(ko 번역+한글 발음, 현행 이식) / **miraheze**(romaji+en 번역,
  MediaWiki `api.php` 검색 — 인덱스 크롤 불필요) / lrclib(가사만) / 유튜브 자막 /
  수동 입력(출처 이름 입력 유지).
- 하꼬곡 romaji-only 페이지: 번역 없으면 그 언어만 LLM 폴백.
- CC BY-SA 4.0: attribution에 license 필드 + 페이지 URL, UI 표기.
  `content.ts:1876`의 `/위키/` 정규식 판별 → 구조화된 source id로 교체.
- 소스 우선순위: 사용자 언어별 기본값(ko: vocaro 우선, en: miraheze 우선) + 설정.

## 확장(크롬)

- `_locales/{ko,en,ja}` + `chrome.i18n` 표준. UI 언어는 브라우저 로케일 자동,
  설정 오버라이드.
- 발음 표기는 매트릭스 자동 결정 + 고급 설정 오버라이드.
- `loadTranslations` 가드(`content.ts:1015-1075`): 응답 `translation_lang`과
  자기 설정 비교, 다르면 재요청. `expectsPronunciation`도 매트릭스 기반 일반화
  (서버 복제본 `sync.py:1015-1027` 동기 수정).
- vocaro/`humanTranslated` 번역 스킵 가드(`content.ts:1009, 1019`):
  언어 일치할 때만 스킵.
- 다중 발음 동시 표시(한글+romaji)는 범위 외 (YAGNI — dict 구조라 나중에 열 수 있음).

## 디버그 표시

- **1단계 — 라인 집중 뷰**: 재생 중 라인의 원문 글자 타이밍 바(conf 색 유지)
  아래에 CTC가 들은 전사(`heard`)를 같은 시간축으로 겹쳐 표시 + 심판 뱃지
  (`みて→みえて, gain −0.04`). 재료(`seg.debug.heard`, `referee.scores`)는 이미
  API에 내려온다 — 확장 타입 선언(`types.ts:121`에 heard/referee 부재)과 렌더만
  추가. 단 `heard`는 문자열뿐이므로 서버 `_heard_lines`(`ctc_engine.py:769-793`)가
  프레임 시각을 함께 뱉는 `heard_spans` 보강 1건 필요.
- **2단계 — 곡 전체 패널**: 전 라인 세로 나열, 원문 vs heard 대비, conf 등급,
  `fixes` 라벨, 스캐폴드/앵커 개입(`debug_meta`에 기존재). 기존의 헷갈리는
  2레인+고스트 오버레이는 1단계 뷰로 대체.

## 오류 처리

- 소스 lookup 실패 → 우선순위 체인 폴백.
- 번역 언어별 부분 실패 허용 (현행 «번역 일부 실패» 패턴).
- 미지원 script 요청 → 한글 폴백 + 로그.

## 회귀 방지 — 6중 방벽

1. 정렬 경로 무변경 (설계 원칙).
2. **골든 스냅샷**: 렌더러 주입 리팩터링 직전 대표 곡들의 현행 한글 발음 출력을
   스냅샷 박제 → 리팩터링 후 바이트 동일 검증. 통과해야 다음 단계.
3. API 자연 게이트: `lang` 없는 요청은 기존 경로.
4. DB 추가 전용 (새 테이블·필드만, 기존 행 무변경).
5. 배포 순서: 서버 먼저 → 구버전 확장으로 실전 확인 → 확장 배포.
6. 릴리스 검증 기준(쓰기 경로 통과) + 기존 1563개 테스트 유지.

## 구현 단계 (커밋 단위)

1. 렌더러 주입 리팩터링 + 골든 스냅샷 (출력 무변화 증명)
2. **디버그 라인 집중 뷰 + `heard_spans` 서버 보강** (사용자 요청으로 앞당김)
3. romaji 렌더러 + `pron` dict 저장 + `lang` 파라미터 + 번역 레이어 테이블
   → ja 곡 × en 사용자 완성
4. 대각선 생략 + 확장 가드 언어 비교 → 보고된 버그 수정
5. 소스 Protocol + miraheze 어댑터 + 라이선스 attribution
6. 확장 `_locales` i18n + 서버 오류 코드
7. `ko_reading.py` + 라틴→가나 음차 → ko·en 곡 × ja/en 사용자 완성
   (유일한 신규 엔진, 격리)
8. 디버그 곡 전체 패널

## 테스트

- 단위: romaji 렌더러(촉음·장음·요음·조사 は/へ), ko_reading(연음·받침·ッ/ン),
  번역 레이어 CRUD, 소스 어댑터 파싱.
- 계약: 골든 스냅샷(한글 경로 무변화), `lang` 무지정 응답 = 현행 응답.
- 통합: worker 파이프라인이 세그먼트에 pron dict를 싣는지, 심판 판정이 전
  표기에 반영되는지.
- E2E: 3언어 요청 시나리오 (같은 곡을 ko→en→ja 순서로 열었을 때 각자 자기
  언어 수신 + 둘째 사용자 즉시 수신), 실행은 `.venv/Scripts/python.exe` +
  `127.0.0.1`.

## 참고 (탐색에서 확인된 사실)

- `target_lang`은 API(`translate.py:55`)→설정(`settings.py:819`)→확장 캐시 키
  (`content.ts:1133-1147`)까지 이미 관통한다. 확장 설정 UI에 ko/en/ja/zh 셀렉터
  기존재(`overlay.ts:1198`).
- CTC 엔진의 어댑터 선택·스크립트 커버리지·심판·heard는 완전 언어 중립.
- ko 하드코딩의 본진은 `kana_hangul.py`(169줄)·`latin_hangul.py`(768줄)와
  결정론 게이트 `_use_deterministic_pron`(`translator.py:514-532`),
  가나 오염 검사 `_KANA_RE`(`api/translate.py:14-49` — ja 사용자 발음을 파괴할
  가드이므로 script 인지형으로 수정 필요).
