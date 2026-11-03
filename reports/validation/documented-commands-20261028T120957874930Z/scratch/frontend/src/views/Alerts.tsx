import { useEffect, useMemo, useState } from 'react';
import { BellRing, Check, Clock3, FileSearch, MapPin, RefreshCw } from 'lucide-react';
import type { Alert, AlertSeverity, AlertStatus, Snapshot } from '../types';
import { api, errorMessage } from '../lib/api';
import { formatMeasurement, formatTimestamp, titleCase } from '../lib/format';
import { Button, ChipGroup, EmptyState, ErrorState, IconButton, KeyValue, KeyValueList, LoadingState, Panel } from '../design-system/components';
import { AlertStatusBadge, SeverityBadge } from '../components/status';
import { PageHead } from '../components/Layout';

export function AlertRow({ alert, onAcknowledge, acknowledging }: { alert: Alert; onAcknowledge?: (id: string) => void; acknowledging?: boolean }) {
  const measurements = Object.entries(alert.measurements);
  return (
    <article className="alert-row" data-severity={alert.severity} data-status={alert.status} data-testid="alert-row">
      <div className="alert-row-head">
        <div className="toolbar"><SeverityBadge severity={alert.severity} /><AlertStatusBadge status={alert.status} /><span className="ds-footnote" style={{ margin: 0 }}>{titleCase(alert.category)}</span></div>
        <time className="ds-footnote" style={{ margin: 0 }} dateTime={alert.triggered_at} title={`${formatTimestamp(alert.triggered_at, 'UTC')} UTC`}>
          <Clock3 size={12} aria-hidden="true" style={{ display: 'inline', marginRight: 4 }} />{formatTimestamp(alert.triggered_at)}
        </time>
      </div>
      <h3>{alert.title}</h3>
      <p>{alert.message}</p>
      <div className="alert-row-meta">
        <span><MapPin size={12} aria-hidden="true" style={{ display: 'inline', marginRight: 4 }} />{titleCase(alert.scope_type)} {alert.scope_id ? <code>{alert.scope_id}</code> : null}</span>
        {alert.evidence_id ? (
          <a className="evidence-link" href={`#evidence?item=${encodeURIComponent(alert.evidence_id)}`}><FileSearch size={13} aria-hidden="true" style={{ display: 'inline', marginRight: 4 }} />View evidence</a>
        ) : <span>No evidence attached</span>}
      </div>
      <details>
        <summary>Measurements & provenance ({measurements.length})</summary>
        <KeyValueList>
          {measurements.map(([key, value]) => <KeyValue key={key} label={titleCase(key)}>{formatMeasurement(value)}</KeyValue>)}
          <KeyValue label="Stable key"><code>{alert.stable_key}</code></KeyValue>
          <KeyValue label="Rule revision">{alert.rule_revision ?? 'Not reported'}</KeyValue>
          <KeyValue label="Acknowledged">{alert.acknowledged_at ? formatTimestamp(alert.acknowledged_at) : 'Not acknowledged'}</KeyValue>
        </KeyValueList>
      </details>
      {onAcknowledge && alert.status === 'OPEN' ? (
        <div className="alert-actions">
          <Button size="sm" icon={Check} loading={acknowledging} disabled={acknowledging} onClick={() => onAcknowledge(alert.id)}>
            {acknowledging ? 'Acknowledging…' : 'Acknowledge'}
          </Button>
        </div>
      ) : null}
    </article>
  );
}

export function Alerts({ snapshot, onAcknowledge }: { snapshot: Snapshot; onAcknowledge: (id: string) => Promise<Alert> }) {
  const [status, setStatus] = useState<AlertStatus | 'ALL'>('ALL');
  const [severity, setSeverity] = useState<AlertSeverity | 'ALL'>('ALL');
  const [category, setCategory] = useState('ALL');
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [ackError, setAckError] = useState<string | null>(null);
  const [acknowledging, setAcknowledging] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const liveCount = snapshot.alerts.length;

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    api.alerts(status, severity, 200, controller.signal)
      .then((value) => { setAlerts(value); setError(null); })
      .catch((reason: unknown) => { if (!(reason instanceof DOMException && reason.name === 'AbortError')) setError(errorMessage(reason)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [status, severity, refresh, liveCount]);

  const categories = useMemo(() => [...new Set(alerts.map((alert) => alert.category))].sort(), [alerts]);
  const shown = alerts.filter((alert) => category === 'ALL' || alert.category === category);

  const acknowledge = async (id: string) => {
    setAcknowledging(id);
    setAckError(null);
    try {
      const updated = await onAcknowledge(id);
      setAlerts((current) => current.flatMap((alert) => (alert.id !== id ? [alert] : status !== 'ALL' && updated.status !== status ? [] : [updated])));
    } catch (reason) {
      setAckError(errorMessage(reason));
    } finally {
      setAcknowledging(null);
    }
  };

  const open = snapshot.alerts.filter((alert) => alert.status === 'OPEN').length;
  return (
    <>
      <PageHead title="Alerts" description="Operational signals with their measurements, provenance, and linked local evidence. Acknowledging records the operator action; it does not resolve the condition." />
      <Panel variant="tight">
        <div className="toolbar">
          <span className="toolbar-label">Status</span>
          <ChipGroup<AlertStatus | 'ALL'> label="Filter by status" value={status} onChange={setStatus}
            options={[{ value: 'ALL', label: 'All' }, { value: 'OPEN', label: 'Open', count: open }, { value: 'ACKNOWLEDGED', label: 'Acknowledged' }, { value: 'RESOLVED', label: 'Resolved' }]} />
          <span className="toolbar-label">Severity</span>
          <ChipGroup<AlertSeverity | 'ALL'> label="Filter by severity" value={severity} onChange={setSeverity}
            options={[{ value: 'ALL', label: 'All' }, { value: 'CRITICAL', label: 'Critical' }, { value: 'WARNING', label: 'Warning' }, { value: 'INFO', label: 'Info' }]} />
          {categories.length > 1 ? (
            <>
              <span className="toolbar-label">Category</span>
              <ChipGroup label="Filter by category" value={category} onChange={setCategory}
                options={[{ value: 'ALL', label: 'All' }, ...categories.map((value) => ({ value, label: titleCase(value) }))]} />
            </>
          ) : null}
          <IconButton label="Refresh alerts" icon={RefreshCw} spinning={loading} disabled={loading} onClick={() => setRefresh((value) => value + 1)} />
          <span className="ds-footnote" role="status" style={{ margin: 0 }}>{loading ? 'Querying…' : `${shown.length} alert${shown.length === 1 ? '' : 's'}`}</span>
        </div>
      </Panel>
      {ackError ? <ErrorState title="Acknowledgement failed" message={ackError} /> : null}
      {loading && alerts.length === 0 ? <LoadingState rows={5} /> : null}
      {error ? <ErrorState title="Alerts could not be loaded" message={error} action={<Button size="sm" onClick={() => setRefresh((value) => value + 1)}>Retry</Button>} /> : null}
      {!loading && !error && shown.length === 0 ? (
        <EmptyState icon={BellRing} title={status !== 'ALL' || severity !== 'ALL' || category !== 'ALL' ? 'No alerts match these filters' : 'No alerts reported'}
          description="The local API returned no matching alerts. The browser never generates warnings." />
      ) : null}
      {shown.length ? <div className="alert-list">{shown.map((alert) => <AlertRow key={alert.id} alert={alert} onAcknowledge={acknowledge} acknowledging={acknowledging === alert.id} />)}</div> : null}
    </>
  );
}
