import { defineConfig, devices } from '@playwright/test';

/**
 * Browser tests run against the production build served by `vite preview`, with the API fully mocked via
 * page.route() (synthetic fixtures in tests/fixtures.ts). Desktop 1280 and mobile 375 viewports.
 * Screenshots are written to evidence/.
 */
export default defineConfig({
  testDir: 'e2e',
  testIgnore: /(real-server|archive-real)\.spec\.ts/, // opt-in real-server suites: playwright.real.config.ts / playwright.archive-real.config.ts
  outputDir: 'test-results',
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  reporter: [['list']],
  use: { baseURL: 'http://127.0.0.1:4173', trace: 'off', screenshot: 'off' },
  webServer: {
    command: 'npm run build && npx vite preview --host 127.0.0.1 --port 4173 --strictPort',
    url: 'http://127.0.0.1:4173',
    reuseExistingServer: true,
    timeout: 120_000,
  },
  projects: [
    { name: 'desktop-1280', testIgnore: [/mobile\.spec\.ts/, /(real-server|archive-real)\.spec\.ts/], use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 900 } } },
    { name: 'mobile-375', use: { ...devices['Pixel 5'], viewport: { width: 375, height: 812 }, deviceScaleFactor: 2 } },
  ],
});
