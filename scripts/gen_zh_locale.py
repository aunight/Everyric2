#!/usr/bin/env python3
"""_locales/zh_TW/messages.json 을 en 카탈로그 구조 + 아래 번역표로 생성한다.

en/messages.json 을 골격으로 쓰는 이유: placeholders 블록(크롬 검증 필수)과 키 집합을
손으로 옮기다 어긋나면 확장이 로드 자체를 거부한다. 여기서는 message 문자열만 갈아끼우고
나머지는 그대로 복사한다. 상류가 키를 추가하면 이 스크립트가 그 키를 빠뜨렸다고 알려준다.

    python3 scripts/gen_zh_locale.py          # 생성
    python3 scripts/gen_zh_locale.py --check  # 검증만 (누락 키·플레이스홀더 불일치)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

LOCALES = Path(__file__).resolve().parent.parent / "everyric2-chrome" / "_locales"
PLACEHOLDER = re.compile(r"\$P\d+\$")

# 繁體中文(台灣) — 用語約定: sync=時間軸 / PiP=子母畫面 / extension=擴充功能 /
# pitch lane=音高軌 / transcription=AI 轉錄 / solfège=唱名 / offset=偏移
ZH: dict[str, str] = {
    # ── PiP ──────────────────────────────────────────────────────────────
    "pip_solfege_names": "Do,Do#,Re,Re#,Mi,Fa,Fa#,Sol,Sol#,La,La#,Ti",
    "pip_docTitle": "Everyric 歌詞",
    "pip_controls_playPause": "播放／暫停",
    "pip_controls_prevTrack": "上一首",
    "pip_controls_nextTrack": "下一首",
    "pip_controls_playlist": "播放清單",
    "pip_controls_mute": "靜音",
    "pip_controls_unmute": "取消靜音",
    "pip_controls_melody": "播放主旋律（合成音）",
    "pip_controls_metronome": "節拍器",
    "pip_controls_metronomeRate": "節拍器速度（½× → 1× → 2×）",
    "pip_controls_metronomeBeat": "設定強拍（重音與小節線一起移動）",
    "pip_controls_windowMinus": "顯示範圍減半（小節變少）",
    "pip_controls_windowLabel": "顯示範圍（畫面上的小節數）",
    "pip_controls_windowPlus": "顯示範圍加倍（小節變多）",
    "pip_controls_modeToggle": "切換模式 — 固定畫面（播放頭移動）↔ 捲動（播放頭固定）",
    "pip_controls_modeFixed": "固定",
    "pip_controls_modeScroll": "捲動",
    "pip_controls_volume": "音量",
    "pip_controls_progressSeek": "點擊跳轉",
    "pip_controls_karaokeToggle": "開關卡拉OK音高軌",
    "pip_controls_videoToggle": "開關影片顯示",
    "pip_controls_panelToggle": "開關歌詞搜尋面板",
    "pip_controls_laneHeightDrag": "拖曳可調整音高軌高度",
    "pip_controls_videoRatioDrag": "拖曳可調整影片與歌詞的比例",
    "pip_songKeyLabel": "$P1$ 調",
    "pip_debug_laneOriginal": "原文",
    "pip_debug_laneHeard": "聽到",
    "pip_debug_lanePron": "發音",
    "pip_debug_alignPrefix": "對齊 ",
    "pip_debug_gradeOk": "良$P1$%",
    "pip_debug_gradeMid": " 中$P1$%",
    "pip_debug_gradeLow": " 差$P1$%",
    "pip_debug_alignmentTextPrefix": " · 轉錄:$P1$",
    "pip_debug_scaffoldApplied": " · 字幕骨架 $P1$ 行(釘選$P2$·內插$P3$·保留$P4$)",
    "pip_debug_scaffoldSkipped": " · 未用字幕骨架:$P1$",
    # ── 除錯面板 ─────────────────────────────────────────────────────────
    "debugPanel_confLow": "差",
    "debugPanel_confMid": "中",
    "debugPanel_confOk": "良",
    "debugPanel_scaffoldApplied": (
        "已套用字幕骨架 — 移動了 $P1$ 行（字幕釘選 $P2$% · 釘選 $P3$·內插 $P4$·保留 $P5$）"
    ),
    "debugPanel_scaffoldSkipped": "未使用字幕骨架 — $P1$",
    "debugPanel_empty": "沒有可顯示的行",
    "debugPanel_heardPrefix": "聽到：$P1$",
    "debugPanel_seekTitle": "點擊跳到這一段",
    # ── 面板 ─────────────────────────────────────────────────────────────
    "panels_serverLog_loadHint": "展開以載入",
    "panels_serverLog_title": "最近的伺服器請求",
    "panels_serverLog_loading": "載入中…",
    "panels_serverLog_empty": "還沒有請求（重新載入擴充功能後會清空）",
    "panels_serverBar_permissionNeeded": (
        "自架伺服器的存取權限在安裝時不會一併授予（預設伺服器不需要）。"
        "如果你原本有授權，可能是擴充功能更新時被撤銷了 — 重新授權一次之後就會持續有效。"
    ),
    "panels_serverBar_openPermissions": "開啟權限設定",
    "panels_serverBar_openPermissionsTitle": "開新分頁授予本機伺服器位址的存取權",
    "panels_serverBar_recheck": "重新檢查",
    "panels_serverBar_recheckTitle": "再試一次連線到伺服器",
    "panels_serverBar_openSettings": "開啟設定",
    "panels_serverBar_openSettingsTitle": "檢查伺服器網址與 API 金鑰",
    "panels_search_titlePlaceholder": "歌名",
    "panels_search_artistPlaceholder": "歌手（可省略）",
    "panels_search_namuwiki": "Namu Wiki",
    "panels_search_google": "Google",
    "panels_search_lyricsWord": "歌詞",
    "panels_search_externalLinksLabel": "去別的地方找歌詞",
    "panels_search_externalLinkTitle": "在 $P1$ 搜尋「$P2$」（開新分頁）",
    "panels_paste_placeholder": (
        "把歌詞貼在這裡，AI 會幫你對出時間軸\n段落標記（[Verse]、Chorus 等）與註解行會自動濾掉。"
    ),
    "panels_paste_attributionPlaceholder": "來源（可省略）— 例如 Namu Wiki、Genius",
    "panels_paste_attributionTitle": (
        "註明這份歌詞的來源，會和時間軸一起存下來並顯示在來源標籤上。留空也可以。"
    ),
    "panels_paste_filterNote": (
        "段落標記（[Verse]、Chorus）與註解／製作名單行會自動移除 — 移除了哪些會告訴你"
    ),
    "panels_paste_generateButton": "用貼上的歌詞產生時間軸",
    "panels_paste_emptyWarning": "請先貼上歌詞",
    "panels_serverDown_text": "無法連線伺服器",
    "panels_serverDown_note": (
        "沒有伺服器也還能搜尋歌詞，但這首歌在外部來源也找不到。"
        "產生、重新產生時間軸以及翻譯都需要伺服器。"
    ),
    "panels_serverDown_retry": "重新搜尋歌詞",
    "panels_serverDown_retryTitlePermission": "先授予權限，再點這裡從頭搜尋一次",
    "panels_serverDown_retryTitleOther": "先把伺服器修好，再點這裡從頭搜尋一次",
    "panels_empty_searchAgain": "重新搜尋",
    "panels_empty_title": "找不到歌詞",
    "panels_empty_detailedSearch": "進階搜尋（自己挑結果、借用時間軸）",
    "panels_loading_manualSearch": "不等了，直接手動搜尋",
    "panels_error_retry": "重試",
    "panels_generating_note": "可以繼續看影片，做完會自動更新。",
    "panels_searchSheet_searchButton": "搜尋",
    "panels_searchSheet_searching": "搜尋中…",
    "panels_searchSheet_back": "← 回到歌詞",
    "panels_searchSheet_title": "搜尋歌詞 — 自己挑結果",
    "panels_searchSheet_sub": (
        "同時搜尋 Vocaloid 歌詞 Wiki（發音與翻譯）＋ LRCLIB（已對時歌詞）"
    ),
    "panels_searchSheet_serverBadNote": (
        "LRCLIB 搜尋還能用，但比對 Vocaloid 歌詞 Wiki 的原文歌名和產生時間軸都需要伺服器。"
    ),
    "panels_results_empty": "沒有結果 — 試試更短的歌名，或用原文（日文）歌名",
    "panels_results_pronTranslationMeta": "發音與翻譯",
    "panels_results_syncedMeta": "已對時",
    "panels_results_plainMeta": "純文字",
    "panels_results_vocaroLabel": "Vocaloid 歌詞 Wiki",
    "panels_results_neteaseLabel": "網易雲音樂",
    "panels_results_neteaseMeta": "已對時 · 可能含中文翻譯",
    "overlay_source_netease": "網易雲音樂",
    "panels_results_lrclibLabel": "LRCLIB",
    # ── 覆蓋層 ───────────────────────────────────────────────────────────
    "overlay_detecting": "偵測歌曲中…",
    "overlay_header_pip": "用子母畫面觀看",
    "overlay_header_regen": "重新產生時間軸（忽略伺服器快取）",
    "overlay_header_regenConfirm": (
        "要重新產生伺服器上的時間軸嗎？\n大約需要一分鐘；完成前現在的歌詞會繼續顯示。"
    ),
    "overlay_header_search": "重新搜尋歌詞（挑別的結果）",
    "overlay_header_settings": "設定",
    "overlay_header_collapse": "收起",
    "overlay_header_expand": "展開",
    "overlay_header_close": "關閉（可從工具列圖示重新開啟）",
    "overlay_genChip_title": "點擊展開我的產生佇列",
    "overlay_genChip_cancel": "取消 AI 轉錄",
    "overlay_genChip_cancelConfirm": "要取消正在進行的 AI 轉錄嗎？",
    "overlay_resumeChip": "回到目前的歌詞",
    "overlay_footer_syncCaption": "同步",
    "overlay_footer_pullEarlier": "歌詞提早 0.1 秒",
    "overlay_footer_pushLater": "歌詞延後 0.1 秒",
    "overlay_footer_resetLabel": "重設",
    "overlay_footer_resetTitle": "重設偏移",
    "overlay_debug_waiting": "debug: 等待中…",
    "overlay_debug_toggleAll": "全部",
    "overlay_debug_toggleAllTitle": "整首歌的除錯面板 — 原文與聽到的內容並排。點一列可跳轉。",
    "overlay_debug_created": "created=$P1$",
    "overlay_debug_grades": "對齊 良$P1$%·中$P2$%·差$P3$%",
    "overlay_debug_alignmentText": "alignedText=$P1$",
    "overlay_debug_alignmentPronunciation": "發音（讀音層）",
    "overlay_debug_alignmentOriginal": "原文",
    "overlay_loading_default": "搜尋歌詞中…",
    "overlay_banner_aiKaraoke": "AI 轉錄可以加上卡拉OK（音高與發音）",
    "overlay_banner_aiTranscribe": "執行 AI 轉錄",
    "overlay_line_seekTitle": "點擊跳到這一行",
    "overlay_plain_generateSync": "產生時間軸",
    "overlay_plain_noTimesync": "這份歌詞沒有時間軸",
    "overlay_search_backToAuto": "回到自動搜尋",
    "overlay_search_resetSync": "重設這部影片的時間軸（從伺服器刪除）",
    "overlay_search_resetSyncTitle": "把貼錯歌詞做出來的時間軸整份清掉，好從頭來一次",
    "overlay_search_resetSyncConfirm": (
        "要刪除這部影片在伺服器上的所有時間軸資料（時間、發音、翻譯）嗎？\n"
        "之後會重新執行自動搜尋，你也可以貼上新的歌詞。"
    ),
    "overlay_link_srcPlaceholder": "原影片網址或 ID（必須已經有 AI 轉錄）",
    "overlay_link_offsetPlaceholder": "偏移（秒）",
    "overlay_link_offsetTitle": "這部影片比原片晚開始就填正數，早開始就填負數",
    "overlay_link_ratePlaceholder": "倍率",
    "overlay_link_rateTitle": "相對原片的播放速度 — nightcore 約 1.25，速度相同就填 1",
    "overlay_link_filterPlaceholder": "搜尋已存的時間軸 — 第一行、影片 ID 或來源",
    "overlay_link_needVideoId": "請輸入影片網址或 11 碼 ID",
    "overlay_link_connecting": "連結中…",
    "overlay_link_sectionTitle": "借用其他影片的時間軸（適用於伴奏版／翻唱）",
    "overlay_link_verifiedBadge": " · 已驗證",
    "overlay_link_unverifiedBadge": " · 未驗證",
    "overlay_link_currentStatus": "已連結到 $P1$（$P2$）$P3$",
    "overlay_link_unlink": "解除連結",
    "overlay_link_connect": "連結",
    "overlay_link_pickFromList": "從伺服器已存的時間軸中挑選",
    "overlay_link_loadingList": "載入清單中…",
    "overlay_link_noSavedSyncs": "伺服器上沒有已存的時間軸",
    "overlay_link_noFilterMatch": "沒有符合搜尋的時間軸",
    "overlay_link_pickHint": "點一項會填入來源欄位 — 確認偏移之後再按「連結」",
    "overlay_link_noFirstLine": "（沒有第一行）",
    "overlay_link_lineCountMeta": "$P1$ 行",
    "overlay_link_describeVerified": "借用 $P1$ 的時間軸 · 自動連結，已用音訊相關性驗證",
    "overlay_link_describeUnverified": (
        "借用 $P1$ 的時間軸 · 手動連結，未驗證 — 如果會飄就調整偏移或解除連結"
    ),
    "overlay_link_describeUnknown": "借用 $P1$ 的時間軸 · 驗證狀態未知（舊版伺服器）",
    "overlay_generating_default": "產生時間軸中… $P1$%",
    "overlay_pip_placeholder": "歌詞正顯示在子母畫面視窗",
    "overlay_pip_backToPanel": "回到面板",
    "overlay_settings_serverStatusTitle": "伺服器狀態 — $P1$",
    "overlay_warn_text": "這份 AI 轉錄可能不準（對齊信賴度 $P1$）",
    "overlay_warn_title": (
        "轉錄／發音的平均對齊信賴度偏低。請確認原始歌詞是否正確，或試著重新產生。"
    ),
    "overlay_warn_close": "關掉這個警告（也可以在設定裡停用）",
    "overlay_genList_currentVideo": "$P1$（目前影片）",
    "overlay_source_vocaro": "Vocaloid 歌詞 Wiki",
    "overlay_source_caption": "YouTube 字幕",
    "overlay_source_syncedLyrics": "已對時歌詞",
    "overlay_source_plainLyrics": "純文字歌詞",
    "overlay_source_openPage": "開啟來源頁面",
    "overlay_settings_fontSize_small": "小",
    "overlay_settings_fontSize_medium": "中",
    "overlay_settings_fontSize_large": "大",
    "overlay_settings_optAuto": "自動",
    "overlay_settings_theme_dark": "深色",
    "overlay_settings_theme_light": "淺色",
    "overlay_settings_pronScript_hangul": "韓文",
    "overlay_settings_pronScript_romaji": "羅馬字",
    "overlay_settings_pronScript_kana": "假名",
    "overlay_settings_sourcePriority_vocaro": "優先 Vocaloid 歌詞 Wiki",
    "overlay_settings_sourcePriority_lrclib": "優先 LRCLIB",
    "overlay_settings_pitchWindow_half": "½ 小節",
    "overlay_settings_pitchWindow_bars": "$P1$ 小節",
    "overlay_settings_pitchMode_page": "固定畫面 · 播放頭移動",
    "overlay_settings_pitchMode_scroll": "捲動 · 播放頭固定",
    "overlay_settings_pitchFont_normal": "中",
    "overlay_settings_pitchFont_large": "大",
    "overlay_settings_pitchFont_xlarge": "特大",
    "overlay_settings_pitchFont_small": "小",
    "overlay_settings_pronPosition_note": "音符上方",
    "overlay_settings_pronPosition_bottom": "畫面底部",
    "overlay_settings_metronomeRate_half": "½×（二分音符）",
    "overlay_settings_metronomeRate_one": "1×（四分音符）",
    "overlay_settings_metronomeRate_two": "2×（八分音符）",
    "overlay_settings_metronomeBeat_n": "第 $P1$ 拍",
    "overlay_settings_micOctave_n": "$P1$ 個八度",
    "overlay_settings_micOctave_none": "不校正",
    "overlay_settings_permBtnTitle": "開新分頁授予你自架伺服器的存取權",
    "overlay_settings_apiKeyPlaceholder": "（可省略）伺服器 API 金鑰",
    "overlay_settings_row_autoSearch": "自動搜尋歌詞（僅音樂影片）",
    "overlay_settings_row_autoSearchTitle": (
        "會依 YouTube Music 中介資料、頻道與標題判斷是不是音樂影片，然後自動開啟歌詞面板。"
        "關掉之後仍可從工具列圖示手動開啟。"
    ),
    "overlay_settings_row_autoSearchShorts": "Shorts 也自動搜尋",
    "overlay_settings_row_autoSearchShortsTitle": (
        "預設關閉 — 在 Shorts 上不會自動開啟歌詞面板。你隨時可以從工具列圖示手動開啟。"
    ),
    "overlay_settings_row_fontSize": "字級",
    "overlay_settings_row_theme": "主題",
    "overlay_settings_row_showTranslation": "顯示歌詞翻譯",
    "overlay_settings_row_translationLanguage": "翻譯語言",
    "overlay_settings_row_uiLanguage": "顯示語言",
    "overlay_settings_row_pronScript": "發音標記方式",
    "overlay_settings_row_showPronunciation": "顯示發音（有的話）",
    "overlay_settings_row_sourcePriority": "歌詞來源優先順序",
    "overlay_settings_row_pipKeepPanel": "子母畫面時面板保留歌詞",
    "overlay_settings_row_pipShowVideo": "子母畫面中一起顯示影片",
    "overlay_settings_row_pitchGuide": "卡拉OK音高軌（BETA · 子母畫面）",
    "overlay_settings_row_pitchWindow": "音高軌顯示範圍",
    "overlay_settings_row_pitchMode": "音高軌捲動模式",
    "overlay_settings_row_pitchFont": "音高軌文字大小",
    "overlay_settings_row_pitchCountdown": "歌詞開始前倒數",
    "overlay_settings_row_pitchF0Curve": "顯示原始音高曲線（f0）",
    "overlay_settings_row_pitchF0CurveTitle": (
        "把音高模型抽出的原始旋律曲線以藍線畫在音高軌上。可以和除錯模式各自開關。"
    ),
    "overlay_settings_row_pronPosition": "發音位置",
    "overlay_settings_row_pronPositionTitle": (
        "有逐字時間的歌曲要把發音顯示在哪。音符上方＝貼在每個音符正下方；"
        "底部＝置中在畫面底部並帶進度漸層。"
    ),
    "overlay_settings_row_melodyPlayback": "播放主旋律（卡拉OK視窗）",
    "overlay_settings_row_melodyPlaybackTitle": (
        "用合成器播放轉錄出來的音符。只有卡拉OK視窗開著時才會出聲。"
    ),
    "overlay_settings_row_metronome": "節拍器",
    "overlay_settings_row_metronomeTitle": (
        "依伺服器估算的 BPM 打拍（假設 4/4，每第 4 拍加重音）。"
    ),
    "overlay_settings_row_metronomeRate": "節拍器速度",
    "overlay_settings_row_metronomeRateTitle": (
        "覺得慢的歌用 2×（八分音符），非常快的歌用 ½×。也可以從卡拉OK視窗裡的按鈕改。"
    ),
    "overlay_settings_row_metronomeBeat": "強拍",
    "overlay_settings_row_metronomeBeatTitle": (
        "歌曲的第一個重音對不上時可以位移強拍。節拍器重音與音高軌的小節線會一起移動。"
    ),
    "overlay_settings_row_audioOut": "卡拉OK音訊輸出裝置",
    "overlay_settings_row_audioOutTitle": (
        "只有主旋律和節拍器會走這個裝置。影片聲音仍用你平常的輸出。"
    ),
    "overlay_settings_row_micPitch": "顯示麥克風音高（音高軌）",
    "overlay_settings_row_micPitchTitle": (
        "把你唱的音高以青色軌跡畫在卡拉OK音高軌上。開啟時會請求麥克風權限。"
    ),
    "overlay_settings_row_micDevice": "麥克風裝置",
    "overlay_settings_row_micOctave": "麥克風八度校正",
    "overlay_settings_row_micOctaveTitle": (
        "如果你的麥克風軌跡整條畫在音符的高或低八度，在這裡校正。"
    ),
    "overlay_settings_deviceNote": "授予麥克風權限一次之後才會顯示裝置名稱",
    "overlay_settings_serverUrlLabel": "時間軸伺服器網址 ",
    "overlay_settings_apiKeyLabel": "API 金鑰",
    "overlay_settings_row_lowConfWarning": "對齊信賴度過低警告",
    "overlay_settings_row_lowConfWarningTitle": (
        "轉錄信賴度非常低的歌曲，會在歌詞面板上方顯示警告條。"
    ),
    "overlay_settings_row_notifyOnComplete": "AI 轉錄完成時通知",
    "overlay_settings_row_notifyOnCompleteTitle": (
        "排入佇列的轉錄完成時發出瀏覽器通知，就算你在別的分頁也會收到。"
    ),
    "overlay_settings_row_debugInfo": "顯示除錯資訊",
    "overlay_settings_serverRequiredNote": "產生時間軸與翻譯需要 Everyric 伺服器",
    "overlay_settings_closeButton": "關閉",
    "overlay_settings_deviceN": "裝置 $P1$",
    "overlay_settings_defaultOutput": "預設輸出",
    "overlay_settings_defaultMic": "預設麥克風",
    # ── 選項頁 ───────────────────────────────────────────────────────────
    "options_pageTitle": "Everyric 權限設定",
    "options_subtitle": "在這裡授予或撤銷你自架（本機）時間軸伺服器的存取權。",
    "options_intro1": (
        "Everyric 預設使用 <code>https://everyric.moref.co</code> 時間軸伺服器。"
        "它的存取權在安裝時就已授予，所以<strong>這裡不需要做任何事</strong>。"
    ),
    "options_intro2": (
        "只有<strong>自己架伺服器</strong>時才需要這一頁。本機伺服器位址只有少數自架者會用到，"
        "所以不在安裝時要求所有人授權，而是做成<em>選擇性權限</em>，只讓需要的人授予。"
        "授予後擴充功能就能把歌詞／時間軸請求送到那個位址；撤銷則立即停止。"
    ),
    "options_currentServerHeading": "目前的時間軸伺服器",
    "options_serverUrlLoading": "載入中…",
    "options_serverKindChecking": "檢查中",
    "options_serverNoteDefault": (
        "伺服器位址請從 YouTube 歌詞面板的設定（⚙）修改 — 這一頁只處理權限。"
    ),
    "options_permHeading": "本機伺服器存取權限",
    "options_grantButtonDefault": "授予",
    "options_revokeButtonDefault": "撤銷",
    "options_recheckButton": "重新檢查狀態",
    "options_tipsHeading": "小提醒",
    "options_tip1": "授予後會一直有效到你移除擴充功能 — 只需做一次。",
    "options_tip2": (
        "擴充功能更新有可能撤銷這個權限，導致自架伺服器忽然斷線。遇到的話回這裡再授予一次就好。"
    ),
    "options_tip3": (
        "你也可以在 <code>chrome://extensions</code> 的 Everyric 詳細資料裡檢查與修改網站存取權，"
        "在那邊改動這裡也會同步顯示。"
    ),
    "options_tip4": "換其他通訊埠的本機伺服器或第三方伺服器位址，需要重新編譯擴充功能才能使用。",
    "options_serverKind_remote": "遠端伺服器",
    "options_serverNote_remote": (
        "你目前設定的是遠端伺服器，所以不需要這一頁的權限。"
        "伺服器位址請從 YouTube 歌詞面板的設定（⚙）修改。"
    ),
    "options_serverKind_notAllowed": "無法授予",
    "options_serverNote_notAllowed": (
        "$P1$ 不是擴充功能可以授予存取權的位址。可授予的本機位址只有 $P2$ — "
        "其他通訊埠或位址需要重新編譯擴充功能。"
    ),
    "options_serverKind_grantedLocal": "本機伺服器 · 已授予",
    "options_serverKind_needsPermLocal": "本機伺服器 · 需要權限",
    "options_serverNote_granted": "可以向這個位址發送請求。",
    "options_serverNote_needsPerm": "在下方授予存取權才能向這個位址發送請求。（請求會送到 $P1$）",
    "options_currentServerMarker": "目前設定的伺服器",
    "options_badgeGranted": "已授予",
    "options_badgeNotGranted": "未授予",
    "options_grantButton_preemptive": "預先授予本機伺服器存取權",
    "options_grantButton_normal": "授予本機伺服器存取權",
    "options_revokeButton_all": "撤銷所有權限",
    "options_revokeButton_one": "撤銷權限",
    "options_grantResult_success": "已授予。已開啟的 YouTube 分頁歌詞面板會自動重新檢查。",
    "options_grantResult_denied": (
        "未授予。使用自架伺服器必須有這個權限；預設伺服器（everyric.moref.co）不需要也能用。"
    ),
    "options_grantResult_error": "無法請求權限 — $P1$",
    "options_revokeResult_success": (
        "已撤銷。請求不會再送到你的本機伺服器 — 歌詞面板會重新顯示需要權限的提示。"
    ),
    "options_revokeResult_failure": "撤銷失敗。",
    "options_revokeResult_error": "撤銷失敗 — $P1$",
    # ── content script ───────────────────────────────────────────────────
    "content_notice_permPageFailed": (
        "無法開啟權限設定頁 — 請改從擴充功能管理頁（chrome://extensions）"
        "開啟 Everyric 的「擴充功能選項」"
    ),
    "content_notice_candidateLoadFailed": "無法載入所選的歌詞 — 換一個結果試試",
    "content_failure_generateRequest": "產生時間軸的請求失敗。",
    "content_failure_regenerateRequest": "重新產生的請求失敗。",
    "content_failure_resetSync": "重設時間軸失敗。",
    "content_failure_cancelRequest": "取消請求失敗。",
    "content_linkProbe_chip": "疑似同一首歌 — 正在檢查自動連結…",
    "content_link_cannotSelf": "不能把影片連結到自己",
    "content_link_replaceOwnConfirm": (
        "這部影片已經有自己的 AI 轉錄。\n連結會刪掉它並改用原影片的時間軸。要繼續嗎？"
    ),
    "content_link_cancelledKeepOwn": "已取消連結 — 保留你自己的轉錄",
    "content_link_ownDeleteFailed": "無法刪除現有的轉錄$P1$",
    "content_link_failedWithNote": "連結失敗 — $P1$",
    "content_link_failedNoNote": "連結失敗 — 請確認原影片有轉錄（時間軸）",
    "content_link_unlinkFailed": "解除連結失敗$P1$",
    "content_link_unlinkFailedCheckServer": " — 請檢查伺服器狀態",
    "content_link_autoConfSuffix": "（音訊相符 $P1$%）",
    "content_link_autoLinked": "已自動連結 — 已帶入這首歌的時間軸$P1$",
    "content_debug_zoneAdlib": "即興★",
    "content_debug_zoneVocal": "人聲",
    "content_debug_zoneInstrumental": "伴奏／靜音",
    "content_age_justNow": "剛剛",
    "content_age_minutesAgo": "$P1$ 分鐘前",
    "content_age_hoursAgo": "$P1$ 小時前",
    "content_age_daysAgo": "$P1$ 天前",
    "content_translation_unavailable": "無法翻譯 — $P1$",
    "content_translation_generating": "產生翻譯與發音中…",
    "content_translation_failedWithStatus": "翻譯失敗 — $P1$",
    "content_translation_failedNoResult": "翻譯失敗 — 伺服器沒有回傳結果",
    "content_translation_lineCountMismatch": (
        "無法套用翻譯 — 行數對不上（翻譯：$P1$ 行 · 歌詞：$P2$ 行）"
    ),
    "content_translation_partialFailure": "翻譯部分失敗 — 有 $P1$ 行沒拿到伺服器的結果",
    "content_translation_aiGenerating": "產生 AI 翻譯與發音中…",
    "content_error_lyricsLoadFailed": "無法載入歌詞",
    "content_error_candidatesLoadFailed": "無法載入候選 — $P1$",
    "content_error_unknown": "未知錯誤",
    "content_error_jobGone": "在伺服器上找不到這個工作（可能重啟過）— 請重新產生一次",
    "content_error_syncGenerationFailed": "產生時間軸失敗",
    "content_loading_selectedCandidate": "載入所選的歌詞中…",
    "content_generate_blockedAutoCaption": (
        "這是自動生成的字幕，轉錄可能不準 — 請貼上正確的歌詞來產生時間軸"
    ),
    "content_generate_tooManyLines": "歌詞太長了（$P1$ 行）— 請縮短到 500 行以內",
    "content_generate_autoCaptionBlocked": (
        "自動生成的字幕不夠準確，無法用來建時間軸 — 請搜尋歌詞或直接貼上"
    ),
    "content_generate_quotaLinkHint": (
        "如果這首歌已經有時間軸（在原片或別的翻唱影片上），連結不會算進額度 — "
        "打開搜尋畫面，用底部的「借用其他影片的時間軸」填入原影片網址即可。"
    ),
    "content_notify_transcribeComplete": "AI 轉錄完成",
    "content_notify_transcribeFailed": "AI 轉錄失敗",
    "content_notify_otherVideoReady": "$P1$ — 時間軸已完成。打開影片看看吧",
    "content_queue_position": "佇列第 $P1$ 位",
    "content_queue_label": "已排入佇列",
    "content_completion_notLoadedMsg": "$P1$ — 無法載入時間軸",
    "content_completion_notLoadedWarning": "無法載入時間軸",
    "content_completion_readyMsg": "$P1$ — 歌詞時間軸已完成",
    "content_completion_pronWord": "發音",
    "content_completion_partialPron": "發音 $P1$/$P2$ 行",
    "content_completion_trWord": "翻譯",
    "content_completion_partialTr": "翻譯 $P1$/$P2$ 行",
    "content_completion_allReadyMsg": "$P1$ — 歌詞時間軸、發音、翻譯都完成了",
    "content_completion_partialReadyMsg": "$P1$ — 時間軸完成，但有些行是空的（$P2$）",
    "content_completion_partialReadyWarning": "部分行只加到了 $P1$ — 試著重新產生一次",
    "content_completion_missingMsg": "$P1$ — 時間軸做好了，但缺少 $P2$$P3$",
    "content_completion_missingWarning": "時間軸做好了，但沒有加上 $P1$$P2$ — 試著重新產生一次",
    "content_genChip_preparing": "準備產生時間軸 — 正在請求 AI 翻譯與發音…",
    "content_genChip_stage_audioPrep": "準備音訊",
    "content_genChip_stage_cacheCheck": "檢查快取",
    "content_genChip_stage_vocalSep": "分離人聲",
    "content_genChip_stage_metaWait": "等待翻譯",
    "content_genChip_stage_align": "對齊歌詞",
    "content_genChip_stage_timing": "微調時間",
    "content_genChip_stage_melody": "分析旋律",
    "content_genChip_stage_save": "儲存",
    "content_genChip_stageProgress": "$P1$ $P2$% · 總進度 $P3$%",
    "content_genChip_percentOnly": "$P1$%",
    "content_genChip_transcribing": "轉錄中 $P1$$P2$",
    "content_genChip_othersSuffix": " · 還有 $P1$ 部",
    "content_genChip_othersOnly": "正在轉錄另外 $P1$ 部影片",
    "content_genChip_stageOnly": "$P1$ $P2$%",
    # ── 伺服器錯誤 ───────────────────────────────────────────────────────
    "serverError_lyrics_too_long": "歌詞太長 — 請縮短後再試",
    "serverError_video_too_long": "這部影片太長 — 目前還不支援這個長度",
    "serverError_captions_no_text": "這條字幕軌沒有文字 — 換一條字幕軌或直接貼上歌詞",
    # ── 語言晶片／翻譯來源 ───────────────────────────────────────────────
    "overlay_langChip_generating": "產生翻譯中…",
    "overlay_langChip_current": "目前選擇的語言",
    "overlay_langChip_switchTo": "切換到這個語言",
    "overlay_langChip_generateFor": "產生這個語言的翻譯",
    "content_translation_originalOnly": "只有原文 — 沒有翻譯",
    "overlay_translationPending": "準備 $P1$ 翻譯中…",
    "overlay_source_miraheze": "VocaloidLyrics Wiki",
    "overlay_translationSource_caption": "翻譯：YouTube 字幕",
    "overlay_translationSource_wiki": "翻譯：$P1$",
    "overlay_translationSource_llm": "翻譯：AI",
    "overlay_settings_row_solfegeNotation": "唱名標記",
    "overlay_settings_solfegeNotation_korean": "唱名（Do-Re-Mi）",
    "overlay_settings_solfegeNotation_english": "音名（C4、D#5）",
    "overlay_settings_solfegeNotation_off": "關閉",
    "overlay_settings_micDisplayMode_notes": "命中音符（日K）",
    "overlay_settings_row_pitchLineOpacity": "音高線亮度",
    "overlay_settings_row_pitchLineOpacityTitle": "調整 f0 曲線與音符條的不透明度",
    # ── 採點 ─────────────────────────────────────────────────────────────
    # ── 伺服器狀態(原本韓文寫死在 server-status.ts) ─────────────────────
    "serverStatus_code_timeout": "無回應（逾時）",
    "serverStatus_code_permission": "沒有主機權限",
    "serverStatus_code_offline": "連線失敗",
    "serverStatus_code_timeoutMs": "超過 $P1$ms",
    "serverStatus_offline": "連不上伺服器 — 伺服器沒開，或位址填錯了",
    "serverStatus_timeout": "伺服器沒有及時回應",
    "serverStatus_auth": "API 金鑰驗證失敗 — 請到設定檢查金鑰",
    "serverStatus_server": "伺服器發生錯誤",
    "serverStatus_notfound": "伺服器沒有這個功能 — 伺服器版本可能太舊",
    "serverStatus_malformed": "無法解析伺服器的回應",
    "serverStatus_client": "伺服器拒絕了這個請求",
    "serverStatus_permission": "沒有存取本機伺服器的權限 — 請先授權",
    "pip_score_label": "採點 $P1$",
    "pip_score_noMic": "採點 — 麥克風關閉",
    "pip_score_best": "最佳 $P1$",
    "overlay_settings_translationApiKeyLabel": "翻譯 API 金鑰（Gemini 等）",
    "overlay_settings_translationApiKeyPlaceholder": "（可省略）你自己的翻譯 LLM 金鑰",
    "overlay_settings_translationApiKeyTitle": (
        "金鑰只存在本機 Chrome 儲存空間；官方預設伺服器永不接收。"
        "只有你設定的自架伺服器明確允許使用者金鑰時才會傳送。"
    ),
    "overlay_header_pipLabel": "採點",
    "overlay_pronChip_title": "發音標記方式 — 點擊切換",
    "pip_controls_pronScriptToggle": "切換發音標記（韓文／羅馬字／假名）",
    "overlay_pronChip_kk": "KK音標",
    "overlay_pronChip_off": "發音關閉",
    "lyricsClean_kind_note": "註解",
    "lyricsClean_kind_section": "段落標記",
    "lyricsClean_kind_repeat": "重複標記",
    "lyricsClean_kind_credit": "製作名單",
    "lyricsClean_kind_footnote": "註腳",
    "lyricsClean_moreLines": "，另 $P1$ 行",
    "lyricsClean_bailed": (
        "看起來像標記的行太多，先全部保留（$P1$ 行）— 請自行刪掉不是歌詞的行"
    ),
    "lyricsClean_removed": "已濾掉$P1$ $P2$ 行 — $P3$",
    "overlay_settings_row_karaokeScore": "採點模式（BETA）",
    "overlay_settings_row_karaokeScoreTitle": (
        "把你麥克風的音高和主旋律音符比對計分。軌跡會變成判定色"
        "（命中＝綠、接近＝黃、失誤＝紅），右上角顯示總分。需要先開啟「顯示麥克風音高」。"
    ),
}


def build() -> tuple[dict, list[str]]:
    """en 골격 + ZH 번역표로 zh_TW 카탈로그를 만든다. (카탈로그, 문제 목록)"""
    en = json.loads((LOCALES / "en" / "messages.json").read_text(encoding="utf-8"))
    problems: list[str] = []
    out: dict[str, dict] = {}

    for key, entry in en.items():
        zh = ZH.get(key)
        if zh is None:
            problems.append(f"missing translation: {key} = {entry['message']!r}")
            continue
        # 플레이스홀더가 어긋나면 크롬이 값을 빈칸으로 소거한다 — 로드는 되고 화면만 깨져
        # 눈으로는 못 잡는다. 그래서 생성 시점에 막는다.
        if sorted(PLACEHOLDER.findall(zh)) != sorted(PLACEHOLDER.findall(entry["message"])):
            problems.append(f"placeholder mismatch: {key}")
            continue
        new = dict(entry)
        new["message"] = zh
        out[key] = new

    for key in ZH:
        if key not in en:
            problems.append(f"stale key (not in en): {key}")

    return out, problems


def main() -> int:
    catalog, problems = build()
    for p in problems:
        print(p, file=sys.stderr)
    if problems:
        return 1
    if "--check" in sys.argv:
        print(f"ok — {len(catalog)} keys")
        return 0
    target = LOCALES / "zh_TW"
    target.mkdir(exist_ok=True)
    (target / "messages.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {target / 'messages.json'} — {len(catalog)} keys")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
