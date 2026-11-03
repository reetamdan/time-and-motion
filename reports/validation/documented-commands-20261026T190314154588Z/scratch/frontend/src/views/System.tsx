import { useState } from 'react';
import {
  Activity,
  CheckCircle2,
  Cpu,
  Gauge,
  HardDrive,
  MemoryStick,
  RefreshCw,
  Server,
  ShieldCheck,
  Timer,
  XCircle,
} from 'lucide-react';
import type { ConnectionState, EnvironmentResponse, ReadinessResponse, Snapshot, SystemMetrics, VersionsResponse } from '../types';
import type { HealthProbe } from '../hooks/useOperations';
import { formatBytes, formatFps, formatInteger, formatMilliseconds, formatPercent, formatTimestamp, reasonOr, titleCase } from '../lib/format';
import { SeverityBadge } from '../components/Alert';
import { PageHeader } from '../components/PageHeader';
import { Badge, DataValue, EmptyState, ErrorNotice, KeyValue, Meter, Panel, QualityBadge, Reason } from '../components/ui';

interface SystemErrors {
  snapshot?: string;
  readiness?: string;
  environment?: string;
  versions?: string;
  health?: string;
}

function ProbeValue({ value, nullReason = 'The local probe returned null for this field.' }: { value: unknown; nullReason?: string }) {
  if (value === null || value === undefined) return <span className="probe-null" title={nullReason}>Unavailable</span>;
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="probe-null">None reported</span>;
    return <ul className="probe-list">{value.map((item, index) => <li key={index}><ProbeValue value={item} nullReason={nullReason} /></li>)}</ul>;
  }
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>;
    const fields = Object.keys(record).length;
    if (fields === 0) return <span className="probe-null">No fields reported</span>;
    return (
      <details className="probe-details">
        <summary>Inspect {fields} field{fields === 1 ? '' : 's'}</summary>
        <ProbeRecord record={record} />
      </details>
    );
  }
  if (typeof value === 'boolean') return <span>{value ? 'Yes' : 'No'}</span>;
  return <span>{String(value)}</span>;
}

function ProbeRecord({ record, excluded = [] }: { record: Record<string, unknown>; excluded?: string[] }) {
  const entries = Object.entries(record).filter(([key]) => !excluded.includes(key));
  if (entries.length === 0) return <p className="muted-copy">No additional fields were returned by this probe.</p>;
  return (
    <dl className="probe-record">
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt>{titleCase(key)}</dt>
          <dd><ProbeValue value={value} /></dd>
        </div>
      ))}
    </dl>
  );
}

function CapabilityList({ capabilities }: { capabilities: unknown }) {
  if (capabilities === null || capabilities === undefined) {
    return <EmptyState title="Capabilities not reported" description="The environment response did not include a capabilities field. Nothing is assumed to be supported." compact />;
  }
  const entries: Array<[string, unknown]> = Array.isArray(capabilities)
    ? capabilities.map((value, index) => {
      if (value && typeof value === 'object') {
        const record = value as Record<string, unknown>;
        return [String(record.name ?? record.id ?? `Capability ${index + 1}`), value];
      }
      return [`Capability ${index + 1}`, value];
    })
    : typeof capabilities === 'object'
      ? Object.entries(capabilities)
      : [['Capabilities', capabilities]];

  if (entries.length === 0) return <p className="muted-copy">The capabilities probe returned an empty collection.</p>;
  return (
    <div className="capability-list">
      {entries.map(([name, value]) => {
        const record = value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
        const state = typeof value === 'boolean'
          ? value
          : record?.available ?? record?.ready ?? record?.supported ?? record?.enabled;
        const detail = record?.detail ?? record?.unavailable_reason ?? record?.reason ?? (record ? undefined : value);
        return (
          <div className="capability-row" key={name}>
            <span className={state === true ? 'capability-icon capability-good' : state === false ? 'capability-icon capability-bad' : 'capability-icon'}>
              {state === true ? <CheckCircle2 size={15} /> : state === false ? <XCircle size={15} /> : <Activity size={15} />}
            </span>
            <div><strong>{titleCase(name)}</strong><p><ProbeValue value={detail} nullReason="No capability detail was supplied." /></p></div>
            <Badge tone={state === true ? 'good' : state === false ? 'danger' : 'neutral'}>{state === true ? 'Available' : state === false ? 'Unavailable' : 'Reported'}</Badge>
          </div>
        );
      })}
    </div>
  );
}

