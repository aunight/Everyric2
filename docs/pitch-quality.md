# 音高辨識品質設定

這份文件供自架 Everyric2 伺服器使用。一般只安裝 Chrome 擴充功能的使用者不需要執行以下
步驟。

## 安裝模型與相依套件

先安裝人聲分離與可選 Dereverb 相依套件，再下載經 SHA-256 驗證的 RMVPE 權重：

```bash
source .venv/bin/activate
pip install -e ".[separator,dereverb]"
python scripts/download_rmvpe.py
```

RMVPE 缺少權重或載入失敗時會明確停用該次音高分析，不會在未告知的情況下改用 FCPE。
若要刻意使用 FCPE，請設定：

```bash
EVERYRIC_MELODY_F0_MODEL=fcpe \
uvicorn everyric2.server.main:app --port 8000
```

## 高品質人聲分離

預設使用速度較快的 `htdemucs`。需要較高品質的人聲分離時，可切換官方 fine-tuned 模型
`htdemucs_ft`；官方說明指出它可能稍好，但約慢四倍：

```bash
EVERYRIC_AUDIO_DEMUCS_MODEL=htdemucs_ft \
uvicorn everyric2.server.main:app --port 8000
```

## 僅音高支線的 Dereverb

只有殘響明顯、且實測對該曲有幫助時才建議開啟 WPE Dereverb。它只處理送進音高模型的音訊
副本，不會改變 CTC 歌詞對齊使用的人聲：

```bash
EVERYRIC_MELODY_DEREVERB=true \
uvicorn everyric2.server.main:app --port 8000
```

目前三段歌唱素材的 A/B 結果未顯示 WPE 有一致改善，因此預設保持關閉。詳細數據請見
[音高後端與 Dereverb A/B](research/pitch-backend-ab-2026-07-31.md)。
