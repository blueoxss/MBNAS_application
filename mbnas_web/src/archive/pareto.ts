/**
 * Non-dominated sets used by the archive scatter (design D6/D9). These are DISPLAY helpers: the numbers on the
 * page come from the server; when the server supplies `front2d` ids the chart uses those, and this module is used
 * for a tier-filtered view and as the independent definition that the tests pin down.
 *
 *  - 2D front: maximise mAP50-95, minimise latency_median_ms.
 *  - 4D front: maximise mAP50-95 and small_target_recall, minimise latency_median_ms and params_deploy.
 * A point with a missing objective value takes no part (it is neither on the front nor does it dominate).
 * Equal points do not dominate each other (both stay on the front).
 */

export interface ObjPoint {
  id: string;
  map: number | null | undefined;
  lat: number | null | undefined;
  recall?: number | null;
  params?: number | null;
}

const fin = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/** a dominates b in (map up, lat down): no worse in both, strictly better in at least one. */
export function dominates2d(a: { map: number; lat: number }, b: { map: number; lat: number }): boolean {
  return a.map >= b.map && a.lat <= b.lat && (a.map > b.map || a.lat < b.lat);
}

export function dominates4d(a: { map: number; recall: number; lat: number; params: number }, b: { map: number; recall: number; lat: number; params: number }): boolean {
  const noWorse = a.map >= b.map && a.recall >= b.recall && a.lat <= b.lat && a.params <= b.params;
  const better = a.map > b.map || a.recall > b.recall || a.lat < b.lat || a.params < b.params;
  return noWorse && better;
}

export function front2d(points: readonly ObjPoint[]): string[] {
  const v = points.filter((p): p is ObjPoint & { map: number; lat: number } => fin(p.map) && fin(p.lat));
  return v.filter((p) => !v.some((q) => q !== p && dominates2d(q, p))).map((p) => p.id);
}

export function front4d(points: readonly ObjPoint[]): string[] {
  const v = points
    .filter((p) => fin(p.map) && fin(p.lat) && fin(p.recall) && fin(p.params))
    .map((p) => ({ id: p.id, map: p.map as number, lat: p.lat as number, recall: p.recall as number, params: p.params as number }));
  return v.filter((p) => !v.some((q) => q !== p && dominates4d(q, p))).map((p) => p.id);
}

/** Front ids ordered by latency ascending (then mAP descending) so they can be joined by a staircase line. */
export function orderFront2d(points: readonly ObjPoint[], ids: readonly string[]): ObjPoint[] {
  const set = new Set(ids);
  return points
    .filter((p) => set.has(p.id) && fin(p.map) && fin(p.lat))
    .sort((a, b) => (a.lat as number) - (b.lat as number) || (b.map as number) - (a.map as number));
}

/** Points whose generation is <= g (the cumulative set shown for slider position g). */
export function upToGeneration<T extends { generation: number }>(rows: readonly T[], g: number): T[] {
  return rows.filter((r) => r.generation <= g);
}

/** Ids on the front now but not before / before but not now. */
export function frontChange(prev: readonly string[], cur: readonly string[]): { added: string[]; removed: string[] } {
  const p = new Set(prev);
  const c = new Set(cur);
  return { added: cur.filter((x) => !p.has(x)), removed: prev.filter((x) => !c.has(x)) };
}
