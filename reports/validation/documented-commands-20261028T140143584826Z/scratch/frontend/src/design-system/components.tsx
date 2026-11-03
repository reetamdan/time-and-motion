/**
 * Typed shared components for Aegis Ops (styles in ./components.css).
 *
 * Interaction patterns adapted from OpenIntelligentUI @ 8391a94c (MIT, Copyright (c) Atai Barkai):
 * the Drawer follows components/demo-gallery/index.tsx (Escape closes, scrim click closes,
 * bottom sheet on phones / right panel on wider screens) with an added focus trap and focus
 * return; ChipGroup follows components/demo-gallery/category-filter.tsx (single-select pill row,
 * gradient active state) with aria-pressed. Everything else is Aegis code.
 */
import { useEffect, useId, useRef } from 'react';
import type { ButtonHTMLAttributes, CSSProperties, ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { AlertTriangle, Inbox, Info, LoaderCircle, X } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

export function cx(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(' ');
}

export const UNAVAILABLE_MARK = '—';

/* ── Buttons ──────────────────────────────────────────────────────────── */

type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'ghost';

export function Button({ variant = 'secondary', size = 'md', icon: Icon, loading = false, children, className, ...props }: {
  variant?: ButtonVariant;
  size?: 'sm' | 'md';
  icon?: LucideIcon;
  loading?: boolean;
  children?: ReactNode;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button type="button" className={cx('ds-button', variant !== 'secondary' && `ds-button-${variant}`, size === 'sm' && 'ds-button-sm', className)}
      aria-busy={loading || undefined} {...props}>
      {loading ? <LoaderCircle size={15} className="spin" aria-hidden="true" /> : Icon ? <Icon size={15} aria-hidden="true" /> : null}
      {children}
    </button>
  );
}

export function IconButton({ label, icon: Icon, spinning = false, className, ...props }: {
  label: string;
  icon: LucideIcon;
  spinning?: boolean;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button type="button" className={cx('ds-icon-button', className)} aria-label={label} title={label} {...props}>
      <Icon size={16} aria-hidden="true" className={spinning ? 'spin' : undefined} />
    </button>
  );
}

/* ── Surfaces ─────────────────────────────────────────────────────────── */

export function Panel({ title, eyebrow, description, actions, children, className, variant, headingLevel = 2, id, testId }: {
  title?: ReactNode;
  eyebrow?: string;
  description?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
  variant?: 'tight' | 'flush';
  headingLevel?: 2 | 3;
  id?: string;
  testId?: string;
}) {
  const headingId = useId();
  const Heading = headingLevel === 2 ? 'h2' : 'h3';
  return (
    <section className={cx('ds-panel', variant && `ds-panel-${variant}`, className)} aria-labelledby={title ? headingId : undefined} id={id} data-testid={testId}>
      {title || eyebrow || actions ? (
        <header className="ds-panel-header">
          <div>
            {eyebrow ? <p className="ds-eyebrow">{eyebrow}</p> : null}
            {title ? <Heading id={headingId}>{title}</Heading> : null}
            {description ? <p>{description}</p> : null}
          </div>
          {actions ? <div className="ds-panel-actions">{actions}</div> : null}
        </header>
      ) : null}
      {children}
    </section>
  );
}

/* ── Badges, chips, segmented ─────────────────────────────────────────── */

export type Tone = 'neutral' | 'muted' | 'good' | 'info' | 'warning' | 'danger' | 'accent';

export function Badge({ tone = 'neutral', icon: Icon, dot = false, live = false, children, className, title }: {
  tone?: Tone;
  icon?: LucideIcon;
  dot?: boolean;
  live?: boolean;
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span className={cx('ds-badge', `ds-badge-${tone}`, className)} title={title}>
      {dot ? <span className={cx('ds-dot', live && 'ds-dot-live')} aria-hidden="true" /> : Icon ? <Icon size={12} aria-hidden="true" /> : null}
      <span>{children}</span>
    </span>
  );
}

export function ChipGroup<T extends string>({ label, value, options, onChange }: {
  label: string;
  value: T;
  options: Array<{ value: T; label: string; count?: number }>;
  onChange: (value: T) => void;
}) {
  return (
    <div className="ds-chip-row" role="group" aria-label={label}>
      {options.map((option) => (
        <button key={option.value} type="button" className="ds-chip" aria-pressed={option.value === value} onClick={() => onChange(option.value)}>
          {option.label}
          {option.count !== undefined ? <span className="ds-chip-count">{option.count}</span> : null}
        </button>
      ))}
    </div>
  );
}

export function Segmented<T extends string>({ label, value, options, onChange, disabled = false }: {
  label: string;
  value: T;
  options: Array<{ value: T; label: string; title?: string }>;
  onChange: (value: T) => void;
  disabled?: boolean;
}) {
  return (
    <div className="ds-segmented" role="group" aria-label={label}>
      {options.map((option) => (
        <button key={option.value} type="button" aria-pressed={option.value === value} disabled={disabled}
          title={option.title} onClick={() => onChange(option.value)}>{option.label}</button>
      ))}
    </div>
  );
}

/* ── Callouts ─────────────────────────────────────────────────────────── */

export function Callout({ tone = 'info', icon: Icon = tone === 'info' ? Info : AlertTriangle, title, children, actions, role, testId }: {
  tone?: 'info' | 'warning' | 'danger';
  icon?: LucideIcon;
  title: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  role?: 'alert' | 'status';
  testId?: string;
}) {
  return (
    <div className={cx('ds-callout', `ds-callout-${tone}`)} role={role} data-testid={testId}>
      <Icon size={17} aria-hidden="true" />
      <div style={{ flex: 1, minWidth: 0 }}><strong>{title}</strong>{children ? <div>{children}</div> : null}</div>
      {actions ? <div className="ds-panel-actions">{actions}</div> : null}
    </div>
  );
}

/* ── Values that may be unavailable ───────────────────────────────────── */

/** Renders a measurement, or an explicit "unavailable" mark carrying its reason — never a zero. */
export function DataValue({ value, formatter, reason, className }: {
  value: number | string | null | undefined;
  formatter?: (value: number | string) => string;
  reason?: string;
  className?: string;
}) {
  const missing = value === null || value === undefined || (typeof value === 'number' && !Number.isFinite(value));
  if (missing) {
    const explanation = reason?.trim() || 'No measurement was reported by the local API.';
    return <span className={cx('ds-unavailable', className)} title={explanation} aria-label={`Unavailable: ${explanation}`}>{UNAVAILABLE_MARK}</span>;
  }
  return <span className={cx('ds-value', className)}>{formatter ? formatter(value) : value}</span>;
}

export function Reason({ children }: { children: ReactNode }) {
  return <p className="ds-reason"><Info size={12} aria-hidden="true" /><span>{children}</span></p>;
}

export function Metric({ label, icon: Icon, value, formatter, unit, reason, sub, testId }: {
  label: string;
  icon?: LucideIcon;
  value: number | string | null | undefined;
  formatter?: (value: number | string) => string;
  unit?: string;
  reason?: string;
  sub?: ReactNode;
  testId?: string;
}) {
  const missing = value === null || value === undefined;
  return (
    <div className="ds-metric" data-testid={testId} data-available={missing ? 'false' : 'true'}>
      <span className="ds-metric-label">{Icon ? <Icon size={13} aria-hidden="true" /> : null}{label}</span>
      <span className="ds-metric-value">
        {missing ? <span className="ds-unavailable" title={reason}>Unavailable</span> : <>{formatter ? formatter(value) : value}{unit ? <span className="ds-metric-unit">{unit}</span> : null}</>}
      </span>
      {missing && reason ? <span className="ds-metric-sub">{reason}</span> : sub ? <span className="ds-metric-sub">{sub}</span> : null}
    </div>
  );
}

export function KeyValue({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return <div title={hint}><dt>{label}</dt><dd>{children}</dd></div>;
}

export function KeyValueList({ children, stack = false, className }: { children: ReactNode; stack?: boolean; className?: string }) {
  return <dl className={cx('ds-kv', stack && 'ds-kv-stack', className)}>{children}</dl>;
}

export function Meter({ value, max = 100, tone }: { value: number | null | undefined; max?: number | null; tone?: 'warning' | 'danger' }) {
  if (value === null || value === undefined || !Number.isFinite(value) || !max || max <= 0) {
    return <div className="ds-meter ds-meter-empty" aria-hidden="true" />;
  }
  const percent = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className={cx('ds-meter', tone && `ds-meter-${tone}`)} role="meter" aria-valuemin={0} aria-valuemax={max} aria-valuenow={value}>
      <span style={{ width: `${percent}%` }} />
    </div>
  );
}

/* ── Empty / loading / error ──────────────────────────────────────────── */

export function EmptyState({ icon: Icon = Inbox, title, description, action, compact = false, testId }: {
  icon?: LucideIcon;
  title: string;
  description: ReactNode;
  action?: ReactNode;
  compact?: boolean;
  testId?: string;
}) {
  return (
    <div className={cx('ds-empty', compact && 'ds-empty-compact')} data-testid={testId}>
      <span className="ds-empty-icon"><Icon size={18} aria-hidden="true" /></span>
      <div>
        <h3>{title}</h3>
        <p>{description}</p>
        {action ? <div className="ds-empty-action">{action}</div> : null}
      </div>
    </div>
  );
}

export function LoadingState({ rows = 3, label = 'Loading local data' }: { rows?: number; label?: string }) {
  return (
    <div className="ds-skeleton" role="status" aria-label={label}>
      {Array.from({ length: rows }, (_, index) => <span key={index} style={{ width: `${92 - index * 11}%` }} />)}
    </div>
  );
}

export function ErrorState({ title = 'Local data unavailable', message, action }: { title?: string; message: string; action?: ReactNode }) {
  return <Callout tone="danger" title={title} role="alert" actions={action}><p>{message}</p></Callout>;
}

/* ── Drawer / dialog ──────────────────────────────────────────────────── */

const FOCUSABLE = 'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), video[controls], [tabindex]:not([tabindex="-1"])';

function useFocusTrap(onClose: () => void) {
  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const panel = panelRef.current;
    (panel?.querySelector<HTMLElement>(FOCUSABLE) ?? panel)?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeRef.current();
        return;
      }
      if (event.key !== 'Tab' || !panel) return;
      const items = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((item) => item.offsetParent !== null);
      if (items.length === 0) { event.preventDefault(); panel.focus(); return; }
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.body.style.overflow = overflow;
      document.removeEventListener('keydown', onKeyDown);
      previous?.focus();
    };
  }, []);
  return panelRef;
}

