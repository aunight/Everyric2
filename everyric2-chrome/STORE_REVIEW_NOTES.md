# Chrome 웹 스토어 심사 노트 / Chrome Web Store Review Notes

이 확장(`manifest.json`)이 요청하는 `host_permissions` 각각의 용도와, 심사자가 기능을
실제로 확인하려면 필요한 것을 설명합니다.

This document explains why this extension requests each of its `host_permissions`
entries, and what a reviewer needs in order to actually exercise the features.

---

## 심사자 접근: 자격증명이 필요하지 않습니다

**설치 직후 아무것도 입력하지 않은 상태에서 모든 기능이 동작합니다.** 심사자용 계정도
API 키도 제공할 것이 없습니다.

실측 (2026-07-26, 인증 헤더를 전혀 보내지 않은 요청):

| 요청 | 응답 |
| --- | --- |
| `GET /health` | `200 {"status":"healthy",…}` |
| `GET /api/sync/8JRuowZtRBc` | `200` — 싱크 가사 데이터를 그대로 반환 |
| `POST /api/sync/generate` `{}` | `422` (`video_id` 필드 누락) |
| `POST /api/sync/regenerate` `{}` | `422` |
| `POST /api/translate` `{}` | `422` |

생성 계열이 `401`이 아니라 **`422`(입력 검증 실패)** 라는 것이 요점입니다 — 인증 단계를
통과하고 본문 검증에서 멈췄으므로, 올바른 본문을 보내면 키 없이 실행됩니다. GPU 연산을
일으키지 않고 인증 여부만 확인하기 위해 일부러 빈 본문으로 측정했습니다.

### 사용량 제한을 키 없이 어떻게 하는가

서버가 **연결 IP로부터 되돌릴 수 없는 해시**(솔트 포함)를 만들어 그것을 제한 단위로
씁니다. 확장은 이 값을 만들지도, 보내지도, 알지도 못합니다.

- 가사 **조회**는 제한하지 않습니다(응답 비용이 무시할 만합니다)
- 싱크 **생성**만 셉니다 — 이용자당 15건/일, 서버 전역 안전판 별도
- IP 원문은 저장하지 않습니다(솔트를 넣은 해시만 남습니다)
- 용도는 사용량 제한뿐이며 다른 목적에 쓰지 않습니다

**따라서 이 확장이 새로 수집하거나 전송하는 이용자 데이터는 없습니다.** 데이터 유형 신고
항목을 고를 때 이 사실이 기준입니다 — 확장이 보내는 자격증명이 없으므로 «인증 정보»가
아니며, 서버 쪽 IP 파생값은 이용자가 어떤 웹사이트에 접속하든 서버가 이미 보는 값입니다.

### ⚠ API 키 칸은 **비워 두십시오** — 값을 넣으면 오히려 막힙니다

설정(⚙)의 **API key** 칸은 **선택 사항**이며, 비워 두면 인증 헤더 자체를 보내지 않습니다
(개인정보처리방침 2번 항목). 자체 호스팅 서버를 쓰거나 별도 할당량을 받은 이용자를 위한
칸입니다.

**중요**: 서버는 «키 없음»과 «키가 있는데 틀림»을 다르게 처리합니다. 빈 칸은 익명 요청으로
허용되지만, **임의의 값을 넣으면 명시적으로 거부**됩니다. 새 프로필로 실측했습니다:

| 키 칸 | 결과 |
| --- | --- |
| 비어 있음(기본값) | 가사 134줄 + 발음 134줄 정상 표시, 상태 표시 초록, 오류 배너 없음 |
| 임의의 값 입력 | `🔑 API 키 인증에 실패했어요 (HTTP 401)` 배너 |

따라서 **이 칸을 건드리지 마십시오.** 값을 넣어야 동작하는 것이 아니라, 넣으면 동작하지
않습니다. (틀린 키로 401이 떠도 이미 표시된 가사는 지우지 않습니다 — 실패가 화면을 비우지
않도록 설계했습니다.)

### 키 없이 되는 것 (새 프로필 실측)

- **기존 싱크 조회** — 서버에 이미 있는 곡은 즉시 표시(원문·한글 발음·카라오케 하이라이트)
- **가사 검색** — LRCLIB(`lrclib.net`)과 보카로 가사 위키 후보가 모두 정상.
  `/api/vocaro/match`도 키 없이 `200`입니다
