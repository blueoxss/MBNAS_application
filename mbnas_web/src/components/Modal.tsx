/**
 * Accessible modal dialog: role=dialog + aria-modal, labelled, focus moves into the dialog, Tab is trapped,
 * ESC closes, focus returns to the element that opened it, the rest of the app is inert while open.
 */
import { useEffect, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

/** Open modals, innermost last: with a modal opened from inside another one (candidate dialog -> image lightbox) only the innermost reacts to Esc/Tab, and the page stays inert until the last one closes. */
const openModals: HTMLElement[] = [];
let inertHolders = 0;

function focusables(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => el.closest('[hidden],[inert]') === null && !(el as HTMLInputElement).disabled);
}

export function Modal({ titleId, onClose, children }: { titleId: string; onClose: () => void; children: ReactNode }): JSX.Element {
  const ref = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const rootEl = document.getElementById('root');
    inertHolders += 1;
    rootEl?.setAttribute('inert', '');
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const dlg = ref.current;
    if (dlg) openModals.push(dlg);
    dlg?.querySelector<HTMLElement>('[data-autofocus]')?.focus();
    if (dlg && !dlg.contains(document.activeElement)) dlg.focus();

    const onKey = (e: KeyboardEvent): void => {
      if (dlg && openModals[openModals.length - 1] !== dlg) return; // an inner modal owns the keyboard
      if (e.key === 'Escape') {
        e.stopPropagation();
        closeRef.current();
        return;
      }
      if (e.key !== 'Tab' || !dlg) return;
      const items = focusables(dlg);
      if (items.length === 0) {
        e.preventDefault();
        dlg.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || active === dlg)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      const at = dlg ? openModals.lastIndexOf(dlg) : -1;
      if (at >= 0) openModals.splice(at, 1);
      inertHolders -= 1;
      if (inertHolders <= 0) {
        inertHolders = 0;
        rootEl?.removeAttribute('inert');
      }
      document.body.style.overflow = prevOverflow;
      if (opener && document.contains(opener)) opener.focus();
    };
  }, []);

  return createPortal(
    <div className="backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()} data-testid="modal-backdrop">
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1} ref={ref}>
        {children}
      </div>
    </div>,
    document.body,
  );
}
