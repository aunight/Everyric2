import { spawn } from "child_process";
import fs from "fs";
import os from "os";
import path from "path";

export interface EngineInstallOptions {
  wheelUrl?: string;
  onProgress: (message: string) => void;
  signal?: AbortSignal;
  /** 설치된 확장 폴더. 여기 동봉된 python 런타임을 씨앗으로 쓴다. */
  extensionRoot?: string | null;
}

const UV_VERSION = "0.11.29";
const UV_DOWNLOAD_URL = `https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-pc-windows-msvc.zip`;
const PYTHON_VERSION = "3.11";
const CUDA_INDEX_URL = "https://download.pytorch.org/whl/cu124";
const FALLBACK_ENGINE_SPEC = "everyric2 @ git+https://github.com/onpe5679/Everyric2.git";

function managedRoot(): string {
  return path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local"), "Everyric");
}

function managedRuntimeDir(): string {
  return path.join(managedRoot(), "runtime");
}

/** ZXP에 동봉된 임베디드 런타임을 복사해 만든 python. */
function embeddedPythonPath(): string {
  return path.join(managedRuntimeDir(), "python.exe");
}

/** 예전 설치(uv가 만든 가상환경)의 python. 이미 깔린 사용자를 위해 계속 인식한다. */
function venvPythonPath(): string {
  return path.join(managedRuntimeDir(), "Scripts", "python.exe");
}

export function managedPythonPath(): string {
  return fs.existsSync(embeddedPythonPath()) ? embeddedPythonPath() : venvPythonPath();
}

export function hasManagedRuntime(): boolean {
  return fs.existsSync(embeddedPythonPath()) || fs.existsSync(venvPythonPath());
}

/**
 * ZXP에 동봉된 python 런타임 씨앗의 경로. 없으면 null(개발 중이거나 경량 배포).
 *
 * 이 씨앗을 그대로 쓰지 않고 %LOCALAPPDATA%로 복사해서 쓴다. 확장 폴더 안에 패키지를 깔면
 * 패널을 업데이트할 때마다 엔진이 통째로 날아가기 때문이다.
 */
export function seedRuntimeDir(extensionRoot: string | null): string | null {
  if (!extensionRoot) return null;
  const candidate = path.join(extensionRoot, "runtime", "python.exe");
  return fs.existsSync(candidate) ? path.join(extensionRoot, "runtime") : null;
}

function abortError(): Error {
  return new Error("엔진 설치를 취소했습니다.");
}

function runCommand(
  command: string,
  args: string[],
  onProgress: (message: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
      env: { ...process.env, PYTHONUTF8: "1", NO_COLOR: "1" },
    });
    let tail = "";
    const forward = (chunk: Buffer): void => {
      const text = chunk.toString("utf8");
      tail = (tail + text).slice(-2400);
      const line = text.trim().split(/\r?\n/).filter(Boolean).pop();
      if (line) onProgress(line.replace(/\x1b\[[0-9;]*m/g, "").slice(0, 140));
    };
    child.stdout.on("data", forward);
    child.stderr.on("data", forward);
    const abort = (): void => {
      child.kill();
    };
    signal?.addEventListener("abort", abort, { once: true });
    child.on("error", (error) => {
      signal?.removeEventListener("abort", abort);
      reject(error);
    });
    child.on("close", (code) => {
      signal?.removeEventListener("abort", abort);
      if (signal?.aborted) reject(abortError());
      else if (code === 0) resolve();
      else reject(new Error(tail.trim().slice(-800) || `종료 코드 ${code}`));
    });
  });
}

async function downloadFile(url: string, target: string, signal?: AbortSignal): Promise<void> {
  const response = await fetch(url, signal ? { signal } : {});
  if (!response.ok) throw new Error(`다운로드 실패 (HTTP ${response.status}): ${url}`);
  const buffer = Buffer.from(await response.arrayBuffer());
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, buffer);
}

