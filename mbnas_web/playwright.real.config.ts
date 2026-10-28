import { defineConfig, devices } from '@playwright/test';

/**
 * REAL-SERVER browser E2E (opt-in, nothing mocked): `npm run e2e:real` with MBNAS_E2E_REAL=1 and the gateway token in
 * MBNAS_E2E_TOKEN (read from the environment, never written to a file or the URL).
 *
 * The page under test is the production build. Default: `vite preview` on 127.0.0.1:8720 (the web preview port, DD-A7),
 * whose `/api` proxy forwards to the live gateway (MBNAS_DEV_API, default http://127.0.0.1:8710). With
 * MBNAS_E2E_BASE_URL (e.g. http://127.0.0.1:8710) the gateway itself serves dist/ same-origin (DD-A30) and no preview
 * server is started.
 * Optional MBNAS_E2E_XORIGIN_API points at a second API origin (no signing key, CORS allowing the page origin) to
 * exercise the explicit-origin + bearer_fetch Blob path.
 * Screenshots: evidence/real_*.png. Tests run serially in one worker (they share the run they create).
 *
 * V4 (WP-6 section 2, DD-A50): start `scripts/dev/web_real_stack.py` (core + owner worker + CPU runner + gateway serving
 * dist/), then run with MBNAS_E2E_V4=1, MBNAS_E2E_BASE_URL=<printed url>, MBNAS_E2E_TOKEN=<printed bearer>,
 * MBNAS_E2E_DATASET_YAML / MBNAS_E2E_OUTPUT_ROOT / MBNAS_E2E_STACK_PID (printed) and `-g "V4-"`. The legacy GPU flow
 * additionally needs MBNAS_E2E_GPU=1. V4 evidence: $MBNAS_E2E_EVIDENCE_DIR (default ../docs/internal/evidence/WP6/V4B).
 */
const BASE_URL = process.env.MBNAS_E2E_BASE_URL ?? 'http://127.0.0.1:8720';
export default defineConfig({
  testDir: 'e2e',
  testMatch: /real-server\.spec\.ts/,
  outputDir: 'test-results/real',
  timeout: 75 * 60_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: { baseURL: BASE_URL, trace: 'off', screenshot: 'off', acceptDownloads: true, actionTimeout: 60_000 },
  webServer: process.env.MBNAS_E2E_BASE_URL
    ? undefined
    : {
        command: 'npm run build && npx vite preview --host 127.0.0.1 --port 8720 --strictPort',
        url: BASE_URL,
        reuseExistingServer: true,
        timeout: 180_000,
      },
  projects: [{ name: 'real-desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 900 } } }],
});
