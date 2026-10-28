/**
 * Sortable data table. Used as the keyboard/screen-reader equivalent of the charts and for all
 * candidate/event/worker lists. Sorting is exposed with aria-sort on the header cell.
 */
import { useMemo, useState, type ReactNode } from 'react';

export interface Column<T> {
  key: string;
  header: ReactNode;
  cell: (row: T) => ReactNode;
  /** value used for sorting; omit for a non-sortable column */
  sort?: (row: T) => string | number | null | undefined;
  num?: boolean;
}

interface Props<T> {
  caption: string;
  columns: Column<T>[];
  rows: readonly T[];
  rowKey: (row: T) => string;
  empty?: string;
  maxHeight?: boolean;
  selectedKey?: string | null;
  testId?: string;
  initialSort?: { key: string; dir: 'asc' | 'desc' };
}

export function DataTable<T>({ caption, columns, rows, rowKey, empty = 'No rows.', maxHeight, selectedKey, testId, initialSort }: Props<T>): JSX.Element {
  const [sort, setSort] = useState<{ key: string; dir: 'asc' | 'desc' } | null>(initialSort ?? null);
  const sorted = useMemo(() => {
    const col = columns.find((c) => c.key === sort?.key);
    if (!sort || !col?.sort) return [...rows];
    const f = col.sort;
    const dir = sort.dir === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
      const va = f(a);
      const vb = f(b);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * dir;
      return String(va).localeCompare(String(vb), undefined, { numeric: true }) * dir;
    });
  }, [rows, columns, sort]);

  return (
    <div className={`table-wrap${maxHeight ? ' table-scroll' : ''}`} data-testid={testId} role="region" aria-label={caption} tabIndex={0}>
      <table className="data">
        <caption>{caption}</caption>
        <thead>
          <tr>
            {columns.map((c) => {
              const active = sort?.key === c.key;
              return (
                <th key={c.key} scope="col" className={c.num ? 'num' : undefined} aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : c.sort ? 'none' : undefined}>
                  {c.sort ? (
                    <button type="button" onClick={() => setSort(active && sort.dir === 'asc' ? { key: c.key, dir: 'desc' } : { key: c.key, dir: 'asc' })}>
                      {c.header}
                      <span aria-hidden="true">{active ? (sort.dir === 'asc' ? '▲' : '▼') : '⇅'}</span>
                    </button>
                  ) : (
                    c.header
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.length === 0 && (
            <tr>
              <td colSpan={columns.length} className="muted">
                {empty}
              </td>
            </tr>
          )}
          {sorted.map((r) => {
            const k = rowKey(r);
            return (
              <tr key={k} className={selectedKey === k ? 'sel' : undefined}>
                {columns.map((c) => (
                  <td key={c.key} className={c.num ? 'num' : undefined}>
                    {c.cell(r)}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
