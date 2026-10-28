// C07 round-2 probe (scratch): title of the "학습 완료 · 미평가" badge of the trained real-stack candidate (detail + Monitor table).
const { chromium } = require('/home/mgjeong/Desktop/etri_hd/mbnas_application/mbnas_web/node_modules/playwright');
(async () => {
  const [url, runId, rowId] = process.argv.slice(2);
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.goto(url + '/#/server');
  await page.getByLabel(/Bearer token/).fill(process.env.PROBE_TOKEN);
  await page.getByTestId('connect-btn').click();
  await page.getByText(/status: (ok|degraded)/).first().waitFor({ timeout: 30000 });
  await page.getByTestId('run-id-input').fill(runId);
  await page.getByTestId('load-run-btn').click();
  await page.waitForTimeout(1500);
  await page.getByTestId('nav-monitor').click();
  const table = page.getByTestId('candidate-table');
  await table.waitFor({ timeout: 30000 });
  const monitorBadge = table.getByText('학습 완료 · 미평가').first();
  const monitorTitle = await monitorBadge.evaluate((el) => (el.closest('[title]') || {}).title || null);
  await table.getByRole('button', { name: new RegExp(`evaluation ${rowId.replace(/\./g, '\\.')}$`) }).first().click();
  const dlg = page.getByRole('dialog');
  const badge = dlg.getByTestId('trained-not-evaluated');
  await badge.waitFor({ timeout: 30000 });
  const detailTitle = await badge.evaluate((el) => (el.closest('[title]') || {}).title || null);
  console.log(JSON.stringify({ monitorTitle, detailTitle, same: monitorTitle === detailTitle }));
  await browser.close();
})().catch((e) => { console.error('PROBE_ERROR', e); process.exit(1); });
