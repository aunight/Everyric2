declare var app: any;
declare var $: any;
declare var File: any;
declare var CompItem: any;
declare var TextLayer: any;
declare var ParagraphJustification: any;

type HostObject = Record<string, any>;

function response(value: HostObject): string {
  try {
    return JSON.stringify(value);
  } catch (error) {
    return '{"ok":false,"error":"JSON response failed"}';
  }
}

function parsePayload(payloadJson?: string): HostObject {
  if (!payloadJson) return {};
  return JSON.parse(payloadJson);
}

function activeComp(): any | null {
  var item = app.project && app.project.activeItem;
  return item && item instanceof CompItem ? item : null;
}

function textProperty(layer: any): any | null {
  try {
    return layer.property("ADBE Text Properties").property("ADBE Text Document");
  } catch (error) {
    return null;
  }
}

function isTextLayer(layer: any): boolean {
  return Boolean(layer && (layer instanceof TextLayer || textProperty(layer)));
}

function textLayerData(layer: any): HostObject {
  var sourceText = textProperty(layer);
  var currentText = "";
  if (sourceText) {
    try {
      currentText = sourceText.value.text || "";
    } catch (error) {}
  }
  return {
    index: layer.index,
    name: layer.name,
    inPoint: layer.inPoint,
    outPoint: layer.outPoint,
    text: currentText,
    sourceTextKeys: sourceText ? sourceText.numKeys : 0,
    locked: Boolean(layer.locked),
  };
}

function getAudioLayers(comp: any): HostObject[] {
  var result: HostObject[] = [];
  for (var index = 1; index <= comp.numLayers; index += 1) {
    var layer = comp.layer(index);
    try {
      if (!layer.hasAudio || !layer.source) continue;
      var filePath: string | undefined;
      if (layer.source.file) filePath = layer.source.file.fsName;
      result.push({
        index: layer.index,
        name: layer.name,
        inPoint: layer.inPoint,
        outPoint: layer.outPoint,
        filePath: filePath,
      });
    } catch (error) {}
  }
  return result;
}

function getSelectedTextLayers(comp: any): HostObject[] {
  var result: HostObject[] = [];
  var selected = comp.selectedLayers || [];
  for (var index = 0; index < selected.length; index += 1) {
    if (isTextLayer(selected[index])) result.push(textLayerData(selected[index]));
  }
  return result;
}

function countGeneratedLayers(comp: any): number {
  var count = 0;
  for (var index = 1; index <= comp.numLayers; index += 1) {
    try {
      if (String(comp.layer(index).comment || "").indexOf("EV2|") === 0) count += 1;
    } catch (error) {}
  }
  return count;
}

function countEveryricMarkers(comp: any): number {
  var count = 0;
  try {
    var markers = comp.markerProperty;
    for (var keyIndex = 1; keyIndex <= markers.numKeys; keyIndex += 1) {
      if (isEveryricMarker(markers.keyValue(keyIndex))) count += 1;
    }
  } catch (error) {}
  for (var layerIndex = 1; layerIndex <= comp.numLayers; layerIndex += 1) {
    try {
      var layerMarkers = comp.layer(layerIndex).property("ADBE Marker");
      for (var layerKey = 1; layerKey <= layerMarkers.numKeys; layerKey += 1) {
        if (isEveryricMarker(layerMarkers.keyValue(layerKey))) count += 1;
      }
    } catch (error) {}
  }
  return count;
}

function everyricGetCompInfo(): string {
  try {
    var comp = activeComp();
    if (!comp) return response({ ok: true, hasComp: false });
    var compId = 0;
    try {
      compId = Number(comp.id) || 0;
    } catch (error) {}
    var projectPath = "";
    try {
      projectPath = app.project.file ? app.project.file.fsName : "";
    } catch (error) {}
    return response({
      ok: true,
      hasComp: true,
      // 싱크 데이터를 이 컴포지션에 매어 두기 위한 신원. 저장 전 프로젝트는 경로가 비고,
      // 그때는 컴포지션 id만으로 구분한다(AE를 다시 켜면 흩어지지만 세션 안에서는 맞다).
      compId: compId,
      projectPath: projectPath,
      name: comp.name,
      width: comp.width,
      height: comp.height,
      duration: comp.duration,
      frameRate: comp.frameRate,
      time: comp.time,
      selectedTextLayers: getSelectedTextLayers(comp),
      generatedLayerCount: countGeneratedLayers(comp),
      everyricMarkerCount: countEveryricMarkers(comp),
      audioLayers: getAudioLayers(comp),
    });
  } catch (error) {
    return response({ ok: false, hasComp: false, error: String(error) });
  }
}

