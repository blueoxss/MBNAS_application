// C07 evaluator: the project's mock-e2e config (mbnas_web/playwright.config.ts) unchanged except for
// (a) absolute testDir, (b) outputDir in scratch, (c) baseURL/webServer on port 4199 serving the dist EXTRACTED FROM THE
// PUBLIC ZIP (no `npm run build`, so mbnas_web/dist is never rewritten).
import base from '<ROOT>/mbnas_web/playwright.config.ts';
const WEB = '<ROOT>/mbnas_web';
const S = '<SCRATCH>/wp3/c07';
const PORT = 4199;
export default {
  ...base,
  testDir: `${WEB}/e2e`,
  outputDir: `${S}/e2e_mock/test-results`,
  use: { ...base.use, baseURL: `http://127.0.0.1:${PORT}` },
  webServer: {
    command: `${WEB}/node_modules/.bin/vite preview --host 127.0.0.1 --port ${PORT} --strictPort --outDir ${S}/zipdist/dist`,
    cwd: WEB,
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
};
