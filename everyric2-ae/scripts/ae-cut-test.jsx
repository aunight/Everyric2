// Everyric Studio 글자 커팅 실측 테스트.
//
// 임시 컴포지션을 만들어 텍스트 레이어를 자르고, 조각들이 원본에서 그 글자가 있던
// 자리에 놓였는지 화면 좌표로 검증한 뒤 컴포지션을 지운다. 프로젝트 파일은 건드리지 않는다.
(function () {
    // 실행 방법 두 가지:
    //   1) npm run ae:test — 실행기가 $.global에 경로를 넣어 준다 (패널이 열려 있어야 함)
    //   2) After Effects에서 File ▸ Scripts ▸ Run Script File…
    //
    // 2)에서는 $.fileName으로 자기 위치를 찾는데, ExtendScript가 주는 경로는 "/c/DevAT/..."
    // 같은 URI 스타일이라 그대로는 File()에 못 넣는다. 드라이브 문자로 되돌린다.
    function toPlatformPath(value) {
        var text = String(value).replace(/\\/g, "/");
        var drive = text.match(/^\/([a-zA-Z])\/(.*)$/);
        return drive ? drive[1].toUpperCase() + ":/" + drive[2] : text;
    }

    var repoRoot = null;
    try {
        repoRoot = toPlatformPath(new File($.fileName).parent.parent.fsName);
    } catch (pathError) {
        repoRoot = null;
    }
    var HOST = $.global.EVERYRIC_TEST_HOST || (repoRoot + "/dist/jsx/host.jsx");
    var OUT = $.global.EVERYRIC_TEST_OUT || (repoRoot + "/artifacts/ae-cut-test-report.txt");
    // 실행기가 붙었으면 모달을 띄우면 안 된다 — alert가 뜨면 evalScript가 돌아오지 않는다.
    var INTERACTIVE = !$.global.EVERYRIC_TEST_HOST;
    var report = { cases: [], notes: [], ok: false };

    function note(message) {
        report.notes.push(String(message));
    }

    function summarize() {
        var lines = [];
        for (var i = 0; i < report.cases.length; i += 1) {
            var entry = report.cases[i];
            lines.push(
                entry.label + ": " + (entry.ok ? "ok" : "FAIL " + (entry.error || entry.exception || "")) +
                (entry.firstOffsetError === undefined
                    ? ""
                    : " | font " + entry.font +
                      " | pos " + entry.firstOffsetError.toFixed(2) + "/" + entry.secondOffsetError.toFixed(2) +
                      "px, seam " + entry.seamGap.toFixed(2) +
                      "px, text " + (entry.textOk ? "ok" : "MISMATCH") +
                      ", timing " + (entry.timingOk ? "ok" : "WRONG") +
                      ", guard " + (entry.guardOk ? "ok" : "FAILED") +
                      (entry.movedPieces ? "" : " (no move)"))
            );
        }
        if (report.notes.length) lines.push("notes: " + report.notes.join(" / "));
        return lines.join("\n");
    }

    function writeReport() {
        try {
            var folder = new File(OUT).parent;
            if (!folder.exists) folder.create();
            var file = new File(OUT);
            file.encoding = "UTF-8";
            file.open("w");
            file.write(report.toSource());
            file.close();
        } catch (writeError) {
            report.notes.push("리포트 기록 실패: " + String(writeError));
        }
        if (INTERACTIVE) alert("Everyric 커팅 테스트\n\n" + summarize() + "\n\n리포트: " + OUT);
    }

    // ExtendScript에 JSON이 있다고 믿지 않는다 — 페이로드는 손으로 만들고 응답은 eval한다.
    function quote(value) {
        var escaped = String(value)
            .replace(/\\/g, "\\\\")
            .replace(/"/g, '\\"')
            .replace(/\r/g, "\\r")
            .replace(/\n/g, "\\n");
        return '"' + escaped + '"';
    }

    function pieceJson(piece) {
        return "{" + [
            '"text":' + quote(piece.text),
            '"head":' + quote(piece.head),
            '"start":' + piece.start,
            '"end":' + piece.end
        ].join(",") + "}";
    }

    function payloadJson(layerIndex, keepOriginalPosition, pieces) {
        var parts = [];
        for (var i = 0; i < pieces.length; i += 1) parts.push(pieceJson(pieces[i]));
        return "{" + [
            '"layerIndex":' + layerIndex,
            '"keepOriginalPosition":' + (keepOriginalPosition ? "true" : "false"),
            '"pieces":[' + parts.join(",") + "]"
        ].join(",") + "}";
    }

    function textWidth(comp, sample, seedLayer) {
        // 측정 전용 레이어. 원본을 복제해 스타일(폰트/크기/자간)을 그대로 물려받는다.
        var probe = seedLayer.duplicate();
        var prop = probe.property("ADBE Text Properties").property("ADBE Text Document");
        var doc = prop.value;
        doc.text = sample;
        prop.setValue(doc);
        var rect = probe.sourceRectAtTime(Math.max(probe.inPoint, 0), false);
        var out = { width: rect.width, left: rect.left };
        probe.remove();
        return out;
    }

    var comp = null;
    try {
        // 실행기가 host.jsx를 미리 넣어 줬으면 그대로 쓴다. 손으로 돌릴 때만 파일에서 읽는다.
        if (typeof everyricSplitTextLayer !== "function") $.evalFile(HOST);
        report.hostLoaded = (typeof everyricSplitTextLayer === "function");
        if (!report.hostLoaded) {
            note("host.jsx에서 everyricSplitTextLayer를 찾지 못했습니다.");
            writeReport();
            return;
        }

        comp = app.project.items.addComp("EV2_CUT_TEST", 1920, 1080, 1, 12, 30);
        comp.openInViewer();

        var cases = [
            { label: "ja-center", text: "君の名前を呼ぶよ", cut: 2, just: "center", size: 90, font: "YuGothic-Medium" },
            { label: "en-left", text: "hello world", cut: 6, just: "left", size: 90 },
            { label: "ko-center", text: "너의 이름을 부를게", cut: 3, just: "center", size: 72 },
            { label: "en-center-kerning", text: "AVATAR Wave", cut: 7, just: "center", size: 90 },
            // 글리프가 없는 폰트로 일본어를 자르는 경우. 위치를 옮기면 안 되고 경고가 나와야 한다.
            { label: "ja-missing-glyph", text: "君の名前を呼ぶよ", cut: 2, just: "center", size: 90,
              font: "GamtanRoad-Batang-Regular", expectNoMove: true }
        ];

        for (var index = 0; index < cases.length; index += 1) {
            var testCase = cases[index];
            var entry = { label: testCase.label, text: testCase.text };
            try {
                app.beginUndoGroup("EV2 cut test " + testCase.label);
                var layer = comp.layers.addText(testCase.text);
                layer.name = "SRC_" + testCase.label;
                var prop = layer.property("ADBE Text Properties").property("ADBE Text Document");
                var doc = prop.value;
                doc.fontSize = testCase.size;
                doc.justification = testCase.just === "left"
                    ? ParagraphJustification.LEFT_JUSTIFY
                    : ParagraphJustification.CENTER_JUSTIFY;
                if (testCase.font) {
                    try { doc.font = testCase.font; } catch (fontError) {}
                }
                prop.setValue(doc);
                entry.font = prop.value.font;
                layer.inPoint = 1;
                layer.outPoint = 5;
                var basePosition = [960, 400 + index * 120];
                layer.property("ADBE Transform Group").property("ADBE Position").setValue(basePosition);

                var originalRect = layer.sourceRectAtTime(1, false);
                entry.originalLeft = originalRect.left;
                entry.originalWidth = originalRect.width;
                entry.originalScreenLeft = basePosition[0] + originalRect.left;

                var headText = testCase.text.substr(0, testCase.cut);
                var tailText = testCase.text.substr(testCase.cut);
                // 패널의 computePieces가 하는 일: 양끝 공백을 떼고 head(처음~조각 끝)를 함께 넘긴다.
                var headTrimmed = headText.replace(/^\s+|\s+$/g, "");
                var tailTrimmed = tailText.replace(/^\s+|\s+$/g, "");

                // 기대 위치를 미리 잰다 (host가 쓰는 것과 같은 방식: head − piece).
                var tailWidth = textWidth(comp, tailTrimmed, layer).width;
                var fullWidth = textWidth(comp, testCase.text, layer).width;
                var expectedPrefix = fullWidth - tailWidth;
                entry.expectedPrefixWidth = expectedPrefix;
                // 커닝 대조군: 접두사만 따로 잰 폭. 둘의 차이가 커닝·공백 오차다.
                entry.prefixWidthAlone = textWidth(comp, headTrimmed, layer).width;
                entry.kerningGap = expectedPrefix - entry.prefixWidthAlone;

                var raw = everyricSplitTextLayer(payloadJson(layer.index, false, [
                    { text: headTrimmed, head: headTrimmed, start: 1, end: 3 },
                    { text: tailTrimmed, head: testCase.text, start: 3, end: 5 }
                ]));
                entry.response = String(raw).substr(0, 400);
                var parsed = eval("(" + raw + ")");
                entry.ok = parsed.ok === true;
                entry.warnings = parsed.warnings;
                if (!parsed.ok) {
                    entry.error = parsed.error;
                    app.endUndoGroup();
                    report.cases.push(entry);
                    continue;
                }

                // 만들어진 조각을 이름으로 찾는다. host가 붙이는 이름은 "<원본> <조각번호>"이고,
                // 레이어 스택 순서는 조각 순서와 반대라 번호로 정렬해야 짝이 맞는다.
                var pieceLayers = [];
                for (var scan = 1; scan <= comp.numLayers; scan += 1) {
                    var candidate = comp.layer(scan);
                    if (candidate.name.indexOf("SRC_" + testCase.label + " ") === 0) pieceLayers.push(candidate);
                }
                pieceLayers.sort(function (a, b) {
                    return Number(a.name.split(" ").pop()) - Number(b.name.split(" ").pop());
                });
                entry.pieceCount = pieceLayers.length;
                entry.pieces = [];
                for (var p = 0; p < pieceLayers.length; p += 1) {
                    var piece = pieceLayers[p];
                    var pieceRect = piece.sourceRectAtTime(Math.max(piece.inPoint, 0), false);
                    var piecePos = piece.property("ADBE Transform Group").property("ADBE Position").value;
                    var pieceText = piece.property("ADBE Text Properties").property("ADBE Text Document").value.text;
                    entry.pieces.push({
                        name: piece.name,
                        pieceText: pieceText,
                        inPoint: piece.inPoint,
                        outPoint: piece.outPoint,
                        positionX: piecePos[0],
                        rectLeft: pieceRect.left,
                        rectWidth: pieceRect.width,
                        screenLeft: piecePos[0] + pieceRect.left
                    });
                }

                // 검증: 첫 조각은 원본과 같은 지점에서 시작하고,
                // 둘째 조각은 접두사 폭만큼 오른쪽에서 시작해야 한다.
                if (entry.pieces.length === 2) {
                    var first = entry.pieces[0];
                    var second = entry.pieces[1];
                    entry.firstOffsetError = first.screenLeft - entry.originalScreenLeft;
                    entry.secondOffsetError = second.screenLeft - (entry.originalScreenLeft + expectedPrefix);
                    entry.textOk = (first.pieceText === headTrimmed) && (second.pieceText === tailTrimmed);
                    // 조각 시각이 요청대로 붙었는지 (inPoint 설정이 레이어를 밀어내는 함정)
                    entry.timingOk = Math.abs(first.inPoint - 1) < 0.001 && Math.abs(first.outPoint - 3) < 0.001 &&
                                     Math.abs(second.inPoint - 3) < 0.001 && Math.abs(second.outPoint - 5) < 0.001;
                    // 조각들이 시각적으로 연속인가: 조각2 시작 − 조각1 시작 ≈ 조각1 폭
                    entry.seamGap = (second.screenLeft - first.screenLeft) - first.rectWidth;
                    // 글리프가 없는 폰트에서는 위치를 옮기지 않고 경고만 내야 한다.
                    entry.movedPieces = Math.abs(first.positionX - basePosition[0]) > 0.01 ||
                                        Math.abs(second.positionX - basePosition[0]) > 0.01;
                    entry.guardOk = testCase.expectNoMove
                        ? (!entry.movedPieces && entry.warnings && entry.warnings.length > 0)
                        : true;
                }
                app.endUndoGroup();
            } catch (caseError) {
                entry.exception = String(caseError);
                try { app.endUndoGroup(); } catch (ignored) {}
            }
            report.cases.push(entry);
        }
        report.ok = true;
    } catch (error) {
        note("실패: " + String(error) + " @line " + (error.line || "?"));
    } finally {
        try {
            if (comp) comp.remove();
        } catch (cleanupError) {
            note("정리 실패: " + String(cleanupError));
        }
        writeReport();
    }
    // 실행기가 evalScript 반환값으로 이 요약을 받는다.
    return summarize();
})();
