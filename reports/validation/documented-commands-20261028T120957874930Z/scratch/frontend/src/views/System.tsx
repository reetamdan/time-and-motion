import { useState } from 'react';
import { CheckCircle2, Clock3, Cpu, Fingerprint, Layers, RefreshCw, Server, ShieldCheck, XCircle } from 'lucide-react';
import type { CapabilityEntry, CapabilityMatrix, ConnectionState, EnvironmentResponse, ReadinessResponse, Snapshot, VersionsResponse } from '../types';
import type { HealthProbe } from '../hooks/useOperations';
import { api, errorMessage } from '../lib/api';
import { formatBytes, formatFps, formatInteger, formatMilliseconds, formatPercent, formatTimestamp, reasonOr, titleCase } from '../lib/format';
import { useResource } from '../hooks/useResource';
import { Badge, Button, Callout, DataValue, EmptyState, ErrorState, KeyValue, KeyValueList, LoadingState, Meter, Panel } from '../design-system/components';
import { PageHead } from '../components/Layout';
import { AcceptanceBadge } from '../components/status';
import { pollKey } from './Overview';

const CAMERA_FEATURES: Array<[keyof CapabilityMatrix['cameras'][string], string]> = [
  ['camera_local_counts', 'Camera-local counts'],
  ['image_line_crossings', 'Image-line crossings'],
  ['floor_positions', 'Floor positions'],
  ['facility_crossings', 'Entries / exits'],
];

function Cell({ entry }: { entry: CapabilityEntry | undefined }) {
  if (!entry) return <td>—</td>;
  if (!entry.available) return <td data-available="false"><span className="cap-no"><XCircle size={13} aria-hidden="true" />{entry.reason ?? 'Unavailable'}</span></td>;
  if (entry.current === false) return <td data-available="true" data-current="false"><span className="cap-no cap-stale"><Clock3 size={13} aria-hidden="true" />Configured · not current: {entry.current_reason}</span></td>;
  return <td data-available="true"><span className="cap-yes"><CheckCircle2 size={13} aria-hidden="true" />Available</span></td>;
}

function CapabilityPanel({ snapshot }: { snapshot: Snapshot }) {
  const { data: matrix, error } = useResource(() => api.capabilities(), pollKey(snapshot, 3));
  return (
    <Panel eyebrow="GET /api/v1/system/capabilities" title="Analytics capabilities" description="What can be measured now, and the exact reason for each item that cannot."
      actions={<Badge icon={Layers}>Calibration-gated</Badge>}>
      {error ? <ErrorState title="Capabilities unavailable" message={error} /> : null}
      {!matrix && !error ? <LoadingState rows={3} /> : null}
      {matrix ? (
        <div className="stack" data-testid="capability-matrix">
          <div className="ds-table-wrap"><div className="ds-table-scroll" tabIndex={0}>
            <table className="ds-table">
              <caption>Per camera</caption>
              <thead><tr><th>Camera</th>{CAMERA_FEATURES.map(([, label]) => <th key={label}>{label}</th>)}</tr></thead>
              <tbody>{Object.entries(matrix.cameras).map(([id, camera]) => (
                <tr key={id}>
                  <td><strong>{id}</strong><small>{camera.running ? (camera.fresh ? 'Running · fresh' : `Running · ${camera.freshness_reason ?? 'not calibrated for facility use'}`) : camera.enabled ? 'Enabled · not running' : 'Disabled'}</small></td>
                  {CAMERA_FEATURES.map(([key]) => <Cell key={key} entry={camera[key] as CapabilityEntry} />)}
                </tr>
              ))}</tbody>
            </table>
          </div></div>
          <div className="ds-table-wrap"><div className="ds-table-scroll" tabIndex={0}>
            <table className="ds-table">
              <caption>Zones, floors and facility</caption>
              <thead><tr><th>Scope</th><th>Occupancy</th><th>Congestion</th><th>Queue</th></tr></thead>
              <tbody>
                {Object.entries(matrix.zones).map(([id, zone]) => (
                  <tr key={id}><td><strong>{id}</strong><small>{titleCase(zone.kind)} zone</small></td><Cell entry={zone.occupancy} /><Cell entry={zone.congestion} />{zone.queue ? <Cell entry={zone.queue} /> : <td>Not a queue zone</td>}</tr>
                ))}
                {Object.entries(matrix.floors ?? {}).map(([id, floor]) => (
                  <tr key={id}><td><strong>{id}</strong><small>floor · {floor.visible_in_zones.fixture_status === 'SURVEYED' ? 'surveyed' : 'illustrative'}</small></td><Cell entry={floor.visible_in_zones} /><td colSpan={2}><small>{floor.visible_in_zones.aggregation}</small></td></tr>
                ))}
                <tr><td><strong>Building estimate</strong></td><Cell entry={matrix.building_estimate} /><td colSpan={2} /></tr>
                <tr><td><strong>Stair association</strong></td><Cell entry={matrix.stair_association} /><td colSpan={2}><small>{matrix.stair_association.identity_continuity}</small></td></tr>
              </tbody>
            </table>
          </div></div>
        </div>
      ) : null}
    </Panel>
  );
}

