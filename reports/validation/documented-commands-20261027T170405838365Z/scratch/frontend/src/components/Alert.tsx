import {
  AlertTriangle,
  BellRing,
  Check,
  CheckCheck,
  Clock3,
  FileSearch,
  Info,
  LoaderCircle,
  MapPin,
} from 'lucide-react';
import type { Alert, AlertSeverity, AlertStatus } from '../types';
import { api } from '../lib/api';
import { formatMeasurement, formatTimestamp, titleCase } from '../lib/format';
import { Badge, DataValue, KeyValue } from './ui';

export function SeverityBadge({ severity }: { severity: AlertSeverity }) {
  const Icon = severity === 'INFO' ? Info : AlertTriangle;
  return <Badge tone={severity === 'INFO' ? 'info' : severity === 'WARNING' ? 'warning' : 'danger'} icon={Icon}>{titleCase(severity)}</Badge>;
}

export function AlertStatusBadge({ status }: { status: AlertStatus }) {
  const Icon = status === 'OPEN' ? BellRing : status === 'ACKNOWLEDGED' ? CheckCheck : Check;
  return <Badge tone={status === 'OPEN' ? 'warning' : status === 'ACKNOWLEDGED' ? 'info' : 'good'} icon={Icon}>{titleCase(status)}</Badge>;
}

export function AlertCard({ alert, onAcknowledge, acknowledging = false, compact = false }: {
  alert: Alert;
  onAcknowledge?: (id: string) => void;
  acknowledging?: boolean;
  compact?: boolean;
}) {
  const measurements = Object.entries(alert.measurements);
  return (
    <article className={`alert-card alert-${alert.severity.toLowerCase()} ${compact ? 'alert-card-compact' : ''}`}>
      <div className="alert-card-topline">
        <div className="alert-badges"><SeverityBadge severity={alert.severity} /><AlertStatusBadge status={alert.status} /></div>
        <time dateTime={alert.triggered_at} title={formatTimestamp(alert.triggered_at, 'UTC')}>
          <Clock3 size={12} aria-hidden="true" />{formatTimestamp(alert.triggered_at)}
        </time>
      </div>
      <div className="alert-card-copy">
        <p className="eyebrow">{titleCase(alert.category)}</p>
        <h3>{alert.title}</h3>
        <p>{alert.message}</p>
      </div>
      <div className="alert-scope"><MapPin size={12} aria-hidden="true" /><span>{titleCase(alert.scope_type)}</span><code><DataValue value={alert.scope_id} reason="This alert has no narrower scope identifier." /></code></div>

      {!compact ? (
        <details className="alert-details">
          <summary>Measurements & evidence <span>{measurements.length} measurement{measurements.length === 1 ? '' : 's'}</span></summary>
          <div className="alert-details-body">
            {measurements.length > 0 ? (
              <dl className="key-value-grid">
                {measurements.map(([key, value]) => <KeyValue key={key} label={titleCase(key)}>{formatMeasurement(value)}</KeyValue>)}
              </dl>
            ) : <p className="muted-copy">No measurements were attached to this alert.</p>}
            <dl className="key-value-grid alert-provenance">
              <KeyValue label="Stable key"><code>{alert.stable_key}</code></KeyValue>
              <KeyValue label="Rule revision"><DataValue value={alert.rule_revision} reason="No alert rule revision was reported." /></KeyValue>
              <KeyValue label="Acknowledged at"><DataValue value={alert.acknowledged_at} formatter={(value) => formatTimestamp(String(value))} reason="This alert has not been acknowledged." /></KeyValue>
            </dl>
            {alert.evidence_id ? (
              <a className="evidence-link" href={api.evidenceUrl(alert.evidence_id)} target="_blank" rel="noreferrer">
                <FileSearch size={15} aria-hidden="true" />Open local evidence <code>{alert.evidence_id}</code>
              </a>
            ) : <p className="muted-copy">No evidence record was attached. Nothing is inferred in its place.</p>}
          </div>
        </details>
      ) : null}

      {onAcknowledge && alert.status === 'OPEN' ? (
        <div className="alert-actions">
          <button type="button" className="button button-secondary button-small" onClick={() => onAcknowledge(alert.id)} disabled={acknowledging}>
            {acknowledging ? <LoaderCircle size={14} className="spin" aria-hidden="true" /> : <Check size={14} aria-hidden="true" />}
            {acknowledging ? 'Acknowledging…' : 'Acknowledge alert'}
          </button>
        </div>
      ) : null}
    </article>
  );
}
