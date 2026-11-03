import { useEffect, useId, useRef } from 'react';
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { cx } from './ui';

const FOCUSABLE = 'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function Modal({ title, subtitle, onClose, children, footer, variant = 'modal' }: {
  title: string;
  subtitle?: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  variant?: 'modal' | 'drawer';
}) {
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const panel = panelRef.current;
    const focusable = panel?.querySelectorAll<HTMLElement>(FOCUSABLE);
    (focusable?.[0] ?? panel)?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== 'Tab' || !panel) return;
      const elements = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (elements.length === 0) {
        event.preventDefault();
        panel.focus();
        return;
      }
      const first = elements[0];
      const last = elements[elements.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener('keydown', onKeyDown);
      previousFocus?.focus();
    };
  }, []);

  return createPortal(
    <div className={cx('modal-overlay', variant === 'drawer' && 'drawer-overlay')} onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <div
        ref={panelRef}
        className={cx('modal-panel', variant === 'drawer' && 'drawer-panel')}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <header className="modal-header">
          <div>
            <p className="eyebrow">Aegis Ops · Local record</p>
            <h2 id={titleId}>{title}</h2>
            {subtitle ? <p>{subtitle}</p> : null}
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label={`Close ${title}`}>
            <X size={19} aria-hidden="true" />
          </button>
        </header>
        <div className="modal-body">{children}</div>
        {footer ? <footer className="modal-footer">{footer}</footer> : null}
      </div>
    </div>,
    document.body,
  );
}
