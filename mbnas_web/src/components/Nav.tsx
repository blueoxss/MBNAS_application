/**
 * Sidebar navigation. On narrow screens it is a disclosure: a menu button (aria-expanded/aria-controls)
 * toggles the list, and the closed list is inert so it cannot take focus.
 */
import { useEffect, useRef, useState } from 'react';
import type { Screen } from '../state/context';

export const NAV_ITEMS: { id: Screen; label: string }[] = [
  { id: 'archive', label: 'NAS 아카이브' },
  { id: 'compare', label: 'Archive run 비교' },
  { id: 'monitor', label: '탐색 모니터링' },
  { id: 'settings', label: '실험 설정' },
  { id: 'lineage', label: '모델 계보' },
  { id: 'history', label: '실행 기록' },
  { id: 'server', label: '서버 연결·실행 검토' },
];

export function useIsMobile(): boolean {
  const q = '(max-width: 767px)';
  const [m, setM] = useState(() => (typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(q).matches : false));
  useEffect(() => {
    if (!window.matchMedia) return;
    const mq = window.matchMedia(q);
    const on = (): void => setM(mq.matches);
    on();
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, []);
  return m;
}

export function Nav({ screen, onSelect }: { screen: Screen; onSelect: (s: Screen) => void }): JSX.Element {
  const mobile = useIsMobile();
  const [open, setOpen] = useState(false);
  const closed = mobile && !open;
  const menuBtn = useRef<HTMLButtonElement>(null);
  // Escape closes the open disclosure and returns focus to its button (keyboard users must not be stranded)
  useEffect(() => {
    if (!mobile || !open) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key !== 'Escape') return;
      setOpen(false);
      menuBtn.current?.focus();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [mobile, open]);
  const inertProps = closed ? ({ inert: '' } as Record<string, string>) : {};
  return (
    <>
      <div className="topbar">
        <div className="brand">
          MBNAS Console
        </div>
        <button ref={menuBtn} className="menu-btn" type="button" aria-expanded={open} aria-controls="main-nav" onClick={() => setOpen((o) => !o)} data-testid="menu-btn">
          {open ? '메뉴 닫기' : '메뉴'}
        </button>
      </div>
      <aside className="sidebar">
        <div className="brand">
          MBNAS Console
          <small>research console</small>
        </div>
        <nav id="main-nav" className="nav" aria-label="Main" data-open={mobile ? String(open) : 'true'} {...inertProps}>
          {NAV_ITEMS.map((n) => (
            <button
              key={n.id}
              type="button"
              aria-current={screen === n.id ? 'page' : undefined}
              onClick={() => {
                onSelect(n.id);
                setOpen(false);
              }}
              data-testid={`nav-${n.id}`}
            >
              {n.label}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">Values on live screens come only from the server.</div>
      </aside>
    </>
  );
}
