# AE 패널 — 글자 커팅 + 서버 조회 + 임베디드 런타임 설계 스펙 (2026-07-27)

배경: `everyric2-ae` 패널(Everyric Studio 2.0.0)은 서버를 전혀 모른다. `src/panel/main.ts`에
API 호출이 하나도 없고, 경로는 로컬 CLI spawn(`local-sync.ts`)과 JSON 파일 로드뿐이다. 그 사이
서버는 잡 큐·워커·`line_meta`·링크·vocaro까지 자랐다. 패널이 뒤처진 게 아니라 **다른 트랙**이었다.

이 스펙은 셋을 한 번에 다룬다: **A. 글자 커팅**(신규, 핵심), **B. 서버 조회**(얇게),
**C. 임베디드 런타임**(배포).

전제(사용자 확정):
- 오디오는 **로컬 파일 중심**. 유튜브는 부차.
- 곡당 정렬은 **1회**, 이후엔 나온 싱크 데이터를 재사용한다. → 실질 작업량은 커팅에 있다.
- 발음은 **작업자 참고용**(일본곡 작업하는 한국인). 최종 렌더 자막이 기본값이 아니다.
- 자른 조각의 배치 기본값은 **글자가 있던 자리 유지**, 원위치 유지는 토글.

---

## A. 글자 커팅

### A-1. 왜 이게 자동 분할보다 맞는가

정렬 결과는 이미 글자 단위 `atoms`를 들고 있다(`planner.ts:51 normalizeAtoms`가
`atoms/words/word_segments/pron_segments`를 받고, 없으면 라인 구간을 가시 글자 수로 균등 분배).
따라서 "글자 사이"는 임의 지점이 아니라 **시각이 이미 확정된 경계**다. 자를 위치를 고르는 순간
`앞 글자.end`/`뒤 글자.start`가 나오므로 분리와 타이밍 정렬이 같은 동작이 된다.

자동 분할(`phraseTargetChars`/`maxTokensPerBlock`/`pauseThreshold`)은 곡마다 원하는 호흡이 달라
프리셋이 못 맞춘다. 커팅은 그 추측을 없앤다. 기존 두 배치 모드는 **그대로 두고**, 커팅은 그
뒤에 붙는 단계다.

### A-2. 순수 로직 — `src/panel/cutter.ts`

패널 UI와 ExtendScript 양쪽에서 쓰지 않고, **순수 함수로 분리**해 `scripts/run-tests.mjs`에서
노드로 검증한다(`planner.ts`와 같은 방식).

```ts
interface CharTiming { char; start; end; visible; interpolated }
interface CutSession { layerIndex; layerName; text; inPoint; outPoint;
                       chars: CharTiming[]; matchQuality; pronunciation?; lineText? }
interface CutPoint  { index; time; auto }        // index: 1..chars.length-1
interface CutPiece  { text; start; end; charStart; charEnd }
```

- `buildCutSession(layer, document)` — 레이어를 싱크 라인에 매칭하고 글자별 시각을 배정
- `defaultCutTime(chars, index)` — 앞 글자 `end`와 뒤 글자 `start`의 중간
- `toggleCut(cuts, index, chars)` — 없으면 생성, 있으면 제거(**되붙이기**)
- `clampCutTime(session, cuts, index, time)` — 이웃 컷과 레이어 경계 안으로 가둠(드래그용)
- `computePieces(session, cuts)` — 조각 목록. 첫 조각 `start` = 레이어 `inPoint`,
  마지막 조각 `end` = 레이어 `outPoint`(원본 구간을 넘기지 않는다)

**매칭 순서**(`matchQuality`로 UI에 표시):
1. `exact` — 공백 정규화 후 라인 텍스트와 완전 일치
2. `substring` — 레이어 텍스트가 라인 텍스트의 부분 문자열(= 배치 모드가 만든 블록).
   해당 오프셋만큼 atom을 슬라이스
3. `time` — 시간 겹침이 가장 큰 라인에서 레이어 구간에 드는 atom만
4. `none` — 매칭 실패. 레이어 `in`~`out`을 가시 글자 수로 균등 분배(폴백이지 정답이 아님을 UI에 명시)

**글자↔atom 대응**: atoms는 가시 글자에만 대응한다(공백 없음). 레이어 텍스트의 가시 글자를
순서대로 atom에 붙이고, 공백·개행은 `interpolated=true`로 이웃에서 보간한다. 가시 글자 수와
atom 수가 어긋나면 비례 배분한다.

