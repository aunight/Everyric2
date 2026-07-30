# Hiragana Pronunciation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve Japanese lyric kanji while attaching Hiragana pronunciation in normal lyrics and karaoke scoring, including old cached Katakana data.

**Architecture:** Keep the existing `kana` wire key for compatibility. Emit Hiragana from the Python worker for new data, and normalize Katakana to Hiragana in the Chrome shared pronunciation resolver so every current rendering surface and old cache follows the same rule.

**Tech Stack:** Python 3.10+, pytest, TypeScript, Node test runner, Chrome MV3/Vite.

---

### Task 1: Lock the backend pronunciation contract

**Files:**
- Modify: `tests/test_worker_pron_dict.py`

- [ ] **Step 1: Change Japanese, Korean, mixed-language and API expectations to Hiragana**

Update representative assertions so `pron["kana"]` and `pron_segs["kana"]` expect values such as
`"あるばいと わ ねくら もーど"`, `"さらんへ"`, and segment text `"て"` rather than Katakana.
Retain assertions that rebuilding segments exactly equals the display string.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
source .venv/bin/activate
pytest -q tests/test_worker_pron_dict.py
```

Expected: failures show actual Katakana where Hiragana is expected.

### Task 2: Emit Hiragana from the worker

**Files:**
- Modify: `everyric2/text/ja_reading.py`
- Modify: `everyric2/server/worker.py`
- Test: `tests/test_worker_pron_dict.py`

- [ ] **Step 1: Expose the existing safe converter**

Rename the private helper to:

```python
def katakana_to_hiragana(text: str) -> str:
    return "".join(
        chr(ord(ch) - 0x60) if _KATAKANA_START <= ch <= _KATAKANA_END else ch
        for ch in text
    )
```

Update internal `ja_reading.py` callers.

- [ ] **Step 2: Stop converting Japanese moras to Katakana**

In `_attach_ja_kana_variant`, use the Hiragana mora value directly:

```python
kana_tokens = [m.kana for m in text_to_moras(text, tokens=mora_tokens_source)]
```

Update docstrings to describe Hiragana while keeping the `kana` key.

- [ ] **Step 3: Normalize Korean and Latin transliterations**

Wrap `hangul_to_kana(text)`, `latin_to_kana(text)`, and Korean mora segment text with
`katakana_to_hiragana(...)`, so every value stored under the `kana` key follows the same contract.

- [ ] **Step 4: Run backend tests and verify GREEN**

Run:

```bash
source .venv/bin/activate
pytest -q tests/test_worker_pron_dict.py
```

Expected: both files pass with display/segment invariants intact.

### Task 3: Normalize old Chrome cache data

**Files:**
- Create: `everyric2-chrome/tests/hiragana-pronunciation.test.mjs`
- Modify: `everyric2-chrome/src/lib/lang.ts`

- [ ] **Step 1: Write frontend regression tests**

Test both the line string and timed segments:

```javascript
assert.equal(
  resolvedPronunciation({ pron: { kana: '僕ハ歌ウ ボク' } }, 'kana'),
  '僕は歌う ぼく',
);
assert.deepEqual(
  resolvedPronSegments({
    pronSegsByScript: {
      kana: [{ text: 'ボ', start: 1, end: 1.2 }, { text: 'ク', start: 1.2, end: 1.4 }],
    },
  }, 'kana'),
  [{ text: 'ぼ', start: 1, end: 1.2 }, { text: 'く', start: 1.2, end: 1.4 }],
);
```

Also assert that the source segment array is not mutated.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
cd everyric2-chrome
node --experimental-strip-types --test tests/hiragana-pronunciation.test.mjs
```

Expected: old Katakana values are returned unchanged.

- [ ] **Step 3: Add shared normalization**

Add a Katakana-to-Hiragana function in `src/lib/lang.ts`. Apply it only when
`script === "kana"` in both `resolvedPronunciation` and `resolvedPronSegments`; clone changed segment
objects and preserve timing/confidence fields.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the same Node command. Expected: all tests pass and source objects remain unchanged.

### Task 4: Rename visible controls

