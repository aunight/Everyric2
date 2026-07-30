import { defineConfig } from 'vite';
import { crx } from '@crxjs/vite-plugin';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import manifest from './manifest.json';

export default defineConfig({
  plugins: [
    crx({ manifest }),
    {
      name: 'third-party-license-assets',
      generateBundle() {
        this.emitFile({
          type: 'asset',
          fileName: 'THIRD_PARTY_NOTICES.md',
          source: readFileSync(resolve(__dirname, '..', 'THIRD_PARTY_NOTICES.md'), 'utf8'),
        });
        this.emitFile({
          type: 'asset',
          fileName: 'LICENSE-APACHE-2.0.txt',
          source: readFileSync(resolve(__dirname, '..', 'LICENSE'), 'utf8'),
        });
      },
    },
  ],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  publicDir: 'public',
});
