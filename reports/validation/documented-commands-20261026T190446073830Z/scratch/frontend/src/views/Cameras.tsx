import { useMemo, useState } from 'react';
import { Camera as CameraIcon, Cctv, Filter, ShieldCheck, VideoOff } from 'lucide-react';
import type { Camera, Health, Snapshot } from '../types';
import { titleCase } from '../lib/format';
import { CameraTile } from '../components/Camera';
import { NoSnapshot } from '../components/NoSnapshot';
import { PageHeader } from '../components/PageHeader';
import { Badge, EmptyState } from '../components/ui';

export function Cameras({ snapshot, onOpenCamera, snapshotError }: {
  snapshot: Snapshot | null;
  onOpenCamera: (camera: Camera) => void;
  snapshotError?: string;
}) {
  const [floorFilter, setFloorFilter] = useState('ALL');
  const [healthFilter, setHealthFilter] = useState<Health | 'ALL'>('ALL');
  const cameras = useMemo(() => snapshot?.cameras.filter((camera) =>
    (floorFilter === 'ALL' || camera.floor_id === floorFilter)
    && (healthFilter === 'ALL' || camera.health === healthFilter),
  ) ?? [], [floorFilter, healthFilter, snapshot]);

  if (!snapshot) {
    return <><PageHeader eyebrow="OPERATIONS / SOURCES" title="Live cameras" description="Real local source media and reported perception telemetry." /><NoSnapshot error={snapshotError} /></>;
  }

  const healthy = snapshot.cameras.filter((camera) => camera.health === 'HEALTHY').length;
  const offline = snapshot.cameras.filter((camera) => camera.health === 'OFFLINE').length;
  const enabled = snapshot.cameras.filter((camera) => camera.enabled).length;

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow="OPERATIONS / SOURCES"
        title="Live cameras"
        description="Source media, calibration, and inference telemetry without browser-generated detections."
        actions={<div className="heading-badges"><Badge tone="good" icon={ShieldCheck}>{healthy} healthy</Badge><Badge tone={offline > 0 ? 'danger' : 'muted'} icon={VideoOff}>{offline} offline</Badge></div>}
      />

      <div className="source-summary" aria-label="Camera source summary">
        <div><Cctv size={16} aria-hidden="true" /><span>Configured cameras</span><strong>{snapshot.cameras.length}</strong></div>
        <div><CameraIcon size={16} aria-hidden="true" /><span>Enabled sources</span><strong>{enabled}</strong></div>
        <div><ShieldCheck size={16} aria-hidden="true" /><span>Healthy analytics</span><strong>{healthy}</strong></div>
      </div>

      <div className="filter-bar">
        <span className="filter-label"><Filter size={14} aria-hidden="true" />Filter sources</span>
        <label><span>Floor</span><select value={floorFilter} onChange={(event) => setFloorFilter(event.target.value)}><option value="ALL">All floors</option>{snapshot.floors.map((floor) => <option key={floor.id} value={floor.id}>{floor.name}</option>)}</select></label>
        <label><span>Health</span><select value={healthFilter} onChange={(event) => setHealthFilter(event.target.value as Health | 'ALL')}><option value="ALL">All health states</option>{(['HEALTHY', 'DEGRADED', 'OFFLINE', 'CALIBRATION WARNING'] as Health[]).map((health) => <option key={health} value={health}>{titleCase(health)}</option>)}</select></label>
        <span className="filter-result" role="status">Showing {cameras.length} of {snapshot.cameras.length}</span>
      </div>

      {cameras.length > 0 ? (
        <div className="camera-grid camera-grid-full">{cameras.map((camera) => <CameraTile key={camera.id} camera={camera} onOpen={onOpenCamera} />)}</div>
      ) : (
        <EmptyState icon={Cctv} title={snapshot.cameras.length === 0 ? 'No cameras configured' : 'No sources match these filters'} description={snapshot.cameras.length === 0 ? 'The local API returned an empty camera list.' : 'Change the floor or health filter to inspect other configured sources.'} action={snapshot.cameras.length > 0 ? <button type="button" className="button button-secondary button-small" onClick={() => { setFloorFilter('ALL'); setHealthFilter('ALL'); }}>Clear filters</button> : undefined} />
      )}
    </div>
  );
}