- **신규 싱크 생성** — `POST /api/sync/generate`가 `401`이 아니라 `422`(본문 검증)이므로
  인증을 통과합니다. 올바른 본문이면 키 없이 실행되며, 서버 자원을 쓰는 이 경로만
  사용량 제한을 받습니다(이용자당 15건/일)

### 기능 확인 절차

> 1. 유튜브에서 노래 영상을 엽니다 — 예: `https://www.youtube.com/watch?v=s5Rkv_5Sbbo`
>    (싱크가 이미 준비된 곡이라 즉시 표시됩니다).
> 2. 가사 패널이 자동으로 열립니다. 닫혔다면 툴바의 Everyric 아이콘을 누릅니다.
> 3. 재생하면 현재 줄이 하이라이트되고, 원문 아래에 한글 발음·번역이 함께 표시됩니다.
> 4. 싱크가 없는 곡에서 **✨ 싱크 생성**을 누르면 서버가 오디오를 분석합니다(곡당 수십 초).

**English**: **No reviewer credentials are needed.** With nothing entered after install, every
feature works. Measured with no auth header at all: `GET /health` → `200`;
`GET /api/sync/8JRuowZtRBc` → `200` with lyrics data; `POST /api/sync/generate` with `{}` →
`422` (missing `video_id`) rather than `401`, i.e. it clears authentication and stops at body
validation. Rate limiting uses a **salted, non-reversible hash of the connecting IP** computed
server-side — the extension neither creates, sends, nor sees it. Lookups are unlimited; only
sync generation is counted (15/day per user). The raw IP is not stored. **The extension
therefore collects and transmits no new user data**, which is the basis for the data-disclosure
answers: there is no credential the extension sends, and the server-side IP-derived value is
something any server already sees. The **API key** field in settings is optional (for
self-hosting or a separate quota); left empty, no auth header is sent.

---

## `https://everyric.moref.co/*`

**한국어**: 이 확장의 기본(디폴트) 백엔드 서버 주소입니다. 사용자가 설정을 전혀
바꾸지 않아도 이 서버에서 시간 동기화 가사(sync) 조회/생성, 가사 번역, 발음 표기
데이터를 가져옵니다. 확장의 핵심 기능(YouTube 재생 중 싱크 가사 표시)이 이 호스트
없이는 동작하지 않습니다.

**English**: This is the extension's default backend server. Even with no settings
changed, the extension calls this host to fetch/generate time-synced lyrics,
translations, and pronunciation data. The extension's core feature (showing
time-synced lyrics while a YouTube video plays) does not work without this host.

---


## API 권한: `storage`, `notifications`

**한국어**:
- `storage` — 설정(서버 URL·표시 언어·발음 표기 여부 등)과 패널 위치, 진행 중인 생성
  작업 목록, 위키 조회 캐시를 `chrome.storage.local`에 저장합니다. 외부로 전송하지
  않습니다.
- `notifications` — 싱크 **생성**은 곡당 수십 초가 걸리므로, 완료 시 OS 알림 하나를
  띄워 다른 탭·창에 있어도 알 수 있게 합니다(`background.ts`의 NOTIFY 처리, 같은 잡은
  같은 id로 갱신되어 중복 알림이 없습니다). 생성을 요청한 경우에만 발생합니다.

**English**:
- `storage` — persists settings (server URL, display language, pronunciation toggle,
  …), panel position, the list of in-flight generation jobs, and a wiki lookup cache in
  `chrome.storage.local`. Nothing is transmitted.
- `notifications` — sync **generation** takes tens of seconds per song, so one OS
  notification is shown on completion so the user notices even from another tab or
  window (NOTIFY handler in `background.ts`; the same job updates the same notification
  id, so there are no duplicates). Only fires for generations the user requested.

---

## `http://localhost:8000/*`, `http://127.0.0.1:8000/*` — **optional_host_permissions**

**이 둘은 설치 시 부여되지 않습니다.** `optional_host_permissions`에 선언되어 있고,
자체 호스팅을 쓰려는 사용자가 확장의 권한 설정 페이지에서 직접 허용해야만 부여됩니다.
설치 화면 권한 목록에도 표시되지 않습니다.

