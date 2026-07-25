# 크롬 웹스토어 리스팅 텍스트

대시보드에 그대로 붙여넣는 **공개** 문구다. 심사자용 비공개 노트는 `STORE_REVIEW_NOTES.md`에
따로 있다(권한 정당화 등) — 그건 사용자에게 보이지 않는다.

**왜 이 문서가 필요한가**: 정책 대조에서 가장 큰 반려 위험으로 나온 것이
`manifest.json`의 description이 "가사 표시"만 말하고 **번역·발음 표기·마이크 음정·PiP·멜로디
레인을 하나도 언급하지 않는다**는 점이었다. 특히 **마이크**는 심사자가 켜 보면 브라우저가
실제로 권한 프롬프트를 띄우는데, 리스팅에 설명이 없으면 "공개하지 않은 기능"으로 읽힌다.
2026-08-01 시행 개정이 "수집 데이터는 공개한 단일 목적에 엄격히 필요한 것"을 요구하므로,
**부가 기능이 어떻게 그 하나의 목적에 부수하는지**를 리스팅에서 말해 두어야 한다.

단일 목적은 **"유튜브에서 재생 중인 곡의 가사를 시간에 맞춰 보여주는 것"** 하나다. 아래
문구는 모든 기능을 그 목적의 표현 방식으로 배치한다 — 없는 사실을 쓰지 않고, 있는 기능을
빠뜨리지도 않는다.

---

## 이름 (그대로 유지, 36자 / 제한 75자)

```
Everyric - Synced Lyrics for YouTube
```

## 짧은 설명 (manifest description, 122자 / 제한 132자 — 그대로 유지)

```
Display time-synced lyrics for any song on YouTube. Karaoke-style lyric highlighting with community-powered lyrics timing.
```

---

## 상세 설명 (한국어)

```
유튜브에서 재생 중인 곡의 가사를 노래에 맞춰 한 줄씩 보여줍니다. 지금 부르는 자리가
음절 단위로 채워져, 가사를 눈으로 따라가며 함께 부를 수 있습니다.

■ 하는 일

· 재생 중인 곡의 가사를 찾아 화면에 얹습니다
· 노래와 가사를 맞춰(음성 정렬) 현재 줄과 음절을 실시간으로 표시합니다
· 가사를 못 찾으면 직접 붙여넣을 수 있고, 여러 후보 중에서 고를 수도 있습니다

■ 가사를 더 잘 따라 부르기 위한 표시들

아래는 모두 "가사를 시간에 맞춰 보여준다"는 한 가지 목적을 돕는 표시 방식입니다.

· 발음 표기 — 일본어 가사의 한글 독음을 원문 밑에 함께 보여줍니다. 한자를 못 읽어도
  따라 부를 수 있습니다
· 번역 — 가사의 뜻을 함께 보여줍니다(한국어·영어·일본어·중국어). 기본 꺼짐
· 별도 창(PiP) — 가사만 담은 작은 창을 띄워, 다른 탭을 보면서도 가사를 계속 볼 수 있습니다
· 음정 표시 — 곡의 멜로디를 가사 옆에 그려 어느 높이로 부르는지 보여줍니다
· 마이크 음정 — 자기 목소리의 높이를 곡의 멜로디와 나란히 볼 수 있습니다.
  **기본 꺼짐이며, 켜면 브라우저가 마이크 권한을 묻습니다. 마이크 소리는 브라우저 안에서만
  분석되어 음 높이 값만 화면에 그려지고, 녹음도 서버 전송도 하지 않습니다.**

■ 어떤 데이터가 어디로 가는지

가사 표시를 위해 영상 ID·곡 제목·가사 텍스트를 가사 정렬 서버로 보냅니다. 무엇을 어디로
왜 보내는지 전부 개인정보처리방침에 적어 두었습니다:
https://everyric.moref.co/privacy

· 쿠키를 읽지 않습니다
· 시청 이력을 모으지 않습니다
· 마이크 소리는 기기를 떠나지 않습니다

■ 가사 저작권

가사의 저작권은 원저작자와 권리자에게 있습니다. 이 확장은 가사에 대한 어떤 권리도
주장하지 않고, 가사를 어디서 가져왔는지 화면에 함께 표시합니다. 저작권자의 요청이 있으면
해당 자료를 즉시 삭제합니다.

■ 직접 서버를 운영할 수 있습니다

소스 코드가 공개되어 있고, 가사 정렬 서버를 직접 띄워 쓸 수 있습니다.
https://github.com/onpe5679/Everyric2
```

## 상세 설명 (영어)

