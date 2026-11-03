import { ArrowRight, BellRing, Cctv, Cpu, DoorOpen, Gauge, Layers3, MoveVertical, ScanEye, Timer, UsersRound } from 'lucide-react';
import type { CapabilityMatrix, Camera, Snapshot, VersionsResponse, ViewId } from '../types';
import { api } from '../lib/api';
import { formatBytes, formatInteger, formatNumber, formatPercent, formatSeconds, formatTimestamp, reasonOr, titleCase } from '../lib/format';
import { useResource } from '../hooks/useResource';
import { Badge, Button, Callout, EmptyState, LoadingState, Meter, Metric, Panel } from '../design-system/components';
import { CameraTile, cameraFreshness } from '../components/Camera';
import { PageHead } from '../components/Layout';
import { AcceptanceBadge, SeverityBadge } from '../components/status';

/** A coarse refresh key: run identity plus a 5 s bucket of the snapshot clock. */
export function pollKey(snapshot: Snapshot | null, seconds = 5): string {
  const bucket = snapshot ? Math.floor(new Date(snapshot.generated_at).getTime() / (seconds * 1000)) : 0;
  return `${snapshot?.run_id ?? ''}:${snapshot?.run_status ?? ''}:${bucket}`;
}

/** Sum of camera-local visible tracks over cameras with a fresh value; null when none is fresh. */
export function cameraVisibleTotal(cameras: Camera[]): { total: number | null; fresh: number; stale: number } {
  const fresh = cameras.filter((camera) => cameraFreshness(camera) === 'live' && camera.visible_count !== null);
  const stale = cameras.filter((camera) => cameraFreshness(camera) === 'stale').length;
  return { total: fresh.length ? fresh.reduce((sum, camera) => sum + (camera.visible_count ?? 0), 0) : null, fresh: fresh.length, stale };
}

function reasonFor(matrix: CapabilityMatrix | null, kind: 'building' | 'entries' | 'queue' | 'stairs' | 'zones'): string {
  if (!matrix) return 'Capability matrix not loaded.';
  if (kind === 'building' || kind === 'entries') return matrix.building_estimate.reason ?? 'No boundary measurement reported for this run.';
  if (kind === 'stairs') return matrix.stair_association.reason ?? 'No stair transition measured in the flow window.';
  const zones = Object.entries(matrix.zones);
  const queue = zones.find(([, zone]) => zone.kind === 'queue');
  const entry = kind === 'queue' ? queue?.[1].queue : zones[0]?.[1].occupancy;
  return entry?.reason ?? entry?.current_reason ?? 'No zone measurement reported.';
}

function FacilityMetrics({ snapshot, matrix }: { snapshot: Snapshot; matrix: CapabilityMatrix | null }) {
  const { kpis } = snapshot;
  return (
    <Panel eyebrow="Facility metrics · calibrated" title="Occupancy, flow and queues"
      description="These need runtime-valid calibration and fresh cameras. Unavailable values show their reason; they are never shown as 0."
      actions={<Badge tone={kpis.quality === 'UNAVAILABLE' ? 'muted' : 'info'}>{titleCase(kpis.quality)}</Badge>} testId="facility-metrics">
      <div className="metric-grid">
        <div className="metric-tile" data-available={kpis.building_expected === null ? 'false' : 'true'}>
          <Metric label="Expected in building" icon={UsersRound} value={kpis.building_expected} formatter={(value) => formatInteger(Number(value))}
            reason={reasonFor(matrix, 'building')} sub={kpis.building_visible !== null ? `${formatInteger(kpis.building_visible)} visible in zones` : undefined} />
        </div>
        <div className="metric-tile" data-available={kpis.entries === null ? 'false' : 'true'}>
          <Metric label="Entries / exits" icon={DoorOpen} value={kpis.entries === null || kpis.exits === null ? null : `${formatInteger(kpis.entries)} / ${formatInteger(kpis.exits)}`}
            reason={reasonFor(matrix, 'entries')} />
        </div>
        <div className="metric-tile" data-available={kpis.queue_total === null ? 'false' : 'true'}>
          <Metric label="Queue" icon={Timer} value={kpis.queue_total} formatter={(value) => formatInteger(Number(value))} unit="waiting"
            reason={reasonFor(matrix, 'queue')} sub={kpis.max_wait_seconds !== null ? `max wait ${formatSeconds(kpis.max_wait_seconds)}` : undefined} />
        </div>
        <div className="metric-tile" data-available={kpis.stair_up_per_min === null && kpis.stair_down_per_min === null ? 'false' : 'true'}>
          <Metric label="Stair flow / min" icon={MoveVertical}
            value={kpis.stair_up_per_min === null && kpis.stair_down_per_min === null ? null : `↑${formatNumber(kpis.stair_up_per_min)} ↓${formatNumber(kpis.stair_down_per_min)}`}
            reason={reasonFor(matrix, 'stairs')} sub="inferred from portal-zone arrivals" />
        </div>
        <div className="metric-tile" data-available={matrix && Object.values(matrix.zones).some((zone) => zone.congestion.available) ? 'true' : 'false'}>
          <Metric label="Congested zones" icon={Gauge}
            value={matrix && Object.values(matrix.zones).some((zone) => zone.congestion.available) ? kpis.congested_zones : null}
            formatter={(value) => formatInteger(Number(value))} reason={reasonFor(matrix, 'zones')} />
        </div>
        <div className="metric-tile" data-available={kpis.unresolved_transitions === null ? 'false' : 'true'}>
          <Metric label="Unresolved transitions" icon={Layers3} value={kpis.unresolved_transitions} formatter={(value) => formatInteger(Number(value))}
            reason="No stair transition has been observed in this run." sub="UNKNOWN identity continuity is never forced" />
        </div>
      </div>
    </Panel>
  );
}

