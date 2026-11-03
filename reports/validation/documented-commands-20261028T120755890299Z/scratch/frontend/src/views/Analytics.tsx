import { useMemo, useState } from 'react';
import { BarChart3, GitCompareArrows, RefreshCw } from 'lucide-react';
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { AssociationRecord, HistoryPoint, Snapshot } from '../types';
import { api } from '../lib/api';
import { formatChartTime, formatGap, formatInteger, formatNumber, formatTimestamp, formatUnknown } from '../lib/format';
import { useResource } from '../hooks/useResource';
import { CHART_CONFIG } from '../design-system/chart';
import { Badge, Callout, ChipGroup, DataValue, EmptyState, ErrorState, IconButton, KeyValue, KeyValueList, LoadingState, Panel } from '../design-system/components';
import { PageHead } from '../components/Layout';
import { QualityBadge } from '../components/status';

const METRICS = [
  { value: 'occupancy', label: 'Building occupancy' },
  { value: 'entries', label: 'Entries' },
  { value: 'exits', label: 'Exits' },
  { value: 'queue', label: 'Queue total' },
];
const RANGES = [{ value: '1', label: '1 h' }, { value: '6', label: '6 h' }, { value: '24', label: '24 h' }, { value: '168', label: '7 d' }];

interface ChartPoint extends HistoryPoint { time: number }