```
Everyric shows the lyrics of the song playing on YouTube, line by line, in time with the
music — the line you are on fills in syllable by syllable so you can sing along.

■ What it does

· Finds the lyrics for the song currently playing and overlays them on the page
· Aligns the lyrics to the audio so the current line and syllable are highlighted live
· If no lyrics are found you can paste your own, or pick from several candidates

■ Extras that serve the same single purpose

Everything below exists to help you follow the lyrics in time — that is the one purpose of
this extension.

· Pronunciation — shows a Korean reading beneath Japanese lyrics, so you can sing along
  without reading kanji
· Translation — shows what the lyrics mean (Korean, English, Japanese, Chinese). Off by default
· Separate window (Picture-in-Picture) — a small lyrics-only window so you can keep reading the
  lyrics while looking at another tab
· Pitch lane — draws the song's melody next to the lyrics so you can see how high each line goes
· Microphone pitch — shows your own voice's pitch next to the song's melody.
  **Off by default. Turning it on makes the browser ask for microphone permission. The audio is
  analysed entirely inside the browser to extract a pitch value for the on-screen display; nothing
  is recorded and nothing is sent to any server.**

■ What data goes where

To display lyrics, the extension sends the video ID, song title and lyrics text to the lyrics
alignment server. Everything that is sent, where, and why is documented in the privacy policy:
https://everyric.moref.co/privacy

· It does not read cookies
· It does not collect browsing or watch history
· Microphone audio never leaves your device

■ Lyrics copyright

Copyright in the lyrics belongs to the original authors and rights holders. This extension claims
no rights in any lyrics and always shows where the lyrics came from. On a rights holder's request,
the material is deleted immediately.

■ You can run your own server

The source code is public and you can run the lyrics alignment server yourself.
https://github.com/onpe5679/Everyric2
```

---

## Privacy practices 탭 — 데이터 유형 신고 (제안)

대시보드 문구는 실제로 열어 확인해야 한다(아래는 정책 문서에서 확인한 표준 카테고리 기준
제안이며, 항목명이 다르면 그쪽을 따른다).

| 카테고리 | 신고 | 근거 |
|---|---|---|
| Website content and resources | **예** | 영상 ID·곡 제목·아티스트·**가사 텍스트 전문**·번역 대상 텍스트를 서버로 전송한다 |
| Authentication information | **예 (조건부)** | 이용자가 설정에 직접 입력한 API 키를 헤더로 보낸다. 입력하지 않으면 헤더 자체가 없다 |
| Personally identifiable information | 아니오 | 이름·이메일·주소를 요청하거나 수집하는 코드가 없다 |
| Financial / Health / Personal communications | 아니오 | 해당 경로가 없다 |
| Location | 아니오 | 위치를 요청하는 코드가 없다 |
| Web browsing activity | **판단 필요** | 재생 중인 영상 하나의 ID를 그때그때 보낸다. 이력을 축적·저장하지는 않는다. **전송 사실이 있으므로 "예"로 신고하고 설명란에 "단발 조회, 이력 축적 없음"을 적는 쪽이 안전하다** — 미신고가 실제 삭제 사유로 확인된 반면(같은 카테고리 확장이 방침 부재로 delisted됨) 보수적 신고로 반려된 사례는 확인되지 않았다 |
| User activity (마우스·키 입력 등) | 아니오 | 마이크 입력은 기기를 떠나지 않으므로 "수집·전송"에 해당하지 않는다. 다만 폼이 로컬 처리까지 묻는 문구라면 그때 다시 판단한다 |

**로컬 저장 항목**(설정·API 키 평문·패널 위치·진행 중 작업·위키 캐시)이 대시보드에서 "수집"에
해당하는지는 폼 문구를 직접 봐야 한다 — 방침 5번 섹션에 전부 적어 두었다.

---

## 제출 전 남은 것

- [ ] 스크린샷 **1280x800 또는 640x400** 5장 (리포의 기존 이미지는 전부 규격 밖이고 개발용
      디버그 산출물이다)
- [ ] 소형 프로모 타일 440x280 (리포에 없음)
- [ ] 마퀴 타일 1400x560 (선택)
- [ ] `https://everyric.moref.co/privacy`가 **외부에서 인증 없이 200**을 주는지 확인
      (2026-07-26 현재 게이트웨이가 401로 막고 있어 조치 중 — 이게 막히면 심사가 반려된다)
- [ ] 대시보드 "권한 정당화" 입력란에 `STORE_REVIEW_NOTES.md` 내용을 채울 것 —
      **파일 자체는 심사자가 보지 않는다**