function DetectionsPanel({ snapshot }: { snapshot: Snapshot }) {
  const running = snapshot.cameras.filter((camera) => camera.epoch);
  const summary = cameraVisibleTotal(running);
  return (
    <Panel eyebrow="Camera-visible detections" title="Tracked people in view" testId="camera-visible-panel">
      <Metric label="Sum over fresh cameras" icon={ScanEye} value={summary.total} formatter={(value) => formatInteger(Number(value))}
        reason={running.length ? 'Every running camera is stale or has no current observation.' : 'No camera has an active epoch. Start the demo.'}
        sub={`${summary.fresh} fresh${summary.stale ? ` · ${summary.stale} stale (excluded)` : ''}`} />
      <div className="telemetry-row" style={{ marginTop: 12 }}>
        {snapshot.cameras.filter((camera) => camera.enabled).map((camera) => (
          <div key={camera.id}><span>{camera.id}</span><strong>{cameraFreshness(camera) === 'live' && camera.visible_count !== null ? formatInteger(camera.visible_count) : 'Unavailable'}</strong></div>
        ))}
      </div>
      <p className="ds-footnote">Camera-local tracks from the selected detector. Overlapping views may see the same person; this is not occupancy.</p>
    </Panel>
  );
}

function StatusPanel({ snapshot, versions, onOpen }: { snapshot: Snapshot; versions: VersionsResponse | null; onOpen: (view: ViewId) => void }) {
  const enabled = snapshot.cameras.filter((camera) => camera.enabled);
  const calibrated = enabled.filter((camera) => camera.calibration_status === 'VALID').length;
  const illustrative = snapshot.notices.some((notice) => notice.title.toLowerCase().includes('illustrative'));
  return (
    <Panel eyebrow="Detector & calibration" title="Measurement status" testId="measurement-status"
      actions={<Button size="sm" variant="ghost" onClick={() => onOpen('system')}>System <ArrowRight size={13} aria-hidden="true" /></Button>}>
      <div className="telemetry-row">
        <div><span>Detector</span><strong>{String(versions?.selected_model ?? 'Unavailable')}</strong></div>
        <div><span>Acceptance</span><AcceptanceBadge acceptance={versions?.model_acceptance as string | undefined} /></div>
        <div><span>Calibrated cameras</span><strong>{calibrated} / {enabled.length}</strong></div>
        <div><span>Floor geometry</span>{illustrative ? <Badge tone="warning">Illustrative</Badge> : <Badge tone="good">Surveyed</Badge>}</div>
      </div>
      {calibrated < enabled.length ? <p className="ds-footnote">Uncalibrated cameras give camera-local counts only; facility metrics stay unavailable.</p> : null}
    </Panel>
  );
}

function AlertsSummary({ snapshot, onOpen }: { snapshot: Snapshot; onOpen: (view: ViewId) => void }) {
  const open = snapshot.alerts.filter((alert) => alert.status === 'OPEN');
  const latest = [...open].sort((a, b) => b.triggered_at.localeCompare(a.triggered_at)).slice(0, 3);
  return (
    <Panel eyebrow="Alerts" title={`${open.length} open`} actions={<Button size="sm" variant="ghost" onClick={() => onOpen('alerts')}>Alert center <ArrowRight size={13} aria-hidden="true" /></Button>}>
      {latest.length ? (
        <div className="alert-list">{latest.map((alert) => (
          <div key={alert.id} className="alert-row alert-compact" data-severity={alert.severity}>
            <div className="alert-row-head"><SeverityBadge severity={alert.severity} /><span className="ds-footnote" style={{ margin: 0 }}>{formatTimestamp(alert.triggered_at)}</span></div>
            <h3>{alert.title}</h3>
          </div>
        ))}</div>
      ) : <EmptyState icon={BellRing} title="No open alerts" description="Nothing requires attention in the latest snapshot." compact />}
    </Panel>
  );
}

