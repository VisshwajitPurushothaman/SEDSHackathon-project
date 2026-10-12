/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: {
    // The 3D scene (three.js + R3F) is lazy-loaded in App.tsx, so it lands in its own chunk
    // and never blocks the 2D console's first paint.
    chunkSizeWarningLimit: 1400,
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
});
