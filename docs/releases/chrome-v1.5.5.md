# Everyric Chrome 확장 1.5.5

**[한국어](#한국어)** | **[English](#english)** | **[日本語](#日本語)**

## 한국어

- **권한 최소화 완성**: `vocaro.wikidot.com` 호스트 권한을 제거했습니다 — 위키 조회(곡 페이지·수록곡 일람)가 백엔드 서버 프록시로 옮겨 갔습니다. 이제 설치 시 요구하는 사이트 접근은 **기본 서버(everyric.moref.co) 하나**뿐입니다(유튜브 2종은 가사 패널 주입용 content script). 기능 변화는 없습니다.
- **곡 감지 개선**: 게임 공식 채널·개인 채널에 올라온 보카로 곡(카테고리가 Gaming·People & Blogs인 MV)에서 패널이 자동으로 열리지 않던 문제를 고쳤습니다 — 카테고리는 양성 신호로만 쓰고, 제목의 보카로 가수명(初音ミク·MEIKO·歌愛ユキ 등)을 곡 신호로 인정합니다. 실브라우저 22곡 검증으로 확인했습니다.
- **번역 언어 안전장치(서버)**: 언어 라벨과 내용이 어긋난 번역이 저장·표시되던 사고를 차단했습니다(한국어 화면에 영어 번역이 뜨던 문제의 근본 수정).
- 참고: 자체 호스팅 서버는 이 버전과 함께 서버도 업데이트해야 vocaro 가사 폴백이 동작합니다(구버전 서버에서는 그 폴백만 조용히 꺼집니다).

## English

- **Minimal permissions, completed** — the `vocaro.wikidot.com` host permission has been removed; wiki lookups (song pages and the song index) now go through the backend server proxy. Site access requested at install is now **just the default server (everyric.moref.co)** (the two YouTube entries are the content script that draws the lyrics panel). No functional change.
- **Better song detection** — the panel now auto-opens on Vocaloid MVs uploaded to game or personal channels (category Gaming / People & Blogs), which were previously blocked by the category check; Vocaloid singer names in the title (初音ミク, MEIKO, 歌愛ユキ, …) now count as a song signal. Verified with a 22-song real-browser run.
- **Translation language safeguard (server)** — translations whose content doesn't match their language label are no longer stored or served (root fix for English text appearing on the Korean display).
- Note for self-hosters: update your server together with this version, or the vocaro lyrics fallback will be silently unavailable (everything else keeps working).

## 日本語

- **権限最小化の完成** — `vocaro.wikidot.com` のホスト権限を削除しました。Wikiの参照(曲ページ・収録曲一覧)はバックエンドサーバーのプロキシ経由になりました。インストール時に要求するサイトアクセスは**デフォルトサーバー(everyric.moref.co)のみ**です(YouTubeの2件は歌詞パネルを描画するcontent script)。機能の変更はありません。
- **曲検出の改善** — ゲーム公式チャンネルや個人チャンネルに投稿されたボカロ曲(カテゴリがGaming・People & Blogsの MV)でパネルが自動で開かなかった問題を修正しました。カテゴリは肯定シグナルとしてのみ使い、タイトルのボカロ歌手名(初音ミク・MEIKO・歌愛ユキなど)を曲のシグナルとして認めます。実ブラウザ22曲で検証済みです。
- **翻訳言語セーフガード(サーバー)** — 言語ラベルと内容が一致しない翻訳の保存・表示を遮断しました(韓国語表示に英語が出る問題の根本修正)。
- セルフホスティングの場合はサーバーも合わせて更新してください(旧サーバーではvocaroの歌詞フォールバックのみ無効になります)。
