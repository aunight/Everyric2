// 실제 곡의 싱크 데이터로 커팅을 끝까지 돌린다.
//
// 합성 문자열이 아니라 정렬 엔진이 낸 진짜 글자 타이밍을 쓴다. 패널이 하는 일을 그대로
// 따라간다: normalizeSyncPayload → buildCutSession → computePieces → everyricSplitTextLayer.
// 만들어진 컴포지션은 지우지 않는다 — 눈으로 확인하라고 남긴다.
//
//   node scripts/ae-real-song-test.mjs [sync.json]
//
// 요구: After Effects에서 패널이 열려 있을 것.
import { execFileSync } from "child_process";
import fs from "fs";
import os from "os";
import path from "path";
import { fileURLToPath } from "url";
import { build } from "esbuild";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const syncPath = path.resolve(process.argv[2] ?? path.join(root, "artifacts", "real-song-sync.json"));
const JAPANESE_FONT = process.env.EVERYRIC_TEST_FONT || "YuGothic-Medium";
// 곡 전체를 자른다. 앞 몇 줄만 보면 후렴 반복 같은 실제 구조가 드러나지 않는다.
const LINES_TO_CUT = Number(process.env.EVERYRIC_TEST_LINES || 0) || Infinity;
// 오디오가 있으면 컴포지션에 얹는다 — 소리 없이는 타이밍이 맞는지 볼 방법이 없다.
const audioPath = process.env.EVERYRIC_TEST_AUDIO ?? path.join(root, "artifacts", "audio", "9UFU4VmcNrA.wav");

if (!fs.existsSync(syncPath)) {
  console.error(`[real-song] 싱크 JSON이 없습니다: ${syncPath}`);
  process.exit(1);
}