function everyricGetSelectedTextLayers(): string {
  try {
    var comp = activeComp();
    if (!comp) return response({ ok: false, layers: [], error: "활성 컴포지션이 없습니다." });
    var layers = getSelectedTextLayers(comp);
    return response({ ok: true, layers: layers });
  } catch (error) {
    return response({ ok: false, layers: [], error: String(error) });
  }
}

function everyricApplyTextAssignments(payloadJson?: string): string {
  var undoStarted = false;
  try {
    var comp = activeComp();
    if (!comp) return response({ ok: false, error: "활성 컴포지션이 없습니다." });
    var payload = parsePayload(payloadJson);
    var assignments = payload.assignments || [];
    if (!assignments.length) return response({ ok: false, error: "적용할 레이어가 없습니다." });

    app.beginUndoGroup("Everyric Studio - Fill selected layers");
    undoStarted = true;
    var updated = 0;
    var skipped = 0;
    var warnings: string[] = [];
    for (var index = 0; index < assignments.length; index += 1) {
      var assignment = assignments[index];
      var layer = comp.layer(Number(assignment.layerIndex));
      if (!layer || !isTextLayer(layer) || layer.locked) {
        skipped += 1;
        warnings.push("레이어를 건너뜀: " + String(assignment.layerName || assignment.layerIndex));
        continue;
      }
      var sourceText = textProperty(layer);
      if (!sourceText || sourceText.numKeys > 0) {
        skipped += 1;
        warnings.push("Source Text 키프레임 레이어를 건너뜀: " + layer.name);
        continue;
      }
      var documentValue = sourceText.value;
      documentValue.text = String(assignment.text || "");
      sourceText.setValue(documentValue);
      updated += 1;
    }
    return response({ ok: true, updated: updated, skipped: skipped, warnings: warnings });
  } catch (error) {
    return response({ ok: false, error: String(error) });
  } finally {
    if (undoStarted) app.endUndoGroup();
  }
}

function justificationValue(name: string): any {
  if (name === "left") return ParagraphJustification.LEFT_JUSTIFY;
  if (name === "right") return ParagraphJustification.RIGHT_JUSTIFY;
  return ParagraphJustification.CENTER_JUSTIFY;
}

function styleSeed(comp: any): HostObject | null {
  var selected = comp.selectedLayers || [];
  for (var index = 0; index < selected.length; index += 1) {
    var sourceText = textProperty(selected[index]);
    if (!sourceText) continue;
    try {
      var documentValue = sourceText.value;
      return {
        font: documentValue.font,
        applyFill: documentValue.applyFill,
        fillColor: documentValue.fillColor,
        applyStroke: documentValue.applyStroke,
        strokeColor: documentValue.strokeColor,
        strokeWidth: documentValue.strokeWidth,
        fauxBold: documentValue.fauxBold,
        fauxItalic: documentValue.fauxItalic,
        tracking: documentValue.tracking,
        autoLeading: documentValue.autoLeading,
        leading: documentValue.leading,
      };
    } catch (error) {}
  }
  return null;
}

function applyDocumentStyle(documentValue: any, block: HostObject, seed: HostObject | null): void {
  documentValue.text = String(block.text || "");
  documentValue.fontSize = Math.max(1, Number(block.fontSize) || 72);
  documentValue.justification = justificationValue(String(block.justification || "center"));
  documentValue.applyFill = true;
  documentValue.fillColor = block.color || [1, 1, 1];
  if (!seed) return;
  try { if (seed.font) documentValue.font = seed.font; } catch (error) {}
  try { documentValue.applyStroke = seed.applyStroke; } catch (error) {}
  try { if (seed.strokeColor) documentValue.strokeColor = seed.strokeColor; } catch (error) {}
  try { if (seed.strokeWidth !== undefined) documentValue.strokeWidth = seed.strokeWidth; } catch (error) {}
  try { documentValue.fauxBold = seed.fauxBold; } catch (error) {}
  try { documentValue.fauxItalic = seed.fauxItalic; } catch (error) {}
  try { documentValue.tracking = seed.tracking; } catch (error) {}
  try { documentValue.autoLeading = seed.autoLeading; } catch (error) {}
  try { if (!seed.autoLeading && seed.leading) documentValue.leading = seed.leading; } catch (error) {}
}