### A-3. 적용 — `host.ts` `everyricSplitTextLayer`

입력: `{ layerIndex, pieces, keepOriginalPosition }`. 전체가 하나의 `beginUndoGroup`.

1. 원본 레이어를 조각 수만큼 `duplicate()` — 이펙트·마스크·표현식·부모 관계가 자동 승계된다
2. 각 복제본의 `sourceText`를 조각 텍스트로, `inPoint`/`outPoint`를 조각 시각으로
3. `keepOriginalPosition=false`(기본)일 때만 x 좌표 재계산 → A-4
4. 원본 삭제. 복제본 `comment`에 `EV2CUT|<원본이름>|<조각순번>` 기록
5. 잠긴 레이어·텍스트 아닌 레이어는 거부하고 사유를 돌려준다

### A-4. x 좌표 — 누적 접두사 폭 측정

ExtendScript에 글자별 위치 API는 없다. 대신 `sourceRectAtTime`으로 우회한다(이미
`host.ts:244 setTextAnchor`가 쓰는 API).

측정 전용 임시 텍스트 레이어를 원본 스타일(font/fontSize/tracking/leading)로 만들고,
누적 접두사(`君`, `君の`, `君の名`…)를 넣어가며 폭을 잰 뒤 삭제한다. 조각 경계마다 한 번이면
되므로 보통 2~4회다.

`justification`에 따라 조각 i의 x를 정한다(원본 x = `x0`, 전체 폭 `W`, 접두사 폭 `p_i`):
- `left`: `x_i = x0 + p_i`
- `center`: `x_i = x0 - W/2 + p_i + w_i/2`
- `right`: `x_i = x0 - W + p_i + w_i`

**커닝 오차**: 접두사 폭의 합이 전체 폭과 정확히 같지 않을 수 있다(글자쌍 커닝). 누적 접두사를
재는 방식이라 오차는 경계 한 쌍에만 걸리고 일본어·한국어에서는 거의 0이다. 라틴 문자에서
얼마나 벌어지는지는 **실측으로 확인해야 하는 항목**이며, 벌어지면 `keepOriginalPosition`을
권장값으로 돌린다.

### A-5. 경계 조건

- **개행 포함 레이어**: 줄 단위로 먼저 다루도록 안내하고 커팅은 거부(x 계산이 줄마다 달라진다)
- **`sourceText` 키프레임 있는 레이어**: 거부(복제 시 키가 따라와 조각마다 잘못된 텍스트가 뜬다)
- **잠긴 레이어**: 거부
- **조각이 1개**(컷 없음): 적용 버튼 비활성

### A-6. UI

패널에 커팅 섹션. 글자를 `span`으로 렌더하고 사이마다 클릭 타깃(캐럿)을 둔다. 각 글자 아래에는
**그 글자의 시각**을 적는다. 컷 표시된 캐럿을 다시 누르면 되붙이고, 컷 경계는 좌우 드래그로
미세조정한다(Shift로 세밀하게).

발음은 **글자마다가 아니라 라인 아래에 한 줄로** 붙는다. 저장된 `pronunciation`이 라인 단위
문자열이라 글자와 1:1로 나눌 근거가 없기 때문이다(`pron_segments`가 있는 곡도 라인 텍스트와
글자 수가 맞지 않는다). 참고용이라는 목적에는 이걸로 충분하다 — 어디서 끊을지 판단할 때 필요한
것은 줄 전체의 읽기다.

추정치는 눈에 보이게 구분한다: 단어 atom을 쪼갠 시각, 공백을 메운 시각, 라인 매칭 실패 시
균등 배분한 시각은 다른 색으로 표시하고, 매칭 품질(정확·부분·시간·없음)을 배지로 띄운다.

발음을 레이어로 굽는 것은 기본값이 아니다.

발음을 레이어로 굽는 것은 기본값이 아니다(참고용이라는 전제). 필요하면 별도 옵션으로 둔다.

---

## B. 서버 조회 — `src/panel/server-client.ts`

로컬 오디오가 주 경로이므로 **조회만** 붙인다.

