// 패키징용: ZXP에 동봉할 임베디드 Python 런타임(+pip)을 만든다.
//
// 무거운 의존성(torch 등)과 모델은 여기서 설치하지 않는다 — 첫 실행 때 패널이 온라인으로
// 받는다. 이 런타임은 "씨앗"이고, 설치 시 %LOCALAPPDATA%\Everyric\runtime으로 복사돼
// 거기서 패키지가 깔린다. 그래야 확장을 업데이트해도 이미 깔린 엔진이 살아남는다.
import { execFileSync } from "child_process";
import fs from "fs";
import os from "os";
import path from "path";
import { fileURLToPath } from "url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PYTHON_VERSION = process.env.EVERYRIC_PYTHON_VERSION || "3.11.9";
const runtimeDir = path.join(root, "runtime");
const temp = os.tmpdir();

function fail(message) {
  console.error(`[build-runtime] ${message}`);
  process.exit(1);
}

async function download(url, target) {
  if (fs.existsSync(target)) {
    console.log(`[build-runtime] 캐시 사용: ${path.basename(target)}`);
    return;
  }
  console.log(`[build-runtime] 다운로드: ${url}`);
  const response = await fetch(url);
  if (!response.ok) fail(`다운로드 실패 (HTTP ${response.status}): ${url}`);
  fs.writeFileSync(target, Buffer.from(await response.arrayBuffer()));
}

if (process.platform !== "win32") fail("임베디드 런타임 빌드는 Windows에서만 가능합니다.");

const zipPath = path.join(temp, `python-${PYTHON_VERSION}-embed-amd64.zip`);
await download(
  `https://www.python.org/ftp/python/${PYTHON_VERSION}/python-${PYTHON_VERSION}-embed-amd64.zip`,
  zipPath,
);

fs.rmSync(runtimeDir, { recursive: true, force: true });
fs.mkdirSync(runtimeDir, { recursive: true });
execFileSync(
  "powershell.exe",
  [
    "-NoProfile",
    "-NonInteractive",
    "-ExecutionPolicy",
    "Bypass",
    "-Command",
    `Expand-Archive -LiteralPath "${zipPath}" -DestinationPath "${runtimeDir}" -Force`,
  ],
  { stdio: "inherit" },
);

// ._pth를 고쳐 site와 site-packages를 켠다. 이게 없으면 pip로 깐 패키지를 import하지 못한다.
const pthFile = fs.readdirSync(runtimeDir).find((name) => name.endsWith("._pth"));
if (!pthFile) fail("._pth 파일을 찾지 못했습니다 — 임베디드 배포본이 맞는지 확인하세요.");
const zipName = fs.readdirSync(runtimeDir).find((name) => /^python\d+\.zip$/.test(name)) ?? "python311.zip";
fs.writeFileSync(
  path.join(runtimeDir, pthFile),
  [zipName, ".", "Lib\\site-packages", "import site", ""].join("\n"),
  "ascii",
);
console.log(`[build-runtime] ${pthFile} 구성 완료`);

const getPip = path.join(temp, "get-pip.py");
await download("https://bootstrap.pypa.io/get-pip.py", getPip);
const pythonExe = path.join(runtimeDir, "python.exe");
execFileSync(pythonExe, [getPip, "--no-warn-script-location"], { stdio: "inherit" });
const pipVersion = execFileSync(pythonExe, ["-m", "pip", "--version"], { stdio: "pipe" }).toString("utf8").trim();
console.log(`[build-runtime] ${pipVersion}`);

// __pycache__는 배포에 쓸모가 없고 ZXP만 키운다.
for (const dir of fs.readdirSync(runtimeDir, { withFileTypes: true, recursive: true })) {
  if (dir.isDirectory() && dir.name === "__pycache__") {
    fs.rmSync(path.join(dir.parentPath ?? dir.path, dir.name), { recursive: true, force: true });
  }
}

let bytes = 0;
for (const entry of fs.readdirSync(runtimeDir, { withFileTypes: true, recursive: true })) {
  if (entry.isFile()) bytes += fs.statSync(path.join(entry.parentPath ?? entry.path, entry.name)).size;
}
console.log(`[build-runtime] 런타임 준비 완료: ${runtimeDir} (${(bytes / 1024 / 1024).toFixed(1)} MB)`);