function setTextAnchor(layer: any, block: HostObject): void {
  try {
    var sampleTime = Math.max(layer.inPoint, 0);
    var rect = layer.sourceRectAtTime(sampleTime, false);
    var anchorX = rect.left + rect.width / 2;
    if (block.justification === "left") anchorX = rect.left;
    if (block.justification === "right") anchorX = rect.left + rect.width;
    var anchorY = rect.top + rect.height / 2;
    layer.property("ADBE Transform Group").property("ADBE Anchor Point").setValue([anchorX, anchorY]);
  } catch (error) {}
}

function isEveryricMarker(marker: any): boolean {
  try {
    if (String(marker.comment || "").indexOf("EV2|") === 0) return true;
  } catch (error) {}
  try {
    if (String(marker.chapter || "").indexOf("EV2|") === 0) return true;
  } catch (error) {}
  try {
    if (String(marker.url || "").indexOf("EV2|") === 0) return true;
  } catch (error) {}
  return false;
}

function removeGeneratedMarkers(comp: any): number {
  var removed = 0;
  try {
    var markers = comp.markerProperty;
    for (var keyIndex = markers.numKeys; keyIndex >= 1; keyIndex -= 1) {
      var marker = markers.keyValue(keyIndex);
      if (isEveryricMarker(marker)) {
        markers.removeKey(keyIndex);
        removed += 1;
      }
    }
  } catch (error) {}
  for (var layerIndex = 1; layerIndex <= comp.numLayers; layerIndex += 1) {
    var layer = comp.layer(layerIndex);
    try {
      var layerMarkers = layer.property("ADBE Marker");
      for (var layerKey = layerMarkers.numKeys; layerKey >= 1; layerKey -= 1) {
        var layerMarker = layerMarkers.keyValue(layerKey);
        if (isEveryricMarker(layerMarker)) {
          layerMarkers.removeKey(layerKey);
          removed += 1;
        }
      }
    } catch (error) {}
  }
  return removed;
}

function removeGeneratedLayers(comp: any): number {
  var removed = 0;
  removeGeneratedMarkers(comp);
  for (var index = comp.numLayers; index >= 1; index -= 1) {
    var layer = comp.layer(index);
    try {
      if (String(layer.comment || "").indexOf("EV2|") === 0) {
        layer.remove();
        removed += 1;
      }
    } catch (error) {}
  }
  return removed;
}

function everyricCreateTypography(payloadJson?: string): string {
  var undoStarted = false;
  try {
    var comp = activeComp();
    if (!comp) return response({ ok: false, error: "활성 컴포지션이 없습니다." });
    var payload = parsePayload(payloadJson);
    var plan = payload.plan;
    if (!plan || !plan.blocks || !plan.blocks.length) return response({ ok: false, error: "생성 계획이 비어 있습니다." });
    if (plan.blocks.length > 1000) return response({ ok: false, error: "안전을 위해 한 번에 1000개 이상의 레이어를 만들 수 없습니다." });

    app.beginUndoGroup("Everyric Studio - Build typography");
    undoStarted = true;
    var removed = payload.replacePrevious ? removeGeneratedLayers(comp) : 0;
    if (!payload.replacePrevious) removeGeneratedMarkers(comp);
    var seed = styleSeed(comp);
    var created = 0;
    var bottomToTop = String(payload.layerOrder || "bottom-to-top") === "bottom-to-top";
    var startIndex = bottomToTop ? 0 : plan.blocks.length - 1;
    var endIndex = bottomToTop ? plan.blocks.length : -1;
    var step = bottomToTop ? 1 : -1;
    for (var index = startIndex; index !== endIndex; index += step) {
      var block = plan.blocks[index];
      var start = Math.max(0, Math.min(comp.duration - comp.frameDuration, Number(block.start)));
      var end = Math.max(start + comp.frameDuration, Math.min(comp.duration, Number(block.end)));
      var layer = comp.layers.addText(String(block.text || ""));
      // 이름은 AE 기본대로 텍스트 내용을 따라가게 둔다. 카드·블록 식별은 comment가 한다.
      layer.comment = "EV2|" + plan.groupId + "|" + block.cardId + "|" + block.id;
      if (payload.autoLabelColors) {
        try {
          var cardNumber = Number(String(block.cardId || "").replace(/[^0-9]/g, ""));
          layer.label = 1 + (cardNumber % 16);
        } catch (error) {}
      }
      layer.startTime = 0;
      layer.inPoint = start;
      layer.outPoint = end;
      var sourceText = textProperty(layer);
      var documentValue = sourceText.value;
      applyDocumentStyle(documentValue, block, seed);
      sourceText.setValue(documentValue);
      setTextAnchor(layer, block);
      layer.property("ADBE Transform Group").property("ADBE Position").setValue(block.position);
      layer.property("ADBE Transform Group").property("ADBE Rotate Z").setValue(Number(block.rotation) || 0);
      created += 1;
    }
    return response({ ok: true, created: created, removed: removed });
  } catch (error) {
    return response({ ok: false, error: String(error) });
  } finally {
    if (undoStarted) app.endUndoGroup();
  }
}

