import type { ReactNode } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  CircleOff,
  Crosshair,
  Eye,
  Info,
  Inbox,
  Sigma,
  XCircle,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { Calibration, Health, Quality } from '../types';
import { reasonOr, titleCase, UNAVAILABLE } from '../lib/format';

export function cx(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(' ');
}

type BadgeTone = 'neutral' | 'good' | 'warning' | 'danger' | 'info' | 'muted';

export function Badge({ children, tone = 'neutral', icon: Icon, className }: {
  children: ReactNode;
  tone?: BadgeTone;
  icon?: LucideIcon;
  className?: string;
}) {
  return (
    <span className={cx('badge', `badge-${tone}`, className)}>
      {Icon ? <Icon size={12} aria-hidden="true" /> : null}
      <span>{children}</span>
    </span>
  );
}

export function HealthBadge({ health, compact = false }: { health: Health; compact?: boolean }) {
  const config = health === 'HEALTHY'
    ? { tone: 'good' as const, icon: CheckCircle2 }
    : health === 'OFFLINE'
      ? { tone: 'danger' as const, icon: XCircle }
      : { tone: 'warning' as const, icon: AlertTriangle };
  return <Badge tone={config.tone} icon={config.icon}>{compact ? titleCase(health) : `Status · ${titleCase(health)}`}</Badge>;
}

export function CalibrationBadge({ status }: { status: Calibration }) {
  return (
    <Badge tone={status === 'VALID' ? 'good' : 'warning'} icon={status === 'VALID' ? Crosshair : AlertTriangle}>
      {status === 'VALID' ? 'Calibrated' : `Calibration · ${titleCase(status)}`}
    </Badge>
  );
}

export function QualityBadge({ quality }: { quality: Quality }) {
  const Icon = quality === 'OBSERVED' ? Eye : quality === 'ESTIMATED' ? Sigma : CircleOff;
  const tone = quality === 'OBSERVED' ? 'good' : quality === 'ESTIMATED' ? 'info' : 'muted';
  return <Badge tone={tone} icon={Icon}>{titleCase(quality)}</Badge>;
}

export function Panel({ title, eyebrow, description, action, children, className, headingId }: {
  title?: string;
  eyebrow?: string;
  description?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  headingId?: string;
}) {
  return (
    <section className={cx('panel', className)} aria-labelledby={headingId}>
      {(title || eyebrow || action) ? (
        <header className="panel-header">
          <div>
            {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
            {title ? <h2 id={headingId} className="panel-title">{title}</h2> : null}
            {description ? <p className="panel-description">{description}</p> : null}
          </div>
          {action ? <div className="panel-action">{action}</div> : null}
        </header>
      ) : null}
      {children}
    </section>
  );
}

export function EmptyState({ icon: Icon = Inbox, title, description, action, compact = false }: {
  icon?: LucideIcon;
  title: string;
  description: string;
  action?: ReactNode;
  compact?: boolean;
}) {
  return (
    <div className={cx('empty-state', compact && 'empty-state-compact')}>
      <span className="empty-icon"><Icon size={22} aria-hidden="true" /></span>
      <div>
        <h3>{title}</h3>
        <p>{description}</p>
        {action ? <div className="empty-action">{action}</div> : null}
      </div>
    </div>
  );
}

export function DataValue({ value, formatter, reason, className }: {
  value: number | string | null | undefined;
  formatter?: (value: number | string) => string;
  reason?: string;
  className?: string;
}) {
  const missing = value === null || value === undefined || (typeof value === 'number' && !Number.isFinite(value));
  if (missing) {
    const explanation = reasonOr(reason);
    return (
      <span className={cx('data-value', 'data-unavailable', className)} title={explanation} aria-label={`Unavailable: ${explanation}`}>
        {UNAVAILABLE}
      </span>
    );
  }
  return <span className={cx('data-value', className)}>{formatter ? formatter(value) : value}</span>;
}

export function Reason({ children, show = true }: { children?: ReactNode; show?: boolean }) {
  if (!show) return null;
  return <p className="reason"><Info size={12} aria-hidden="true" /><span>{children}</span></p>;
}

export function KeyValue({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <div className="key-value" title={hint}>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

export function Meter({ value, max = 100, tone = 'info' }: { value: number | null; max?: number; tone?: 'info' | 'warning' | 'danger' }) {
  if (value === null || !Number.isFinite(value) || max <= 0) return <div className="meter meter-unavailable" aria-hidden="true" />;
  const percentage = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className={cx('meter', `meter-${tone}`)} role="meter" aria-valuemin={0} aria-valuemax={max} aria-valuenow={value}>
      <span style={{ width: `${percentage}%` }} />
    </div>
  );
}

export function ErrorNotice({ title = 'Local data unavailable', message }: { title?: string; message: string }) {
  return (
    <div className="error-notice" role="alert">
      <AlertTriangle size={17} aria-hidden="true" />
      <div><strong>{title}</strong><p>{message}</p></div>
    </div>
  );
}

export function LoadingBlock({ rows = 3 }: { rows?: number }) {
  return (
    <div className="loading-block" aria-label="Loading local data" role="status">
      {Array.from({ length: rows }, (_, index) => <span key={index} style={{ width: `${92 - index * 9}%` }} />)}
    </div>
  );
}

export function PendingBadge() {
  return <Badge tone="info" icon={CircleDashed}>Updating</Badge>;
}
