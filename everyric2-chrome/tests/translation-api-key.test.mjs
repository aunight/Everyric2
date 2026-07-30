import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import * as hostPermissions from '../src/lib/host-permissions.ts';

const selectTranslationApiKey = hostPermissions.selectTranslationApiKey;

test('never forwards a personal translation API key to the public default server', () => {
  assert.equal(typeof selectTranslationApiKey, 'function');
  assert.equal(
    selectTranslationApiKey('https://everyric.moref.co', 'personal-gemini-key'),
    undefined,
  );
  assert.equal(
    selectTranslationApiKey('https://EVERYRIC.MOREF.CO/api', 'personal-gemini-key'),
    undefined,
  );
});

test('forwards a trimmed translation API key only to an explicitly self-hosted server', () => {
  assert.equal(
    selectTranslationApiKey('http://127.0.0.1:8000', '  personal-gemini-key  '),
    'personal-gemini-key',
  );
  assert.equal(
    selectTranslationApiKey('https://lyrics.example.test', 'personal-gemini-key'),
    'personal-gemini-key',
  );
  assert.equal(selectTranslationApiKey('not a URL', 'personal-gemini-key'), undefined);
  assert.equal(selectTranslationApiKey('https://lyrics.example.test', '   '), undefined);
});

test('background applies the server-aware key filter before creating ServerConfig', async () => {
  const source = await readFile(new URL('../src/background.ts', import.meta.url), 'utf8');
  assert.match(source, /selectTranslationApiKey\s*\(\s*serverUrl\s*,\s*translationApiKey\s*\)/);
});

test('loopback server access remains an optional Chrome permission', async () => {
  const manifest = JSON.parse(await readFile(
    new URL('../manifest.json', import.meta.url),
    'utf8',
  ));
  for (const origin of ['http://localhost:8000/*', 'http://127.0.0.1:8000/*']) {
    assert.ok(manifest.optional_host_permissions?.includes(origin));
    assert.ok(!manifest.host_permissions.includes(origin));
  }
});
