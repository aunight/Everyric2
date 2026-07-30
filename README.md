# Everyric2

Everyric2 是一套 YouTube 動態歌詞與卡拉 OK 採點工具。Chrome 擴充功能會在影片上顯示
同步歌詞、翻譯及發音，也能開啟獨立的音高畫面，將歌曲的目標音符和麥克風偵測結果放在
同一條時間軸上。

> [!IMPORTANT]
> 本儲存庫是 [Everyric2 原始專案](https://github.com/onpe5679/Everyric2)的修改版 fork。
> 原作者為 **onpe（GitHub：[onpe5679](https://github.com/onpe5679)）**。本 fork 的新增與
> 修改由 fork 維護者負責，並依 Apache License 2.0 發布。完整聲明請見
> [LICENSE](LICENSE) 與 [NOTICE](NOTICE)。

![YouTube 動態歌詞面板](docs/images/chrome-overlay.png)

![卡拉 OK 音高畫面](docs/images/karaoke-pip.png)

## 主要功能

- **YouTube 動態歌詞**：可拖曳的歌詞面板會跟著影片時間逐行、逐字顯示目前唱到的位置。
- **自動搜尋歌詞**：依序尋找伺服器既有同步、Vocaloid 歌詞網站、LRCLIB、網易雲音樂及
  YouTube 字幕；找不到時可貼上歌詞產生新的 AI 同步。
- **翻譯與發音**：依歌曲原語顯示翻譯、羅馬拼音、平假名或其他適合的發音標記；原文和翻譯
  為同一語言時不重複顯示。
- **卡拉 OK 音高畫面**：在獨立的子母畫面視窗顯示目標音符、歌詞、發音、節拍、歌曲調性與
  麥克風即時音高。
- **採點顯示方式**：可在連續的「線條軌跡」與「命中音符（日K）」之間切換。
- **其他影片共用時間軸**：伴奏版、翻唱版及 Nightcore 影片可沿用原曲同步，並調整偏移秒數
  與播放倍率。
- **背景轉錄通知**：同步工作可在背景執行，完成後由瀏覽器通知。

## 此 fork 新增與調整

- **完整繁體中文介面**：加入 Chrome 擴充功能繁中介面、OpenCC 台灣繁體轉換及中文翻譯目標。
- **Apple Silicon 加速**：支援 M 系列晶片的 PyTorch MPS 裝置選擇、記憶體管理與安全分段；
  MPS 不支援的強制對齊運算會自動回退 CPU。
- **翻譯 API 擴充**：加入 OpenAI Chat Completions 相容介面，可自訂 API 端點、模型與金鑰，
  連接 OpenAI、DeepSeek、Qwen、GLM 或其他相容服務。
- **歌詞來源與清理**：加入網易雲音樂搜尋及來源優先順序；自動精簡 YouTube 中日文歌名，
  移除 Official MV、Full Size、動畫 OP／ED 宣傳文字與重複歌名，並過濾歌詞中的作詞、
  作曲、編曲及混音等製作人員資訊。
- **日文歌詞顯示**：保留漢字原文並在上方附加平假名；原本就是假名的文字不重複顯示。
  一般歌詞與採點音符使用相同的日文讀音規則。
- **卡拉 OK 採點顯示改善**：採點顯示方式新增「目標命中音符」顯示模式，並加入已唱歌詞
  淡化及中文原文音符標示；唱名標記只新增「關閉」選項，Do-Re-Mi 與英文字母音名原本即已提供。
- **音高辨識品質與可信度**：RMVPE 設定不再於缺檔或載入失敗時暗中改用 FCPE；加入經
  SHA-256 驗證的權重下載工具、`htdemucs_ft` 高品質分離選項，以及只作用於音高支線的
  可選 WPE Dereverb。自架設定請見[音高辨識品質設定](docs/pitch-quality.md)。
- **版面與操作改善**：重新調整歌詞、控制列、語言按鈕及設定面板；返回按鈕與轉錄提示使用
  獨立的圓角毛玻璃底，並修正遮擋、層級及歌詞截斷。語言按鈕會將歌曲原語排在第一、
  系統／介面語言排在第二；AI 轉錄開始後會立即顯示紫色進度狀態。

## 安裝 Chrome 擴充功能

1. 前往 [Releases](https://github.com/aunight/Everyric2/releases)，下載最新的
   `Everyric-Chrome-<版本>.zip`。
2. 將下載的 ZIP 檔解壓縮。
3. 在 Chrome 網址列輸入 `chrome://extensions`。
4. 開啟右上角的「開發人員模式」。
5. 按下「載入未封裝項目」，選擇剛才解壓縮的資料夾。

安裝完成後，建議將 Everyric2 固定在 Chrome 工具列，方便開啟設定或檢查連線狀態。

## 基本使用方法

### 顯示歌詞

1. 在 YouTube 開啟歌曲影片。
2. 擴充功能會自動搜尋歌詞，找到後直接顯示同步面板。
3. 如果歌曲尚未建立同步，按下「執行 AI 轉錄」並貼上歌詞；送出後會立即顯示紫色進度狀態。

### 顯示翻譯與發音

1. 按下歌詞面板右上角的設定按鈕。
2. 開啟翻譯或發音顯示，並選擇需要的語言。
3. 標題列的語言按鈕會優先顯示歌曲原語，其次為系統／介面語言。

### 開啟卡拉 OK 採點

1. 在歌詞面板按下「採點」。
2. 第一次使用時，允許 Chrome 存取麥克風。
3. 在設定的「採點顯示方式」選擇「線條軌跡」或「命中音符（日K）」。
4. 唱名標記可選擇 Do-Re-Mi、英文字母音名或關閉。

## 注意事項

- 預設伺服器為 `https://everyric.moref.co`，一般使用不需要 API 金鑰，設定中的金鑰欄位可以留白。
- 歌詞搜尋不限次數；產生新的 AI 同步時，每位使用者每天最多可送出 15 次。
- 歌詞必須和影片中實際唱出的內容一致。大量即興、重疊人聲、特殊發音或伴奏嚴重蓋過人聲時，
  對齊與音高辨識可能不夠準確。
- 音高偵測會先分離人聲，再使用 RMVPE；缺少或無法載入權重時會明確停用該次音高分析，
  不會以 FCPE 冒充 RMVPE。強烈殘響、和聲或分離後的樂器殘留仍可能造成八度或音符誤判。
- 隱私權政策：<https://everyric.moref.co/privacy>

## 原作者、資料來源與授權

- **原始專案與作者**：[onpe5679/Everyric2](https://github.com/onpe5679/Everyric2)，作者 onpe。
- **程式授權**：[Apache License 2.0](LICENSE)。其他著作權及非強制性致意說明請見
  [NOTICE](NOTICE)。
- **歌詞、發音與翻譯來源**：[Vocaloid Lyrics Wiki](http://vocaro.wikidot.com/)、
  [VocaloidLyrics Wiki](https://vocaloidlyrics.miraheze.org/)、[LRCLIB](https://lrclib.net/)
  及網易雲音樂。擴充功能會在可取得時顯示來源。
- **歌詞對齊**：[MMS wav2vec2](https://huggingface.co/facebook/mms-300m)。
- **音高偵測**：[RMVPE](https://github.com/Dream-High/RMVPE) 與
  [torchfcpe](https://github.com/CNChTu/FCPE)。
- **人聲分離**：[Demucs](https://github.com/facebookresearch/demucs)。
- **可選殘響抑制**：[NARA-WPE](https://github.com/fgnt/nara_wpe)。

第三方歌詞、模型與工具各自適用其原始授權條款；重新發布或商業使用前請另外確認。
