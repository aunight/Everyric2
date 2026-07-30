# 音高後端與 Dereverb A/B（2026-07-31）

## 環境

- Apple Silicon，PyTorch 2.8，裝置 `mps`
- RMVPE 權重 SHA-256：
  `6d62215f4306e3ca278246188607209f09af3dc77ed4232efdd069798c4ec193`
- NARA-WPE 0.0.11
- 三段現有歌唱素材，各取前 30 秒
- 同一原始音訊直接比較，這一輪未另外跑 Demucs，以免每次分離差異污染模型比較

## 結果摘要

| 條件 | 相鄰幀 >7 半音跳躍率（3 段平均） | 局部低八度比例（3 段平均） |
|---|---:|---:|
| RMVPE | 0.98% | 0.55% |
| RMVPE + WPE | 1.14% | 1.05% |
| FCPE | 4.44% | 5.88% |
| FCPE + WPE | 4.43% | 7.76% |

三段素材上，RMVPE 的大跳與局部低八度比例都一致低於 FCPE。WPE 在這三段素材沒有改善
這兩個指標，反而略為惡化，因此保持預設關閉，只提供給殘響特別嚴重、且有逐曲 A/B
證據的情況。

這些是沒有人工標註真值的穩定度指標，不能單獨證明每一幀音高正確；它們只驗證先前觀察到的
八度鎖定與大跳失敗模式。

## GAME 與 MuScriptor 的定位

- [GAME](https://github.com/openvpi/GAME) 專為歌聲轉樂譜，能從原始歌聲產生 MIDI，也能配合
  已知 word boundaries；這和 Everyric2 的「目標音符」語義相符。但官方目前列出的測試環境
  是 Python 3.12、PyTorch 2.8、CUDA 12.9，尚未證明本專案的 Apple MPS 部署可直接使用。
- [MuScriptor](https://github.com/muscriptor/muscriptor) 是一般多樂器轉譜模型，不是歌聲專用。
  模型從約 100M 到 1.3B 參數，且權重採 CC BY-NC；它不適合直接取代目前可在 MPS 即時執行的
  RMVPE，也不適合在未釐清服務用途授權前放進公開伺服器。

因此正式流程保留 RMVPE 做逐幀 f0 與即時曲線；若未來要更換「目標音符切分／樂譜」，
優先另做 GAME 的離線可選後端，而不是以 MuScriptor 取代 RMVPE。