- 설정에 서버 URL(기본 `https://everyric.moref.co`)과 선택 API 키
- 유튜브 URL/ID 입력 → `GET /api/sync/{video_id}` → `timestamps` + `line_meta`(번역·발음) +
  `attribution`을 `SyncDocument`로 변환(`normalizeSyncPayload`가 `timestamps` 키를 이미 읽는다)
- 실패는 조용히 로컬 경로로 되돌린다

**붙이지 않는 것**(의도적):
- `POST /api/sync/generate` — `video_id`가 필수라 로컬 오디오에 못 쓰고, 유튜브 곡은 크롬
  확장이 이미 하는 일이다
- `POST /api/translate` — 로컬 CLI `--translate`로 직접 처리한다. 어차피 NIM 키가 필요한 건
  같은데 경유만 늘어난다

---

## C. 임베디드 런타임 + ZXP

`lipsync-ae`(`tools/build_embedded.ps1`, `tools/make_zxp.ps1`)의 구조를 가져오되 **모델 위치에서
의도적으로 갈라진다**.

```
everyric2-ae/runtime/        python 3.11 embeddable — ZXP에 동봉
%LOCALAPPDATA%\Everyric\     엔진 패키지(pylibs) — 첫 실행 설치, wheel 교체로 갱신
%USERPROFILE%\.cache\huggingface\   모델 — 어느 업데이트도 건드리지 않는다
```

### C-1. 금지 조항 — 모델 캐시를 확장 폴더 안에 두지 않는다

`lipsync-ae`는 `<확장>\pylibs`, `<확장>\models`를 쓴다. 확장을 지우면 같이 사라져 깔끔하지만,
**확장을 업데이트할 때마다 모델을 다시 받는다**. everyric2는 모델이 수 GB이고 패널·엔진 모두
자주 갱신되므로 이 방식을 쓸 수 없다.

현재 `engine-install.ts`는 `HF_HOME`을 **설정하지 않아** 모델이 사용자 홈의 기본 HF 캐시로 간다.
이 성질이 그동안 엔진을 수없이 갈아끼우고도 캐시가 살아남은 이유다(개발 PC 실측:
`~/.cache/huggingface` 47GB). **이 동작을 유지한다.** `HF_HOME`/`HF_HUB_CACHE`/`--cache-dir`을
확장 폴더나 `%LOCALAPPDATA%\Everyric` 아래로 돌리는 변경은 금지한다.

재다운로드가 정당한 경우는 셋뿐이다: 캐시 경로 변경(위에서 금지), 엔진이 참조하는 모델
ID/리비전 변경, torch 의존성 핀 변경(모델은 아니지만 ~2.5GB). 셋 다 아니면
**엔진 업데이트 = wheel 하나 교체**다.

### C-2. python 조달 경로 교체

지금은 uv가 python을 내려받는다(`engine-install.ts` `UV_DOWNLOAD_URL` → `uv python install`).
이를 ZXP에 동봉한 임베디드 런타임으로 대체한다. 첫 실행 다운로드가 그만큼 줄고 uv 부트스트랩
실패 지점이 사라진다. 패키지 설치에는 계속 uv(또는 임베디드 pip)를 쓴다.

개발 환경 배려: 설정의 `pythonPath`로 기존 `.venv`를 그대로 가리킬 수 있게 유지한다. 임베디드
런타임은 **배포용**이며 개발 중 중복 설치를 강요하지 않는다.

### C-3. 업데이트

기존 `updater.ts`의 `latest.json` 폴링을 그대로 쓴다. 엔진은 `engine-v*` 태그의 wheel,
패널은 `ae-v*` 태그의 ZXP.

---

## 검증

곡당 정렬이 1회뿐이라 **커팅이 실질 작업량 전부**다. 검증도 거기에 집중한다.

1. `cutter.ts` 순수 함수 단위 테스트(`scripts/run-tests.mjs`) — 매칭 4단계, 되붙이기,
   경계 clamp, 조각 경계가 원본 `in`/`out`을 넘지 않음
2. `npm run build`(typecheck + test + 번들) 통과
3. 실제 AE에서 곡 하나를 커팅하고 조각 위치·타이밍을 눈으로 확인 —
   `artifacts/real-song-test/`에 선례가 있다
4. A-4 커닝 오차 실측: 라틴/일본어/한국어 각각에서 접두사 폭 합과 전체 폭의 차이

**보고 금지**: 3번을 통과하지 않은 상태에서 "커팅이 동작한다"고 말하지 않는다.
