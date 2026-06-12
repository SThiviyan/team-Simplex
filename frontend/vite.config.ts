import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // In `make dev` (docker compose), the backend is reachable as the
      // compose service name. Override via VITE_PROXY_TARGET if you run the
      // frontend outside compose (e.g. `npm run dev` while backend runs on
      // host port 8080 — then set VITE_PROXY_TARGET=http://localhost:8080).
      '/api': {
        target: process.env.VITE_PROXY_TARGET ?? 'http://backend:8080',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
});
