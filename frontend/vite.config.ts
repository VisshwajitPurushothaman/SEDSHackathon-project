/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: {
    // MapLibre is lazy-loaded with the map panels.
    chunkSizeWarningLimit: 1400,
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
});
