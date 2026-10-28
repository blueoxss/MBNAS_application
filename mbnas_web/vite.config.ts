/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

/**
 * API base = same origin ('/api/v1', the empty API origin of the Server screen).
 * Prod (DD-A30): the gateway (mbnas_server, port 8710) serves this dist/ at '/' with an SPA fallback, so the bundle and
 * the API share one origin; no CORS, no API URL is baked into the bundle.
 * Dev and `vite preview` (preview.proxy defaults to server.proxy): /api is proxied to the local gateway, default
 * http://127.0.0.1:8710 (MBNAS_DEV_API overrides; no CORS needed).
 * A remote API on another origin is still possible: enter an explicit https:// origin on the Server screen; that gateway
 * must then allow this page's exact origin via MBNAS_GW_CORS_ORIGINS.
 * Test fixtures live in tests/ and e2e/ and are never imported from src/, so they are not in the bundle
 * (`npm run check:bundle` greps dist/ for host facts and the removed policy embeds after a build).
 */
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: process.env.MBNAS_DEV_API ?? 'http://127.0.0.1:8710', changeOrigin: false },
    },
  },
  preview: { port: 4173 },
  build: { sourcemap: false, target: 'es2022' },
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.test.{ts,tsx}'],
    setupFiles: ['tests/setup.ts'],
    css: false,
  },
});
