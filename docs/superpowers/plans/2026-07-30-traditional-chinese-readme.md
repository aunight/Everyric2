# Traditional Chinese README Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the multilingual, developer-focused root README with a concise Traditional Chinese
guide for Chrome extension users.

**Architecture:** Keep all user-facing documentation in the root `README.md` and enforce its scope
with a small Node test. Preserve attribution, licensing, release, privacy, and screenshot links while
removing duplicated language guides and server-development documentation.

**Tech Stack:** Markdown, Node.js built-in test runner, Git

---

### Task 1: Define the README scope with regression tests

**Files:**
- Modify: `everyric2-chrome/tests/readme-features.test.mjs`
- Test: `everyric2-chrome/tests/readme-features.test.mjs`

- [ ] **Step 1: Replace the trilingual README assertions with the approved scope**

```js
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const readme = readFileSync(new URL('../../README.md', import.meta.url), 'utf8');

test('README is a Traditional Chinese guide for Chrome extension users', () => {
  assert.match(readme, /^# Everyric2/m);
  assert.match(readme, /## 主要功能/);
  assert.match(readme, /## 此 fork 新增與調整/);
  assert.match(readme, /## 安裝 Chrome 擴充功能/);
  assert.match(readme, /## 基本使用方法/);
  assert.match(readme, /## 注意事項/);
  assert.match(readme, /## 原作者、資料來源與授權/);
  assert.match(readme, /chrome:\/\/extensions/);
  assert.match(readme, /Everyric-Chrome-<版本>\.zip/);
});

test('README omits duplicated language guides and developer-only sections', () => {
  assert.doesNotMatch(readme, /^## 한국어 사용 안내/m);
  assert.doesNotMatch(readme, /^## English Guide/m);
  assert.doesNotMatch(readme, /^## 日本語ガイド/m);
  assert.doesNotMatch(readme, /^## 自架伺服器/m);
  assert.doesNotMatch(readme, /^## 伺服器 API/m);
  assert.doesNotMatch(readme, /^## 設定（環境變數）/m);
  assert.doesNotMatch(readme, /^## CLI/m);
  assert.doesNotMatch(readme, /^## 開發/m);
});

test('fork README distinguishes new scoring options from existing features', () => {
  assert.match(readme, /採點顯示方式新增「目標命中音符」顯示模式/);
  assert.match(readme, /唱名標記只新增「關閉」選項/);
});

test('fork README does not claim score persistence or existing pitch modes as new', () => {
  assert.doesNotMatch(readme, /新增麥克風即時音高偵測/);
  assert.doesNotMatch(readme, /分數保存|スコア保存|persistent scores/);
  assert.doesNotMatch(readme, /switchable target-note/);
});
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
cd everyric2-chrome
node --experimental-strip-types --test tests/readme-features.test.mjs
```

Expected: the first two tests fail because the current README still contains multilingual guides and
developer-only sections.

### Task 2: Rewrite the root README for general users

**Files:**
- Modify: `README.md`
- Test: `everyric2-chrome/tests/readme-features.test.mjs`

- [ ] **Step 1: Replace the existing README with the approved Traditional Chinese structure**

The new document must contain these sections in order:

1. `# Everyric2`
2. A one-paragraph Traditional Chinese description
3. A concise Traditional Chinese fork and original-author notice
4. Existing overlay and karaoke screenshots with Traditional Chinese captions
5. `## 主要功能`
6. `## 此 fork 新增與調整`
7. `## 安裝 Chrome 擴充功能`
8. `## 基本使用方法`
9. `## 注意事項`
10. `## 原作者、資料來源與授權`

The `主要功能` section must cover:

- YouTube lyrics overlay and character-level karaoke highlighting
- automatic lyrics lookup and AI synchronization
- translations and pronunciation labels
- karaoke pitch lane and microphone pitch comparison
- linked timelines for instrumental and cover videos

The `此 fork 新增與調整` section must preserve the seven approved feature groups:

- Traditional Chinese UI and OpenCC conversion
- Apple Silicon MPS acceleration
- OpenAI-compatible translation API
- NetEase lyrics source and title/credit cleanup
- Kanji with Hiragana furigana
- target-note hit display, dimmed past lyrics, Chinese original note labels, and only the new solfège
  Off option
- layout, glass surface, language ordering, and immediate transcription progress feedback

The installation section must use these exact user actions:

1. Download `Everyric-Chrome-<版本>.zip` from
   `https://github.com/aunight/Everyric2/releases`.
2. Unzip the archive.
3. Open `chrome://extensions`.
4. Enable Chrome developer mode.
5. Choose `載入未封裝項目` and select the unzipped folder.

The basic usage section must explain:

- opening a YouTube music video
- generating sync for an unknown song
- showing translation and pronunciation from settings
- opening karaoke scoring and allowing microphone permission
- switching between `線條軌跡` and `命中音符（日K）`

The notes section must retain:

- default server URL `https://everyric.moref.co`
- no API key needed for the default server
- lookup is unlimited and new sync generation is limited to 15 requests per user per day
- privacy policy `https://everyric.moref.co/privacy`
- lyrics must match the performed audio and overlapping vocals or heavy ad-libs may reduce accuracy

The attribution section must retain:

- original project link `https://github.com/onpe5679/Everyric2`
- original author `onpe`
- `LICENSE` and `NOTICE`
- lyrics sources: Vocaro Lyrics Wiki, VocaloidLyrics Wiki, LRCLIB, and NetEase Cloud Music
- model/tool credits: MMS wav2vec2, RMVPE, torchfcpe, and demucs

- [ ] **Step 2: Run the focused README tests**

Run:

```bash
cd everyric2-chrome
node --experimental-strip-types --test tests/readme-features.test.mjs
```

Expected: 4 tests pass and 0 fail.

- [ ] **Step 3: Run Markdown and content checks**

Run:

```bash
git diff --check
rg -n '^## (한국어 사용 안내|English Guide|日本語ガイド|自架伺服器|伺服器 API|設定（環境變數）|CLI|開發)' README.md
```

Expected: `git diff --check` exits successfully and `rg` returns no matches.

- [ ] **Step 4: Run the complete Chrome extension verification**

Run:

```bash
cd everyric2-chrome
npm test
npm run typecheck
npm run build
node scripts/score-check.mjs
```

Expected: all tests pass, TypeScript reports no errors, Vite builds successfully, and the scoring
check prints `score-check ok`.

- [ ] **Step 5: Commit the implementation**

```bash
git add README.md everyric2-chrome/tests/readme-features.test.mjs
git commit -m "docs: simplify README for Traditional Chinese users"
```