async function ensureUv(onProgress: (message: string) => void, signal?: AbortSignal): Promise<string> {
  const binDir = path.join(managedRoot(), "bin");
  const uvPath = path.join(binDir, "uv.exe");
  if (fs.existsSync(uvPath)) return uvPath;
  onProgress(`uv ${UV_VERSION} 다운로드 중…`);
  const zipPath = path.join(binDir, "uv.zip");
  await downloadFile(UV_DOWNLOAD_URL, zipPath, signal);
  if (signal?.aborted) throw abortError();
  await runCommand(
    "powershell.exe",
    [
      "-NoProfile",
      "-NonInteractive",
      "-ExecutionPolicy",
      "Bypass",
      "-Command",
      `Expand-Archive -LiteralPath "${zipPath}" -DestinationPath "${binDir}" -Force`,
    ],
    onProgress,
    signal,
  );
  fs.rmSync(zipPath, { force: true });
  if (!fs.existsSync(uvPath)) throw new Error("uv.exe 압축 해제에 실패했습니다.");
  return uvPath;
}

export function detectNvidiaGpu(): Promise<boolean> {
  return new Promise((resolve) => {
    const child = spawn("nvidia-smi", ["-L"], { windowsHide: true, stdio: ["ignore", "pipe", "ignore"] });
    let stdout = "";
    child.stdout.on("data", (chunk: Buffer) => (stdout += chunk.toString("utf8")));
    child.on("error", () => resolve(false));
    child.on("close", (code) => resolve(code === 0 && stdout.trim().length > 0));
  });
}

/**
 * 동봉된 런타임을 %LOCALAPPDATA%로 복사한다. 네트워크를 쓰지 않는 유일한 준비 경로다.
 */
function copySeedRuntime(seed: string, onProgress: (message: string) => void): void {
  onProgress("Python 런타임 준비 중… (최초 1회)");
  fs.mkdirSync(managedRoot(), { recursive: true });
  fs.cpSync(seed, managedRuntimeDir(), { recursive: true });
  if (!fs.existsSync(embeddedPythonPath())) {
    throw new Error("동봉된 Python 런타임 복사에 실패했습니다.");
  }
}

/** 씨앗이 없을 때만 쓰는 예비 경로 — uv로 python을 내려받아 가상환경을 만든다. */
async function bootstrapWithUv(
  onProgress: (message: string) => void,
  signal?: AbortSignal,
): Promise<string> {
  const uvPath = await ensureUv(onProgress, signal);
  if (signal?.aborted) throw abortError();
  onProgress(`Python ${PYTHON_VERSION} 가상환경 생성 중… (최초 1회)`);
  await runCommand(uvPath, ["venv", managedRuntimeDir(), "--python", PYTHON_VERSION], onProgress, signal);
  return venvPythonPath();
}

export async function installEngine(options: EngineInstallOptions): Promise<string> {
  const { onProgress, signal } = options;
  if (process.platform !== "win32") throw new Error("관리형 런타임 설치는 Windows에서만 지원합니다.");
  fs.mkdirSync(managedRoot(), { recursive: true });

  let pythonPath = managedPythonPath();
  if (!fs.existsSync(pythonPath)) {
    const seed = seedRuntimeDir(options.extensionRoot ?? null);
    if (seed) {
      copySeedRuntime(seed, onProgress);
      pythonPath = embeddedPythonPath();
    } else {
      pythonPath = await bootstrapWithUv(onProgress, signal);
    }
  }
  if (signal?.aborted) throw abortError();

  const gpu = await detectNvidiaGpu();
  onProgress(gpu ? "엔진 설치 중 · CUDA 빌드 (수 GB 다운로드)…" : "엔진 설치 중 · CPU 빌드…");
  const spec = options.wheelUrl || FALLBACK_ENGINE_SPEC;
  // 모델 캐시(HF_HOME)는 건드리지 않는다. 기본값인 사용자 홈에 두어야 패널·엔진을 몇 번
  // 갈아끼워도 수 GB를 다시 받지 않는다.
  const args = ["-m", "pip", "install", "--upgrade", "--disable-pip-version-check", spec];
  if (gpu) args.push("--extra-index-url", CUDA_INDEX_URL);
  await runCommand(pythonPath, args, onProgress, signal);
  return pythonPath;
}
