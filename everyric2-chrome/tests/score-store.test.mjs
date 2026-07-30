import assert from 'node:assert/strict';
import test from 'node:test';

test('concurrent score writes preserve every completed song', async () => {
  let records = [];
  globalThis.chrome = {
    storage: {
      local: {
        get: async () => {
          const snapshot = structuredClone(records);
          await Promise.resolve();
          return { scoreHistory: snapshot };
        },
        set: async value => {
          await Promise.resolve();
          records = structuredClone(value.scoreHistory);
        },
      },
    },
  };

  const { addScore } = await import('../src/lib/score-store.ts');
  await Promise.all([
    addScore('video-a', 'Song A', 80),
    addScore('video-b', 'Song B', 90),
  ]);

  assert.deepEqual(
    new Set(records.map(record => record.videoId)),
    new Set(['video-a', 'video-b']),
  );
});
