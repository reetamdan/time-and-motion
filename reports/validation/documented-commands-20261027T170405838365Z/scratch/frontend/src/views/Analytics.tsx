import { useEffect, useMemo, useState } from 'react';
import {
  ArrowDownLeft,
  ArrowUpRight,
  BarChart3,
  CloudOff,
  Database,
  Equal,
  GitCompareArrows,
  History,
  Link2,
  Minus,
  RefreshCw,
} from 'lucide-react';
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { AssociationRecord, HistoryPoint, HistoryResponse, Snapshot } from '../types';
import { api, errorMessage } from '../lib/api';
import {
  formatChartTime,
  formatGap,
  formatInteger,
  formatNumber,
  formatTimestamp,
  formatUnknown,
  UNAVAILABLE,
} from '../lib/format';
import { PageHeader } from '../components/PageHeader';
import { Badge, DataValue, EmptyState, ErrorNotice, KeyValue, LoadingBlock, Panel, QualityBadge, Reason } from '../components/ui';

const METRICS = [
  { value: 'occupancy', label: 'Building occupancy' },
  { value: 'entries', label: 'Entries (IN)' },
  { value: 'exits', label: 'Exits (OUT)' },
  { value: 'queue', label: 'Queue total' },
];

const RANGES = [
  { value: '1h', label: 'Last hour', hours: 1 },
  { value: '6h', label: 'Last 6 hours', hours: 6 },
  { value: '24h', label: 'Last 24 hours', hours: 24 },
  { value: '7d', label: 'Last 7 days', hours: 168 },
];

interface ChartPoint extends HistoryPoint {
  time: number;
}

function AssociationValue({ value, code = false }: { value: unknown; code?: boolean }) {
  const formatted = formatUnknown(value);
  if (formatted === 'UNKNOWN') return <span className="unknown-value">UNKNOWN</span>;
  return code ? <code>{formatted}</code> : <span>{formatted}</span>;
}

function associationTime(record: AssociationRecord): string | null {
  const value = record.timestamp ?? record.occurred_at ?? record.created_at;
  return typeof value === 'string' ? value : null;
}

