interface AdobeCepBridge {
  evalScript(script: string, callback: (result: string) => void): void;
  getSystemPath?(type: string): string;
}

declare global {
  interface Window {
    __adobe_cep__?: AdobeCepBridge;
  }
}

const HOST_FUNCTIONS = new Set([
  "everyricGetCompInfo",
  "everyricGetSelectedTextLayers",
  "everyricApplyTextAssignments",
  "everyricCreateTypography",
  "everyricRemoveGeneratedLayers",
  "everyricCreateLineMarkers",
  "everyricRemoveGeneratedMarkers",
  "everyricSplitTextLayer",
  "everyricPickFile",
]);

function bridge(): AdobeCepBridge {
  if (!window.__adobe_cep__) {
    throw new Error("After Effects CEP 브리지를 찾을 수 없습니다.");
  }
  return window.__adobe_cep__;
}

export function isCepHost(): boolean {
  return Boolean(window.__adobe_cep__);
}

/**
 * 설치된 확장 폴더의 파일 시스템 경로. ZXP에 동봉한 python 런타임 씨앗을 찾는 데 쓴다.
 *
 * CEP 버전에 따라 file:/// URL을 돌려주기도 해서 둘 다 받아 넘긴다.
 * 브라우저에서 열었을 때처럼 알아낼 수 없으면 null.
 */
export function extensionRoot(): string | null {
  const raw = window.__adobe_cep__?.getSystemPath?.("extension");
  if (typeof raw !== "string" || raw === "") return null;
  if (!raw.startsWith("file://")) return raw;
  try {
    return decodeURIComponent(raw.replace(/^file:\/{2,3}/, "")).replace(/\//g, "\\");
  } catch {
    return null;
  }
}

export function evalHost<T>(functionName: string, payload?: unknown): Promise<T> {
  if (!HOST_FUNCTIONS.has(functionName)) {
    return Promise.reject(new Error(`허용되지 않은 호스트 함수: ${functionName}`));
  }
  const encoded = payload === undefined ? "" : JSON.stringify(JSON.stringify(payload));
  const script = `${functionName}(${encoded})`;
  return new Promise((resolve, reject) => {
    try {
      bridge().evalScript(script, (result) => {
        if (!result || result === "EvalScript error.") {
          reject(new Error("After Effects 스크립트 실행에 실패했습니다."));
          return;
        }
        try {
          resolve(JSON.parse(result) as T);
        } catch {
          reject(new Error(`After Effects 응답을 해석할 수 없습니다: ${result.slice(0, 180)}`));
        }
      });
    } catch (error) {
      reject(error instanceof Error ? error : new Error(String(error)));
    }
  });
}