function MetricLine({ label, value, formatter, reason, max, meter = false }: {
  label: string;
  value: number | null | undefined;
  formatter: (value: number) => string;
  reason: string;
  max?: number | null;
  meter?: boolean;
}) {
  const normalized = value ?? null;
  return (
    <div className="system-metric-line">
      <div><span>{label}</span><strong><DataValue value={normalized} formatter={(item) => formatter(Number(item))} reason={reason} /></strong></div>
      {meter ? <Meter value={normalized} max={max ?? 100} /> : null}
    </div>
  );
}

function MetricsPanel({ title, eyebrow, icon, metrics, children }: {
  title: string;
  eyebrow: string;
  icon: React.ReactNode;
  metrics: SystemMetrics | null;
  children: React.ReactNode;
}) {
  return (
    <Panel className="system-metric-panel">
      <header className="system-metric-panel-header"><span>{icon}</span><div><p className="eyebrow">{eyebrow}</p><h2>{title}</h2></div></header>
      {metrics ? children : <EmptyState title="Metrics unavailable" description="No system metrics were included in a facility snapshot. Unsupported probes are not shown as zero." compact />}
    </Panel>
  );
}

export function System({ snapshot, readiness, environment, versions, health, connection, errors, onRefresh }: {
  snapshot: Snapshot | null;
  readiness: ReadinessResponse | null;
  environment: EnvironmentResponse | null;
  versions: VersionsResponse | null;
  health: HealthProbe | null;
  connection: ConnectionState;
  errors: SystemErrors;
  onRefresh: () => Promise<void>;
}) {
  const [refreshing, setRefreshing] = useState(false);
  const metrics = snapshot?.system_metrics ?? null;
  const metricReason = reasonOr(metrics?.unavailable_reason, errors.snapshot, 'The local system-metrics probe did not report this value.');
  const readyCount = readiness?.checks.filter((check) => check.ready).length;
  const totalChecks = readiness?.checks.length;
  const runtime = environment?.runtime;
  const runtimeCapabilities = runtime && typeof runtime === 'object' && !Array.isArray(runtime) && 'capabilities' in runtime
    ? (runtime as Record<string, unknown>).capabilities
    : undefined;
  const capabilities = environment && 'capabilities' in environment ? environment.capabilities : runtimeCapabilities;

  const refresh = async () => {
    setRefreshing(true);
    await onRefresh();
    setRefreshing(false);
  };

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow="LOCAL RUNTIME / DIAGNOSTICS"
        title="System & readiness"
        description="Actual host capabilities, runtime probes, performance, and configuration provenance."
        actions={<button type="button" className="button button-secondary button-small" onClick={() => void refresh()} disabled={refreshing}><RefreshCw size={14} className={refreshing ? 'spin' : ''} />{refreshing ? 'Refreshing…' : 'Refresh probes'}</button>}
      />

      <div className="system-status-strip">
        <div><span className="system-status-icon"><Server size={17} /></span><span>API transport</span><strong>{titleCase(connection)}</strong></div>
        <div><span className="system-status-icon"><ShieldCheck size={17} /></span><span>Readiness</span><strong>{readyCount !== undefined && totalChecks !== undefined ? `${readyCount} / ${totalChecks} checks ready` : 'Unavailable'}</strong></div>
        <div><span className="system-status-icon"><Timer size={17} /></span><span>API round-trip</span><strong>{health ? formatMilliseconds(health.rttMs) : 'Unavailable'}</strong></div>
        <div><span className="system-status-icon"><Activity size={17} /></span><span>Metrics scope</span><strong>{metrics ? titleCase(metrics.scope) : 'Unavailable'}</strong></div>
      </div>

      <div className="system-top-grid">
        <Panel title="Readiness checks" eyebrow="GET /HEALTH/READY" action={readiness ? <Badge tone={readiness.checks.every((check) => check.ready) ? 'good' : 'warning'}>{titleCase(readiness.status)}</Badge> : undefined}>
          {errors.readiness ? <ErrorNotice title="Readiness probe failed" message={errors.readiness} /> : null}
          {readiness ? (
            <div className="readiness-list">
              {readiness.checks.map((check) => (
                <div className="readiness-row" key={check.name}>
                  <span className={check.ready ? 'readiness-icon readiness-good' : 'readiness-icon readiness-bad'}>{check.ready ? <CheckCircle2 size={16} /> : <XCircle size={16} />}</span>
                  <div><strong>{titleCase(check.name)}</strong><p>{check.detail}</p></div>
                  <Badge tone={check.ready ? 'good' : 'danger'}>{check.ready ? 'Ready' : 'Not ready'}</Badge>
                </div>
              ))}
              {readiness.checks.length === 0 ? <p className="muted-copy">The readiness endpoint returned no checks. This does not establish readiness.</p> : null}
            </div>
          ) : !errors.readiness ? <EmptyState title="Readiness unavailable" description="No readiness response has been received. Missing prerequisites cannot be ruled out." compact /> : null}
        </Panel>

        <Panel title="Environment" eyebrow="ACTUAL HOST PROBE" action={<Badge tone="neutral" icon={HardDrive}>Loopback</Badge>}>
          {errors.environment ? <ErrorNotice title="Environment probe failed" message={errors.environment} /> : null}
          {environment ? <ProbeRecord record={environment} excluded={['capabilities']} /> : !errors.environment ? <EmptyState title="Environment unavailable" description="No environment response has been received." compact /> : null}
        </Panel>
      </div>

      <Panel title="Runtime capabilities" eyebrow="SUPPORTED ONLY WHEN PROBED">
        {environment ? <CapabilityList capabilities={capabilities} /> : <EmptyState title="Capabilities unavailable" description={errors.environment ?? 'Wait for the environment probe to return capability data.'} compact />}
      </Panel>

      <div className="system-metrics-heading">
        <div><p className="eyebrow">LIVE PERFORMANCE</p><h2>Host & process metrics</h2></div>
        {metrics ? <div><Badge tone={metrics.scope === 'unavailable' ? 'muted' : 'info'}>{titleCase(metrics.scope)}</Badge><time dateTime={metrics.timestamp}>{formatTimestamp(metrics.timestamp, 'UTC')}</time></div> : null}
      </div>
      <div className="system-metrics-grid">
        <MetricsPanel title="GPU & decode" eyebrow={metrics?.gpu_name ?? 'GPU NAME UNAVAILABLE'} icon={<Gauge size={19} />} metrics={metrics}>
          <MetricLine label="GPU utilization" value={metrics?.gpu_utilization} formatter={formatPercent} reason={metricReason} meter />
          <MetricLine label="NVDEC utilization" value={metrics?.nvdec_utilization} formatter={formatPercent} reason={metricReason} meter />
          <MetricLine label="GPU temperature" value={metrics?.gpu_temperature_c} formatter={(value) => `${formatInteger(value)} °C`} reason={metricReason} />
          <MetricLine label="VRAM used" value={metrics?.vram_used_mib} formatter={(value) => formatBytes(value)} reason={metricReason} max={metrics?.vram_total_mib} meter={metrics?.vram_total_mib != null} />
          <MetricLine label="VRAM total" value={metrics?.vram_total_mib} formatter={(value) => formatBytes(value)} reason={metricReason} />
          {metrics && (metrics.gpu_name === null || metrics.gpu_utilization === null || metrics.nvdec_utilization === null || metrics.gpu_temperature_c === null || metrics.vram_used_mib === null || metrics.vram_total_mib === null) ? <Reason>{metricReason}</Reason> : null}
        </MetricsPanel>

        <MetricsPanel title="Host resources" eyebrow="CPU & MEMORY" icon={<Cpu size={19} />} metrics={metrics}>
          <MetricLine label="CPU utilization" value={metrics?.cpu_utilization} formatter={formatPercent} reason={metricReason} meter />
          <MetricLine label="RAM used" value={metrics?.ram_used_mb} formatter={(value) => formatBytes(value, 'MB')} reason={metricReason} max={metrics?.ram_total_mb} meter={metrics?.ram_total_mb != null} />
          <MetricLine label="RAM total" value={metrics?.ram_total_mb} formatter={(value) => formatBytes(value, 'MB')} reason={metricReason} />
          <MetricLine label="Process RSS" value={metrics?.process_rss_mb} formatter={(value) => formatBytes(value, 'MB')} reason={metricReason} />
          {metrics && (metrics.cpu_utilization === null || metrics.ram_used_mb === null || metrics.ram_total_mb === null || metrics.process_rss_mb === null) ? <Reason>{metricReason}</Reason> : null}
        </MetricsPanel>

        <Panel className="system-metric-panel">
          <header className="system-metric-panel-header"><span><Timer size={19} /></span><div><p className="eyebrow">MEASURED LOCALLY</p><h2>Probe & latency</h2></div></header>
          <div className="system-metric-line"><div><span>API round-trip</span><strong>{health ? formatMilliseconds(health.rttMs) : <DataValue value={null} reason={errors.health ?? 'The health latency probe has not completed.'} />}</strong></div><Meter value={health?.rttMs ?? null} max={1000} /></div>
          <dl className="key-value-grid probe-health-details">
            <KeyValue label="Liveness status"><DataValue value={health?.status} reason={errors.health ?? 'No liveness response was received.'} /></KeyValue>
            <KeyValue label="Probe timestamp"><DataValue value={health?.timestamp} formatter={(value) => formatTimestamp(String(value), 'UTC')} reason={errors.health ?? 'No liveness timestamp was received.'} /></KeyValue>
            <KeyValue label="Snapshot sequence"><DataValue value={snapshot?.sequence} formatter={(value) => formatInteger(Number(value))} reason="No facility snapshot was received." /></KeyValue>
            <KeyValue label="Watermark"><DataValue value={snapshot?.watermark} formatter={(value) => formatInteger(Number(value))} reason="No facility watermark was received." /></KeyValue>
          </dl>
          {errors.health ? <Reason>{errors.health}</Reason> : <Reason>Round-trip is measured by the browser around GET /health/live; it is not inference latency.</Reason>}
        </Panel>
      </div>

      <Panel title="Per-camera latency" eyebrow="REPORTED FRAME TELEMETRY" action={<Badge tone="neutral" icon={MemoryStick}>{snapshot ? `${snapshot.cameras.length} sources` : 'Sources unavailable'}</Badge>}>
        {snapshot && snapshot.cameras.length > 0 ? (
          <div className="table-scroll"><table className="data-table"><thead><tr><th>Camera</th><th>Frame age</th><th>Age basis</th><th>Source FPS</th><th>Analytics FPS</th><th>Last good</th></tr></thead><tbody>
            {snapshot.cameras.map((camera) => <tr key={camera.id}><td><strong>{camera.name}</strong><small className="table-subline">{camera.id}</small></td><td><DataValue value={camera.frame_age_ms} formatter={(value) => formatMilliseconds(Number(value))} reason={reasonOr(camera.unavailable_reason)} /></td><td><DataValue value={camera.frame_age_basis} reason="No frame-age basis was reported." /></td><td><DataValue value={camera.source_fps} formatter={(value) => formatFps(Number(value))} reason={reasonOr(camera.unavailable_reason)} /></td><td><DataValue value={camera.analytics_fps} formatter={(value) => formatFps(Number(value))} reason={reasonOr(camera.unavailable_reason)} /></td><td><DataValue value={camera.last_good_age_ms} formatter={(value) => formatMilliseconds(Number(value))} reason={reasonOr(camera.unavailable_reason)} /></td></tr>)}
          </tbody></table></div>
        ) : <EmptyState title="Per-camera latency unavailable" description={errors.snapshot ?? 'No camera telemetry has been returned by the facility snapshot.'} compact />}
      </Panel>

      {snapshot && snapshot.notices.length > 0 ? (
        <Panel title="Runtime notices" eyebrow="READINESS REMAINS VISIBLE">
          <div className="runtime-notices">{snapshot.notices.map((notice, index) => <div key={`${notice.title}-${index}`}><SeverityBadge severity={notice.severity} /><div><strong>{notice.title}</strong><p>{notice.message}</p></div></div>)}</div>
        </Panel>
      ) : null}

      <div className="system-bottom-grid">
        <Panel title="Versions & revisions" eyebrow="GET /API/V1/VERSIONS">
          {errors.versions ? <ErrorNotice title="Version probe failed" message={errors.versions} /> : null}
          {versions ? <ProbeRecord record={versions} /> : !errors.versions ? <EmptyState title="Version provenance unavailable" description="No version response has been received. Missing revisions remain unavailable." compact /> : null}
        </Panel>
        <Panel title="Snapshot provenance" eyebrow="CURRENT FACILITY STATE" action={snapshot ? <QualityBadge quality={snapshot.kpis.quality} /> : undefined}>
          {snapshot ? <dl className="key-value-grid provenance-grid"><KeyValue label="Schema version">{snapshot.schema_version}</KeyValue><KeyValue label="Run ID"><DataValue value={snapshot.run_id} reason="No active run ID was reported." /></KeyValue><KeyValue label="Run status">{titleCase(snapshot.run_status)}</KeyValue><KeyValue label="Sequence">{formatInteger(snapshot.sequence)}</KeyValue><KeyValue label="Watermark">{formatInteger(snapshot.watermark)}</KeyValue><KeyValue label="Generated at">{formatTimestamp(snapshot.generated_at, 'UTC')}</KeyValue></dl> : <EmptyState title="Snapshot provenance unavailable" description={errors.snapshot ?? 'No snapshot has been received.'} compact />}
        </Panel>
      </div>
    </div>
  );
}
