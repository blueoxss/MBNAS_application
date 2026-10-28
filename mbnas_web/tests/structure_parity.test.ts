/**
 * Parity of the web structure diff with the server `compare_structures` for the same input (WP-W1).
 *
 * `structure_v2_server_fixture.json` is NOT hand written. Both the mbnas-structure/2 documents (real CPU build + forward
 * hooks of tiny Ultralytics models at imgsz 64) and the expected diffs (the real `compare_structures()` output) were
 * produced by the server code with `docs/integration_20261021/evidence_impl/WPW1/gen_server_structure_fixture.py`
 * (command and output: evidence_impl/WPW1/gen_fixture.txt).
 */
import { describe, expect, it } from 'vitest';
import fixture from './structure_v2_server_fixture.json';
import { diffStructureDocuments, structureEdges, structureNodes } from '../src/lib/structure';

type Doc = Record<string, unknown> & { blocks: unknown[]; edges: { source: number; target: number; from_ref: number }[] };
const docs = fixture.documents as unknown as Record<string, Doc>;

describe('web structure diff == server compare_structures (same documents)', () => {
  for (const pair of fixture.pairs) {
    it(`${pair.left} -> ${pair.right}`, () => {
      expect(diffStructureDocuments(docs[pair.left], docs[pair.right])).toEqual(pair.expected);
    });
  }

  it('the fixture covers changed rows, added / removed rows and edge differences in both directions', () => {
    const by = Object.fromEntries(fixture.pairs.map((p) => [`${p.left}>${p.right}`, p.expected]));
    expect(by['base>same_len'].edges_added.length + by['base>same_len'].edges_removed.length).toBeGreaterThan(0);
    expect(by['base>longer'].added).toHaveLength(1);
    expect(by['longer>base'].removed).toHaveLength(1);
    expect(by['base>changed'].changed[0].fields).toEqual(['module', 'repeats', 'args']);
    expect(by['base>base']).toMatchObject({ added: [], removed: [], changed: [], edges_added: [], edges_removed: [] });
  });

  it('the edges the view derives from `from` are exactly the edges of the real report', () => {
    for (const name of ['base', 'same_len', 'longer', 'changed']) {
      const doc = docs[name];
      const nodes = structureNodes(doc.blocks, name, { representation: doc.representation, schema: doc.schema });
      const position = new Map(nodes.map((n) => [n.id, n.position]));
      const derived = structureEdges(nodes).map((e) => [e.source === null ? -1 : position.get(e.source), position.get(e.target), e.reference]);
      expect(derived, name).toEqual(doc.edges.map((e) => [e.source, e.target, e.from_ref]));
    }
  });

  it('the diff also works on the declared-only document (no observation) and never claims gene ancestry', () => {
    expect(diffStructureDocuments(docs.base, docs.declared_only).gene_ancestry_inferred).toBe(false);
    expect(docs.declared_only.observation).toBeNull();
  });
});
