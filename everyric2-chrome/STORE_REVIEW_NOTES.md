# Chrome 웹 스토어 심사 노트 / Chrome Web Store Review Notes

이 확장(`manifest.json`)이 요청하는 `host_permissions` 각각의 용도와, 심사자가 기능을
실제로 확인하려면 필요한 것을 설명합니다.

This document explains why this extension requests each of its `host_permissions`
entries, and what a reviewer needs in order to actually exercise the features.

---

## ⚠ 제출 전 필수: 심사자용 API 키를 대시보드에 입력할 것

**이 항목이 빠지면 심사자는 확장이 아무것도 하지 않는 것으로 봅니다.**

기본 설정의 API 키는 빈 문자열(`src/lib/settings.ts`)이고, 백엔드는 키 없는 요청을
거부합니다(실측: `GET https://everyric.moref.co/api/health` → `401
{"error":"unauthorized","hint":"personal key required"}`). 즉 확장을 설치만 하고 키를
넣지 않으면 **가사가 한 줄도 표시되지 않습니다.** 심사자가 그 상태를 보면 "기능이 동작하지
않는 확장"으로 판단합니다.

키가 필요한 이유(심사자에게 설명할 내용): 가사 정렬은 서버에서 GPU로 오디오를 분석하는
작업이라 한 곡당 수십 초의 연산이 듭니다. 무기명 공개 시 남용으로 서버가 마비되므로 키로
사용량을 제한합니다. 개인 식별 정보와는 무관하며, 이용자가 설정에 직접 입력한 값만
서버 인증 헤더로 전송됩니다(개인정보처리방침 2번 항목).

**해야 할 일**: 크롬 웹스토어 개발자 대시보드의 심사자용 자격증명 입력란(테스트 계정/로그인
정보 필드)에 **심사 전용 키와 사용법**을 적습니다. 사용법은 다음과 같이 쓰면 됩니다.

> 1. 유튜브에서 아무 노래 영상을 엽니다(예: `https://www.youtube.com/watch?v=s5Rkv_5Sbbo`).
> 2. 툴바의 Everyric 아이콘을 눌러 가사 패널을 엽니다.
> 3. 패널 우측 상단의 ⚙(설정)을 열고 **API key** 칸에 아래 키를 붙여넣습니다.
> 4. 패널이 자동으로 가사를 찾아 재생에 맞춰 한 줄씩 하이라이트합니다.
>
> API key: `(대시보드에 직접 입력)`

**키 값을 이 파일에 적지 마십시오.** 이 저장소는 공개되어 있습니다. 키는 대시보드
입력란에만 넣고, 심사가 끝나면 폐기·회전할 수 있는 전용 키를 쓰십시오.

**English**: The default API key is empty and the backend rejects unauthenticated requests
(`401 unauthorized`), so an installed extension shows no lyrics at all until a key is entered.
A reviewer seeing that state would conclude the extension is non-functional. Provide a
review-only key and the four steps above in the dashboard's reviewer-credentials field.
Lyrics alignment runs GPU audio analysis on the server (tens of seconds per song), so
anonymous access is rate-limited by key; the key is unrelated to any personal data and is only
sent as an auth header when the user enters one. **Do not put the key value in this file — this
repository is public.**

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

## `http://vocaro.wikidot.com/*`

**한국어**: 보컬로이드(Vocaloid) 곡의 가사 폴백 소스입니다. 저작권 있는 정식
음원이 일반 가사 DB(LRCLIB)나 자체 서버에 없을 때, 커뮤니티가 정리한 보카로 가사
위키에서 원문·발음·번역을 가져옵니다. 이 사이트는 HTTPS를 지원하지 않고 `https://`
요청을 `http://`로 리다이렉트하므로 평문 `http://`로 등록했습니다. 실측 결과 이
사이트는 CORS 응답 헤더(`Access-Control-Allow-Origin`)를 전혀 보내지 않아
`host_permissions` 없이는 확장이 응답을 읽을 수 없습니다(요청 자체는 가지만 응답이
차단됨). 호출은 background service worker에서만 발생하며, YouTube 페이지에 삽입되는
content script에서는 이 호스트를 호출하지 않습니다(타입 참조만 있음).

**English**: This is a fallback lyrics source for Vocaloid songs. When a song isn't
in the main lyrics database (LRCLIB) or our own server, the extension fetches
original text, pronunciation, and translation from a community-maintained Vocaloid
lyrics wiki. The site does not support HTTPS (it redirects `https://` to `http://`),
so it is registered as plain `http://`. We measured this endpoint directly: it sends
no `Access-Control-Allow-Origin` header at all, so without `host_permissions` the
extension's fetch would succeed but the response would be blocked by CORS. This host
is only called from the background service worker — not from the content script
injected into YouTube pages (the content script only imports TypeScript types from
that module, no runtime network call).

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

- `lrclib.net`(가사 DB), `api.everyric.com`(구버전 서버 주소, 현재 미사용)은 위
  목록에서 **제외**했습니다.
  - `lrclib.net`: 실제 사용 엔드포인트(`GET /api/search`)에 `Origin: chrome-extension://…`
    헤더로 요청 및 preflight(OPTIONS)를 보내 확인한 결과
    `Access-Control-Allow-Origin: *`, `Access-Control-Allow-Headers`에 확장이 보내는
    커스텀 헤더(`Lrclib-Client`)까지 포함되어 있어 CORS가 완전히 열려 있습니다.
    `host_permissions` 없이도 정상 동작하므로 제외했습니다.
  - `api.everyric.com`: 소스 코드(`src/`) 전체를 검색한 결과 실제로 호출하는 코드가
    없습니다(구버전 PRD 문서에만 남아 있던 흔적). 사용하지 않는 권한이라 제거했습니다.
