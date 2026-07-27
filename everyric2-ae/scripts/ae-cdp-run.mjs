// 열려 있는 CEP 패널에 붙어 ExtendScript를 실행한다.
//
// AE에 스크립트를 자동으로 넣을 방법이 이것뿐이다: AfterFX.exe -r 은 AE가 이미 떠 있으면
// 무시되고, 시작과 동시에 주면 스플래시에서 멈춘다. 패널의 디버그 포트(.debug의 Port)로
// 붙으면 패널 컨텍스트에서 __adobe_cep__.evalScript를 부를 수 있고, 그게 곧 AE다.
//
//   node scripts/ae-cdp-run.mjs <script.jsx>
//
// 요구: After Effects에서 패널이 열려 있을 것 (Window ▸ Extensions ▸ Everyric Studio).
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
// localhost는 Windows에서 IPv6로 먼저 물려 요청마다 2초씩 먹는다. 반드시 127.0.0.1.
const HOST = "127.0.0.1";

function fail(message) {
  console.error(`[ae-cdp] ${message}`);
  process.exit(1);
}

function debugPort() {
  const debugFile = path.join(root, ".debug");
  if (!fs.existsSync(debugFile)) fail(".debug 파일이 없습니다.");
  const match = fs.readFileSync(debugFile, "utf8").match(/Port="(\d+)"/);
  if (!match) fail(".debug에서 포트를 찾지 못했습니다.");
  return Number(match[1]);
}

async function findTarget(port) {
  const response = await fetch(`http://${HOST}:${port}/json`, { signal: AbortSignal.timeout(5000) });
  if (!response.ok) fail(`디버그 포트 응답 HTTP ${response.status}`);
  const targets = await response.json();
  const target = targets.find((item) => item.webSocketDebuggerUrl);
  if (!target) fail("붙을 수 있는 패널이 없습니다 — After Effects에서 패널을 열어 주세요.");
  return target;
}

function evaluate(socket, expression, id) {
  return new Promise((resolve, reject) => {
    const onMessage = (event) => {
      let message;
      try {
        message = JSON.parse(event.data);
      } catch {
        return;
      }
      if (message.id !== id) return;
      socket.removeEventListener("message", onMessage);
      if (message.error) reject(new Error(JSON.stringify(message.error)));
      else resolve(message.result);
    };
    socket.addEventListener("message", onMessage);
    socket.send(JSON.stringify({
      id,
      method: "Runtime.evaluate",
      params: { expression, awaitPromise: true, returnByValue: true },
    }));
  });
}

const scriptArg = process.argv[2];
if (!scriptArg) fail("실행할 .jsx 경로를 인자로 주세요.");
const scriptPath = path.resolve(scriptArg);
if (!fs.existsSync(scriptPath)) fail(`스크립트가 없습니다: ${scriptPath}`);

const port = debugPort();
const target = await findTarget(port);
console.log(`[ae-cdp] 연결: ${target.title || target.url}`);

const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener("open", resolve, { once: true });
  socket.addEventListener("error", () => reject(new Error("웹소켓 연결 실패")), { once: true });
});

// 패널 컨텍스트에서 evalScript를 부른다. 스크립트 본문을 문자열로 넘기지 않고
// $.evalFile로 파일을 읽게 해서 따옴표·개행 이스케이프 사고를 피한다.
const posix = scriptPath.replace(/\\/g, "/");
const expression = `new Promise(function (resolve) {
  if (!window.__adobe_cep__) { resolve("NO_CEP_BRIDGE"); return; }
  window.__adobe_cep__.evalScript('$.evalFile(new File("${posix}"))', function (result) {
    resolve(String(result));
  });
})`;

try {
  const result = await evaluate(socket, expression, 1);
  const value = result?.result?.value;
  console.log(`[ae-cdp] 결과: ${value === "" || value === undefined ? "(빈 문자열 — 스크립트가 값을 돌려주지 않음)" : value}`);
  if (value === "NO_CEP_BRIDGE") fail("패널에 CEP 브리지가 없습니다 (브라우저로 연 페이지일 수 있습니다).");
  if (String(value).indexOf("EvalScript error") === 0) fail("ExtendScript 실행이 실패했습니다.");
} finally {
  socket.close();
}
