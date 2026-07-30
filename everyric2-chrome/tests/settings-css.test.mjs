import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const css = readFileSync(new URL('../public/overlay.css', import.meta.url), 'utf8');

function ruleBodies(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const matches = [...css.matchAll(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`, 'g'))];
  assert.ok(matches.length > 0, `missing CSS rule: ${selector}`);
  return matches.map(match => match[1]);
}

function numericDeclaration(body, property) {
  const match = body.match(new RegExp(`${property}\\s*:\\s*(\\d+)`));
  assert.ok(match, `missing ${property}`);
  return Number(match[1]);
}

test('settings sheet is stacked above the language chips', () => {
  const settingsZ = numericDeclaration(ruleBodies('.ey-settings').join('\n'), 'z-index');
  const chipsZ = numericDeclaration(ruleBodies('.ey-lang-chips').join('\n'), 'z-index');
  assert.ok(settingsZ > chipsZ, 'settings sheet must cover language chips');
});

test('glass blur is clipped to the return button instead of its sticky row', () => {
  const settingsTop = ruleBodies('.ey-settings-top').join('\n');
  const settingsBack = ruleBodies('.ey-settings-back').join('\n');
  assert.doesNotMatch(settingsTop, /backdrop-filter|background\s*:/);
  assert.match(settingsBack, /backdrop-filter\s*:/);
  assert.match(settingsBack, /border-radius\s*:\s*999px/);
  assert.match(settingsBack, /overflow\s*:\s*hidden/);
});
