import { execFileSync } from "child_process";
import fs from "fs";
import os from "os";
import path from "path";
import { fileURLToPath } from "url";

const EXTENSION_ID = "com.everyric.studio";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const target = path.join(process.env.APPDATA || os.homedir(), "Adobe", "CEP", "extensions", EXTENSION_ID);
const required = [
  "index.html",
  "css/style.css",
  "CSXS/manifest.xml",
  "dist/js/main.js",
  "dist/jsx/host.jsx",
];

for (const relative of required) {
  if (!fs.existsSync(path.join(root, relative))) throw new Error(`빌드 파일 누락: ${relative}`);
}

fs.rmSync(target, { recursive: true, force: true });
fs.mkdirSync(target, { recursive: true });
for (const relative of ["index.html", ".debug", "css", "CSXS", "dist"]) {
  fs.cpSync(path.join(root, relative), path.join(target, relative), { recursive: true });
}

// 임베디드 런타임은 30MB가 넘어서 개발 설치마다 복사하면 느리다. junction으로 걸어 두면
// 확장에서는 실제 폴더처럼 보이고(엔진 설치가 씨앗을 찾는다) 복사 비용은 0이다.
const runtimeSource = path.join(root, "runtime");
if (fs.existsSync(runtimeSource)) {
  try {
    fs.symlinkSync(runtimeSource, path.join(target, "runtime"), "junction");
    console.log("runtime/ linked (junction)");
  } catch (error) {
    fs.cpSync(runtimeSource, path.join(target, "runtime"), { recursive: true });
    console.log(`runtime/ copied (junction 실패: ${error.message})`);
  }
} else {
  console.log("runtime/ 없음 — 엔진 설치는 uv 경로로 동작합니다 (npm run build:runtime으로 만들 수 있습니다).");
}

if (process.platform === "win32") {
  for (const version of ["11", "12", "13"]) {
    try {
      execFileSync("reg.exe", [
        "add",
        `HKCU\\Software\\Adobe\\CSXS.${version}`,
        "/v",
        "PlayerDebugMode",
        "/t",
        "REG_SZ",
        "/d",
        "1",
        "/f",
      ], { stdio: "ignore", windowsHide: true });
    } catch {
      // The extension can still work when debug mode is already configured by another version.
    }
  }
}

console.log(`Everyric Studio installed: ${target}`);
