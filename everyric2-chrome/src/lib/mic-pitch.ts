/** 마이크 피치 샘플 — at은 performance.now()/1000 (벽시계 초) */
export interface MicSample {
  at: number;
  midi: number;
}

const HISTORY_SEC = 4;
const SAMPLE_MS = 45;

/**
 * 마이크 입력 실시간 피치 검출 — 자기상관(ACF) 기반.
 * 가라오케 레인이 samples()를 읽어 사용자 음정 궤적을 그린다.
 * echo/noise 억제를 꺼서 노래 원음에 가까운 신호를 받는다.
 */
export class MicPitch {
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private analyser: AnalyserNode | null = null;
  private buf: Float32Array<ArrayBuffer> | null = null;
  private timer: number | undefined;
  private history: MicSample[] = [];
  private starting = false;
  private deviceId = '';

  isRunning(): boolean {
    return this.timer !== undefined || this.starting;
  }

  /** 權限要求中不算已擷取；只有取樣計時器開始後才可把空樣本解讀成「正在沉默」。 */
  isCapturing(): boolean {
    return this.timer !== undefined;
  }

  currentDeviceId(): string {
    return this.deviceId;
  }

  /** 마이크 권한을 요청하고 검출을 시작. 거부/실패 시 false. */
  async start(deviceId?: string): Promise<boolean> {
    if (this.isRunning()) return true;
    this.starting = true;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          deviceId: deviceId ? { exact: deviceId } : undefined,
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
        },
      });
      this.stream = stream;
      this.deviceId = deviceId ?? '';
      this.ctx = new AudioContext();
      const src = this.ctx.createMediaStreamSource(stream);
      this.analyser = this.ctx.createAnalyser();
      // 4096(85ms@48kHz) — 2048은 저음 남성역(80~120Hz)에서 주기 4~5개밖에 안 들어와
      // NSDF가 흔들렸다. 창을 두 배로 키우면 저음 안정성이 눈에 띄게 좋아지고,
      // 45ms 샘플 주기는 그대로라 반응성 손해는 사실상 없다.
      this.analyser.fftSize = 4096;
      src.connect(this.analyser);
      this.buf = new Float32Array(this.analyser.fftSize);
      this.timer = window.setInterval(() => this.sample(), SAMPLE_MS);
      return true;
    } catch {
      this.stop();
      return false;
    } finally {
      this.starting = false;
    }
  }

  stop(): void {
    if (this.timer !== undefined) {
      clearInterval(this.timer);
      this.timer = undefined;
    }
    this.stream?.getTracks().forEach(t => t.stop());
    this.stream = null;
    void this.ctx?.close().catch(() => { /* 이미 닫힘 */ });
    this.ctx = null;
    this.analyser = null;
    this.buf = null;
    this.history = [];
    this.deviceId = '';
  }

  samples(): MicSample[] {
    return this.history;
  }

  /** 최근 원시 미디값 3개 — 중앙값 필터용 (단발 스파이크가 궤적·채점에 들어가지 않게) */
  private recentMidi: number[] = [];

  private sample(): void {
    if (!this.analyser || !this.buf || !this.ctx) return;
    this.analyser.getFloatTimeDomainData(this.buf);
    const freq = autoCorrelate(this.buf, this.ctx.sampleRate);
    const now = performance.now() / 1000;
    if (this.history.length > 0 && now - this.history[0].at > HISTORY_SEC) {
      this.history = this.history.filter(s => now - s.at < HISTORY_SEC);
    }
    if (freq <= 0) {
      this.recentMidi = []; // 무성 구간을 넘긴 중앙값 오염 방지 — 다음 발성은 새로 모은다
      return;
    }
    const midi = 69 + 12 * Math.log2(freq / 440);
    this.recentMidi.push(midi);
    if (this.recentMidi.length > 3) this.recentMidi.shift();
    // 3점 중앙값 — 검출기가 한 프레임 삐끗해도(옥타브 점프 등) 궤적은 흔들리지 않는다.
    // 지연은 최대 1프레임(45ms)로 GRACE_SEC(150ms) 안에 넉넉히 들어온다.
    const sorted = [...this.recentMidi].sort((a, b) => a - b);
    this.history.push({ at: now, midi: sorted[Math.floor(sorted.length / 2)] });
  }
}

