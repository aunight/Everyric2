export interface TimingAtom {
  text: string;
  start: number;
  end: number;
  confidence?: number;
}

export interface SyncLine {
  text: string;
  start: number;
  end: number;
  confidence?: number;
  translation?: string;
  pronunciation?: string;
  atoms: TimingAtom[];
}

export interface SyncDocument {
  lines: SyncLine[];
  language: string;
  sourceLabel: string;
  duration: number;
}

export type MatchQuality = "exact" | "substring" | "time" | "none";

export interface CharTiming {
  char: string;
  start: number;
  end: number;
  /** 공백·개행이 아니라 실제로 그려지는 글자인가. */
  visible: boolean;
  /** 시각이 atom 측정치가 아니라 추정치인가(단어 atom 분할, 공백 메움, 폴백 배분). */
  interpolated: boolean;
}

export interface CutSession {
  layerIndex: number;
  layerName: string;
  text: string;
  inPoint: number;
  outPoint: number;
  chars: CharTiming[];
  matchQuality: MatchQuality;
  lineText?: string;
  pronunciation?: string;
  translation?: string;
  /** 값이 있으면 이 레이어는 자를 수 없고, 문자열이 그 사유다. */
  blocked?: string;
}

export interface CutPoint {
  /** chars 배열에서 이 인덱스 앞을 자른다. 1..chars.length-1 */
  index: number;
  time: number;
  /** 기본 계산값 그대로인가(드래그로 옮기면 false). */
  auto: boolean;
}

export interface CutPiece {
  text: string;
  start: number;
  end: number;
  charStart: number;
  charEnd: number;
  /**
   * 원본 텍스트의 처음부터 이 조각의 끝까지. host가 "접두사+조각"에서 "조각"을 빼는 방식으로
   * 앞선 글자들의 폭을 재는 데 쓴다. ExtendScript는 코드포인트 단위 slice를 못 하므로
   * 패널이 만들어서 넘긴다.
   */
  head: string;
}

export interface TextLayerInfo {
  index: number;
  name: string;
  inPoint: number;
  outPoint: number;
  text: string;
  sourceTextKeys: number;
  locked: boolean;
}

export interface CompInfo {
  hasComp: boolean;
  name?: string;
  width?: number;
  height?: number;
  duration?: number;
  frameRate?: number;
  time?: number;
  selectedTextLayers?: TextLayerInfo[];
  generatedLayerCount?: number;
  everyricMarkerCount?: number;
  audioLayers?: Array<{
    index: number;
    name: string;
    inPoint: number;
    outPoint: number;
    filePath?: string;
  }>;
  error?: string;
}

export type Density = "readable" | "balanced" | "rhythmic";
export type LayoutPreset = "auto" | "center" | "editorial" | "split" | "diagonal";
export type RevealMode = "cumulative" | "simultaneous";
export type LayerOrder = "bottom-to-top" | "top-to-bottom";
export type UiLocale = "ko" | "ja" | "en";
export type TypographyMode = "designed" | "line";

export interface PlannerOptions {
  density: Density;
  layout: LayoutPreset;
  width: number;
  height: number;
  frameRate: number;
  fontSize: number;
  preRollFrames: number;
  postRollFrames: number;
  pauseThreshold: number;
  maxBlocksPerCard: number;
  phraseTargetChars: number;
  maxTokensPerBlock: number;
  revealMode: RevealMode;
}

export interface TypographyBlock {
  id: string;
  cardId: string;
  text: string;
  start: number;
  end: number;
  position: [number, number];
  fontSize: number;
  rotation: number;
  justification: "left" | "center" | "right";
  color: [number, number, number];
  emphasis: number;
}

export interface TypographyCard {
  id: string;
  start: number;
  end: number;
  sourceText: string;
  blocks: TypographyBlock[];
}

export interface TypographyPlan {
  groupId: string;
  cards: TypographyCard[];
  blocks: TypographyBlock[];
  warnings: string[];
}

export interface FillAssignment {
  layerIndex: number;
  layerName: string;
  text: string;
  inPoint: number;
  outPoint: number;
  skippedReason?: string;
}

export interface LocalSyncOptions {
  pythonPath: string;
  engine: string;
  language: string;
  audioPath: string;
  lyrics: string;
}

export interface AppSettings {
  uiLocale: UiLocale;
  pythonPath: string;
  engine: string;
  language: string;
  density: Density;
  typographyMode: TypographyMode;
  layout: LayoutPreset;
  fontSize: number;
  preRollFrames: number;
  postRollFrames: number;
  pauseThreshold: number;
  maxBlocksPerCard: number;
  phraseTargetChars: number;
  maxTokensPerBlock: number;
  revealMode: RevealMode;
  layerOrder: LayerOrder;
  replacePrevious: boolean;
  autoLabelColors: boolean;
  /** 커팅으로 나뉜 조각을 원본 위치에 그대로 둘지. 끄면 각 글자가 있던 자리로 옮긴다. */
  keepCutPosition: boolean;
  /** 이미 만들어진 싱크를 영상 ID로 조회할 서버. */
  serverUrl: string;
  serverApiKey: string;
}

export interface HostResult {
  ok: boolean;
  created?: number;
  updated?: number;
  skipped?: number;
  removed?: number;
  generatedLayerCount?: number;
  markerCount?: number;
  error?: string;
  warnings?: string[];
}

export interface EnvironmentReport {
  everyricVersion: string;
  nodeVersion: string;
  platform: string;
  cpu: string;
  systemMemoryGb: number;
  gpuName?: string;
  vramTotalMb?: number;
  vramFreeMb?: number;
  cudaVersion?: string;
  recommended: {
    minimumVramGb: number;
    comfortableVramGb: number;
    systemMemoryGb: number;
  };
  notes: string[];
}