function Associations({ records, error }: { records: AssociationRecord[]; error?: string }) {
  return (
    <Panel title="Association ledger" eyebrow="CROSS-CAMERA RECORDS" action={<Badge tone="neutral" icon={Link2}>{records.length} records</Badge>}>
      {error ? <ErrorNotice title="Associations unavailable" message={error} /> : null}
      {!error && records.length === 0 ? (
        <EmptyState icon={GitCompareArrows} title="No associations observed" description="The ledger is empty until real cross-camera observations exist. UNKNOWN is never replaced with a fabricated identity." compact />
      ) : null}
      {records.length > 0 ? (
        <div className="table-scroll">
          <table className="data-table association-table">
            <thead><tr><th>Association</th><th>From</th><th>To</th><th>Observed</th><th>Score</th><th>Status</th><th>Payload</th></tr></thead>
            <tbody>
              {records.map((record, index) => {
                const id = record.id ?? record.association_id ?? record.track_id ?? record.person_id;
                const from = record.from_camera_id ?? record.source_camera_id ?? record.from_zone_id ?? record.from;
                const to = record.to_camera_id ?? record.target_camera_id ?? record.to_zone_id ?? record.to;
                const timestamp = associationTime(record);
                const formattedTime = timestamp ? formatTimestamp(timestamp) : 'UNKNOWN';
                return (
                  <tr key={typeof id === 'string' ? id : `association-${index}`}>
                    <td><AssociationValue value={id} code /></td>
                    <td><AssociationValue value={from} code /></td>
                    <td><AssociationValue value={to} code /></td>
                    <td>{formattedTime === UNAVAILABLE ? <span>{timestamp}</span> : <AssociationValue value={formattedTime} />}</td>
                    <td><AssociationValue value={record.confidence ?? record.score} /></td>
                    <td><AssociationValue value={record.status ?? record.quality} /></td>
                    <td><details className="record-details"><summary>Inspect</summary><pre>{JSON.stringify(record, null, 2)}</pre></details></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
      <p className="table-note">Scores are displayed in the raw scale supplied by the API. Missing fields remain UNKNOWN.</p>
    </Panel>
  );
}

function ConservationSummary({ snapshot }: { snapshot: Snapshot | null }) {
  const kpis = snapshot?.kpis;
  const netFlow = kpis?.entries !== null && kpis?.entries !== undefined && kpis.exits !== null && kpis.exits !== undefined
    ? kpis.entries - kpis.exits
    : null;
  const reason = 'Net flow requires both IN and OUT measurements from the local API.';
  return (
    <Panel title="Conservation summary" eyebrow="CURRENT RUN" action={kpis ? <QualityBadge quality={kpis.quality} /> : undefined}>
      {kpis ? (
        <>
          <div className="conservation-flow" aria-label="Net entrance flow calculation">
            <div><span><ArrowUpRight size={13} />IN</span><strong><DataValue value={kpis.entries} formatter={(value) => formatInteger(Number(value))} reason={reason} /></strong></div>
            <Minus size={17} aria-hidden="true" />
            <div><span><ArrowDownLeft size={13} />OUT</span><strong><DataValue value={kpis.exits} formatter={(value) => formatInteger(Number(value))} reason={reason} /></strong></div>
            <Equal size={17} aria-hidden="true" />
            <div className="conservation-net"><span>NET FLOW</span><strong><DataValue value={netFlow} formatter={(value) => formatInteger(Number(value))} reason={reason} /></strong></div>
          </div>
          {netFlow === null ? <Reason>{reason}</Reason> : null}
          <dl className="key-value-grid conservation-values">
            <KeyValue label="Expected occupancy"><DataValue value={kpis.building_expected} formatter={(value) => formatInteger(Number(value))} reason="No expected building occupancy was reported." /></KeyValue>
            <KeyValue label="Visible occupancy"><DataValue value={kpis.building_visible} formatter={(value) => formatInteger(Number(value))} reason="No visible building occupancy was reported." /></KeyValue>
            <KeyValue label="Estimated occupancy"><DataValue value={kpis.building_estimated} formatter={(value) => formatInteger(Number(value))} reason="No estimated building occupancy was reported." /></KeyValue>
            <KeyValue label="Unresolved transitions"><DataValue value={kpis.unresolved_transitions} formatter={(value) => formatInteger(Number(value))} reason="No unresolved transition count was reported." /></KeyValue>
            <KeyValue label="Reported discrepancy"><DataValue value={kpis.discrepancy} formatter={(value) => formatNumber(Number(value))} reason="No conservation discrepancy was reported." /></KeyValue>
            <KeyValue label="Run"><DataValue value={snapshot?.run_id} reason="No active run identifier was reported." /></KeyValue>
          </dl>
          <p className="panel-footnote">Net flow is only IN − OUT. The discrepancy remains the backend-reported value; the browser does not force reconciliation.</p>
        </>
      ) : <EmptyState icon={Database} title="No current-run KPI snapshot" description="Conservation values become available when the facility snapshot endpoint responds." compact />}
    </Panel>
  );
}

export function Analytics({ snapshot, associations, associationsError }: {
  snapshot: Snapshot | null;
  associations: AssociationRecord[];
  associationsError?: string;
}) {
  const [metric, setMetric] = useState('occupancy');
  const [range, setRange] = useState('24h');
  const [history, setHistory] = useState<HistoryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshIndex, setRefreshIndex] = useState(0);
  const runId = snapshot?.run_id;
  const rangeHours = RANGES.find((item) => item.value === range)?.hours ?? 24;

  useEffect(() => {
    const controller = new AbortController();
    const to = new Date();
    const from = new Date(to.getTime() - rangeHours * 60 * 60 * 1000);
    setLoading(true);
    setError(null);
    api.history(metric, from.toISOString(), to.toISOString(), 200, controller.signal)
      .then((response) => setHistory(response))
      .catch((requestError: unknown) => {
        if (requestError instanceof DOMException && requestError.name === 'AbortError') return;
        setError(errorMessage(requestError));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [metric, rangeHours, refreshIndex, runId]);

  const chartPoints = useMemo<ChartPoint[]>(() => history?.points.map((point) => ({
    ...point,
    time: new Date(point.timestamp).getTime(),
  })).filter((point) => Number.isFinite(point.time)) ?? [], [history]);
  const hasMeasurements = chartPoints.some((point) => point.value !== null && Number.isFinite(point.value));
  const metricLabel = METRICS.find((item) => item.value === metric)?.label ?? metric;

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow="INTELLIGENCE / HISTORY"
        title="Analytics & reconciliation"
        description="Actual local history, conservation fields, and cross-camera associations with explicit gaps."
        actions={<div className="heading-badges"><Badge tone="neutral" icon={Database}>Run {snapshot?.run_id ?? 'unavailable'}</Badge><Badge tone="info" icon={History}>Max 200 points</Badge></div>}
      />

      <Panel className="history-panel">
        <div className="history-toolbar">
          <div><p className="eyebrow">TIME-SERIES EXPLORER</p><h2>{metricLabel}</h2></div>
          <div className="history-controls">
            <label><span>Metric</span><select value={metric} onChange={(event) => setMetric(event.target.value)}>{METRICS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
            <label><span>Range</span><select value={range} onChange={(event) => setRange(event.target.value)}>{RANGES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
            <button type="button" className="icon-button" onClick={() => setRefreshIndex((value) => value + 1)} disabled={loading} aria-label="Refresh history" title="Refresh history"><RefreshCw size={16} className={loading ? 'spin' : ''} /></button>
          </div>
        </div>
        <div className="history-meta">
          <span>UTC range · evaluated at request time</span>
          <span>{history ? `${history.points.length} returned samples · ${history.gaps.length} reported gaps` : 'Waiting for history response'}</span>
        </div>

        {loading ? <LoadingBlock rows={5} /> : null}
        {!loading && error ? <EmptyState icon={CloudOff} title="History query failed" description={error} action={<button type="button" className="button button-secondary button-small" onClick={() => setRefreshIndex((value) => value + 1)}>Retry query</button>} /> : null}
        {!loading && !error && (!history || chartPoints.length === 0 || !hasMeasurements) ? (
          <EmptyState icon={BarChart3} title="No usable measurements in this range" description={history?.points.length ? 'The API returned timestamps but no numeric values. Null samples are not converted to zero.' : 'No samples were returned for this metric and range. Try another range or start a local run.'} compact />
        ) : null}
        {!loading && !error && history && chartPoints.length > 0 && hasMeasurements ? (
          <div className="history-chart" role="img" aria-label={`${metricLabel} history chart. Null samples are shown as gaps.`}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartPoints} margin={{ top: 16, right: 18, left: 0, bottom: 4 }}>
                <CartesianGrid stroke="#1b2931" strokeDasharray="3 5" vertical={false} />
                <XAxis dataKey="time" type="number" scale="time" domain={['dataMin', 'dataMax']} tickFormatter={(value: number) => formatChartTime(value, rangeHours)} stroke="#60717b" tick={{ fill: '#81919a', fontSize: 11 }} tickLine={false} axisLine={false} minTickGap={42} />
                <YAxis stroke="#60717b" tick={{ fill: '#81919a', fontSize: 11 }} tickLine={false} axisLine={false} width={48} domain={['auto', 'auto']} />
                <Tooltip
                  cursor={{ stroke: '#38505a', strokeDasharray: '3 3' }}
                  content={({ active, payload }) => {
                    const point = payload?.[0]?.payload as ChartPoint | undefined;
                    if (!active || !point) return null;
                    return (
                      <div className="chart-tooltip">
                        <time>{formatTimestamp(point.timestamp, 'UTC')}</time>
                        <strong>{point.value === null ? 'Unavailable' : formatNumber(point.value)}</strong>
                        <span>Quality · {point.quality}</span>
                        <span>Run · {point.run_id ?? 'UNKNOWN'}</span>
                      </div>
                    );
                  }}
                />
                <Line type="linear" dataKey="value" stroke="#6bd8d1" strokeWidth={2} connectNulls={false} dot={chartPoints.length <= 60 ? { r: 2, fill: '#0b1419', stroke: '#6bd8d1', strokeWidth: 1.5 } : false} activeDot={{ r: 4, fill: '#6bd8d1', stroke: '#0b1419' }} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        ) : null}
        {history && history.gaps.length > 0 ? (
          <details className="gap-details">
            <summary>Reported history gaps <Badge tone="warning">{history.gaps.length}</Badge></summary>
            <ul>{history.gaps.slice(0, 8).map((gap, index) => <li key={index}>{formatGap(gap)}</li>)}</ul>
          </details>
        ) : null}
        <p className="panel-footnote">Null samples remain gaps. Line segments are not connected across missing observations.</p>
        {history?.next_cursor ? <p className="muted-copy">More history is available; this view shows the first API page.</p> : null}
      </Panel>

      <div className="analytics-grid">
        <ConservationSummary snapshot={snapshot} />
        <Associations records={associations} error={associationsError} />
      </div>
    </div>
  );
}
