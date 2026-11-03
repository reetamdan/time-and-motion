import { useEffect, useState } from 'react';
import { BellRing, CheckCheck, Filter, RefreshCw, ShieldAlert } from 'lucide-react';
import type { Alert, AlertSeverity, AlertStatus, Snapshot } from '../types';
import { api, errorMessage } from '../lib/api';
import { AlertCard } from '../components/Alert';
import { PageHeader } from '../components/PageHeader';
import { Badge, EmptyState, ErrorNotice, LoadingBlock } from '../components/ui';

export function Alerts({ snapshot, onAcknowledge }: {
  snapshot: Snapshot | null;
  onAcknowledge: (id: string) => Promise<Alert>;
}) {
  const [status, setStatus] = useState<AlertStatus | 'ALL'>('ALL');
  const [severity, setSeverity] = useState<AlertSeverity | 'ALL'>('ALL');
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [ackError, setAckError] = useState<string | null>(null);
  const [acknowledgingId, setAcknowledgingId] = useState<string | null>(null);
  const [refreshIndex, setRefreshIndex] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    api.alerts(status, severity, 100, controller.signal)
      .then((response) => setAlerts(response))
      .catch((requestError: unknown) => {
        if (requestError instanceof DOMException && requestError.name === 'AbortError') return;
        setError(errorMessage(requestError));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [status, severity, refreshIndex]);

  const acknowledge = async (id: string) => {
    setAcknowledgingId(id);
    setAckError(null);
    try {
      const updated = await onAcknowledge(id);
      setAlerts((current) => current.flatMap((alert) => {
        if (alert.id !== id) return [alert];
        if (status !== 'ALL' && updated.status !== status) return [];
        return [updated];
      }));
    } catch (requestError) {
      setAckError(errorMessage(requestError));
    } finally {
      setAcknowledgingId(null);
    }
  };

  const openCount = snapshot?.alerts.filter((alert) => alert.status === 'OPEN').length;
  const acknowledgedCount = snapshot?.alerts.filter((alert) => alert.status === 'ACKNOWLEDGED').length;
  const filtersActive = status !== 'ALL' || severity !== 'ALL';

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow="OPERATIONS / ALERTS"
        title="Alert center"
        description="Operational signals with their actual measurements, provenance, and local evidence."
        actions={<div className="heading-badges"><Badge tone={openCount ? 'warning' : 'muted'} icon={ShieldAlert}>{openCount === undefined ? 'Open count unavailable' : `${openCount} open`}</Badge><Badge tone="info" icon={CheckCheck}>{acknowledgedCount === undefined ? 'Acknowledged count unavailable' : `${acknowledgedCount} acknowledged`}</Badge></div>}
      />

      <div className="filter-bar alert-filter-bar">
        <span className="filter-label"><Filter size={14} aria-hidden="true" />Alert filters</span>
        <label><span>Status</span><select value={status} onChange={(event) => setStatus(event.target.value as AlertStatus | 'ALL')}><option value="ALL">All statuses</option><option value="OPEN">Open</option><option value="ACKNOWLEDGED">Acknowledged</option><option value="RESOLVED">Resolved</option></select></label>
        <label><span>Severity</span><select value={severity} onChange={(event) => setSeverity(event.target.value as AlertSeverity | 'ALL')}><option value="ALL">All severities</option><option value="INFO">Info</option><option value="WARNING">Warning</option><option value="CRITICAL">Critical</option></select></label>
        <span className="filter-result" role="status">{loading ? 'Querying local alerts…' : `${alerts.length} alert${alerts.length === 1 ? '' : 's'}`}</span>
        <button type="button" className="icon-button filter-refresh" onClick={() => setRefreshIndex((value) => value + 1)} disabled={loading} aria-label="Refresh alerts" title="Refresh alerts"><RefreshCw size={15} className={loading ? 'spin' : ''} /></button>
      </div>

      {ackError ? <ErrorNotice title="Alert acknowledgement failed" message={ackError} /> : null}
      {loading ? <div className="alerts-loading"><LoadingBlock rows={5} /></div> : null}
      {!loading && error ? <EmptyState icon={BellRing} title="Alerts could not be loaded" description={error} action={<button type="button" className="button button-secondary button-small" onClick={() => setRefreshIndex((value) => value + 1)}>Retry alerts</button>} /> : null}
      {!loading && !error && alerts.length === 0 ? (
        <EmptyState
          icon={BellRing}
          title={filtersActive ? 'No alerts match these filters' : 'No alerts reported'}
          description={filtersActive ? 'Clear the status and severity filters to return to the complete local alert list.' : 'The local API returned an empty alert list. No synthetic warnings are generated in the browser.'}
          action={filtersActive ? <button type="button" className="button button-secondary button-small" onClick={() => { setStatus('ALL'); setSeverity('ALL'); }}>Clear filters</button> : undefined}
        />
      ) : null}
      {!loading && !error && alerts.length > 0 ? (
        <div className="alert-list alert-list-full">
          {alerts.map((alert) => <AlertCard key={alert.id} alert={alert} onAcknowledge={acknowledge} acknowledging={acknowledgingId === alert.id} />)}
        </div>
      ) : null}
    </div>
  );
}
