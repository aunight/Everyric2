import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const css = readFileSync(new URL('../public/overlay.css', import.meta.url), 'utf8');
const overlaySource = readFileSync(new URL('../src/ui/overlay.ts', import.meta.url), 'utf8');

function ruleBody(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
  assert.ok(match, `missing CSS rule: ${selector}`);
  return match[1];
}

test('link offset and rate controls share one compact horizontal row', () => {
  assert.match(
    overlaySource,
    /className:\s*'ey-link-parameter-row'[\s\S]*?offsetInput[\s\S]*?rateInput/,
  );
  assert.match(ruleBody('.ey-link-parameter-row'), /grid-template-columns\s*:\s*repeat\(2/);
  assert.match(ruleBody('.ey-link-field'), /flex-direction\s*:\s*row/);
  assert.match(ruleBody('.ey-link-field'), /align-items\s*:\s*center/);
});

test('candidate rows prioritize the full song title above secondary metadata', () => {
  const item = ruleBody('.ey-result-item');
  const title = ruleBody('.ey-result-title');
  const meta = ruleBody('.ey-result-meta');

  assert.match(item, /display\s*:\s*grid/);
  assert.match(item, /grid-template-columns\s*:\s*auto\s+minmax\(0,\s*1fr\)/);
  assert.match(title, /white-space\s*:\s*normal/);
  assert.doesNotMatch(title, /text-overflow\s*:\s*ellipsis/);
  assert.match(meta, /grid-column\s*:\s*2/);
});
