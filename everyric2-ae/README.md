# Everyric Studio for After Effects

> **繁體中文**：本面板來自 **onpe（[onpe5679](https://github.com/onpe5679)）**的
> [Everyric2 原始專案](https://github.com/onpe5679/Everyric2)；此目錄包含 fork 維護者的
> 修改，並依 Apache License 2.0 發布。
>
> **日本語**：このパネルは **onpe（[onpe5679](https://github.com/onpe5679)）**氏の
> [Everyric2 オリジナルプロジェクト](https://github.com/onpe5679/Everyric2)を基にした
> フォーク版で、Apache License 2.0 の下で公開されています。
>
> **English**: This panel is based on the
> [original Everyric2 project](https://github.com/onpe5679/Everyric2) by
> **onpe ([onpe5679](https://github.com/onpe5679))**. This directory includes fork-maintained
> modifications distributed under the Apache License 2.0.

Everyric Studio converts Everyric2 alignment data into editable After Effects typography. It creates
ordinary text layers, static transforms, layer in/out points, and layer-comment metadata only. It does not
create Text Animators or motion keyframes.

## Modes

- **Fill selected layers** keeps each selected layer's bounds and visual design, replacing only its
  Source Text with the timed lyric content that best fits the interval.
- **Build typography** converts alignment atoms into readable blocks and screen cards. Blocks reveal
  cumulatively or together and share a card exit time.

- **Character cut** takes a layer that already sits on the timeline and splits it between two
  characters. The split time comes from the alignment atoms, so separating the layer and retiming
  the pieces are the same action. Clicking a cut again rejoins it; dragging a boundary retimes it.
  By default each piece keeps the position its characters were drawn at, so one line can be revealed
  left to right; a toggle keeps every piece at the original position instead.

## Split and timing controls

`Readable`, `Balanced`, and `Rhythmic` are starting presets, not fixed answers. Mode B exposes the
underlying controls so each video can choose its own pacing:

- phrase target length (characters)
- maximum words per block
- pause-cut sensitivity
- cumulative phrase reveal or simultaneous line reveal
- pre-roll, post-roll, and maximum blocks per card

The current `Balanced` defaults are 9 characters, 4 words, a 0.32 second pause threshold, and
cumulative reveal.

## Development

```powershell
cd everyric2-ae
npm install
npm run build
npm run install-plugin
```

Open After Effects and choose **Window → Extensions → Everyric Studio**. Use **구조 테스트용
컴포지션 만들기** from the settings drawer for a self-contained structural check. A real workflow
still requires a file-backed vocal audio layer and its lyrics.

For local alignment, set the Python executable to the environment where Everyric2 is installed. The
panel runs:

```text
python -m everyric2.cli sync <audio> <lyrics> --output <json> --format json
```

The plugin can also load CLI arrays, server `{segments: [...]}` responses, and `.everyric.json`
project files. No credential is embedded in the extension.

Paste a YouTube URL to pull a sync the server already has (`GET /api/sync/{video_id}`), including
its translation and pronunciation. This is lookup only — the panel never asks the server to
generate a sync, because that endpoint requires a `video_id` and cannot take a local audio file.

## Engine runtime

The ZXP ships an embedded Python 3.11. On first install it is copied to
`%LOCALAPPDATA%\Everyric\runtime` and the engine is installed there, so updating the panel never
touches the installed engine. Models stay in the default HuggingFace cache in the user's home
directory — **never redirect `HF_HOME` into the extension or the managed folder**, or every panel
update would re-download gigabytes. A test in `scripts/run-tests.mjs` enforces this.

`npm run build:runtime` builds the embedded runtime (Windows only; downloads python.org's
embeddable package and bootstraps pip). Without it the ZXP is still valid — the panel then falls
back to downloading uv and a Python build on first run. Dev installs link `runtime/` as a junction
instead of copying it.

## Release

```powershell
npm run release:zxp   # build + signed .zxp + manual zip + SHA256SUMS into release/
```

Signing resolves a certificate in this order: `EVERYRIC_CERT_PATH`/`EVERYRIC_CERT_PASSWORD` env vars →
`../secrets/ElysianCert.p12` (password from env or `../secrets/cert-password.txt`) → an auto-generated
10-year self-signed certificate in `../secrets/`. ZXPSignCmd is downloaded to `scripts/.tools/` on first use.

Publishing: bump the version in `package.json`, `CSXS/manifest.xml`, and `src/panel/version.ts`
(the build fails if they disagree), then push a `ae-v<version>` tag. GitHub Actions builds, signs
(secrets `ZXP_CERT_BASE64` + `ZXP_CERT_PASSWORD`, otherwise self-signed), attaches release assets, and
updates the root `latest.json` that deployed panels poll for update badges and managed engine installs.
Engine releases follow the same flow with `engine-v<version>` tags (version source: `pyproject.toml`).
