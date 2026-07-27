// E2E 브라우저 기동 진단 — 어떤 채널이 --load-extension과 함께 뜨는지만 본다.
import { chromium } from 'playwright';
import { fileURLToPath } from 'url';
import { dirname, resolve } from 'path';
import { mkdtempSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';

const distDir = resolve(dirname(fileURLToPath(import.meta.url)), '../dist');
const channel = process.argv[2] || undefined; // 'msedge' | 'chrome' | (없으면 번들 chromium)

try {
  const ctx = await chromium.launchPersistentContext(mkdtempSync(join(tmpdir(), 'ey-probe-')), {
  // Playwright 신버전은 --disable-extensions-except를 줘도 기본 --disable-extensions를
  // 빼주지 않는다(구버전 특례 삭제) — 이게 남으면 --load-extension이 조용히 무시된다.
  ignoreDefaultArgs: ['--disable-extensions'],
    ...(channel ? { channel } : {}),
    headless: process.env.EVERYRIC_E2E_HEADLESS === '1',
    args: [
      `--disable-extensions-except=${distDir}`,
      `--load-extension=${distDir}`,
      '--mute-audio',
    ],
  });
  const sw = ctx.serviceWorkers()[0] ?? await ctx.waitForEvent('serviceworker', { timeout: 45000 });
  console.log('OK channel=', channel ?? 'bundled-chromium', '| sw:', sw.url().slice(0, 60));
  await ctx.close();
  process.exit(0);
} catch (e) {
  console.log('FAIL channel=', channel ?? 'bundled-chromium', '|', String(e).slice(0, 200));
  process.exit(1);
}