function everyricRemoveGeneratedLayers(): string {
  var undoStarted = false;
  try {
    var comp = activeComp();
    if (!comp) return response({ ok: false, error: "활성 컴포지션이 없습니다." });
    app.beginUndoGroup("Everyric Studio - Remove generated layers");
    undoStarted = true;
    return response({
      ok: true,
      removed: removeGeneratedLayers(comp),
      markerCount: countEveryricMarkers(comp),
    });
  } catch (error) {
    return response({ ok: false, error: String(error) });
  } finally {
    if (undoStarted) app.endUndoGroup();
  }
}

function everyricCreateLineMarkers(payloadJson?: string): string {
  var undoStarted = false;
  try {
    var comp = activeComp();
    if (!comp) return response({ ok: false, error: "활성 컴포지션이 없습니다." });
    var payload = parsePayload(payloadJson);
    var documentValue = payload.document;
    var lines = documentValue && documentValue.lines ? documentValue.lines : [];
    if (!lines.length) return response({ ok: false, error: "마커로 만들 싱크 라인이 없습니다." });

    app.beginUndoGroup("Everyric Studio - Add line markers");
    undoStarted = true;
    removeGeneratedMarkers(comp);
    var created = 0;
    for (var index = 0; index < lines.length; index += 1) {
      var line = lines[index];
      var start = Math.max(0, Math.min(comp.duration, Number(line.start) || 0));
      var end = Math.max(start, Math.min(comp.duration, Number(line.end) || start));
      var lyric = String(line.text || "Line " + String(index + 1)).slice(0, 180);
      var metadata = "EV2|LINE|" + String(index + 1) + "|" + start.toFixed(3) + "|" + end.toFixed(3);
      var marker = new MarkerValue(lyric);
      marker.comment = lyric;
      try { marker.chapter = metadata; } catch (error) {}
      try { marker.url = metadata; } catch (error) {}
      try { marker.duration = Math.max(comp.frameDuration, end - start); } catch (error) {}
      comp.markerProperty.setValueAtTime(start, marker);
      created += 1;
    }
    return response({ ok: true, created: created });
  } catch (error) {
    return response({ ok: false, error: String(error) });
  } finally {
    if (undoStarted) app.endUndoGroup();
  }
}

/** sourceRectAtTime을 재는 시각. setTextAnchor와 같은 기준을 쓴다. */
function measureTime(layer: any): number {
  try {
    return Math.max(layer.inPoint, 0);
  } catch (error) {
    return 0;
  }
}

/**
 * 텍스트 하나의 바운딩 박스를 잰다. ExtendScript에는 글자별 위치 API가 없어서
 * 문자열을 넣어 보고 재는 것이 유일한 방법이다.
 *
 * **측정마다 레이어를 새로 만들어 지운다.** 한 레이어의 텍스트를 갈아 끼우며 연달아 재면
 * sourceRectAtTime이 이전 문자열의 값을 돌려준다(실측 확인). 그러면 조각 하나가 통째로
 * 엉뚱한 자리에 놓인다.
 */
function measureText(sourceLayer: any, text: string): HostObject | null {
  if (!text) return { left: 0, width: 0 };
  var probe = null;
  try {
    probe = sourceLayer.duplicate();
    var sourceText = textProperty(probe);
    if (!sourceText) return null;
    var documentValue = sourceText.value;
    documentValue.text = text;
    sourceText.setValue(documentValue);
    var rect = probe.sourceRectAtTime(Math.max(probe.inPoint, 0), false);
    return { left: rect.left, width: rect.width };
  } catch (error) {
    return null;
  } finally {
    if (probe) {
      try {
        probe.remove();
      } catch (removeError) {}
    }
  }
}