function EvidenceSummary({ snapshot, onOpen }: { snapshot: Snapshot; onOpen: (view: ViewId) => void }) {
  const { data, error } = useResource(() => api.evidence(undefined, 6), pollKey(snapshot, 15));
  const groups = new Set(data?.items.map((item) => item.group_id ?? item.id));
  return (
    <Panel eyebrow="Evidence" title={data ? `${groups.size} marker${groups.size === 1 ? '' : 's'} recently` : 'Evidence'}
      actions={<Button size="sm" variant="ghost" onClick={() => onOpen('evidence')}>Open <ArrowRight size={13} aria-hidden="true" /></Button>}>
      {error ? <Callout tone="warning" title="Evidence list unavailable"><p>{error}</p></Callout> : null}
      {data ? (
        <div className="telemetry-row">
          <div><span>Stored</span><strong>{data.usage.records} / {data.usage.max_records} files</strong></div>
          <div><span>Size</span><strong>{formatNumber(data.usage.bytes / 1048576, 1)} MiB</strong></div>
          <div><span>Extraction</span>{data.capability.available ? <Badge tone="good">Available</Badge> : <Badge tone="warning" title={data.capability.unavailable_reason ?? undefined}>Unavailable</Badge>}</div>
        </div>
      ) : !error ? <LoadingState rows={2} /> : null}
      <p className="ds-footnote">Evidence is operator-marked from a camera inspector; nothing is generated automatically for this clip.</p>
    </Panel>
  );
}

function HostPanel({ snapshot }: { snapshot: Snapshot }) {
  const metrics = snapshot.system_metrics;
  const reason = reasonOr(metrics.unavailable_reason);
  const rows: Array<[string, number | null, number | null, string]> = [
    ['GPU', metrics.gpu_utilization, 100, formatPercent(metrics.gpu_utilization)],
    ['NVDEC', metrics.nvdec_utilization, 100, formatPercent(metrics.nvdec_utilization)],
    ['VRAM', metrics.vram_used_mib, metrics.vram_total_mib, metrics.vram_used_mib === null ? 'Unavailable' : `${formatBytes(metrics.vram_used_mib)}`],
    ['CPU', metrics.cpu_utilization, 100, formatPercent(metrics.cpu_utilization)],
  ];
  return (
    <Panel eyebrow={`Host · ${metrics.scope}`} title={metrics.gpu_name ?? 'GPU unavailable'} actions={<Cpu size={16} aria-hidden="true" />}>
      <div className="telemetry-row">
        {rows.map(([label, value, max, text]) => (
          <div key={label} style={{ display: 'grid', gridTemplateColumns: '64px 1fr auto', alignItems: 'center' }}>
            <span>{label}</span><Meter value={value} max={max} /><strong title={value === null ? reason : undefined} style={{ minWidth: 76, textAlign: 'right' }}>{value === null ? 'Unavailable' : text}</strong>
          </div>
        ))}
      </div>
    </Panel>
  );
}

export function Overview({ snapshot, onOpenCamera, onViewChange, versions }: {
  snapshot: Snapshot;
  onOpenCamera: (camera: Camera) => void;
  onViewChange: (view: ViewId) => void;
  versions: VersionsResponse | null;
}) {
  const matrix = useResource(() => api.capabilities(), pollKey(snapshot));
  const enabled = snapshot.cameras.filter((camera) => camera.enabled);
  const shown = enabled.length ? enabled : snapshot.cameras;
  return (
    <>
      <PageHead title="Operations overview" description="Live camera feeds first; calibrated facility metrics and system state alongside. Every value comes from the local API."
        actions={<Badge tone="neutral" icon={Layers3}>{snapshot.floors.length} floors · {snapshot.cameras.length} cameras</Badge>} />
      <div className="overview-grid">
        <div className="stack">
          <Panel eyebrow="Live feeds" title="Camera wall" actions={<Button size="sm" variant="ghost" onClick={() => onViewChange('cameras')}>All cameras <ArrowRight size={13} aria-hidden="true" /></Button>}>
            {shown.length ? <div className="camera-wall">{shown.map((camera) => <CameraTile key={camera.id} camera={camera} onOpen={onOpenCamera} />)}</div>
              : <EmptyState icon={Cctv} title="No cameras configured" description="The local API returned no cameras. Nothing is simulated." />}
            {enabled.length < snapshot.cameras.length ? <p className="ds-footnote">{snapshot.cameras.length - enabled.length} configured camera(s) disabled — see Cameras.</p> : null}
          </Panel>
          <FacilityMetrics snapshot={snapshot} matrix={matrix.data} />
        </div>
        <div className="stack">
          <DetectionsPanel snapshot={snapshot} />
          <StatusPanel snapshot={snapshot} versions={versions} onOpen={onViewChange} />
          <AlertsSummary snapshot={snapshot} onOpen={onViewChange} />
          <EvidenceSummary snapshot={snapshot} onOpen={onViewChange} />
          <HostPanel snapshot={snapshot} />
        </div>
      </div>
      <p className="ds-footnote">Snapshot seq {snapshot.sequence} · generated {formatTimestamp(snapshot.generated_at, 'UTC')} UTC</p>
    </>
  );
}
