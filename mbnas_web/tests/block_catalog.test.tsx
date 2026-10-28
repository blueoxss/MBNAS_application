/**
 * WP-S6 / DD-38: block design-origin catalog in the web.
 *
 * tests/block_catalog_server_fixture.json is REAL server output (evidence_impl/WPS6/gen_catalog_fixture.py): a checked YAML
 * document (source_path resolved), a declared document with one module the catalog does not know, the ETRI hybrid native
 * document, and the GET /structure/block-catalog body.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/App';
import { ModelStructure } from '../src/components/ModelStructure';
import { blockCatalogOf, catalogEntryFor, catalogSummaryOf, formatOriginDistribution, originDistribution, structureNodes, type BlockCatalogTable } from '../src/lib/structure';
import { AppProvider } from '../src/state/context';
import { installArchiveFetch, newFixtureState, RUN_A, SYNTH_A } from './archive_fixtures';
import fixture from './block_catalog_server_fixture.json';

type Doc = Record<string, unknown> & { blocks: Record<string, unknown>[] };
const docs = fixture.documents as unknown as Record<string, Doc>;
const table = fixture.table as unknown as BlockCatalogTable;

afterEach(() => { cleanup(); window.history.replaceState(null, '', '/'); });

describe('catalog readers (lib/structure.ts)', () => {
  it('reads blocks[].catalog and treats a missing key as "older document", not as unknown', () => {
    const c = blockCatalogOf(docs.checked.blocks[0].catalog);
    expect(c).toMatchObject({ originFamily: 'YOLOv5', introducedIn: 'YOLOv5', sourcePath: 'ultralytics/nn/modules/conv.py' });
    expect(c?.reference).toMatch(/^https:\/\/docs\.ultralytics\.com\//);
    expect(blockCatalogOf(undefined)).toBeNull();
    expect(blockCatalogOf('x')).toBeNull();
    expect(structureNodes(docs.checked.blocks, 'm')[0].catalog?.originFamily).toBe('YOLOv5');
    expect(structureNodes([{ ...docs.checked.blocks[0], catalog: undefined }], 'm')[0].catalog).toBeNull();
  });

  it('an unknown module keeps every field null (출처 미상) instead of a guess', () => {
    const unknown = docs.declared_with_unknown.blocks.find((b) => b.module === 'MyCustomBlock');
    expect(blockCatalogOf(unknown?.catalog)).toMatchObject({ originFamily: null, introducedIn: null, role: null, reference: null, sourcePath: null });
  });

  it('formats the distribution from catalog_summary: largest first, 미상 last, percent of all blocks', () => {
    const summary = catalogSummaryOf({ catalog_summary: { by_origin_family: { YOLO11: 6, 'ETRI mixed operator pool': 2 }, unknown: 2 } });
    expect(formatOriginDistribution(summary)).toBe('YOLO11 60% · ETRI mixed 20% · 미상 20%');
    expect(originDistribution(summary).map((s) => s.count)).toEqual([6, 2, 2]);
    expect(formatOriginDistribution(catalogSummaryOf(docs.declared_with_unknown))).toBe('YOLOv5 50% · YOLOv8 33% · 미상 17%');
    expect(formatOriginDistribution(catalogSummaryOf(docs.etri_native))).toBe('ETRI hybrid 93% · PyTorch 7%');
    expect(formatOriginDistribution(catalogSummaryOf({ catalog_summary: { by_origin_family: { A: 1, B: 299 }, unknown: 0 } }))).toBe('B 100% · A <1%');
  });

  it('no summary or zero blocks gives no distribution (a document without the key is not "all unknown")', () => {
    expect(catalogSummaryOf({})).toBeNull();
    expect(formatOriginDistribution(null)).toBe('');
    expect(originDistribution(catalogSummaryOf({ catalog_summary: { by_origin_family: {}, unknown: 0 } }))).toEqual([]);
  });

  it('looks modules up in the served table: nn. prefix stripped, unknown -> null', () => {
    expect(catalogEntryFor(table, 'C3k2')?.origin_family).toBe('YOLO11');
    expect(catalogEntryFor(table, 'nn.Upsample')?.origin_family).toBe('PyTorch torch.nn');
    expect(catalogEntryFor(table, 'NASWindowTransformer')?.origin_family).toBe('ETRI mixed operator pool');
    expect(catalogEntryFor(table, 'NoSuchModule')).toBeNull();
    expect(catalogEntryFor(null, 'Conv')).toBeNull();
    expect(table.native_entries['rfdetr:MultiScaleProjector'].origin_family).toBe('LW-DETR');
  });
});

describe('ModelStructure: distribution line and block rows', () => {
  it('shows the origin distribution under the header, from catalog_summary', () => {
    render(<ModelStructure blocks={docs.checked.blocks} modelKey="checked" report={docs.checked} />);
    expect(screen.getByTestId('model-origin-distribution')).toHaveTextContent('구성 블록 출처 분포: YOLOv5 60% · YOLOv8 40%');
    expect(screen.getByTestId('model-origin-distribution').compareDocumentPosition(screen.getByTestId('connection-legend')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('block detail rows: 출처 계열 / 도입 버전 / 역할 / 참고 (link) and the installed source path', () => {
    render(<ModelStructure blocks={docs.checked.blocks} modelKey="checked" report={docs.checked} />);
    fireEvent.click(screen.getByTestId('gene-1')); // C2f
    const origin = within(screen.getByTestId('block-origin'));
    expect(origin.getByText('출처 계열')).toBeInTheDocument();
    expect(origin.getByText('도입 버전')).toBeInTheDocument();
    expect(origin.getByText('역할')).toBeInTheDocument();
    expect(origin.getByText('참고')).toBeInTheDocument();
    expect(screen.getByTestId('origin-family')).toHaveTextContent('YOLOv8');
    expect(screen.getByTestId('origin-introduced')).toHaveTextContent('YOLOv8');
    expect(screen.getByTestId('origin-role')).toHaveTextContent('C2f');
    const link = within(screen.getByTestId('origin-reference')).getByRole('link');
    expect(link).toHaveAttribute('href', expect.stringContaining('docs.ultralytics.com/reference/nn/modules/block/'));
    expect(link).toHaveAttribute('rel', expect.stringContaining('noreferrer'));
    expect(screen.getByTestId('origin-source')).toHaveTextContent('ultralytics/nn/modules/block.py');
  });

  it('null fields read 출처 미상 and the block is still selectable', () => {
    render(<ModelStructure blocks={docs.declared_with_unknown.blocks} modelKey="declared" report={docs.declared_with_unknown} />);
    expect(screen.getByTestId('model-origin-distribution')).toHaveTextContent('미상 17%');
    const index = docs.declared_with_unknown.blocks.findIndex((b) => b.module === 'MyCustomBlock');
    fireEvent.click(screen.getByTestId(`gene-${index}`));
    for (const id of ['origin-family', 'origin-introduced', 'origin-role', 'origin-reference']) expect(screen.getByTestId(id)).toHaveTextContent('출처 미상');
    expect(screen.getByTestId('origin-source')).toHaveTextContent('미확인');
    expect(screen.getByTestId('origin-notes')).toHaveTextContent('unknown');
    // a declared-only document never carries a resolved source path
    fireEvent.click(screen.getByTestId('gene-0'));
    expect(screen.getByTestId('origin-family')).toHaveTextContent('YOLOv5');
    expect(screen.getByTestId('origin-source')).toHaveTextContent('미확인');
  });

  it('native blocks (ETRI hybrid): family from module_class / path rule; a repository path is text, not a link', () => {
    render(<ModelStructure blocks={docs.etri_native.blocks} modelKey="etri" report={docs.etri_native} />);
    expect(screen.getByTestId('model-origin-distribution')).toHaveTextContent('ETRI hybrid 93% · PyTorch 7%');
    fireEvent.click(screen.getByTestId('gene-0'));
    expect(screen.getByTestId('origin-family')).toHaveTextContent('ETRI controlled hybrid');
    expect(screen.getByTestId('origin-role')).toHaveTextContent('CNN 스템');
    expect(within(screen.getByTestId('origin-reference')).queryByRole('link')).toBeNull();
    expect(screen.getByTestId('origin-reference')).toHaveTextContent('etri_hybrid/model.py');
  });

  it('a document from before DD-38 (no catalog, no summary) shows neither the line nor the rows', () => {
    const old = { ...docs.checked, catalog_summary: undefined, blocks: docs.checked.blocks.map(({ catalog: _drop, ...rest }) => rest) };
    render(<ModelStructure blocks={old.blocks} modelKey="old" report={old} />);
    fireEvent.click(screen.getByTestId('gene-1'));
    expect(screen.queryByTestId('model-origin-distribution')).toBeNull();
    expect(screen.queryByTestId('block-origin')).toBeNull();
    expect(screen.getByTestId('selected-block')).toBeInTheDocument();
  });
});

describe('archive candidate modal: origin beside each module', () => {
  let restore: () => void = () => undefined;
  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => 'blob:synthetic') as unknown as typeof URL.createObjectURL;
    URL.revokeObjectURL = vi.fn();
  });
  afterEach(() => { restore(); window.history.replaceState(null, '', '#/server'); });

  const json = (status: number, body: unknown): Response => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
  const cid = SYNTH_A.rows.find((r) => r.generation === 2 && r.lineage?.mode === 'ga_crossover' && r.status === 'ok' && r.tier === 'T@8e')?.candidate_id as string;

  async function open(catalogResponse: () => Response): Promise<ReturnType<typeof installArchiveFetch>> {
    window.history.replaceState(null, '', `#/archive?run=${RUN_A}`);
    const api = installArchiveFetch(newFixtureState(), (url) => url.pathname === '/api/v1/structure/block-catalog' ? catalogResponse() : undefined);
    restore = api.restore;
    const user = userEvent.setup();
    render(<AppProvider><App /></AppProvider>);
    await user.type(screen.getByLabelText('API 토큰'), 'TEST-TOKEN');
    await user.click(screen.getByTestId('gate-connect'));
    await user.click((await screen.findAllByTestId(`cand-open-${cid}`))[0]);
    await screen.findByRole('dialog');
    await screen.findByTestId('acand-evals');
    return api;
  }

  it('lists each histogram module with the family from the served catalog (the web holds no table)', async () => {
    const api = await open(() => json(200, table));
    const origins = await screen.findByTestId('module-origins');
    await waitFor(() => expect(screen.getByTestId('origin-Conv')).toHaveTextContent('YOLOv5'));
    expect(screen.getByTestId('origin-C3k2')).toHaveTextContent('YOLO11');
    expect(screen.getByTestId('origin-SPPF')).toHaveTextContent('YOLOv5');
    expect(screen.getByTestId('origin-Detect')).toHaveTextContent('YOLOv8');
    // the existing histogram is still there next to it
    expect(screen.getByTestId('module-hist')).toBeInTheDocument();
    expect(within(origins).getAllByRole('row').length).toBeGreaterThan(2);
    const call = api.calls.filter((c) => c.path === '/api/v1/structure/block-catalog');
    expect(call.length).toBe(1);
    expect(call[0].method).toBe('GET');
    expect(JSON.stringify(call[0].headers)).toMatch(/Bearer TEST-TOKEN/i);
  });

  it('a module the served catalog does not list reads 출처 미상', async () => {
    const trimmed: BlockCatalogTable = { ...table, entries: Object.fromEntries(Object.entries(table.entries).filter(([name]) => name !== 'Conv')) };
    await open(() => json(200, trimmed));
    await waitFor(() => expect(screen.getByTestId('origin-C3k2')).toHaveTextContent('YOLO11'));
    expect(screen.getByTestId('origin-Conv')).toHaveTextContent('출처 미상');
  });

  it('when the catalog request fails the modal still works and says why the origins are missing', async () => {
    await open(() => json(500, { code: 'INTERNAL', message: 'catalog unavailable' }));
    expect(await screen.findByTestId('origin-catalog-error')).toHaveTextContent('catalog unavailable');
    expect(screen.getByTestId('origin-Conv')).toHaveTextContent('—');
    expect(screen.getByTestId('acand-evals')).toBeInTheDocument();
    expect(screen.getByTestId('module-hist')).toBeInTheDocument();
  });
});
