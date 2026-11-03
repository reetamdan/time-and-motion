import type { ReactNode } from 'react';
import {
  Activity,
  ArrowDownLeft,
  ArrowRight,
  ArrowUpRight,
  BellRing,
  Camera,
  Cpu,
  DoorOpen,
  Gauge,
  GitCompareArrows,
  Layers3,
  MemoryStick,
  MoveVertical,
  Timer,
  UsersRound,
  Video,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { Camera as CameraRecord, Snapshot } from '../types';
import { formatBytes, formatInteger, formatNumber, formatPercent, formatSeconds, formatTimestamp, reasonOr } from '../lib/format';
import { AlertCard } from '../components/Alert';
import { CameraTile } from '../components/Camera';
import { FloorOccupancy, FloorPlan } from '../components/FloorPlan';
import { NoSnapshot } from '../components/NoSnapshot';
import { PageHeader } from '../components/PageHeader';
import { Badge, DataValue, EmptyState, KeyValue, Meter, Panel, QualityBadge, Reason } from '../components/ui';

function KpiCard({ title, icon: Icon, children, badge, className = '' }: {
  title: string;
  icon: LucideIcon;
  children: ReactNode;
  badge?: ReactNode;
  className?: string;
}) {
  return (
    <article className={`kpi-card ${className}`}>
      <header><span className="kpi-icon"><Icon size={16} aria-hidden="true" /></span><h2>{title}</h2>{badge}</header>
      {children}
    </article>
  );
}

function PerformanceMini({ snapshot }: { snapshot: Snapshot }) {
  const metrics = snapshot.system_metrics;
  const reason = reasonOr(metrics.unavailable_reason);
  const vramMax = metrics.vram_total_mib ?? 100;
  return (
    <Panel
      title="System performance"
      eyebrow="LIVE HOST PROBE"
      className="performance-panel"
      action={<Badge tone={metrics.scope === 'unavailable' ? 'muted' : 'info'} icon={Activity}>{metrics.scope === 'unavailable' ? 'Unavailable' : metrics.scope}</Badge>}
    >
      <div className="performance-host">
        <span><Cpu size={14} aria-hidden="true" />{metrics.gpu_name ?? 'GPU name unavailable'}</span>
        <time dateTime={metrics.timestamp}>{formatTimestamp(metrics.timestamp)}</time>
      </div>
      <div className="performance-list">
        <div className="performance-row">
          <div><span>GPU utilization</span><strong><DataValue value={metrics.gpu_utilization} formatter={(value) => formatPercent(Number(value))} reason={reason} /></strong></div>
          <Meter value={metrics.gpu_utilization} />
        </div>
        <div className="performance-row">
          <div><span>CPU utilization</span><strong><DataValue value={metrics.cpu_utilization} formatter={(value) => formatPercent(Number(value))} reason={reason} /></strong></div>
          <Meter value={metrics.cpu_utilization} />
        </div>
        <div className="performance-row">
          <div><span>NVDEC</span><strong><DataValue value={metrics.nvdec_utilization} formatter={(value) => formatPercent(Number(value))} reason={reason} /></strong></div>
          <Meter value={metrics.nvdec_utilization} />
        </div>
        <div className="performance-row">
          <div><span>VRAM</span><strong>{metrics.vram_used_mib === null ? <DataValue value={null} reason={reason} /> : `${formatBytes(metrics.vram_used_mib)}${metrics.vram_total_mib === null ? '' : ` / ${formatBytes(metrics.vram_total_mib)}`}`}</strong></div>
          <Meter value={metrics.vram_total_mib === null ? null : metrics.vram_used_mib} max={vramMax} />
        </div>
      </div>
      {(metrics.gpu_name === null || metrics.gpu_utilization === null || metrics.cpu_utilization === null || metrics.nvdec_utilization === null || metrics.vram_used_mib === null || metrics.vram_total_mib === null) ? <Reason>{reason}</Reason> : null}
    </Panel>
  );
}

export function Overview({ snapshot, onOpenCamera, onViewChange, snapshotError }: {
  snapshot: Snapshot | null;
  onOpenCamera: (camera: CameraRecord) => void;
  onViewChange: (view: 'cameras' | 'floors' | 'alerts' | 'system') => void;
  snapshotError?: string;
}) {
  if (!snapshot) {
    return <><PageHeader eyebrow="OPERATIONS / OVERVIEW" title="Facility command" description="A single local view of occupancy, flow, perception health, and operational risk." /><NoSnapshot error={snapshotError} /></>;
  }

  const { kpis } = snapshot;
  const occupancyReason = 'No building occupancy measurement was reported by the local API.';
  const flowReason = 'No entrance flow measurements were reported for this run.';
  const queueReason = 'No queue measurements were reported for this run.';
  const stairReason = 'No stair-flow measurements were reported for this run.';
  const transitionReason = 'No cross-camera transition reconciliation was reported.';
  const activeAlerts = snapshot.alerts
    .filter((alert) => alert.status === 'OPEN')
    .sort((a, b) => new Date(b.triggered_at).getTime() - new Date(a.triggered_at).getTime())
    .slice(0, 4);

  return (
    <div className="page-stack overview-page">
      <PageHeader
        eyebrow="OPERATIONS / OVERVIEW"
        title="Facility command"
        description="Occupancy, movement, and perception health — reconciled from local observations only."
        actions={<div className="heading-badges"><QualityBadge quality={kpis.quality} /><Badge tone="neutral" icon={Layers3}>{snapshot.floors.length} floor{snapshot.floors.length === 1 ? '' : 's'}</Badge></div>}
      />

      <section className="kpi-grid" aria-label="Facility key performance indicators">
        <KpiCard title="Building occupancy" icon={UsersRound} badge={<QualityBadge quality={kpis.quality} />} className="kpi-occupancy">
          <div className="occupancy-layout">
            <div className="occupancy-expected"><DataValue value={kpis.building_expected} formatter={(value) => formatInteger(Number(value))} reason={occupancyReason} /><span>Expected in building</span></div>
            <div className="occupancy-semantics">
              <div><span>Visible</span><strong><DataValue value={kpis.building_visible} formatter={(value) => formatInteger(Number(value))} reason={occupancyReason} /></strong><small>Direct observations</small></div>
              <div><span>Estimated</span><strong><DataValue value={kpis.building_estimated} formatter={(value) => formatInteger(Number(value))} reason={occupancyReason} /></strong><small>API estimate</small></div>
            </div>
          </div>
          {(kpis.building_expected === null || kpis.building_visible === null || kpis.building_estimated === null) ? <Reason>{occupancyReason}</Reason> : null}
        </KpiCard>

        <KpiCard title="IN / OUT" icon={DoorOpen}>
          <div className="split-metric flow-metric">
            <div><span><ArrowUpRight size={13} aria-hidden="true" />IN</span><strong><DataValue value={kpis.entries} formatter={(value) => formatInteger(Number(value))} reason={flowReason} /></strong></div>
            <div><span><ArrowDownLeft size={13} aria-hidden="true" />OUT</span><strong><DataValue value={kpis.exits} formatter={(value) => formatInteger(Number(value))} reason={flowReason} /></strong></div>
          </div>
          {(kpis.entries === null || kpis.exits === null) ? <Reason>{flowReason}</Reason> : null}
        </KpiCard>

        <KpiCard title="Queue" icon={Timer}>
          <div className="kpi-primary"><DataValue value={kpis.queue_total} formatter={(value) => formatInteger(Number(value))} reason={queueReason} /><span>people waiting</span></div>
          <div className="kpi-secondary"><span>Maximum wait</span><strong><DataValue value={kpis.max_wait_seconds} formatter={(value) => formatSeconds(Number(value))} reason={queueReason} /></strong></div>
          {(kpis.queue_total === null || kpis.max_wait_seconds === null) ? <Reason>{queueReason}</Reason> : null}
        </KpiCard>

        <KpiCard title="Stair flow" icon={MoveVertical}>
          <div className="split-metric">
            <div><span>UP / MIN</span><strong><DataValue value={kpis.stair_up_per_min} formatter={(value) => formatNumber(Number(value))} reason={stairReason} /></strong></div>
            <div><span>DOWN / MIN</span><strong><DataValue value={kpis.stair_down_per_min} formatter={(value) => formatNumber(Number(value))} reason={stairReason} /></strong></div>
          </div>
          {(kpis.stair_up_per_min === null || kpis.stair_down_per_min === null) ? <Reason>{stairReason}</Reason> : null}
        </KpiCard>

        <KpiCard title="Congestion" icon={Gauge}>
          <div className="kpi-primary"><DataValue value={kpis.congested_zones} formatter={(value) => formatInteger(Number(value))} /><span>congested zones</span></div>
          <div className="kpi-secondary"><span>Classification</span><strong>{kpis.congested_zones > 0 ? 'Attention required' : 'None reported'}</strong></div>
        </KpiCard>

        <KpiCard title="Transition reconciliation" icon={GitCompareArrows}>
          <div className="split-metric">
            <div><span>UNRESOLVED</span><strong><DataValue value={kpis.unresolved_transitions} formatter={(value) => formatInteger(Number(value))} reason={transitionReason} /></strong></div>
            <div><span>DISCREPANCY</span><strong><DataValue value={kpis.discrepancy} formatter={(value) => formatNumber(Number(value))} reason={transitionReason} /></strong></div>
          </div>
          {(kpis.unresolved_transitions === null || kpis.discrepancy === null) ? <Reason>{transitionReason}</Reason> : null}
        </KpiCard>

        <KpiCard title="Camera health" icon={Camera}>
          <div className="kpi-primary"><DataValue value={kpis.healthy_cameras} formatter={(value) => formatInteger(Number(value))} /><span>/ {formatInteger(kpis.enabled_cameras)} enabled healthy</span></div>
          <Meter value={kpis.healthy_cameras} max={Math.max(1, kpis.enabled_cameras)} tone={kpis.healthy_cameras < kpis.enabled_cameras ? 'warning' : 'info'} />
          <div className="kpi-secondary"><span>Operational sources</span><strong>{kpis.enabled_cameras === 0 ? 'No enabled cameras' : kpis.healthy_cameras === kpis.enabled_cameras ? 'All reported healthy' : 'Degraded coverage'}</strong></div>
        </KpiCard>
      </section>

      <div className="overview-main-grid">
        <Panel
          title="Live camera coverage"
          eyebrow="SOURCE & ANALYTICS"
          className="overview-cameras-panel"
          action={<button type="button" className="text-button" onClick={() => onViewChange('cameras')}>All cameras <ArrowRight size={13} /></button>}
        >
          {snapshot.cameras.length > 0 ? (
            <div className="camera-grid camera-grid-overview">
              {snapshot.cameras.slice(0, 4).map((camera) => <CameraTile key={camera.id} camera={camera} onOpen={onOpenCamera} />)}
            </div>
          ) : <EmptyState icon={Video} title="No cameras configured" description="The local API returned an empty camera list. No preview placeholders are shown." compact />}
        </Panel>
        <PerformanceMini snapshot={snapshot} />
      </div>

      <div className="overview-secondary-grid">
        <Panel
          title="Facility floors"
          eyebrow="SPATIAL OPERATIONS"
          action={<button type="button" className="text-button" onClick={() => onViewChange('floors')}>Open floor plans <ArrowRight size={13} /></button>}
        >
          {snapshot.floors.length > 0 ? (
            <div className="mini-floor-grid">
              {snapshot.floors.slice(0, 2).map((floor) => (
                <article key={floor.id} className="mini-floor">
                  <header><div><span>LEVEL {floor.level}</span><h3>{floor.name}</h3></div><Badge tone="neutral">{floor.zones.length} zones</Badge></header>
                  <FloorPlan floor={floor} compact />
                  <FloorOccupancy floor={floor} />
                </article>
              ))}
            </div>
          ) : <EmptyState icon={Layers3} title="No floor plans available" description="No floor geometry was returned by the local API." compact />}
        </Panel>

        <Panel
          title="Active alerts"
          eyebrow="REQUIRES ATTENTION"
          action={<button type="button" className="text-button" onClick={() => onViewChange('alerts')}>Alert center <ArrowRight size={13} /></button>}
        >
          {activeAlerts.length > 0 ? <div className="alert-list alert-list-compact">{activeAlerts.map((alert) => <AlertCard key={alert.id} alert={alert} compact />)}</div> : (
            <EmptyState icon={BellRing} title="No active alerts" description="No OPEN alerts were reported in the latest facility snapshot." compact />
          )}
          <div className="panel-bottom-link"><button type="button" className="text-button" onClick={() => onViewChange('system')}><MemoryStick size={13} />Inspect readiness & host metrics <ArrowRight size={13} /></button></div>
        </Panel>
      </div>

      <div className="overview-provenance">
        <KeyValue label="Snapshot"><code>seq {snapshot.sequence} · watermark {snapshot.watermark}</code></KeyValue>
        <span>Generated <time dateTime={snapshot.generated_at}>{formatTimestamp(snapshot.generated_at, 'UTC')}</time></span>
      </div>
    </div>
  );
}
