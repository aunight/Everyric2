# Everyric Chrome 확장

> **繁體中文**：本擴充功能來自 **onpe（[onpe5679](https://github.com/onpe5679)）**的
> [Everyric2 原始專案](https://github.com/onpe5679/Everyric2)；此目錄包含 fork 維護者的
> 修改，並依 Apache License 2.0 發布。
>
> **日本語**：この拡張機能は **onpe（[onpe5679](https://github.com/onpe5679)）**氏の
> [Everyric2 オリジナルプロジェクト](https://github.com/onpe5679/Everyric2)を基にした
> フォーク版で、Apache License 2.0 の下で公開されています。
>
> **English**: This extension is based on the
> [original Everyric2 project](https://github.com/onpe5679/Everyric2) by
> **onpe ([onpe5679](https://github.com/onpe5679))**. This directory includes fork-maintained
> modifications distributed under the Apache License 2.0.

YouTube / YouTube Music 위에 싱크 가사를 표시하는 MV3 확장 프로그램.

> **그냥 쓰고 싶다면** 이 문서는 필요 없습니다 — 저장소 루트의
> [README](../README.md#한국어-사용-안내) ([English](../README.md#english-guide) /
> [日本語](../README.md#日本語ガイド))에서 배포된 zip을 받아 설치하세요. 이 문서는
> 소스에서 빌드하거나 기여하려는 사람을 위한 것입니다.

## 빌드

```bash
npm install
npm run build     # typecheck + vite build → dist/
npm run dev       # watch 모드
```

## 설치 (개발용)

1. `npm run build`
2. Chrome → `chrome://extensions` → 개발자 모드 ON
3. **압축해제된 확장 프로그램을 로드합니다** → `everyric2-chrome/dist` 선택

## 사용법

- YouTube/YT Music에서 영상을 재생하면 가사 패널이 자동으로 열립니다.
- 패널을 닫았다면 **툴바의 Everyric 아이콘**을 눌러 다시 열 수 있습니다.
- 헤더를 드래그해 위치 이동, 우하단 모서리를 드래그해 크기 조절.
- 가사 줄 클릭 → 해당 시점으로 이동. 푸터의 ±0.1s 버튼으로 싱크 미세 조정.
- **PiP 버튼**(Chrome 116+): 가사만 별도 항상-위 창으로 띄웁니다.
  다른 탭/앱으로 이동해도 가사가 계속 보입니다.
- **가라오케 음정 레인**: Everyric 서버가 생성한 싱크에 멜로디(`notes`)가 포함되면
  PiP 하단에 노래방식 오선지가 자동 표시됩니다 — 현재 라인의 음표(계이름 라벨,
  부른 만큼 채색)와 그 아래 가사 음절·발음·번역이 타이밍에 맞춰 정렬됩니다.
  노트는 서버가 정렬된 음절 경계에서 잘라 가사 하이라이트와 타이밍이 일치하고,
  레인 페이지는 패널과 같은 라인 인덱스로 구동됩니다. 레인이 켜지면 기존 현재-라인
  표시(스테이지)는 중복이라 자동으로 숨겨지며, 레인 위 가로 그립을 드래그하면
  높이를 조절할 수 있습니다(설정에 저장). 설정(⚙)의 "가라오케 음정 바 (PiP)"로
  끌 수 있습니다 (없는 곡은 기존 UI 그대로).

## 설정 (패널 ⚙)

- 자동 가사 검색 / 폰트 크기 / 테마
- **가사 번역 표시** + 번역 언어(ko/en/ja/zh) — 원문 아래에 번역 표시. ko/en/ja는
  제목바의 언어 칩으로도 바로 전환할 수 있고, 곡의 원어와 같은 언어를 고르면 번역
  자체가 자동으로 생략됩니다
- **발음 표기 표시** (기본 ON) — 원문과 번역 사이에 발음을 표시(한글·로마자·가나 중
  자동 선택). 패널·PiP(가라오케) 모두 적용, 설정에서 끌 수 있음.
  PiP 가라오케 레인에서는 부른 만큼 색이 차오르며, 서버가 음절 타이밍(`pron_segments`)을
  주면 음절 경계에서 정확히 스텝하고 없으면 라인 진행률 그라데이션으로 폴백
- **PiP 중에도 패널 가사 유지** (기본 ON) — 끄면 PiP를 열 때 패널은 안내 화면으로 전환
- 싱크 서버 URL / API 키 — 기본 서버(`everyric.moref.co`)를 쓰면 **API 키 칸은 비워
  두세요**. 값을 넣지 않으면 인증 헤더 자체를 안 보내고, 그 상태로 모든 기능이
  동작합니다. 이 칸은 자체 호스팅이나 별도 할당량을 받은 경우에만 채웁니다 — 틀린
  값을 넣으면 오히려 401로 막힙니다
- **디버그 정보 표시** (기본 OFF) — 패널 하단에 내부 상태 표시
  (`vid=… src=… line=… / t=… video=OK|MISMATCH …`). 서버 싱크에서는 현재 구간
  판정(`zone=가창|간주·무성|추임새★`)과 라인 진단(발성 비율 `act=…%`, 반복구
  클램프 여부 `CLAMP`)도 함께 표시되고, 글자별 CTC 정렬 신뢰도가 색으로 나타납니다
  (빨강 <0.5, 노랑 <0.75 — 패널과 PiP 레인 공통). 문제를 신고할 때 이 값을 함께
  알려주면 진단이 빨라집니다.

## 싱크 생성·번역 (로컬 서버)

타임싱크가 없는 곡은 로컬 Everyric 서버로 싱크를 생성할 수 있습니다.

```bash
# 저장소 루트에서
uvicorn everyric2.server.main:app --port 8000
```

서버가 켜져 있으면 패널의 **✨ 싱크 생성** 버튼이 활성화됩니다.
가사가 아예 없는 곡은 가사를 직접 붙여넣어 생성할 수 있습니다.
서버 주소는 패널 설정(⚙)에서 변경할 수 있습니다. 기본값은 `https://everyric.moref.co`이고,
자체 호스팅 서버(`http://localhost:8000`)로 바꾸려면 **확장의 권한 설정 페이지에서 로컬 접근을
한 번 허용**해야 합니다 — 그 호스트 권한은 `optional_host_permissions`라서 설치 시 부여되지
않습니다(기본 서버를 쓰는 사용자에게 불필요한 권한을 주지 않기 위한 것입니다).

가라오케 노트의 음정 정확도를 위해 서버는 demucs 보컬 분리 후 FCPE로 f0를
추출합니다 (`uv pip install demucs` 필요 — 없으면 믹스에서 추출로 폴백하는데,
반주 피치가 섞여 정확도가 크게 떨어집니다. CPU 기준 곡당 약 1분 추가).
번역은 `GEMINI_API_KEY`가 있으면 Gemini, 없으면 무료 구글 웹 번역으로 폴백합니다.

## 구조

```
src/
  content.ts        오케스트레이션 (내비게이션 감지, 상태 관리)
  background.ts     메시지 허브 (API 호출, 툴바 토글)
  lib/              곡 감지 / LRC 파서 / 싱크 엔진 / API 클라이언트 / 설정
  ui/               오버레이 패널(Shadow DOM) + Document PiP 창
public/
  overlay.css       패널·PiP 공용 스타일 (web_accessible_resources)
```

가사 소스 우선순위: **Everyric 서버**(단어 타이밍 지원) → **LRCLIB** 싱크 → LRCLIB 일반 가사
→ 보카로 가사 위키 두 곳(**vocaro.wikidot.com** — 원문+한국어 번역,
**vocaloidlyrics.miraheze.org** — 원문+로마자 발음+영어 번역). 두 위키 중 어느 쪽을
먼저 볼지는 번역 언어 설정 기준입니다 — 한국어면 vocaro를, 그 외(영어·일본어 등)면
miraheze를 먼저 봅니다(사용자 언어와 맞는 위키를 우선).

위키 폴백은 공개 라이브러리에 없는 보컬로이드 곡에서 동작합니다. vocaro 곡 페이지는
(1) ASCII 제목의 슬러그 직접 추측, (2) 수록곡 일람 인덱스(제목 첫 글자 기준, 24시간
캐시) 매칭으로 찾습니다. 두 위키 모두 CC 라이선스(vocaro는 CC BY 4.0, miraheze는
CC BY-SA 4.0)이므로 푸터의 위키 배지를 클릭하면 출처 페이지가 열립니다. 위키의 사람
번역이 가사에 포함되므로 이 소스에서는 서버 기계번역을 호출하지 않습니다.
참고: 일본어 원제 그대로의 영상은 매칭이 어려울 수 있습니다 — 패널의 "다시 검색"에
곡명을 다시 입력하면 위키에서 다시 찾습니다.

위키 가사로 "싱크 생성"을 하면 위키 페이지를 videoId별로 기억해 두었다가,
서버 싱크가 완성된 뒤에도 **발음 표기와 사람 번역을 라인 텍스트 매칭으로 다시
입힙니다** (기계번역으로 덮어쓰지 않음). 페이지를 새로 열어도 유지됩니다.

## E2E 테스트

```bash
node scripts/smoke-test.mjs        # 목업 서버 기반 스모크 (EVERYRIC_SMOKE_* 모드)
node scripts/real-e2e.mjs          # 실서버(:8000) + 실제 영상 전체 여정
```

`real-e2e.mjs`는 실서버가 :8000에 떠 있어야 하며, 기본값으로 로키 공식 MV에서
「빈 상태 → "로키" 재검색 → 보카로 위키(발음/번역) → 싱크 생성(다운로드+CTC+FCPE)
→ 하이라이트 → PiP 음정 바 → 넓은 창 레이아웃」을 순서대로 검증합니다.