function ArtifactPanel({ snapshot }: { snapshot: Snapshot }) {
  const [nonce, setNonce] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const { data, error } = useResource(() => api.artifacts(), `${snapshot.run_id}:${nonce}`);
  const cached = data?.capability?.cached;
  const invalidate = async () => {
    setBusy(true);
    setMessage(null);
    try { await api.invalidateCapability(); setMessage('Cache invalidated; the next start re-probes the container.'); setNonce((value) => value + 1); }
    catch (reason) { setMessage(errorMessage(reason)); }
    finally { setBusy(false); }
  };
  const active = ['RUNNING', 'STARTING', 'DEGRADED'].includes(snapshot.run_status);
  return (
    <Panel eyebrow="GET /api/v1/system/artifacts" title="Runtime artifact identity" description="Recorded expected sha256 vs the hash recomputed now. Any difference blocks perception."
      actions={<Badge icon={Fingerprint}>Expected vs current</Badge>}>
      {error ? <ErrorState title="Artifact identities unavailable" message={error} /> : null}
      {data && data.artifacts.length === 0 ? <EmptyState title="No artifact identities" description="This perception adapter reports no runtime artifacts." compact /> : null}
      {data && data.artifacts.length ? (
        <div className="ds-table-wrap" data-testid="artifact-identity"><div className="ds-table-scroll" tabIndex={0}>
          <table className="ds-table">
            <thead><tr><th>Artifact</th><th>Status</th><th>Expected (recorded)</th><th>Current (recomputed)</th></tr></thead>
            <tbody>{data.artifacts.map((item) => (
              <tr key={item.name}>
                <td><strong>{item.name}</strong><small>{item.path}</small></td>
                <td><Badge tone={item.status === 'MATCH' ? 'good' : 'danger'}>{item.status}</Badge></td>
                <td><code className="hash">{item.expected_sha256 ? `${item.expected_sha256.slice(0, 16)}…` : 'none recorded'}</code></td>
                <td><code className="hash">{item.current_sha256 ? `${item.current_sha256.slice(0, 16)}…` : 'unavailable'}</code></td>
              </tr>
            ))}</tbody>
          </table>
        </div></div>
      ) : null}
      {data?.capability ? (
        <div className="toolbar" style={{ marginTop: 12, justifyContent: 'space-between' }}>
          <p className="ds-footnote" style={{ margin: 0 }}>
            Container capability cache: {cached ? `${cached.available ? 'validated' : 'failed'} ${cached.validated_at_utc ? formatTimestamp(cached.validated_at_utc) : ''} (probe ${cached.probe_seconds ?? '—'} s)` : 'not probed yet'}
            {data.capability.last_invalidation ? ` · last invalidated: ${data.capability.last_invalidation.reason}` : ''}
          </p>
          <Button size="sm" icon={RefreshCw} loading={busy} disabled={busy || active} title={active ? 'Stop the run before invalidating' : 'Force the next start to re-probe the container'} onClick={() => void invalidate()}>Invalidate cache</Button>
        </div>
      ) : null}
      {message ? <p className="ds-footnote" role="status">{message}</p> : null}
    </Panel>
  );
}

