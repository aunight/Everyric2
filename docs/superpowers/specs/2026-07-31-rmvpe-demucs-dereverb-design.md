# RMVPE、Demucs 高品質模式與音高支線 Dereverb 設計

## Problem 1-Pager

### Background

Everyric2 的採點目標音符由伺服器先分離人聲，再估計全曲 f0。設定預設寫成
`f0_model="rmvpe"`，但目前本機沒有 `models/rmvpe/rmvpe.pt`；載入失敗時程式會捕捉所有
例外並靜默改跑 FCPE。因此使用者看到的是 RMVPE 設定，實際結果卻可能來自 FCPE。強殘響、
和聲與分離殘留也會讓 f0 出現八度鎖定或錯誤音符。

### Problem

- RMVPE 缺權重或載入失敗時，後端身分不可信。
- RMVPE 的可用性仍錯誤地綁定 `torchfcpe`。
- Demucs 雖接受任意模型字串，沒有驗證或清楚標示 `htdemucs_ft` 高品質選項。
- 沒有只供音高辨識使用的 Dereverb；若直接處理共用 vocals，CTC 對齊也會被改變。
- 缺少可重現的模型／前處理 A/B 指標。

### Goal

- 選 RMVPE 時只允許真正的 RMVPE；缺權重或載入錯誤要明確失敗，絕不靜默換成 FCPE。
- 提供有 SHA-256 驗證的 RMVPE 權重下載工具，並在本機以 MPS 完成實際推論驗證。
- 將 `htdemucs_ft` 做成受驗證且有文件的高品質選項；保留較快的 `htdemucs` 預設值。
- 提供預設關閉的 WPE Dereverb，只在送進 f0 模型的波形副本上執行。
- 以現有歌唱素材輸出 FCPE／RMVPE 與 Dereverb 開關的可比較統計。

### Non-goals

- 本階段不把 GAME 或 MuScriptor 直接放進正式產生流程。
- 不改 Chrome 麥克風即時音高偵測。
- 不讓 Dereverb 改寫共用 Demucs stem、CTC 對齊輸入或原始音訊。
- 不把 181 MB RMVPE 權重提交到 Git。

### Constraints

- Apple Silicon 必須繼續使用共用 `resolve_device()`，RMVPE 在 MPS 執行。
- 選用功能不得在未安裝相依套件時假裝成功。
- 長音訊仍沿用既有 f0 chunking；Dereverb 先於 f0 chunking，且輸出長度必須與輸入一致。
- 模型檔必須先核對官方發布的 SHA-256 才原子性寫入目標路徑。

## Design

### 1. Strict backend contract

`MelodyExtractor.is_available()` 依所選後端判定：

- `fcpe`：需要 `torchfcpe`。
- `rmvpe`：需要 PyTorch 與存在的 RMVPE 權重。

`_get_model()` 不再捕捉 RMVPE 載入錯誤並回退。缺檔時拋出包含下載指令與實際路徑的
`PitchBackendUnavailableError`；載入成功後明確記錄後端與裝置。

### 2. Verified weight installer

新增 `scripts/download_rmvpe.py`，從 RVC 生態系使用的
`lj1995/VoiceConversionWebUI/rmvpe.pt` 官方模型檔下載，串流計算 SHA-256，只有雜湊符合
`6d62215f4306e3ca278246188607209f09af3dc77ed4232efdd069798c4ec193` 才以原子 rename
放入 `models/rmvpe/rmvpe.pt`。中斷或驗證失敗不留下可被載入的半成品。

### 3. Demucs model validation

`AudioSettings.demucs_model` 改成已知模型的 `Literal`，其中
`htdemucs_ft` 是明確的高品質選項。預設仍為 `htdemucs`，因官方說明指出 fine-tuned
版本可能稍佳，但約慢四倍。自架伺服器可用
`EVERYRIC_AUDIO_DEMUCS_MODEL=htdemucs_ft` 啟用。

### 4. Pitch-only WPE Dereverb

新增 `everyric2.audio.dereverb.dereverb_for_pitch()`，使用 NARA-WPE 的 NumPy offline WPE。
設定 `EVERYRIC_MELODY_DEREVERB=true` 時，`MelodyExtractor._infer_f0()` 在選定 vocals/mix
後只處理本地波形副本，再交給 RMVPE／FCPE。傳給 CTC 的 `align_audio` 與共用 vocals
物件不變。

Dereverb 啟用但 `nara_wpe` 未安裝時拋出清楚的 `DereverbUnavailableError`；不靜默跳過。
輸出會裁切／補零至原長度並轉回有限的 `float32`，避免時間軸漂移。

### 5. A/B report

新增腳本針對同一人聲音訊輸出：

- voiced ratio；
- 大於 7 半音的相鄰幀跳躍率；
- 低八度（相對局部中位數約 -12 半音）比例；
- 執行時間與實際 backend。

GAME 是歌聲轉目標樂譜的模型，MuScriptor 是更廣泛的多樂器轉譜模型；兩者和 RMVPE
逐幀 f0 的輸出語義不同，不以同一個「真值缺失」統計硬排排名。這一輪先保留 A/B
輸入／輸出介面與研究結論，不把新模型直接加入正式流程。
