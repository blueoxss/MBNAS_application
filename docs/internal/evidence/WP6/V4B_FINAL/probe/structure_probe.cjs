// C07 evaluator probe (scratch only): the trained real-stack candidate's structure view in the SHIPPED dist.
// Reads the gene list, the SVG edges (data-source/target/from), kinds, the block table, then checks that a click on a
// graph node and a click on the gene button of the same block give the same selected-block detail and URL ?block=.
// Token comes from the environment only (PROBE_TOKEN); nothing is written outside the scratch directory.
const { chromium } = require('<ROOT>/mbnas_web/node_modules/playwright');
const fs = require('fs');
(async () => {
  const [url, runId, rowId, outDir] = process.argv.slice(2);
  const token = process.env.PROBE_TOKEN;
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const out = { url, runId, rowId, errors: [] };
  page.on('pageerror', (e) => out.errors.push(String(e)));
  await page.goto(url + '/#/server');
  await page.getByLabel(/Bearer token/).fill(token);
  await page.getByTestId('connect-btn').click();
  await page.getByText(/status: (ok|degraded)/).first().waitFor({ timeout: 30000 });
  await page.getByTestId('run-id-input').fill(runId);
  await page.getByTestId('load-run-btn').click();
  await page.waitForTimeout(1500);
  const menu = page.getByTestId('menu-btn');
  if (await menu.isVisible()) await menu.click();
  await page.getByTestId('nav-monitor').click();
  await page.getByTestId('candidate-table').getByRole('button', { name: new RegExp(`evaluation ${rowId.replace(/\./g, '\\.')}$`) }).first().click();
  const dlg = page.getByRole('dialog');
  await dlg.getByTestId('candidate-view').waitFor({ timeout: 30000 });
  await dlg.getByTestId('model-tab-structure').click();
  const graph = dlg.getByTestId('structure-graph');
  await graph.waitFor({ timeout: 60000 });
  out.genes = await dlg.locator('[data-testid^="gene-"]').evaluateAll((els) => els.map((e) => e.innerText.replace(/\s+/g, ' ').trim()));
  out.kinds = {};
  for (const g of out.genes) { const k = (g.match(/^#\d+ · (\w+)/) || [])[1]; out.kinds[k] = (out.kinds[k] || 0) + 1; }
  out.edges = await graph.locator('g[data-target]').evaluateAll((gs) => gs.map((g) => ({ s: g.getAttribute('data-source'), t: g.getAttribute('data-target'), from: g.getAttribute('data-from'), c: g.getAttribute('data-connection') })));
  out.multi_input_targets = {};
  for (const e of out.edges) { (out.multi_input_targets[e.t] = out.multi_input_targets[e.t] || []).push(e.s); }
  for (const k of Object.keys(out.multi_input_targets)) if (out.multi_input_targets[k].length < 2) delete out.multi_input_targets[k];
  const nodes = graph.locator('g[role="button"]');
  out.graph_nodes = await nodes.count();
  const pick = Number(process.env.PROBE_BLOCK || '12');
  await nodes.nth(pick).click();
  out.via_graph = { detail: (await dlg.getByTestId('selected-block').innerText()).slice(0, 4000), block: new URL(page.url()).searchParams.get('block'), pressed: await nodes.nth(pick).getAttribute('aria-pressed') };
  await dlg.getByTestId('gene-0').click();
  out.after_gene0 = new URL(page.url()).searchParams.get('block');
  await dlg.getByTestId(`gene-${pick}`).click();
  out.via_gene = { detail: (await dlg.getByTestId('selected-block').innerText()).slice(0, 4000), block: new URL(page.url()).searchParams.get('block'), graph_pressed: await nodes.nth(pick).getAttribute('aria-pressed') };
  out.graph_and_gene_same_detail = out.via_graph.detail === out.via_gene.detail && out.via_graph.block === out.via_gene.block;
  await page.screenshot({ path: `${outDir}/probe-structure-graph-block${pick}.png` });
  // a repeated block (C3k2, repeats 2): internal layers (no 1:1 claim) + shared marks
  const rep = Number(process.env.PROBE_REPEAT_BLOCK || '2');
  await dlg.getByTestId(`gene-${rep}`).click();
  const layers = dlg.getByTestId('internal-layers');
  out.repeat_block = { detail_head: (await dlg.getByTestId('selected-block').innerText()).slice(0, 600) };
  if (await layers.count()) {
    await layers.locator('summary').click();
    out.repeat_block.summary = await layers.locator('summary').innerText();
    out.repeat_block.rows = await layers.locator('tbody tr').count();
    out.repeat_block.shared_rows = await layers.locator('tbody tr[data-shared="true"]').count();
    out.repeat_block.paths = await layers.locator('tbody tr td:first-child').evaluateAll((tds) => tds.map((t) => t.textContent));
    out.repeat_block.repeats = { declared: await dlg.getByTestId('repeats-declared').innerText(), scaled: await dlg.getByTestId('repeats-scaled').innerText(), observed: await dlg.getByTestId('repeats-observed').innerText() };
    await layers.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${outDir}/probe-internal-layers-block${rep}.png` });
  }
  await dlg.getByTestId('model-tab-all').click();
  out.block_table_rows = await dlg.getByTestId('block-table').locator('tbody tr').count();
  out.block_table_row_buttons = await dlg.getByTestId('block-table').locator('tbody button, tbody [role="button"], tbody a').count();
  fs.writeFileSync(`${outDir}/structure_probe.json`, JSON.stringify(out, null, 1));
  console.log(JSON.stringify({ genes: out.genes.length, kinds: out.kinds, edges: out.edges.length, multi_input_targets: out.multi_input_targets, graph_nodes: out.graph_nodes, same: out.graph_and_gene_same_detail, via_graph_block: out.via_graph.block, block_table_rows: out.block_table_rows, block_table_row_buttons: out.block_table_row_buttons, repeat_block: { rows: out.repeat_block.rows, shared: out.repeat_block.shared_rows, repeats: out.repeat_block.repeats }, errors: out.errors }));
  await browser.close();
})().catch((e) => { console.error('PROBE_ERROR', e); process.exit(1); });
