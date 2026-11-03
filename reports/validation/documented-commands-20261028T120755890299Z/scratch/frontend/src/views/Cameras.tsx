import { useMemo, useState } from 'react';
import { Cctv } from 'lucide-react';
import type { Camera, Snapshot } from '../types';
import { Badge, Button, ChipGroup, EmptyState, Panel } from '../design-system/components';
import { CameraTile, cameraFreshness } from '../components/Camera';
import { PageHead } from '../components/Layout';

type HealthFilter = 'ALL' | 'LIVE' | 'STALE' | 'OFFLINE' | 'DISABLED';

export function Cameras({ snapshot, onOpenCamera }: { snapshot: Snapshot; onOpenCamera: (camera: Camera) => void }) {
  const [floor, setFloor] = useState('ALL');
  const [state, setState] = useState<HealthFilter>('ALL');
  const classify = (camera: Camera): HealthFilter => (!camera.enabled ? 'DISABLED' : camera.health === 'OFFLINE' ? 'OFFLINE'
    : cameraFreshness(camera) === 'live' ? 'LIVE' : 'STALE');
  const cameras = useMemo(() => snapshot.cameras.filter((camera) => (floor === 'ALL' || camera.floor_id === floor)
    && (state === 'ALL' || classify(camera) === state)), [floor, state, snapshot.cameras]);
  const count = (value: HealthFilter) => snapshot.cameras.filter((camera) => classify(camera) === value).length;

  return (
    <>
      <PageHead title="Cameras" description="Source media and per-camera perception telemetry. Select a camera for its inspector: timeline, rates, history, and evidence marking."
        actions={<><Badge tone="good" dot>{count('LIVE')} live</Badge><Badge tone="warning">{count('STALE')} stale / idle</Badge><Badge tone="muted">{count('DISABLED')} disabled</Badge></>} />
      <Panel variant="tight">
        <div className="toolbar">
          <span className="toolbar-label">Floor</span>
          <ChipGroup label="Filter by floor" value={floor} onChange={setFloor}
            options={[{ value: 'ALL', label: 'All floors' }, ...snapshot.floors.map((item) => ({ value: item.id, label: item.name, count: snapshot.cameras.filter((camera) => camera.floor_id === item.id).length }))]} />
          <span className="toolbar-label">State</span>
          <ChipGroup<HealthFilter> label="Filter by state" value={state} onChange={setState}
            options={(['ALL', 'LIVE', 'STALE', 'OFFLINE', 'DISABLED'] as HealthFilter[]).map((value) => ({ value, label: value === 'ALL' ? 'All' : value.charAt(0) + value.slice(1).toLowerCase(), count: value === 'ALL' ? undefined : count(value) }))} />
          <span className="ds-footnote" role="status" style={{ margin: 0 }}>Showing {cameras.length} of {snapshot.cameras.length}</span>
        </div>
      </Panel>
      {cameras.length ? (
        <div className="camera-wall">{cameras.map((camera) => <CameraTile key={camera.id} camera={camera} onOpen={onOpenCamera} />)}</div>
      ) : (
        <EmptyState icon={Cctv} title={snapshot.cameras.length ? 'No cameras match these filters' : 'No cameras configured'}
          description={snapshot.cameras.length ? 'Change the floor or state filter.' : 'The local API returned no cameras.'}
          action={snapshot.cameras.length ? <Button size="sm" onClick={() => { setFloor('ALL'); setState('ALL'); }}>Clear filters</Button> : undefined} />
      )}
    </>
  );
}
