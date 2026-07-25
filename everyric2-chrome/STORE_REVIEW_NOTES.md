# Chrome 웹 스토어 심사 노트 / Chrome Web Store Review Notes

이 확장(`manifest.json`)이 요청하는 `host_permissions` 4개 각각의 용도를 설명합니다.
심사자가 "왜 이 호스트가 필요한가"를 물으면 아래 내용을 그대로 제출/답변에 사용하세요.

This document explains why this extension requests each of its 4 `host_permissions`
entries. Use the text below directly when the reviewer asks "why is this host needed".

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

## `http://localhost:8000/*`, `http://127.0.0.1:8000/*`

**한국어**: 사용자가 이 프로젝트의 오픈소스 백엔드 서버(`everyric2/server`, FastAPI)를
자신의 컴퓨터에서 직접 구동해 확장에 연결할 수 있게 하는 기능입니다. 확장 설정 패널
(⚙)에서 서버 URL을 `http://localhost:8000`으로 바꾸면, 개발자나 자체 호스팅을 원하는
사용자가 시간 동기화 가사 생성(로컬 음원 분석 기반)과 번역을 자신의 컴퓨터에서 실행할
수 있습니다. 기본값이 아니며(기본값은 `everyric.moref.co`), 사용자가 명시적으로 설정을
변경해야만 사용됩니다. 두 주소(`localhost`, `127.0.0.1`)는 같은 로컬 서버를 가리키는
동의어로, 브라우저/OS 환경에 따라 어느 쪽으로 해석될지 달라 둘 다 등록했습니다.

**English**: This enables users to run this project's open-source backend server
(`everyric2/server`, FastAPI) on their own machine and point the extension at it.
Via the extension's settings panel (⚙), a user can change the server URL to
`http://localhost:8000` to generate time-synced lyrics (via local audio analysis) and
translations locally instead of using our hosted server. This is not the default
(the default is `everyric.moref.co`) and only takes effect if the user explicitly
changes the setting. Both `localhost` and `127.0.0.1` are registered because,
depending on the browser/OS network stack, either form may be used to reach the same
local server.

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