/**
 * 각 조각을 원본에서 그 글자들이 있던 자리에 놓기 위한 x 이동량.
 *
 * 로컬 좌표에서 조각의 시작은 rect.left이고, 원본에서 그 글자의 시작은
 * (원본 rect.left + 앞선 글자들의 폭)이다. 둘의 차이가 이동량이라 justification과 무관하게
 * 성립한다. 앞선 글자들의 폭은 "접두사+조각"에서 "조각"을 빼서 구한다 — 접두사만 재면
 * 끝의 공백이 바운딩 박스에서 빠져 조각이 왼쪽으로 밀린다.
 *
 * 실패하면 null을 돌려주고 호출부가 위치를 건드리지 않는다.
 */
function pieceOffsets(layer: any, pieces: any[], time: number): number[] | null {
  var originalRect;
  try {
    originalRect = layer.sourceRectAtTime(time, false);
  } catch (error) {
    return null;
  }
  if (!originalRect || !(originalRect.width > 0)) return null;

  try {
    var offsets: number[] = [];
    var widthSum = 0;
    for (var index = 0; index < pieces.length; index += 1) {
      var piece = pieces[index];
      var pieceRect = measureText(layer, String(piece.text || ""));
      var headRect = measureText(layer, String(piece.head || piece.text || ""));
      if (!pieceRect || !headRect) return null;
      // 폭이 0인 조각 = 이 폰트에 그 글자의 글리프가 없다. 폭을 믿을 수 없으니 포기한다.
      if (!(pieceRect.width > 0)) return null;
      widthSum += pieceRect.width;
      var prefixWidth = headRect.width - pieceRect.width;
      offsets.push(originalRect.left + prefixWidth - pieceRect.left);
    }
    // 조각 폭의 합은 원본 폭에 가까워야 한다. 차이는 자간뿐이라 몇 %를 넘지 않는다
    // (실측: 일본어 폰트 2.2%). 글리프가 없는 폰트에서는 30%까지 벌어지고, 그때 계산한
    // 위치는 글자가 있던 자리와 아무 상관이 없다 — 옮기지 않는 편이 낫다.
    if (Math.abs(originalRect.width - widthSum) > originalRect.width * 0.1) return null;
    return offsets;
  } catch (error) {
    return null;
  }
}

/** 로컬 x 이동량을 레이어의 회전·스케일을 거쳐 컴포지션 좌표의 이동으로 바꾼다. */
function localShiftToComp(layer: any, dx: number): number[] {
  var scaleX = 1;
  var radians = 0;
  try {
    scaleX = Number(layer.property("ADBE Transform Group").property("ADBE Scale").value[0]) / 100;
    if (!(scaleX > 0)) scaleX = 1;
  } catch (error) {}
  try {
    radians = (Number(layer.property("ADBE Transform Group").property("ADBE Rotate Z").value) * Math.PI) / 180;
  } catch (error) {}
  var shift = dx * scaleX;
  return [shift * Math.cos(radians), shift * Math.sin(radians)];
}

/**
 * in/out을 설정한다. **반드시 in을 먼저, out을 나중에.**
 *
 * AE에서 inPoint를 설정하면 레이어가 트림되는 게 아니라 길이를 유지한 채 이동한다
 * (실측: 1~5 레이어에 inPoint=3을 주면 3~5가 아니라 3~7이 된다). 그래서 in으로 자리를
 * 잡고 out으로 잘라내는 순서여야 한다. 반대로 하면 out이 in 설정에 다시 밀려난다.
 */
function setLayerSpan(layer: any, start: number, end: number, comp: any): void {
  var safeEnd = Math.max(comp.frameDuration, Math.min(comp.duration, end));
  var safeStart = Math.max(0, Math.min(safeEnd - comp.frameDuration, start));
  try {
    layer.inPoint = safeStart;
  } catch (error) {}
  try {
    layer.outPoint = safeEnd;
  } catch (error) {}
}