**한국어**: 사용자가 이 프로젝트의 오픈소스 백엔드 서버(`everyric2/server`, FastAPI)를
자신의 컴퓨터에서 직접 구동해 확장에 연결할 수 있게 하는 기능입니다. 기본 서버는
`everyric.moref.co`이고(`src/lib/settings.ts`), 대부분의 사용자는 이 권한을 부여받지
않은 채로 확장의 모든 기능을 씁니다. 자체 호스팅을 원하는 사용자만 옵션 페이지
(`src/options.html`)의 버튼으로 허용하고, 같은 페이지에서 언제든 철회할 수 있습니다.

권한이 없는 상태에서 로컬 주소로 요청하려 하면 확장이 **요청을 보내기 전에 막고**
"호스트 권한 없음"이라고 표시합니다(`src/lib/host-permissions.ts`의
`localPermissionBlock`). 권한 없이 fetch를 시도하면 실패가 네트워크 오류로 분류돼
사용자가 서버를 의심하게 되므로, 원인을 정확히 말하도록 설계했습니다.

두 주소를 모두 선언한 이유: 요청은 항상 `127.0.0.1`로 정규화되어 나가지만(Windows에서
`localhost`가 IPv6를 먼저 시도해 요청마다 지연이 붙습니다), 정규화 규칙이 바뀌거나 이
권한이 필수였던 이전 버전에서 이미 허용된 경우에도 목록에 보이고 철회할 수 있어야 합니다.
**허용은 실제로 필요한 하나만 요청합니다** — 두 주소를 한꺼번에 부여받지 않습니다.

**English**: These let a user run this project's open-source backend
(`everyric2/server`, FastAPI) on their own machine and point the extension at it.
**Neither is granted at install time** — they are declared under
`optional_host_permissions` and are only granted if the user explicitly allows them on
the extension's options page (`src/options.html`), which also offers revocation. The
default server is `everyric.moref.co`, so the vast majority of users never receive
these permissions and still get every feature.

If the server URL points at a local address without the permission, the extension
**blocks the request before sending it** and reports "no host permission" rather than
letting the fetch fail and be misclassified as a network error — otherwise the user
would suspect their server while the real cause is invisible
(`localPermissionBlock` in `src/lib/host-permissions.ts`).

Both forms are declared because requests are always normalised to `127.0.0.1` (on
Windows, `localhost` resolves to IPv6 first and adds latency per request), yet the
declaration must remain visible and revocable if that rule changes or if the permission
was already granted by an older version where it was required. **Only the one pattern
actually needed is requested** — the extension never asks for both at once.

---

## 확인/실측 방법 요약 (참고용)

- `lrclib.net`(가사 DB), `vocaro.wikidot.com`(한국어권 가사 위키),
  `vocaloidlyrics.miraheze.org`(영어권 가사 위키), `api.everyric.com`(구버전 서버 주소,
  현재 미사용)은 위 목록에서 **제외**했습니다.
  - `vocaro.wikidot.com`: 1.5.4까지 host 권한으로 선언했으나(위키가 CORS 헤더를 전혀
    보내지 않아 직접 조회에 필요했음), 1.5.5에서 위키 조회를 백엔드 서버 프록시
    (`/api/vocaro/page`·`/index`)로 옮기고 권한을 제거했습니다. 확장은 이제 이
    호스트에 어떤 요청도 보내지 않습니다.
  - `vocaloidlyrics.miraheze.org`: 1.5.0~1.5.3에서는 host 권한으로 선언했으나,
    MediaWiki API의 익명 CORS(`origin=*` 파라미터, 실측 2026-07-28:
    `access-control-allow-origin: *`)로 권한 없이 응답을 읽을 수 있음을 확인하고
    1.5.4에서 제거했습니다. 확장 코드는 이미 모든 호출에 `origin=*`를 싣고 있어
    기능 변화가 없습니다(`src/lib/miraheze.ts`).
  - `lrclib.net`: 실제 사용 엔드포인트(`GET /api/search`)에 `Origin: chrome-extension://…`
    헤더로 요청 및 preflight(OPTIONS)를 보내 확인한 결과
    `Access-Control-Allow-Origin: *`, `Access-Control-Allow-Headers`에 확장이 보내는
    커스텀 헤더(`Lrclib-Client`)까지 포함되어 있어 CORS가 완전히 열려 있습니다.
    `host_permissions` 없이도 정상 동작하므로 제외했습니다.
  - `api.everyric.com`: 소스 코드(`src/`) 전체를 검색한 결과 실제로 호출하는 코드가
    없습니다(구버전 PRD 문서에만 남아 있던 흔적). 사용하지 않는 권한이라 제거했습니다.