const temp = fs.mkdtempSync(path.join(os.tmpdir(), "everyric-real-song-"));
try {
  const bundle = path.join(temp, "panel.mjs");
  await build({
    outfile: bundle,
    bundle: true,
    platform: "node",
    format: "esm",
    target: "node18",
    absWorkingDir: root,
    stdin: {
      contents: `export { normalizeSyncPayload } from "${path.join(root, "src/panel/planner").replace(/\\/g, "/")}";
export { buildCutSession, computePieces, defaultCutTime, toggleCut, pieceWarnings } from "${path.join(root, "src/panel/cutter").replace(/\\/g, "/")}";`,
      resolveDir: root,
      loader: "ts",
    },
  });
  const panel = await import(`${new URL(`file:///${bundle.replace(/\\/g, "/")}`).href}?v=${Date.now()}`);

  const payload = JSON.parse(fs.readFileSync(syncPath, "utf8"));
  const document = panel.normalizeSyncPayload(payload, path.basename(syncPath));
  console.log(`[real-song] ${document.lines.length}줄 · 언어 ${document.language} · ${document.duration.toFixed(1)}초`);

  // 자를 보람이 있는 줄만 고른다: 글자가 넉넉하고 atom이 실제로 붙어 있는 줄.
  const candidates = document.lines
    .filter((line) => Array.from(line.text.replace(/\s/g, "")).length >= 4 && line.atoms.length >= 3)
    .slice(0, LINES_TO_CUT === Infinity ? undefined : LINES_TO_CUT);
  if (candidates.length === 0) {
    console.error("[real-song] 자를 만한 줄이 없습니다.");
    process.exit(1);
  }

  // 패널이 하는 것과 같은 순서로 세션을 만들고 컷을 놓는다.
  const plans = candidates.map((line, index) => {
    const layer = {
      index: index + 1,
      // AE에서 텍스트 레이어의 이름은 내용이다. 패널이 보는 것과 같은 값을 넘긴다.
      name: line.text,
      inPoint: line.start,
      outPoint: line.end,
      text: line.text,
      sourceTextKeys: 0,
      locked: false,
    };
    const session = panel.buildCutSession(layer, document);
    // 글자 수에 따라 2~3조각. 사용자가 손으로 고르는 자리를 흉내 낸다.
    const visible = session.chars.filter((entry) => entry.visible).length;
    const wanted = visible >= 14 ? 3 : 2;
    let cuts = [];
    for (let piece = 1; piece < wanted; piece += 1) {
      const target = Math.round((session.chars.length * piece) / wanted);
      cuts = panel.toggleCut(session, cuts, Math.min(session.chars.length - 1, Math.max(1, target)));
    }
    const pieces = panel.computePieces(session, cuts);
    return {
      layerName: layer.name,
      line,
      session,
      cuts,
      pieces,
      warnings: panel.pieceWarnings(session, pieces),
    };
  });

  const pieceTotal = plans.reduce((sum, plan) => sum + plan.pieces.length, 0);
  const quality = plans.reduce((acc, plan) => {
    acc[plan.session.matchQuality] = (acc[plan.session.matchQuality] ?? 0) + 1;
    return acc;
  }, {});
  console.log(`[real-song] ${plans.length}줄 → ${pieceTotal}조각 · 매칭 ${JSON.stringify(quality)}`);
  // 조각이 자기 줄 구간을 벗어나면 커팅이 아니라 매칭이 깨진 것이다.
  const strays = plans.filter((plan) =>
    plan.pieces.some((piece) => piece.start < plan.line.start - 1e-6 || piece.end > plan.line.end + 1e-6),
  );
  console.log(`[real-song] 구간 이탈 조각이 있는 줄: ${strays.length}`);
  for (const plan of plans.slice(0, 4)) {
    console.log(`  [${plan.line.start.toFixed(2)}-${plan.line.end.toFixed(2)}] ${plan.line.text}`);
    console.log(`     ${plan.pieces.map((piece) => `「${piece.text}」${piece.start.toFixed(2)}-${piece.end.toFixed(2)}`).join("  ")}`);
  }
  const warned = plans.filter((plan) => plan.warnings.length > 0);
  if (warned.length) {
    console.log(`[real-song] 경고 있는 줄 ${warned.length}개:`);
    for (const plan of warned.slice(0, 6)) console.log(`  「${plan.line.text}」: ${plan.warnings.join(" / ")}`);
  }

  // ExtendScript 생성. 값은 전부 JSON으로 직렬화해 따옴표 사고를 없앤다.
  const jsxData = JSON.stringify(
    plans.map((plan) => ({
      text: plan.line.text,
      start: plan.line.start,
      end: plan.line.end,
      pieces: plan.pieces.map((piece) => ({
        text: piece.text,
        head: piece.head,
        start: piece.start,
        end: piece.end,
      })),
    })),
  );

  const jsx = `(function () {
    var DATA = ${jsxData};
    var FONT = ${JSON.stringify(JAPANESE_FONT)};
    var AUDIO = ${JSON.stringify(fs.existsSync(audioPath) ? audioPath.replace(/\\/g, "/") : "")};
    var COMP_NAME = "EV2 실제곡 커팅";
    var out = [];
    var failures = [];
    var okCount = 0;
    var pieceCount = 0;
    var warnCount = 0;
    var fontUsed = "";

    // 같은 이름의 이전 컴포지션은 지우고 새로 만든다. 겹쳐 두면 어느 것이 최신인지 모른다.
    for (var existing = app.project.numItems; existing >= 1; existing -= 1) {
        var item0 = app.project.item(existing);
        if (item0.name === COMP_NAME) { try { item0.remove(); } catch (e0) {} }
    }

    var comp = app.project.items.addComp(COMP_NAME, 1920, 1080, 1, ${Math.ceil(document.duration + 2)}, 30);
    comp.openInViewer();
    app.beginUndoGroup("Everyric 실제곡 커팅 테스트");
    try {
        // 오디오를 먼저 깔아 둔다. 소리가 없으면 타이밍이 맞는지 판단할 수 없다.
        if (AUDIO) {
            try {
                var audioFile = new File(AUDIO);
                if (audioFile.exists) {
                    var footage = app.project.importFile(new ImportOptions(audioFile));
                    var audioLayer = comp.layers.add(footage);
                    audioLayer.name = "AUDIO";
                    audioLayer.startTime = 0;
                    audioLayer.locked = true;
                    out.push("audio: " + footage.name + " (" + footage.duration.toFixed(1) + "s)");
                } else {
                    out.push("audio: 파일 없음 " + AUDIO);
                }
            } catch (audioError) {
                out.push("audio: 실패 " + audioError);
            }
        }

        for (var i = 0; i < DATA.length; i += 1) {
            var item = DATA[i];
            var layer = comp.layers.addText(item.text);
            // 이름은 손대지 않는다 — AE가 텍스트 내용에 맞춰 붙이고, 조각도 자기 텍스트를
            // 이름으로 갖게 된다. 조각 추적은 레이어 id로 한다.
            var idsBefore = {};
            var prop = layer.property("ADBE Text Properties").property("ADBE Text Document");
            var doc = prop.value;
            doc.fontSize = 64;
            doc.font = FONT;
            doc.justification = ParagraphJustification.CENTER_JUSTIFY;
            prop.setValue(doc);
            var appliedFont = prop.value.font;
            layer.inPoint = item.start;
            layer.outPoint = item.end;
            // 가라오케처럼 모든 줄을 한자리에. 각 줄은 자기 시간에만 보이므로 겹치지 않는다.
            layer.property("ADBE Transform Group").property("ADBE Position").setValue([960, 820]);

            var originalRect = layer.sourceRectAtTime(Math.max(layer.inPoint, 0), false);
            var originalScreenLeft = 960 + originalRect.left;

            for (var pre = 1; pre <= comp.numLayers; pre += 1) {
                try { idsBefore[comp.layer(pre).id] = true; } catch (idError) {}
            }
            var payload = { layerIndex: layer.index, keepOriginalPosition: false, pieces: item.pieces };
            var raw = everyricSplitTextLayer(payload.toSource ? toJson(payload) : "");
            var parsed = eval("(" + raw + ")");

            // 새로 생긴 레이어가 조각이다. host가 뒤 조각부터 복제하므로 스택 순서는 역순.
            var made = [];
            for (var scan = 1; scan <= comp.numLayers; scan += 1) {
                var candidate = comp.layer(scan);
                try { if (!idsBefore[candidate.id]) made.push(candidate); } catch (idError2) {}
            }
            made.reverse();

            var joined = "";
            var timingOk = true;
            var spans = [];
            for (var m = 0; m < made.length; m += 1) {
                var piece = made[m];
                var pieceText = piece.property("ADBE Text Properties").property("ADBE Text Document").value.text;
                joined += pieceText;
                var want = item.pieces[m];
                if (Math.abs(piece.inPoint - want.start) > 0.001 || Math.abs(piece.outPoint - want.end) > 0.001) timingOk = false;
                spans.push(piece.inPoint.toFixed(2) + "-" + piece.outPoint.toFixed(2));
            }
            var expectedJoined = item.text.replace(/\\s+/g, "");
            var textOk = joined.replace(/\\s+/g, "") === expectedJoined;

            var problem = !parsed.ok || !textOk || !timingOk || made.length !== item.pieces.length;
            if (problem) {
                failures.push("「" + item.text + "」: " + (parsed.ok ? "ok" : "FAIL " + parsed.error)
                    + " | 조각 " + made.length + "/" + item.pieces.length
                    + " | text " + (textOk ? "ok" : "MISMATCH(" + joined + ")")
                    + " | timing " + (timingOk ? "ok" : "WRONG")
                    + " | " + spans.join(", "));
            } else {
                okCount += 1;
                pieceCount += made.length;
            }
            if (parsed.warnings && parsed.warnings.length) warnCount += 1;
            fontUsed = appliedFont;
        }
    } catch (e) {
        out.push("ERROR " + e + " @line " + e.line);
    } finally {
        app.endUndoGroup();
    }
    out.push("줄 " + okCount + "/" + DATA.length + " 통과 · 조각 " + pieceCount
        + " · 폰트 " + fontUsed + " · 위치경고 " + warnCount + "줄");
    if (failures.length) {
        out.push("--- 문제 있는 줄 ---");
        for (var fi = 0; fi < failures.length && fi < 12; fi += 1) out.push("  " + failures[fi]);
        if (failures.length > 12) out.push("  … 외 " + (failures.length - 12) + "줄");
    }
    out.push("컴포지션 «" + COMP_NAME + "» 를 남겨 두었습니다. 재생하며 확인하세요.");
    return out.join("\\n");

    function toJson(value) {
        // ExtendScript에 JSON이 없을 수 있어 필요한 만큼만 직렬화한다.
        function quote(text) {
            return '"' + String(text).replace(/\\\\/g, "\\\\\\\\").replace(/"/g, '\\\\"')
                .replace(/\\r/g, "\\\\r").replace(/\\n/g, "\\\\n") + '"';
        }
        var parts = [];
        for (var p = 0; p < value.pieces.length; p += 1) {
            var piece = value.pieces[p];
            parts.push("{" + [
                '"text":' + quote(piece.text),
                '"head":' + quote(piece.head),
                '"start":' + piece.start,
                '"end":' + piece.end
            ].join(",") + "}");
        }
        return "{" + [
            '"layerIndex":' + value.layerIndex,
            '"keepOriginalPosition":' + (value.keepOriginalPosition ? "true" : "false"),
            '"pieces":[' + parts.join(",") + "]"
        ].join(",") + "}";
    }
})();`;

  const jsxPath = path.join(root, "artifacts", "ae-real-song.jsx");
  fs.mkdirSync(path.dirname(jsxPath), { recursive: true });
  fs.writeFileSync(jsxPath, jsx, "utf8");
  console.log(`[real-song] ExtendScript 생성: ${jsxPath}`);

  console.log("[real-song] After Effects에서 실행합니다…");
  const output = execFileSync(
    process.execPath,
    [path.join(root, "scripts", "ae-cdp-run.mjs"), jsxPath],
    { encoding: "utf8", cwd: root },
  );
  console.log(output.trim());
} finally {
  fs.rmSync(temp, { recursive: true, force: true });
}
