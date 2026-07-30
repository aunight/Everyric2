# Apple Silicon 推論穩定性與進度顯示設計

## 背景

Everyric2 本機伺服器已將 CTC 與 RMVPE 的 `auto` 裝置解析加入 Apple MPS。M1 Pro 16 GB
實機中，連續工作出現 `MPS backend out of memory`、`Invalid buffer size: 17.60 GiB`
與 `Invalid buffer size: 10.66 GiB`。失敗工作停留在「對齊歌詞 100%／總進度 72%」。

## 問題

- CTC 使用最長 360 秒的單一 forward，普通歌曲仍可能形成超大型 MPS buffer。
- RMVPE 全曲音高推論與 CTC 同時在同一 MPS 裝置執行，疊加記憶體峰值。
- 工作後的 GPU 回收只處理 CUDA；MPS 失敗後實測仍保留約 14.8 GB physical footprint。
- Demucs 呼叫端以 `torch.cuda.is_available()` 判斷是否使用加速，Apple Silicon 因而固定走 CPU。
- 時間型進度監視器會在實際階段尚未返回前抵達階段上限，造成「100%」假完成。

## 目標

- MPS CTC forward 使用不超過 90 秒的重疊分段。
- MPS 上的 RMVPE 與 CTC 串行執行；CUDA 保留既有平行路徑。
- 工作成功或失敗後同步 MPS、清空未使用 cache，必要時丟棄暖模型。
- Demucs 在 MPS 可用時收到 MPS 加速指示，失敗仍可退回 CPU。
- 執行中的近似進度不顯示階段 100%；只有階段切換後才進入下一個視窗。

## 非目標

- 不改 CTC、RMVPE 或 Demucs 模型本身。
- 不關閉旋律／音高功能。
- 不設定 `PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0`，避免取消保護後拖垮整個系統。
- 不改 CUDA 工作站已驗證的 360 秒分段與 CTC/RMVPE 平行策略。

## 約束

- 維持單一工作併發上限與既有輸出資料格式。
- MPS 不支援的 forced alignment 仍在 CPU 執行。
- 回收函式在 torch 未安裝、CPU-only 或後端 API 不可用時必須安全 no-op。
- 所有裝置分支以可單元測試的純判斷或窄介面呈現，不依賴實機 GPU 才能測試。

## 設計

### MPS 分段

CTC 引擎根據實際裝置計算有效分段長度：MPS 將設定值限制為 90 秒；CUDA 與 CPU 維持原設定。
既有重疊切片與 CPU stitching 不變，避免重新設計時間軸。

### MPS 串行

工作器只在非 MPS 裝置預先啟動 `precompute_f0` 執行緒。MPS 路徑等 CTC 與時間修正完成後，
在「旋律分析」階段由既有 `annotate_timestamps(..., precomputed_f0=None)` 同步推論。

### 記憶體回收

`gpu_mem.release_scratch()` 分別支援 CUDA 與 MPS：

- CUDA 保持 `synchronize → empty_cache → ipc_collect`。
- MPS 使用 `torch.mps.synchronize → torch.mps.empty_cache`，並以 driver allocated memory
  回報剩餘 GiB。
- 超過既有 guard 閾值時清除暖模型、執行 Python GC，再次清空後端 cache。

### Demucs

Demucs 呼叫端改用共用 `resolve_device()` 判斷是否存在加速器，不再把「GPU」等同 CUDA。
分離器仍負責把實際裝置名稱傳給 Demucs 與 GPU 失敗後的 CPU fallback。

### 進度

時間型階段監視器在當前視窗最多走到 `hi - 1`。下一個階段真正呼叫 `report()` 後才跳到下一
視窗的 `lo`，因此執行中不再顯示 100%。

## 驗證

- GPU-free 單元測試覆蓋 MPS 回收、MPS 分段、MPS 串行與 Demucs 裝置判斷。
- 進度測試確認長時間停留同一階段不會抵達 100%，切換後仍單調前進。
- 執行相關 pytest、完整 pytest、Ruff 與 Chrome build。
- 重啟本機伺服器後確認 `/health` 回報 GPU 可用，閒置 physical footprint 不再維持在失敗
  工作的 15 GB 峰值。