**Files:**
- Modify: `everyric2-chrome/_locales/zh_TW/messages.json`
- Modify: `everyric2-chrome/_locales/en/messages.json`
- Modify: `everyric2-chrome/_locales/ja/messages.json`
- Modify: `everyric2-chrome/_locales/ko/messages.json`
- Modify: `everyric2-chrome/src/ui/pip.ts`
- Modify: `everyric2-chrome/tests/hiragana-pronunciation.test.mjs`

- [ ] **Step 1: Add locale/control source assertions**

Assert the four locale values are `平假名`, `Hiragana`, `ひらがな`, and `히라가나`, and the PiP
short label uses `ひら`.

- [ ] **Step 2: Run the frontend test and verify RED**

Run the focused Node test. Expected: the current generic Kana labels fail.

- [ ] **Step 3: Update all visible labels**

Update `overlay_settings_pronScript_kana`, each pronunciation toggle tooltip, and the PiP button
label. Keep the setting value `kana`.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the focused Node test. Expected: all Hiragana wording assertions pass.

### Task 5: Render Hiragana as furigana over kanji

**Files:**
- Create: `everyric2-chrome/tests/ruby-readings.test.mjs`
- Modify: `everyric2-chrome/tests/pitch-ui.test.mjs`
- Modify: `everyric2-chrome/src/ui/karaoke.ts`
- Modify: `everyric2-chrome/src/ui/overlay.ts`
- Modify: `everyric2-chrome/src/ui/pip.ts`
- Modify: `everyric2-chrome/public/overlay.css`

- [ ] **Step 1: Write failing word-to-reading and rendering contract tests**

Use Japanese `未来は変わる` fixtures with word timing and Hiragana mora timing. Assert that only
the kanji words receive readings, multiple mora concatenate on the same kanji, normal lyrics/PiP
use `<ruby><rt>`, and the scoring canvas draws pronunciation above the base lyric.

- [ ] **Step 2: Run the focused tests and verify RED**

```bash
cd everyric2-chrome
node --experimental-strip-types --test tests/ruby-readings.test.mjs tests/pitch-ui.test.mjs
```

Expected: the ruby mapper and inline rendering contracts are absent.

- [ ] **Step 3: Add a shared timing-overlap mapper**

Add `buildKanjiRubyReadings(line, segments)` to `src/ui/karaoke.ts`. Match every pronunciation
segment to the original word with the greatest positive time overlap, append readings only when
that original word contains kanji, and return a `Map<WordSegment, string>`.

- [ ] **Step 4: Render ruby in both lyric surfaces**

In the overlay and PiP current-line callbacks, render:

```html
<span class="ey-word">
  <ruby class="ey-ruby">未<rt>み</rt></ruby>
</span>
```

Skip the duplicate standalone pronunciation row when at least one reliable ruby annotation exists.
Keep the row as fallback when word timing is unavailable.

- [ ] **Step 5: Put scoring pronunciation above kanji**

Keep `PitchNote.lyric` and `PitchNote.pron` independent, but calculate the two canvas baselines as a
single ruby group: small pronunciation first and bold lyric directly below it.

- [ ] **Step 6: Add ruby CSS and verify GREEN**

Style `rt` at roughly half the base size, centered above the kanji, inheriting current/past karaoke
color. Hide `rt` with the existing pronunciation-off state.

Run the focused Node command again. Expected: all ruby and pitch UI tests pass.

### Task 6: Full verification

**Files:**
- Verify all modified files above.

- [ ] **Step 1: Run the complete backend suite**

```bash
source .venv/bin/activate
pytest -q
```

Expected: zero failures.

- [ ] **Step 2: Run the complete Chrome suite**

```bash
cd everyric2-chrome
npm test
npm run typecheck
npm run build
```

Expected: all tests pass, TypeScript exits 0, and Vite build exits 0.

- [ ] **Step 3: Validate locale JSON**

```bash
python -m json.tool everyric2-chrome/_locales/zh_TW/messages.json >/dev/null
python -m json.tool everyric2-chrome/_locales/en/messages.json >/dev/null
python -m json.tool everyric2-chrome/_locales/ja/messages.json >/dev/null
python -m json.tool everyric2-chrome/_locales/ko/messages.json >/dev/null
```

Expected: all four commands exit 0.

- [ ] **Step 4: Review the final diff against the acceptance criteria**

Confirm that original lyric text is untouched, both pronunciation resolvers normalize old data,
new worker payloads are Hiragana, and no timing or scoring behavior changed.
