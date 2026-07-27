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
const LINES_TO_CUT = 6;

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
    .filter((line) => Array.from(line.text.replace(/\s/g, "")).length >= 8 && line.atoms.length >= 6)
    .slice(0, LINES_TO_CUT);
  if (candidates.length === 0) {
    console.error("[real-song] 자를 만한 줄이 없습니다.");
    process.exit(1);
  }

  // 패널이 하는 것과 같은 순서로 세션을 만들고 컷을 놓는다.
  const plans = candidates.map((line, index) => {
    const layer = {
      index: index + 1,
      name: `LINE_${String(index + 1).padStart(2, "0")}`,
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

  for (const plan of plans) {
    console.log(`  ${plan.layerName} [${plan.line.start.toFixed(2)}-${plan.line.end.toFixed(2)}] ${plan.line.text}`);
    console.log(`     매칭 ${plan.session.matchQuality} · 발음 ${plan.session.pronunciation ?? "(없음)"}`);
    for (const piece of plan.pieces) {
      console.log(`     조각 「${piece.text}」 ${piece.start.toFixed(2)}–${piece.end.toFixed(2)}`);
    }
    if (plan.warnings.length) console.log(`     경고: ${plan.warnings.join(" / ")}`);
  }

  // ExtendScript 생성. 값은 전부 JSON으로 직렬화해 따옴표 사고를 없앤다.
  const jsxData = JSON.stringify(
    plans.map((plan) => ({
      name: plan.layerName,
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
    var out = [];
    var comp = app.project.items.addComp("EV2 실제곡 커팅", 1920, 1080, 1, ${Math.ceil(document.duration + 2)}, 30);
    comp.openInViewer();
    app.beginUndoGroup("Everyric 실제곡 커팅 테스트");
    try {
        for (var i = 0; i < DATA.length; i += 1) {
            var item = DATA[i];
            var layer = comp.layers.addText(item.text);
            layer.name = item.name;
            var prop = layer.property("ADBE Text Properties").property("ADBE Text Document");
            var doc = prop.value;
            doc.fontSize = 64;
            doc.font = FONT;
            doc.justification = ParagraphJustification.CENTER_JUSTIFY;
            prop.setValue(doc);
            var appliedFont = prop.value.font;
            layer.inPoint = item.start;
            layer.outPoint = item.end;
            layer.property("ADBE Transform Group").property("ADBE Position").setValue([960, 200 + i * 120]);

            var originalRect = layer.sourceRectAtTime(Math.max(layer.inPoint, 0), false);
            var originalScreenLeft = 960 + originalRect.left;

            var payload = { layerIndex: layer.index, keepOriginalPosition: false, pieces: item.pieces };
            var raw = everyricSplitTextLayer(payload.toSource ? toJson(payload) : "");
            var parsed = eval("(" + raw + ")");

            var made = [];
            for (var scan = 1; scan <= comp.numLayers; scan += 1) {
                var candidate = comp.layer(scan);
                if (candidate.name.indexOf(item.name + " ") === 0) made.push(candidate);
            }
            made.sort(function (a, b) { return Number(a.name.split(" ").pop()) - Number(b.name.split(" ").pop()); });

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

            out.push(item.name + ": " + (parsed.ok ? "ok" : "FAIL " + parsed.error)
                + " | font=" + appliedFont
                + " | 조각 " + made.length + "/" + item.pieces.length
                + " | text " + (textOk ? "ok" : "MISMATCH(" + joined + ")")
                + " | timing " + (timingOk ? "ok" : "WRONG")
                + " | " + spans.join(", ")
                + (parsed.warnings && parsed.warnings.length ? " | 경고: " + parsed.warnings.join(";") : ""));
        }
    } catch (e) {
        out.push("ERROR " + e + " @line " + e.line);
    } finally {
        app.endUndoGroup();
    }
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
