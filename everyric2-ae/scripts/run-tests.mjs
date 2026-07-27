import assert from "assert/strict";
import fs from "fs";
import os from "os";
import path from "path";
import { pathToFileURL, fileURLToPath } from "url";
import { build } from "esbuild";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const temp = fs.mkdtempSync(path.join(os.tmpdir(), "everyric-ae-tests-"));
const outfile = path.join(temp, "planner.mjs");

try {
  await build({
    entryPoints: [path.join(root, "src/panel/planner.ts")],
    outfile,
    bundle: true,
    platform: "node",
    format: "esm",
    target: "node18",
  });
  const { normalizeSyncPayload, planTypography, planLineLyrics, planLayerFill } = await import(
    `${pathToFileURL(outfile).href}?v=${Date.now()}`
  );

  const document = normalizeSyncPayload({
    metadata: { language: "ko" },
    results: [
      {
        text: "아무렇지 않은 척 너를 보내고 돌아섰지만",
        start_time: 1,
        end_time: 6,
        word_segments: Array.from("아무렇지않은척너를보내고돌아섰지만").map((word, index) => ({
          word,
          start: 1 + index * 0.2,
          end: 1.18 + index * 0.2,
        })),
      },
    ],
  });
  assert.equal(document.lines.length, 1);
  assert.equal(document.language, "ko");
  assert.ok(document.lines[0].atoms.length > 10);

  const options = {
    density: "balanced",
    layout: "auto",
    width: 1920,
    height: 1080,
    frameRate: 30,
    fontSize: 94,
    preRollFrames: 3,
    postRollFrames: 8,
    pauseThreshold: 0.32,
    maxBlocksPerCard: 4,
    phraseTargetChars: 9,
    maxTokensPerBlock: 4,
    revealMode: "cumulative",
  };
  const plan = planTypography(document, options, "TEST-GROUP");
  assert.ok(plan.blocks.length >= 2, "readable blocks should be generated");
  assert.ok(plan.blocks.every((block) => block.text.length > 1), "single character blocks are avoided");
  for (const card of plan.cards) {
    assert.ok(card.blocks.every((block) => block.end === card.end), "blocks accumulate to card end");
    assert.ok(card.blocks.every((block) => block.position[0] >= 0 && block.position[0] <= 1920));
    assert.ok(card.blocks.every((block) => block.position[1] >= 0 && block.position[1] <= 1080));
  }

  const phraseDocument = normalizeSyncPayload({
    results: [
      {
        text: "시작의 첫걸음이 꿈을 정한 그날의 마음이 저기 널 부르고 있는걸",
        start_time: 1,
        end_time: 11,
        word_segments: Array.from("시작의첫걸음이꿈을정한그날의마음이저기널부르고있는걸").map((word, index) => ({
          word,
          start: 1 + index * 0.32,
          end: 1.2 + index * 0.32,
        })),
      },
    ],
  });
  const phrasePlan = planTypography(phraseDocument, options, "PHRASE-GROUP");
  assert.ok(phrasePlan.blocks.length <= 4, "balanced mode should keep one lyric line in a readable card");
  assert.ok(
    phrasePlan.blocks.every((block) => block.text.includes(" ") || block.text.length >= 5),
    "balanced mode should avoid isolated short words",
  );

  const finePlan = planTypography(phraseDocument, {
    ...options,
    phraseTargetChars: 5,
    maxTokensPerBlock: 2,
  }, "FINE-GROUP");
  const coarsePlan = planTypography(phraseDocument, {
    ...options,
    phraseTargetChars: 16,
    maxTokensPerBlock: 7,
  }, "COARSE-GROUP");
  assert.ok(finePlan.blocks.length > coarsePlan.blocks.length, "phrase controls should change split granularity");

  const togetherPlan = planTypography(phraseDocument, {
    ...options,
    revealMode: "simultaneous",
  }, "TOGETHER-GROUP");
  for (const card of togetherPlan.cards) {
    assert.ok(card.blocks.every((block) => block.start === card.start), "together reveal should share one in-point");
  }

  const linePlan = planLineLyrics(phraseDocument, options, "LINE-GROUP");
  assert.equal(linePlan.blocks.length, phraseDocument.lines.length, "line lyric mode should create one text layer per sync line");
  assert.equal(linePlan.blocks[0].text, phraseDocument.lines[0].text, "line lyric mode should keep the original line text intact");

  const assignments = planLayerFill(document, [
    { index: 3, name: "A", inPoint: 1, outPoint: 3, text: "old", sourceTextKeys: 0, locked: false },
    { index: 2, name: "B", inPoint: 3, outPoint: 5, text: "old", sourceTextKeys: 1, locked: false },
    { index: 1, name: "C", inPoint: 3.35, outPoint: 7, text: "old", sourceTextKeys: 0, locked: false },
  ]);
  assert.equal(assignments.length, 3);
  assert.equal(assignments[1].skippedReason, "Source Text 키프레임 있음");
  assert.equal(assignments[1].text, "old");
  const assignedText = assignments.filter((item) => !item.skippedReason).map((item) => item.text).join(" ");
  assert.ok(assignedText.includes("아무렇지"));
  assert.ok(assignedText.includes("돌아섰지만"));

  const bounded = planLayerFill(document, [
    { index: 1, name: "Short", inPoint: 1, outPoint: 2.8, text: "old", sourceTextKeys: 0, locked: false },
  ]);
  assert.ok(!bounded[0].text.includes("돌아섰지만"), "tokens beyond the selected range must not be crammed into the final layer");

  const duplicatedTiming = planLayerFill(document, [
    { index: 10, name: "Main", inPoint: 1, outPoint: 3, text: "old", sourceTextKeys: 0, locked: false },
    { index: 11, name: "Shadow", inPoint: 1, outPoint: 3, text: "old", sourceTextKeys: 0, locked: false },
  ]);
  assert.equal(duplicatedTiming[0].text, duplicatedTiming[1].text);
  assert.ok(duplicatedTiming[0].text.length > 0, "duplicated layers with identical timing should receive the same lyric text");

  const overlappingTiming = planLayerFill(document, [
    { index: 20, name: "Wide", inPoint: 1, outPoint: 5, text: "old", sourceTextKeys: 0, locked: false },
    { index: 21, name: "Inset", inPoint: 2, outPoint: 4, text: "old", sourceTextKeys: 0, locked: false },
  ]);
  assert.ok(overlappingTiming[0].text.includes(overlappingTiming[1].text), "overlapping layers should not steal text from each other");

  const cutterOutfile = path.join(temp, "cutter.mjs");
  await build({
    entryPoints: [path.join(root, "src/panel/cutter.ts")],
    outfile: cutterOutfile,
    bundle: true,
    platform: "node",
    format: "esm",
    target: "node18",
  });
  const { buildCutSession, cutBlocker, toggleCut, moveCut, clampCutTime, computePieces, defaultCutTime, pieceWarnings } =
    await import(`${pathToFileURL(cutterOutfile).href}?v=${Date.now()}`);

  const cutDocument = normalizeSyncPayload({
    results: [
      {
        text: "君の名前を呼ぶよ",
        start_time: 10,
        end_time: 14,
        word_segments: Array.from("君の名前を呼ぶよ").map((word, index) => ({
          word,
          start: 10 + index * 0.5,
          end: 10.5 + index * 0.5,
        })),
      },
    ],
  });

  const wholeLine = { index: 4, name: "Lyric", inPoint: 10, outPoint: 14, text: "君の名前を呼ぶよ", sourceTextKeys: 0, locked: false };
  const exactSession = buildCutSession(wholeLine, cutDocument);
  assert.equal(exactSession.matchQuality, "exact", "identical text must match its sync line exactly");
  assert.equal(exactSession.chars.length, 8);
  assert.equal(exactSession.chars[0].start, 10);
  assert.equal(exactSession.chars[2].start, 11, "each character keeps its own measured atom time");
  assert.ok(exactSession.chars.every((entry) => !entry.interpolated), "character atoms are measurements, not estimates");

  // 배치 모드가 만든 블록 = 라인의 부분 문자열. 그 조각의 atom만 잘라 와야 한다.
  const blockLayer = { index: 5, name: "Block", inPoint: 12, outPoint: 14, text: "を呼ぶよ", sourceTextKeys: 0, locked: false };
  const blockSession = buildCutSession(blockLayer, cutDocument);
  assert.equal(blockSession.matchQuality, "substring");
  assert.equal(blockSession.chars.length, 4);
  assert.equal(blockSession.chars[0].start, 12, "substring match must offset into the line's atoms");

  const strangerLayer = { index: 6, name: "Stranger", inPoint: 11, outPoint: 13, text: "없는가사", sourceTextKeys: 0, locked: false };
  const timeSession = buildCutSession(strangerLayer, cutDocument);
  assert.equal(timeSession.matchQuality, "time", "unknown text falls back to the overlapping line");
  const orphanSession = buildCutSession(strangerLayer, null);
  assert.equal(orphanSession.matchQuality, "none");
  assert.equal(orphanSession.chars[0].start, 11, "without a sync line the layer's own range is spread evenly");
  assert.ok(orphanSession.chars.every((entry) => entry.interpolated), "evenly spread times must be marked as estimates");
  assert.ok(pieceWarnings(orphanSession, computePieces(orphanSession, toggleCut(orphanSession, [], 2))).some((text) => text.includes("균등 배분")));

  // 단어 단위 atom(영어 정렬)은 글자 수로 나눠 글자 시각을 만든다.
  const wordDocument = normalizeSyncPayload({
    results: [{ text: "hello world", start_time: 0, end_time: 2, words: [
      { word: "hello", start: 0, end: 1 },
      { word: "world", start: 1, end: 2 },
    ] }],
  });
  const wordSession = buildCutSession(
    { index: 7, name: "EN", inPoint: 0, outPoint: 2, text: "hello world", sourceTextKeys: 0, locked: false },
    wordDocument,
  );
  assert.equal(wordSession.chars.length, 11);
  assert.equal(wordSession.chars[0].start, 0);
  assert.ok(Math.abs(wordSession.chars[1].start - 0.2) < 1e-9, "word atoms are split evenly across their characters");
  assert.ok(wordSession.chars[0].interpolated, "characters derived from a word atom are estimates");
  assert.equal(wordSession.chars[5].visible, false, "the space between words is not a drawn character");

  assert.equal(toggleCut(exactSession, [], 0).length, 0, "the start of the line is not a cut point");
  assert.equal(toggleCut(exactSession, [], 8).length, 0, "the end of the line is not a cut point");
  const oneCut = toggleCut(exactSession, [], 2);
  assert.equal(oneCut.length, 1);
  assert.equal(oneCut[0].time, defaultCutTime(exactSession, 2));
  assert.equal(toggleCut(exactSession, oneCut, 2).length, 0, "clicking a cut again rejoins the pieces");

  const pieces = computePieces(exactSession, oneCut);
  assert.equal(pieces.length, 2);
  assert.equal(pieces[0].text, "君の");
  assert.equal(pieces[1].text, "名前を呼ぶよ");
  assert.equal(pieces[0].start, exactSession.inPoint, "the first piece keeps the layer's in point");
  assert.equal(pieces[1].end, exactSession.outPoint, "the last piece keeps the layer's out point");
  assert.equal(pieces[0].end, pieces[1].start, "pieces meet at the cut with no gap");
  assert.deepEqual(pieceWarnings(exactSession, pieces), []);
  assert.ok(pieceWarnings(exactSession, computePieces(exactSession, [])).some((text) => text.includes("자를 지점")));

  // 공백에서 자르면 조각 텍스트에 공백이 남지 않고, x 계산 기준도 실제 첫 글자로 좁혀진다.
  const spacedSession = buildCutSession(
    { index: 8, name: "Spaced", inPoint: 0, outPoint: 2, text: "hello world", sourceTextKeys: 0, locked: false },
    wordDocument,
  );
  const spacedPieces = computePieces(spacedSession, toggleCut(spacedSession, [], 5));
  assert.equal(spacedPieces[0].text, "hello");
  assert.equal(spacedPieces[1].text, "world");
  assert.equal(spacedPieces[1].charStart, 6, "the leading space is dropped from the piece and its character range");
  assert.equal(spacedPieces[1].head, "hello world", "head carries the prefix so the host can measure the offset");
  assert.equal(spacedPieces[0].head, "hello");

  const twoCuts = toggleCut(exactSession, oneCut, 5);
  assert.equal(clampCutTime(exactSession, twoCuts, 5, 0), oneCut[0].time + 1 / 30, "a cut cannot cross the one before it");
  assert.equal(clampCutTime(exactSession, twoCuts, 2, 99), twoCuts[1].time - 1 / 30, "a cut cannot cross the one after it");
  assert.equal(clampCutTime(exactSession, oneCut, 2, 0), exactSession.inPoint + 1 / 30, "a cut stays inside the layer");
  const moved = moveCut(exactSession, oneCut, 2, 11.4);
  assert.equal(moved[0].time, 11.4);
  assert.equal(moved[0].auto, false, "a dragged cut is no longer the computed default");
  assert.equal(computePieces(exactSession, moved)[0].end, 11.4, "dragging the boundary retimes the pieces");

  assert.equal(cutBlocker(wholeLine), undefined);
  assert.ok(cutBlocker({ ...wholeLine, locked: true }).includes("잠긴"));
  assert.ok(cutBlocker({ ...wholeLine, sourceTextKeys: 2 }).includes("키프레임"));
  assert.ok(cutBlocker({ ...wholeLine, text: "두\n줄" }).includes("여러 줄"));
  assert.ok(cutBlocker({ ...wholeLine, text: "君" }).includes("두 개 이상"));
  assert.ok(buildCutSession({ ...wholeLine, locked: true }, cutDocument).blocked, "a blocked layer still builds a session so the panel can explain why");

  const hostSource = fs.readFileSync(path.join(root, "src/jsx/host.ts"), "utf8");
  assert.ok(hostSource.includes('layer.comment = "EV2|"'), "generated ownership metadata must remain in layer comments");
  assert.ok(hostSource.includes('layer.name = "EV2 " + block.id + " · "'), "generated layer names should expose card-block ids and lyric text");
  assert.ok(hostSource.includes("if (payload.autoLabelColors)"), "label color cycling must be optional");
  assert.ok(hostSource.includes("layer.label = 1 + (cardNumber % 16)"), "optional label color cycling should still be available");
  assert.ok(hostSource.includes("function everyricRemoveGeneratedLayers"), "cleanup tool must remove generated Everyric layers explicitly");
  assert.ok(hostSource.includes("function everyricCreateLineMarkers"), "line timing markers must be explicit opt-in");
  assert.ok(hostSource.includes("marker.comment = lyric"), "line marker names should display lyric text");
  assert.ok(hostSource.includes("marker.chapter = metadata"), "line marker metadata should not replace visible lyric text");
  assert.ok(!/function everyricCreateTypography[\\s\\S]*new MarkerValue/.test(hostSource), "typography generation must not add timeline markers");
  assert.ok(hostSource.includes("function everyricSplitTextLayer"), "character cutting must be applied by a dedicated host function");
  assert.ok(hostSource.includes("$.global.everyricSplitTextLayer"), "the split function must be reachable from the panel");
  assert.ok(hostSource.includes("layer.duplicate()"), "pieces are duplicates so effects, masks and parenting survive the cut");
  assert.ok(hostSource.includes("headRect.width - pieceRect.width"), "prefix width must be measured by subtraction so trailing spaces count");
  assert.ok(hostSource.includes("function localShiftToComp"), "the horizontal shift must respect layer rotation and scale");
  assert.ok(/beginUndoGroup\("Everyric Studio - Split text layer"\)/.test(hostSource), "a cut must be a single undo step");
  assert.ok(/if \(sourceText\.numKeys > 0\)[\s\S]{0,200}자를 수 없습니다/.test(hostSource), "layers with Source Text keyframes must be refused by the host too");

  const versionOutfile = path.join(temp, "version.mjs");
  await build({
    entryPoints: [path.join(root, "src/panel/version.ts")],
    outfile: versionOutfile,
    bundle: true,
    platform: "node",
    format: "esm",
    target: "node18",
  });
  const { compareVersions, isNewerVersion, satisfiesRange, PANEL_VERSION } = await import(
    `${pathToFileURL(versionOutfile).href}?v=${Date.now()}`
  );
  assert.equal(compareVersions("2.1.0", "2.0.9"), 1);
  assert.equal(compareVersions("2.0.0", "2.0.0"), 0);
  assert.equal(compareVersions("0.9.0", "0.10.0"), -1);
  assert.ok(isNewerVersion("2.1.0", "2.0.0"));
  assert.ok(!isNewerVersion("2.0.0", "2.1.0"));
  assert.ok(!isNewerVersion("available", "2.0.0"), "unparseable versions must never suggest an update");
  assert.ok(satisfiesRange("0.4.2", ">=0.1.0 <1.0.0"));
  assert.ok(!satisfiesRange("1.0.0", ">=0.1.0 <1.0.0"));
  assert.ok(!satisfiesRange("weird", ">=0.1.0"), "unparseable versions never satisfy a range");
  const panelPackage = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8"));
  assert.equal(PANEL_VERSION, panelPackage.version, "PANEL_VERSION must match package.json version");

  console.log("Everyric Studio planner tests passed");
} finally {
  fs.rmSync(temp, { recursive: true, force: true });
}