/**
 * MPM(McLeod Pitch Method) 피치 검출 — 사람 목소리 대역(70Hz~1kHz).
 *
 * 이전의 소박한 자기상관(ACF)은 배음이 강한 성부에서 옥타브를 자주 틀렸다(최대 상관
 * 피크가 2배 주기에 앉는 고전적 실패) — 채점이 멀쩡한 발성을 '미스'로 깎는 실사용 불만의
 * 근원. MPM은 NSDF(정규화 제곱차 함수)의 **첫 유의미 피크**를 고르는 규칙(전역 최대의
 * k배 문턱)으로 그 실패를 구조적으로 막는다. Tarsos·pitchy 등 노래용 튜너의 표준.
 * 검출 실패(무음·비주기·불명료)면 -1.
 */
const MPM_K = 0.9; // 첫 피크 채택 문턱 — McLeod 논문 권장 0.8~1.0, 노래엔 0.9가 균형
// NSDF 피크값 하한 — 이보다 흐리면 잡음/무성음으로 버린다.
// 0.6은 스피커 반주가 섞여 드는 실사용 마이크에서 정상 발성까지 버려 궤적이 뚝뚝
// 끊겼다(실보고) — 옛 ACF의 채택 문턱(상관 0.5)과 비슷한 관대함으로 내린다.
const MPM_CLARITY = 0.45;

export function autoCorrelate(buf: Float32Array, sampleRate: number): number {
  const size = buf.length;
  let energy = 0;
  for (let i = 0; i < size; i++) energy += buf[i] * buf[i];
  const rms = Math.sqrt(energy / size);
  if (rms < 0.015 || energy === 0) return -1;

  const minLag = Math.max(2, Math.floor(sampleRate / 1000));
  const maxLag = Math.min(size - 2, Math.floor(sampleRate / 70));
  if (maxLag <= minLag) return -1;

  // NSDF: n(τ) = 2·Σ x[i]x[i+τ] / Σ (x[i]² + x[i+τ]²) — [-1, 1] 범위로 정규화돼
  // 신호 크기와 창 끝 감쇠에 불변이다 (맨 ACF는 τ가 클수록 값이 깎여 저음이 불리했다)
  const nsdf = new Float32Array(maxLag + 1);
  for (let lag = minLag; lag <= maxLag; lag++) {
    let acf = 0;
    let norm = 0;
    for (let i = 0; i < size - lag; i++) {
      acf += buf[i] * buf[i + lag];
      norm += buf[i] * buf[i] + buf[i + lag] * buf[i + lag];
    }
    nsdf[lag] = norm > 0 ? (2 * acf) / norm : 0;
  }

  // 양의 구간별 극대(key maxima) 수집 — 음→양 전환 후의 최고점만 후보로 삼는다
  const peaks: number[] = [];
  let lag = minLag;
  while (lag <= maxLag && nsdf[lag] > 0) lag++; // 첫 양의 구간(τ=0 주변 잔재)은 건너뜀
  while (lag <= maxLag) {
    while (lag <= maxLag && nsdf[lag] <= 0) lag++;
    let best = -1;
    while (lag <= maxLag && nsdf[lag] > 0) {
      if (best < 0 || nsdf[lag] > nsdf[best]) best = lag;
      lag++;
    }
    if (best > 0) peaks.push(best);
  }
  if (peaks.length === 0) return -1;

  // 첫 유의미 피크 — 전역 최대의 k배를 넘는 가장 이른 피크가 기본 주기다.
  // (최대 피크를 그냥 고르면 2τ 배음 피크에 앉아 옥타브가 떨어진다)
  const globalMax = Math.max(...peaks.map(p => nsdf[p]));
  if (globalMax < MPM_CLARITY) return -1;
  const chosen = peaks.find(p => nsdf[p] >= MPM_K * globalMax) ?? peaks[0];

  // 포물선 보간으로 서브샘플 정밀도 확보 (센트 단위 오차용)
  let refined = chosen;
  if (chosen > minLag && chosen < maxLag) {
    const a = nsdf[chosen - 1];
    const b = nsdf[chosen];
    const c = nsdf[chosen + 1];
    const denom = a - 2 * b + c;
    if (Math.abs(denom) > 1e-9) refined = chosen + 0.5 * (a - c) / denom;
  }
  return sampleRate / refined;
}
