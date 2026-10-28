import { afterEach, describe, expect, it } from 'vitest';
import { ApiClient } from '../src/api/client';
import { structureEdges, structureNodes } from '../src/lib/structure';
import { GEN_CID, GEN_RUN_ID, SYNTHETIC_CONCAT_INDEX, SYNTHETIC_DETECT_INDEX, SYNTHETIC_EDGE_COUNT, syntheticGeneratedDetail, syntheticGeneratedRows, syntheticStructureReport } from './detail_fixtures';
import { installMockApi } from './mockapi';

let restore: (() => void) | undefined;
afterEach(() => restore?.());

describe('synthetic generated-candidate detail (shared with the Playwright mock)', () => {
  it('has an mbnas-structure/2 report whose list-from rows resolve to the expected edges', () => {
    const report = syntheticStructureReport();
    expect(report.schema).toBe('mbnas-structure/2');
    expect(report.flops_status).toBe('NOT_MEASURED'); // DD-14: never estimated
    const nodes = structureNodes(report.blocks as unknown[], GEN_CID);
    const edges = structureEdges(nodes);
    expect(edges).toHaveLength(SYNTHETIC_EDGE_COUNT);
    expect(edges.every((e) => e.state !== 'unresolved')).toBe(true);
    const into = (i: number): (string | null)[] => edges.filter((e) => e.target === nodes[i].id).map((e) => e.source).sort();
    expect(into(SYNTHETIC_CONCAT_INDEX)).toEqual(['block-0002', 'block-0005']);
    expect(into(SYNTHETIC_DETECT_INDEX)).toEqual(['block-0004', 'block-0007']);
    // the report's own edge list agrees with the edges derived from `from`
    const own = (report.edges as { source: string | null; target: string }[]).map((e) => `${e.source}>${e.target}`).sort();
    expect(own).toEqual(edges.map((e) => `${e.source}>${e.target}`).sort());
  });

  it('lists the generated rows out of generation order (the table has to sort them)', () => {
    expect(syntheticGeneratedRows().map((r) => r.generation_order)).toEqual([2, 0, 1]);
    expect(syntheticGeneratedRows().find((r) => r.parameters === null)?.generation_order).toBe(2);
  });

  it('is served by installMockApi at GET /runs/{id}/candidates/{cid}; unknown ids stay 404', async () => {
    const mock = installMockApi({ details: { [GEN_CID]: syntheticGeneratedDetail() } });
    restore = mock.restore;
    const client = new ApiClient({ origin: '', token: 'synthetic-token' });
    const detail = await client.candidateDetail(GEN_RUN_ID, GEN_CID);
    expect(detail.structure_status).toBe('PASS');
    expect(detail.evaluation_status).toBe('NOT_EVALUATED');
    await expect(client.candidateDetail(GEN_RUN_ID, 'synthetic-unknown')).rejects.toThrow();
  });
});