function ProvenancePanel({ snapshot, versions }: { snapshot: Snapshot; versions: VersionsResponse | null }) {
  const { data, error } = useResource(snapshot.run_id ? () => api.provenance(snapshot.run_id as string) : null, snapshot.run_id);
  const detector = data?.detector;
  return (
    <Panel eyebrow="Detector profile & provenance" title={String(versions?.selected_model ?? detector?.id ?? 'Detector unavailable')}
      actions={<AcceptanceBadge acceptance={(detector?.acceptance ?? versions?.model_acceptance) as string | undefined} />}>
      <KeyValueList>
        <KeyValue label="Selection mode">{String(versions?.model_selection_mode ?? data?.selection_mode ?? 'unavailable')}</KeyValue>
        <KeyValue label="Model revision"><code>{String(versions?.model_revision ?? detector?.revision ?? 'unavailable')}</code></KeyValue>
        <KeyValue label="Person class">{detector?.profile ? `${detector.profile.person_label} (class ${detector.profile.person_class_id})` : 'recorded per run'}</KeyValue>
        <KeyValue label="Pipeline frame">{detector?.profile ? `${detector.profile.pipeline_frame.join('×')} · ${detector.profile.source_geometry_policy}` : 'recorded per run'}</KeyValue>
        <KeyValue label="Tracker">{data?.tracker ? `${data.tracker.name} · ${data.tracker.revision}` : String(versions?.tracker_revision ?? 'unavailable')}</KeyValue>
        <KeyValue label="Run replay mode">{data?.replay_mode ? titleCase(data.replay_mode) : 'no run'}</KeyValue>
        <KeyValue label="Evaluation reference">{detector?.evaluation_ref ?? 'none (no labelled evaluation yet)'}</KeyValue>
        <KeyValue label="Configuration revision"><code className="hash">{String(versions?.configuration_revision ?? 'unavailable').slice(0, 16)}</code></KeyValue>
      </KeyValueList>
      {error && snapshot.run_id ? <p className="ds-footnote">Run provenance: {error}</p> : null}
      {!snapshot.run_id ? <p className="ds-footnote">Run provenance is recorded when a run starts.</p> : null}
      {versions?.model_acceptance !== 'accepted' ? <p className="ds-footnote">No detector is an accepted baseline yet: counts are real detector outputs but their accuracy is not established (human-reviewed labels are required).</p> : null}
    </Panel>
  );
}

