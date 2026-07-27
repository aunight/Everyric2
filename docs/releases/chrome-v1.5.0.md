# Everyric Chrome 확장 1.5.0

**[한국어](#한국어)** | **[English](#english)** | **[日本語](#日本語)**

3개 언어 요약 — 설치·전체 사용법은 저장소 루트 [README](../../README.md)를 참고하세요.

## 한국어

- **다국어 번역·발음**: 한국어·영어·일본어를 서로 번역 — 곡 원어와 같은 언어는 자동 생략되고, 발음은 원문에 맞춰 한글(hangul)·로마자(romaji)·가나(kana) 중 자동으로 골라 표시합니다.
- **언어 칩**: 제목바에서 이 곡에 어떤 언어가 준비돼 있는지 한눈에 보고 클릭 한 번으로 전환합니다 — 재조회 없이 즉시 반영됩니다.
- **자막·위키 번역 자동 채택**: 유튜브 수동 자막이나 가사 위키(vocaro·miraheze)에 이미 사람이 옮긴 번역이 있으면 재번역하지 않고 그대로 씁니다.
- **가라오케 개선**: 음정 레인을 마우스 휠로 확대·축소하고, 계이름을 영어 표기(C5)로도 선택하며, 음정선 밝기를 페이더로 조절할 수 있습니다.
- **편의 개선**: 한국어·영어 커버 영상("불러보았다"·cover 등)의 원곡 인식이 좋아졌고, 수동 가사 입력란이 클릭 없이 바로 열려 있습니다.
- **안정성**: 응답을 gzip으로 압축하고 요청 타임아웃을 늘려(8초) 간헐적인 조회 실패를 줄였습니다.

## English

- **Multilingual translation & pronunciation** — Korean, English, and Japanese translated into each other; a song already in your language skips translation automatically, and pronunciation is shown in Hangul, romaji, or kana, chosen automatically to match the original text.
- **Language chips** — the title bar shows which languages are ready for this song; one click switches, and it applies instantly without a re-fetch.
- **Auto-adopted caption/wiki translations** — when a manual YouTube caption or a lyrics wiki (vocaro/miraheze) already has a human translation, the extension uses it as-is instead of asking the server to re-translate.
- **Karaoke improvements** — zoom the pitch lane with the mouse wheel, switch note labels to English notation (C5), and adjust pitch-line brightness with a fader.
- **Quality of life** — cover videos titled in Korean or English ("불러보았다", "cover", …) now match their original song more reliably, and the manual lyrics paste box is open from the start — no extra click.
- **Stability** — gzip-compressed responses and a longer request timeout (8s) cut down on intermittent lookup failures.

## 日本語

- **多言語翻訳・発音表記** — 韓国語・英語・日本語を相互に翻訳します。曲の原語と同じ言語を選ぶと翻訳は自動的にスキップされ、発音は原文に合わせてハングル・ローマ字・かなの中から自動的に選ばれて表示されます。
- **言語チップ** — タイトルバーでこの曲にどの言語が準備できているか一目で分かり、クリック一つで切り替えられます。再取得なしで即座に反映されます。
- **字幕・Wiki翻訳の自動採用** — YouTubeの手動字幕や歌詞Wiki(vocaro・miraheze)に既に人の手による翻訳がある場合、サーバーに再翻訳を頼まずそのまま採用します。
- **カラオケ改善** — 音程レーンをマウスホイールで拡大・縮小、階名を英語表記(C5)にも切り替え、音程線の明るさをフェーダーで調整できます。
- **使い勝手** — 韓国語・英語のカバー動画(「불러보았다」「cover」など)の原曲認識が向上し、手動歌詞の貼り付け欄がクリックなしで最初から開いています。
- **安定性** — レスポンスをgzip圧縮し、リクエストのタイムアウトを延長(8秒)することで、断続的な取得失敗を減らしました。