function Associations({ records, error }: { records: AssociationRecord[]; error?: string }) {
  return (
    <Panel eyebrow="Cross-camera / stair transitions" title="Association ledger" actions={<Badge icon={GitCompareArrows}>{records.length} records</Badge>}>
      {error ? <ErrorState title="Associations unavailable" message={error} /> : null}
      {!error && records.length === 0 ? (
        <EmptyState icon={GitCompareArrows} title="No transitions observed" compact
          description="Records appear only when a calibrated stair/portal camera pair observes a departure and an arrival. UNKNOWN is never replaced by a guessed identity." />
      ) : null}
      {records.length ? (
        <div className="ds-table-wrap"><div className="ds-table-scroll" tabIndex={0}>
          <table className="ds-table">
            <thead><tr><th>Decision</th><th>Portal</th><th>Source track</th><th>Candidate</th><th>Score</th><th>Reason</th></tr></thead>
            <tbody>{records.map((record, index) => (
              <tr key={String(record.id ?? index)}>
                <td><Badge tone={record.status === 'CONFIRMED' ? 'good' : 'warning'}>{formatUnknown(record.status)}</Badge><small>{record.created_at ? formatTimestamp(String(record.created_at)) : ''}</small></td>
                <td>{formatUnknown(record.portal_id)}</td>
                <td><code>{formatUnknown(record.source_track_key)}</code></td>
                <td><code>{formatUnknown(record.candidate_track_key)}</code></td>
                <td>{record.score === null || record.score === undefined ? 'none supplied' : formatNumber(Number(record.score), 2)}</td>
                <td>{formatUnknown(record.decision_reason)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div></div>
      ) : null}
    </Panel>
  );
}

export function Analytics({ snapshot, associations, associationsError }: { snapshot: Snapshot; associations: AssociationRecord[]; associationsError?: string }) {
  const [metric, setMetric] = useState('occupancy');
  const [range, setRange] = useState('24');
  const [refresh, setRefresh] = useState(0);
  const hours = Number(range);
  const history = useResource(() => {
    const to = new Date();
    return api.history(metric, new Date(to.getTime() - hours * 3600_000).toISOString(), to.toISOString(), 500);
  }, `${metric}:${range}:${refresh}:${snapshot.run_id}`);
  const points = useMemo<ChartPoint[]>(() => (history.data?.points ?? []).map((point) => ({ ...point, time: new Date(point.timestamp).getTime() }))
    .filter((point) => Number.isFinite(point.time)), [history.data]);
  const measured = points.some((point) => point.value !== null);
  const { kpis } = snapshot;
  const net = kpis.entries !== null && kpis.exits !== null ? kpis.entries - kpis.exits : null;

  return (
    <>
      <PageHead title="Analytics" description="Facility history and conservation from calibrated analytics, plus the stair/cross-camera association ledger. Camera-local track counts live in each camera inspector." />
      <Callout tone="info" title="Two kinds of numbers">
        <p><strong style={{ display: 'inline' }}>Camera-local</strong>: tracked detections inside one camera image (Cameras, inspector history). <strong style={{ display: 'inline' }}>Facility</strong>: positions on a calibrated floor map, de-duplicated across zones and overlapping cameras (this page, Floors). Facility figures stay unavailable until calibrations validate.</p>
      </Callout>
      <Panel eyebrow="Facility history" title={METRICS.find((item) => item.value === metric)?.label}
        actions={<IconButton label="Refresh history" icon={RefreshCw} spinning={history.loading} onClick={() => setRefresh((value) => value + 1)} />}>
        <div className="toolbar" style={{ marginBottom: 12 }}>
          <ChipGroup label="Metric" value={metric} onChange={setMetric} options={METRICS} />
          <ChipGroup label="Range" value={range} onChange={setRange} options={RANGES} />
        </div>
        {history.loading && !history.data ? <LoadingState rows={4} /> : null}
        {history.error ? <ErrorState title="History query failed" message={history.error} /> : null}
        {!history.error && history.data && !measured ? (
          <EmptyState icon={BarChart3} title="No measurements in this range"
            description={points.length ? 'Samples exist but all are unavailable (null); they are not drawn as zero.' : 'Nothing was persisted for this metric — facility metrics need calibrated cameras.'} compact />
        ) : null}
        {measured ? (
          <div className="chart-panel" role="img" aria-label="Facility metric history; unavailable samples are gaps">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={points} margin={{ top: 10, right: 16, left: 0, bottom: 4 }}>
                <CartesianGrid {...CHART_CONFIG.grid} />
                <XAxis dataKey="time" type="number" scale="time" domain={['dataMin', 'dataMax']} tickFormatter={(value: number) => formatChartTime(value, hours)} {...CHART_CONFIG.axis} minTickGap={42} />
                <YAxis {...CHART_CONFIG.axis} width={44} />
                <Tooltip contentStyle={CHART_CONFIG.tooltipStyle} labelFormatter={(value) => formatTimestamp(new Date(Number(value)).toISOString(), 'UTC')}
                  formatter={(value) => [value === null ? 'Unavailable' : formatNumber(Number(value)), 'Value']} />
                <Line type="linear" dataKey="value" stroke="var(--chart-series-1)" strokeWidth={2} {...CHART_CONFIG.line} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        ) : null}
        {history.data?.gaps.length ? <p className="ds-footnote">Reported gaps: {history.data.gaps.slice(0, 4).map(formatGap).join(' · ')}</p> : null}
      </Panel>
      <div className="grid-2">
        <Panel eyebrow="Current run" title="Conservation" actions={<QualityBadge quality={kpis.quality} />}>
          <KeyValueList>
            <KeyValue label="Entries"><DataValue value={kpis.entries} formatter={(value) => formatInteger(Number(value))} reason="No calibrated facility boundary is measured" /></KeyValue>
            <KeyValue label="Exits"><DataValue value={kpis.exits} formatter={(value) => formatInteger(Number(value))} reason="No calibrated facility boundary is measured" /></KeyValue>
            <KeyValue label="Net flow (IN − OUT)"><DataValue value={net} formatter={(value) => formatInteger(Number(value))} reason="Needs both entries and exits" /></KeyValue>
            <KeyValue label="Expected occupancy"><DataValue value={kpis.building_expected} formatter={(value) => formatInteger(Number(value))} reason="Ledger requires current calibrated boundary cameras" /></KeyValue>
            <KeyValue label="Visible in zones"><DataValue value={kpis.building_visible} formatter={(value) => formatInteger(Number(value))} reason="Needs every zone current" /></KeyValue>
            <KeyValue label="Discrepancy"><DataValue value={kpis.discrepancy} formatter={(value) => formatNumber(Number(value))} reason="No ledger snapshot" /></KeyValue>
          </KeyValueList>
          <p className="ds-footnote">The discrepancy is the backend value; the browser never forces reconciliation.</p>
        </Panel>
        <Associations records={associations} error={associationsError} />
      </div>
    </>
  );
}