export function System({ snapshot, readiness, environment, versions, health, connection, errors, onRefresh }: {
  snapshot: Snapshot;
  readiness: ReadinessResponse | null;
  environment: EnvironmentResponse | null;
  versions: VersionsResponse | null;
  health: HealthProbe | null;
  connection: ConnectionState;
  errors: Partial<Record<string, string>>;
  onRefresh: () => Promise<void>;
}) {
  const [refreshing, setRefreshing] = useState(false);
  const metrics = snapshot.system_metrics;
  const reason = reasonOr(metrics.unavailable_reason);
  const refresh = async () => { setRefreshing(true); await onRefresh(); setRefreshing(false); };
  const ready = readiness?.checks.filter((check) => check.ready).length;
  const telemetry: Array<[string, number | null, number | null, string]> = [
    ['GPU utilization', metrics.gpu_utilization, 100, formatPercent(metrics.gpu_utilization)],
    ['NVDEC utilization', metrics.nvdec_utilization, 100, formatPercent(metrics.nvdec_utilization)],
    ['VRAM', metrics.vram_used_mib, metrics.vram_total_mib, metrics.vram_used_mib === null ? '' : `${formatBytes(metrics.vram_used_mib)}${metrics.vram_total_mib ? ` / ${formatBytes(metrics.vram_total_mib)}` : ''}`],
    ['GPU temperature', metrics.gpu_temperature_c, 100, metrics.gpu_temperature_c === null ? '' : `${formatInteger(metrics.gpu_temperature_c)} °C`],
    ['CPU utilization', metrics.cpu_utilization, 100, formatPercent(metrics.cpu_utilization)],
    ['RAM', metrics.ram_used_mb, metrics.ram_total_mb, metrics.ram_used_mb === null ? '' : `${formatBytes(metrics.ram_used_mb, 'MB')}${metrics.ram_total_mb ? ` / ${formatBytes(metrics.ram_total_mb, 'MB')}` : ''}`],
    ['Backend RSS', metrics.process_rss_mb, null, metrics.process_rss_mb === null ? '' : formatBytes(metrics.process_rss_mb, 'MB')],
  ];
  return (
    <>
      <PageHead title="System" description="Readiness, measured telemetry, analytics capabilities, artifact identities, and detector provenance."
        actions={<Button icon={RefreshCw} loading={refreshing} disabled={refreshing} onClick={() => void refresh()}>Refresh probes</Button>} />
      <div className="grid-3">
        <Panel eyebrow="Transport" title={titleCase(connection)} actions={<Server size={16} aria-hidden="true" />}>
          <KeyValueList stack>
            <KeyValue label="API round-trip (GET /health/live)">{health ? formatMilliseconds(health.rttMs) : <DataValue value={null} reason={errors.health ?? 'Not measured yet'} />}</KeyValue>
            <KeyValue label="Snapshot sequence">{formatInteger(snapshot.sequence)}</KeyValue>
          </KeyValueList>
        </Panel>
        <Panel eyebrow="GET /health/ready" title={readiness ? `${ready} / ${readiness.checks.length} checks ready` : 'Readiness unavailable'} actions={<ShieldCheck size={16} aria-hidden="true" />}>
          {errors.readiness ? <ErrorState title="Readiness probe failed" message={errors.readiness} /> : null}
          {readiness ? (
            <div className="telemetry-row">{readiness.checks.map((check) => (
              <div key={check.name} title={check.detail}><span>{titleCase(check.name)}</span>{check.ready ? <Badge tone="good">Ready</Badge> : <Badge tone="danger">Not ready</Badge>}</div>
            ))}</div>
          ) : null}
        </Panel>
        <Panel eyebrow={`Telemetry · ${metrics.scope}`} title={metrics.gpu_name ?? 'GPU unavailable'} actions={<Cpu size={16} aria-hidden="true" />}>
          <div className="telemetry-row">
            {telemetry.map(([label, value, max, text]) => (
              <div key={label} style={{ display: 'grid', gridTemplateColumns: '1fr', gap: 4 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>{label}</span><strong>{value === null ? <DataValue value={null} reason={reason} /> : text}</strong></div>
                {max ? <Meter value={value} max={max} /> : null}
              </div>
            ))}
          </div>
          <p className="ds-footnote">Sampled {formatTimestamp(metrics.timestamp, 'UTC')} UTC. Unsupported probes show as unavailable, not 0.</p>
        </Panel>
      </div>
      {readiness?.checks.some((check) => !check.ready) ? (
        <Callout tone="warning" title="Setup items still open">
          <ul style={{ paddingLeft: 18 }}>{readiness.checks.filter((check) => !check.ready).map((check) => <li key={check.name}><p>{titleCase(check.name)}: {check.detail}</p></li>)}</ul>
        </Callout>
      ) : null}
      <div className="grid-2">
        <ProvenancePanel snapshot={snapshot} versions={versions} />
        <Panel eyebrow="Per-camera processing" title="Rates and freshness">
          <div className="ds-table-wrap"><div className="ds-table-scroll" tabIndex={0}>
            <table className="ds-table">
              <thead><tr><th>Camera</th><th>Source</th><th>Analytics</th><th>Last good</th><th>Queue</th></tr></thead>
              <tbody>{snapshot.cameras.map((camera) => (
                <tr key={camera.id}>
                  <td><strong>{camera.id}</strong><small>{camera.enabled ? titleCase(camera.health) : 'Disabled'}</small></td>
                  <td><DataValue value={camera.source_fps} formatter={(value) => formatFps(Number(value))} reason={reasonOr(camera.unavailable_reason)} /></td>
                  <td><DataValue value={camera.analytics_fps} formatter={(value) => formatFps(Number(value))} reason={reasonOr(camera.unavailable_reason)} /></td>
                  <td><DataValue value={camera.last_good_age_ms} formatter={(value) => formatMilliseconds(Number(value))} reason={reasonOr(camera.unavailable_reason)} /></td>
                  <td><DataValue value={camera.backend_queue_ms ?? null} formatter={(value) => formatMilliseconds(Number(value))} reason="Not reported" /></td>
                </tr>
              ))}</tbody>
            </table>
          </div></div>
        </Panel>
      </div>
      <CapabilityPanel snapshot={snapshot} />
      <ArtifactPanel snapshot={snapshot} />
      {environment ? (
        <Panel eyebrow="GET /api/v1/system/environment" title="Environment">
          <details><summary className="ds-footnote" style={{ cursor: 'pointer' }}>Show raw environment probe</summary>
            <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, color: 'var(--text-secondary)', marginTop: 8 }}>{JSON.stringify(environment, null, 2)}</pre>
          </details>
        </Panel>
      ) : errors.environment ? <ErrorState title="Environment probe failed" message={errors.environment} /> : null}
    </>
  );
}