function everyricSplitTextLayer(payloadJson?: string): string {
  var undoStarted = false;
  try {
    var comp = activeComp();
    if (!comp) return response({ ok: false, error: "활성 컴포지션이 없습니다." });
    var payload = parsePayload(payloadJson);
    var pieces = payload.pieces || [];
    if (pieces.length < 2) return response({ ok: false, error: "자를 지점을 하나 이상 선택하세요." });

    var layer = comp.layer(Number(payload.layerIndex));
    if (!layer || !isTextLayer(layer)) return response({ ok: false, error: "텍스트 레이어를 찾을 수 없습니다." });
    if (layer.locked) return response({ ok: false, error: "잠긴 레이어는 자를 수 없습니다." });
    var sourceText = textProperty(layer);
    if (!sourceText) return response({ ok: false, error: "Source Text를 읽을 수 없습니다." });
    if (sourceText.numKeys > 0) {
      return response({ ok: false, error: "Source Text에 키프레임이 있는 레이어는 자를 수 없습니다." });
    }

    app.beginUndoGroup("Everyric Studio - Split text layer");
    undoStarted = true;

    var warnings: string[] = [];
    var time = measureTime(layer);
    var offsets: number[] | null = payload.keepOriginalPosition ? null : pieceOffsets(layer, pieces, time);
    if (!payload.keepOriginalPosition && !offsets) {
      warnings.push(
        "글자 폭이 신뢰할 수 없어 조각을 원래 위치에 두었습니다. " +
          "이 폰트에 해당 글자의 글리프가 없을 수 있습니다 — 가사 언어를 지원하는 폰트로 바꿔 보세요.",
      );
    }

    var basePosition;
    try {
      basePosition = layer.property("ADBE Transform Group").property("ADBE Position").value;
    } catch (error) {
      basePosition = null;
    }

    var baseComment = String(layer.comment || "");
    var baseName = String(layer.name || "");
    var created = 0;
    // 위에서부터 쌓으면 조각 순서가 타임라인에서 뒤집힌다 — 뒤 조각부터 복제해 순서를 맞춘다.
    for (var index = pieces.length - 1; index >= 0; index -= 1) {
      var piece = pieces[index];
      var clone = layer.duplicate();
      // 이름은 건드리지 않는다. AE는 이름을 직접 주지 않은 텍스트 레이어의 이름을 내용에
      // 맞춰 따라가게 하고, 한 번 name을 쓰면 그 연결이 끊긴다. 조각 식별은 comment로 한다.
      if (baseComment.indexOf("EV2|") === 0) {
        clone.comment = baseComment + "|CUT" + String(index + 1);
      } else if (baseComment === "") {
        // 사용자가 써 둔 코멘트는 덮지 않는다. 비어 있을 때만 출처를 남긴다.
        clone.comment = "EV2CUT|" + baseName + "|" + String(index + 1);
      }

      var cloneText = textProperty(clone);
      if (cloneText) {
        var documentValue = cloneText.value;
        documentValue.text = String(piece.text || "");
        cloneText.setValue(documentValue);
      }
      setLayerSpan(clone, Number(piece.start), Number(piece.end), comp);

      if (offsets && basePosition) {
        var shift = localShiftToComp(layer, offsets[index] || 0);
        try {
          clone
            .property("ADBE Transform Group")
            .property("ADBE Position")
            .setValue([basePosition[0] + shift[0], basePosition[1] + shift[1]]);
        } catch (error) {
          warnings.push("조각 " + String(index + 1) + "의 위치를 옮기지 못했습니다.");
        }
      }
      created += 1;
    }

    layer.remove();
    return response({ ok: true, created: created, removed: 1, warnings: warnings });
  } catch (error) {
    return response({ ok: false, error: String(error) });
  } finally {
    if (undoStarted) app.endUndoGroup();
  }
}

function everyricPickFile(payloadJson?: string): string {
  try {
    var payload = parsePayload(payloadJson);
    var kind = String(payload.kind || "json");
    var filter = kind === "json" ? "JSON:*.json" : "All files:*.*";
    var file = File.openDialog("Everyric Studio - 파일 선택", filter, false);
    if (!file) return response({ ok: false });
    return response({ ok: true, path: file.fsName });
  } catch (error) {
    return response({ ok: false, error: String(error) });
  }
}

$.global.everyricGetCompInfo = everyricGetCompInfo;
$.global.everyricGetSelectedTextLayers = everyricGetSelectedTextLayers;
$.global.everyricApplyTextAssignments = everyricApplyTextAssignments;
$.global.everyricCreateTypography = everyricCreateTypography;
$.global.everyricRemoveGeneratedLayers = everyricRemoveGeneratedLayers;
$.global.everyricCreateLineMarkers = everyricCreateLineMarkers;
$.global.everyricSplitTextLayer = everyricSplitTextLayer;
$.global.everyricRemoveGeneratedMarkers = function (): string {
  try {
    var comp = activeComp();
    if (!comp) return response({ ok: false, error: "활성 컴포지션이 없습니다." });
    return response({ ok: true, removed: removeGeneratedMarkers(comp) });
  } catch (error) {
    return response({ ok: false, error: String(error) });
  }
};
$.global.everyricPickFile = everyricPickFile;