export function Drawer({ title, subtitle, eyebrow, onClose, children, footer, side = 'right', width, headerExtra, testId }: {
  title: string;
  subtitle?: ReactNode;
  eyebrow?: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  side?: 'right' | 'left';
  width?: number;
  headerExtra?: ReactNode;
  testId?: string;
}) {
  const titleId = useId();
  const panelRef = useFocusTrap(onClose);
  return createPortal(
    <>
      <div className="ds-scrim" aria-hidden="true" onClick={onClose} />
      <div ref={panelRef} className={cx('ds-drawer', side === 'left' && 'ds-drawer-left')} role="dialog" aria-modal="true"
        aria-labelledby={titleId} tabIndex={-1} data-testid={testId}
        style={width ? ({ '--drawer-width': `${width}px` } as CSSProperties) : undefined}>
        <header className="ds-drawer-header">
          <div style={{ minWidth: 0 }}>
            {eyebrow ? <p className="ds-eyebrow">{eyebrow}</p> : null}
            <h2 id={titleId}>{title}</h2>
            {subtitle ? <p>{subtitle}</p> : null}
            {headerExtra}
          </div>
          <IconButton label={`Close ${title}`} icon={X} onClick={onClose} />
        </header>
        <div className="ds-drawer-body">{children}</div>
        {footer ? <footer className="ds-drawer-footer">{footer}</footer> : null}
      </div>
    </>,
    document.body,
  );
}

export function Dialog({ title, subtitle, onClose, children, actions }: {
  title: string;
  subtitle?: string;
  onClose: () => void;
  children: ReactNode;
  actions: ReactNode;
}) {
  const titleId = useId();
  const panelRef = useFocusTrap(onClose);
  return createPortal(
    <div className="ds-dialog-overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div ref={panelRef} className="ds-dialog" role="alertdialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}>
        <header className="ds-drawer-header">
          <div><h2 id={titleId}>{title}</h2>{subtitle ? <p>{subtitle}</p> : null}</div>
          <IconButton label={`Close ${title}`} icon={X} onClick={onClose} />
        </header>
        <div className="ds-drawer-body">{children}</div>
        <div className="ds-dialog-actions">{actions}</div>
      </div>
    </div>,
    document.body,
  );
}
