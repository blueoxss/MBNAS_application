import { defineConfig, devices } from '@playwright/test';

/**
 * REAL-SERVER browser E2E for the NAS archive screens (opt-in, nothing mocked): `npm run e2e:archive-real` with
 * MBNAS_E2E_REAL=1 and the gateway token in MBNAS_E2E_TOKEN (environment only, never written to a file or the URL).
 *
 * The page under test is the production build. Default: `vite preview` on 127.0.0.1:8720 (the web preview port, DD-A7;
 * reused when already listening), whose `/api` proxy forwards to the live gateway (MBNAS_DEV_API, default
 * http://127.0.0.1:8710); the gateway reads the run artifacts under its configured archive roots. With
 * MBNAS_E2E_BASE_URL (e.g. http://127.0.0.1:8710) the gateway itself serves dist/ (DD-A30) and no preview is started.
 * Optional MBNAS_E2E_ARCHIVE_RUN pins the run id; default is the VisDrone smoke run.
 * Screenshots: evidence/real_archive_*.png (new files only; existing evidence is never touched).
 */
const BASE_URL = process.env.MBNAS_E2E_BASE_URL ?? 'http://127.0.0.1:8720';
export default defineConfig({
  testDir: 'e2e',
  testMatch: /archive-real\.spec\.ts/,
  outputDir: 'test-results/archive-real',
  timeout: 10 * 60_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: { baseURL: BASE_URL, trace: 'off', screenshot: 'off', actionTimeout: 30_000 },
  webServer: process.env.MBNAS_E2E_BASE_URL
    ? undefined
    : {
        command: 'npm run build && npx vite preview --host 127.0.0.1 --port 8720 --strictPort',
        url: BASE_URL,
        reuseExistingServer: true,
        timeout: 180_000,
      },
  projects: [{ name: 'archive-real-desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 900 } } }],
});
