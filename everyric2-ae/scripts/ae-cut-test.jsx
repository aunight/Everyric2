// Everyric Studio 글자 커팅 실측 테스트.
//
// 임시 컴포지션을 만들어 텍스트 레이어를 자르고, 조각들이 원본에서 그 글자가 있던
// 자리에 놓였는지 화면 좌표로 검증한 뒤 컴포지션을 지운다. 프로젝트 파일은 건드리지 않는다.
(function () {
    // 실행 방법: After Effects에서 File ▸ Scripts ▸ Run Script File… 로 이 파일을 연다.
    // 경로는 이 스크립트 위치(everyric2-ae/scripts/)를 기준으로 잡으므로 리포 어디에 두든 동작한다.
    var here = new File($.fileName).parent;
    var repoRoot = here.parent;
    var HOST = repoRoot.fsName.replace(/\\/g, "/") + "/dist/jsx/host.jsx";
    var OUT = repoRoot.fsName.replace(/\\/g, "/") + "/artifacts/ae-cut-test-report.txt";
    var report = { cases: [], notes: [], ok: false };

    function note(message) {
        report.notes.push(String(message));
    }

    function writeReport() {
        var folder = new Folder(new File(OUT).parent.fsName);
        if (!folder.exists) folder.create();
        var file = new File(OUT);
        file.encoding = "UTF-8";
        file.open("w");
        file.write(report.toSource());
        file.close();
        var summary = [];
        for (var i = 0; i < report.cases.length; i += 1) {
            var entry = report.cases[i];
            summary.push(
                entry.label + ": " + (entry.ok ? "ok" : "FAIL " + (entry.error || entry.exception || "")) +
                (entry.firstOffsetError === undefined
                    ? ""
                    : " | 첫 조각 오차 " + entry.firstOffsetError.toFixed(2) +
                      "px, 둘째 조각 오차 " + entry.secondOffsetError.toFixed(2) +
                      "px, 커닝차 " + entry.kerningGap.toFixed(2) + "px")
            );
        }
        alert("Everyric 커팅 테스트\n\n" + summary.join("\n") + "\n\n리포트: " + OUT);
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
        $.evalFile(new File(HOST));
        report.hostLoaded = (typeof everyricSplitTextLayer === "function");
        if (!report.hostLoaded) {
            note("host.jsx에서 everyricSplitTextLayer를 찾지 못했습니다.");
            writeReport();
            return;
        }

        comp = app.project.items.addComp("EV2_CUT_TEST", 1920, 1080, 1, 12, 30);
        comp.openInViewer();

        var cases = [
            { label: "ja-center", text: "君の名前を呼ぶよ", cut: 2, just: "center", size: 90 },
            { label: "en-left", text: "hello world", cut: 6, just: "left", size: 90 },
            { label: "ko-center", text: "너의 이름을 부를게", cut: 3, just: "center", size: 72 },
            { label: "en-center-kerning", text: "AVATAR Wave", cut: 7, just: "center", size: 90 }
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
                prop.setValue(doc);
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

                // 만들어진 조각을 이름으로 찾는다.
                var pieceLayers = [];
                for (var scan = 1; scan <= comp.numLayers; scan += 1) {
                    var candidate = comp.layer(scan);
                    if (candidate.name.indexOf("SRC_" + testCase.label + " ") === 0) pieceLayers.push(candidate);
                }
                entry.pieceCount = pieceLayers.length;
                entry.pieces = [];
                for (var p = 0; p < pieceLayers.length; p += 1) {
                    var piece = pieceLayers[p];
                    var pieceRect = piece.sourceRectAtTime(Math.max(piece.inPoint, 0), false);
                    var piecePos = piece.property("ADBE Transform Group").property("ADBE Position").value;
                    var pieceText = piece.property("ADBE Text Properties").property("ADBE Text Document").value.text;
                    entry.pieces.push({
                        name: piece.name,
                        text: pieceText,
                        inPoint: piece.inPoint,
                        outPoint: piece.outPoint,
                        positionX: piecePos[0],
                        rectLeft: pieceRect.left,
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
})();
