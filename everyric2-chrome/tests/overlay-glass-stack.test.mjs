import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const css = readFileSync(new URL('../public/overlay.css', import.meta.url), 'utf8');
const source = readFileSync(new URL('../src/ui/overlay.ts', import.meta.url), 'utf8');

function ruleBodies(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const matches = [...css.matchAll(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`, 'g'))];
  assert.ok(matches.length > 0, `missing CSS rule: ${selector}`);
  return matches.map(match => match[1]).join('\n');
}

function numericDeclaration(body, property) {
  const match = body.match(new RegExp(`${property}\\s*:\\s*(\\d+)`));
  assert.ok(match, `missing ${property}`);
  return Number(match[1]);
}

test('upper controls share a measured floating stack instead of a fixed chip offset', () => {
  assert.match(source, /className:\s*'ey-top-stack'/);
  assert.match(source, /topStackResizeObserver\.observe\(this\.topStack\)/);
  assert.match(source, /--ey-top-stack-height/);
  assert.doesNotMatch(source, /ey-under-chips/);

  const topStack = ruleBodies('.ey-top-stack');
  const body = ruleBodies('.ey-body');
  assert.match(topStack, /position\s*:\s*absolute/);
  assert.match(body, /--ey-top-stack-height/);
  assert.doesNotMatch(css, /\.ey-body\.ey-under-chips/);
});

test('each upper surface owns its rounded glass blur', () => {
  for (const selector of [
    '.ey-header',
    '.ey-lang-chips',
    '.ey-banner',
    '.ey-server-bar',
    '.ey-warn-bar',
    '.ey-gen-chip',
    '.ey-gen-list',
    '.ey-translation-pending',
  ]) {
    const body = ruleBodies(selector);
    assert.match(body, /background\s*:/, `${selector} needs its own background`);
    assert.match(body, /border-radius\s*:/, `${selector} needs rounded clipping`);
    assert.match(body, /backdrop-filter\s*:/, `${selector} needs backdrop blur`);
    assert.match(body, /-webkit-backdrop-filter\s*:/, `${selector} needs WebKit blur`);
  }
});

test('settings remain above the floating lyrics controls', () => {
  const settingsZ = numericDeclaration(ruleBodies('.ey-settings'), 'z-index');
  const topStackZ = numericDeclaration(ruleBodies('.ey-top-stack'), 'z-index');
  assert.ok(settingsZ > topStackZ);
});

test('current-song generation replaces the yellow prompt with a purple glass row', () => {
  assert.match(source, /setGenerationChip\([\s\S]*?currentActive/);
  assert.match(source, /bannerHiddenForGeneration/);
  assert.match(source, /ey-current-generation/);

  const generation = ruleBodies('.ey-gen-chip.ey-current-generation');
  assert.match(generation, /rgba\(124,\s*92,\s*255/);
  assert.match(generation, /border-radius\s*:/);
  assert.match(generation, /backdrop-filter\s*:/);
  assert.match(generation, /-webkit-backdrop-filter\s*:/);
});
