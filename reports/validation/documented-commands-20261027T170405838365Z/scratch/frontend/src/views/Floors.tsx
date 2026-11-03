import { useState } from 'react';
import type { KeyboardEvent } from 'react';
import { AlertTriangle, Eye, Layers3, Map, ScanLine, UsersRound } from 'lucide-react';
import type { Snapshot } from '../types';
import { formatInteger, titleCase } from '../lib/format';
import { DirectionIcon, FloorOccupancy, FloorPlan, ZoneDetail } from '../components/FloorPlan';
import { NoSnapshot } from '../components/NoSnapshot';
import { PageHeader } from '../components/PageHeader';
import { Badge, DataValue, EmptyState, Panel } from '../components/ui';

export function Floors({ snapshot, snapshotError }: { snapshot: Snapshot | null; snapshotError?: string }) {
  const [activeFloorId, setActiveFloorId] = useState<string | null>(null);
  const [selectedZoneId, setSelectedZoneId] = useState<string | null>(null);
  const [showCoverage, setShowCoverage] = useState(true);

  if (!snapshot) {
    return <><PageHeader eyebrow="OPERATIONS / SPATIAL" title="Floor plans" description="API-provided facility geometry, coverage, and zone measurements." /><NoSnapshot error={snapshotError} /></>;
  }

  const activeFloor = snapshot.floors.find((floor) => floor.id === activeFloorId) ?? snapshot.floors[0] ?? null;
  const selectedZone = activeFloor?.zones.find((zone) => zone.id === selectedZoneId) ?? activeFloor?.zones[0] ?? null;
  const calibrationWarnings = snapshot.floors.reduce((count, floor) => count + floor.cameras.filter((camera) => camera.calibration_status !== 'VALID').length, 0);

  const handleTabKey = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    const direction = event.key === 'ArrowRight' ? 1 : -1;
    const nextIndex = (index + direction + snapshot.floors.length) % snapshot.floors.length;
    const nextFloor = snapshot.floors[nextIndex];
    setActiveFloorId(nextFloor.id);
    setSelectedZoneId(null);
    document.getElementById(`floor-tab-${nextFloor.id}`)?.focus();
  };

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow="OPERATIONS / SPATIAL"
        title="Floor plans"
        description="Zone status and source coverage rendered directly from local facility geometry."
        actions={<div className="heading-badges"><Badge tone="neutral" icon={Layers3}>{snapshot.floors.length} floors</Badge>{calibrationWarnings > 0 ? <Badge tone="warning" icon={AlertTriangle}>{calibrationWarnings} calibration warnings</Badge> : <Badge tone="good" icon={ScanLine}>Calibration valid</Badge>}</div>}
      />

      {activeFloor ? (
        <>
          <div className="floor-toolbar">
            <div className="floor-tabs" role="tablist" aria-label="Facility floors">
              {snapshot.floors.map((floor, index) => (
                <button
                  id={`floor-tab-${floor.id}`}
                  key={floor.id}
                  type="button"
                  role="tab"
                  aria-selected={activeFloor.id === floor.id}
                  aria-controls="floor-plan-panel"
                  tabIndex={activeFloor.id === floor.id ? 0 : -1}
                  className={activeFloor.id === floor.id ? 'floor-tab floor-tab-active' : 'floor-tab'}
                  onClick={() => { setActiveFloorId(floor.id); setSelectedZoneId(null); }}
                  onKeyDown={(event) => handleTabKey(event, index)}
                >
                  <span>LEVEL {floor.level}</span><strong>{floor.name}</strong><small>{floor.zones.length} zones · {floor.cameras.length} cameras</small>
                </button>
              ))}
            </div>
            <label className="coverage-toggle"><input type="checkbox" checked={showCoverage} onChange={(event) => setShowCoverage(event.target.checked)} /><Eye size={14} aria-hidden="true" />Show coverage</label>
          </div>

          <div className="floor-view-grid" id="floor-plan-panel" role="tabpanel" aria-labelledby={`floor-tab-${activeFloor.id}`}>
            <Panel className="floor-canvas-panel">
              <div className="floor-canvas-heading">
                <div><p className="eyebrow">FLOOR {activeFloor.level} / LIVE GEOMETRY</p><h2>{activeFloor.name}</h2></div>
                <FloorOccupancy floor={activeFloor} />
              </div>
              <FloorPlan floor={activeFloor} selectedZone={selectedZone} onZoneSelect={(zone) => setSelectedZoneId(zone.id)} showCoverage={showCoverage} />
              <p className="plan-disclaimer">Polygons and positions are API data. The browser does not draw people, detections, or inferred tracks.</p>
            </Panel>

            <aside className="floor-inspector" aria-label="Floor details">
              <Panel title="Zone inspector" eyebrow="SELECT ON MAP OR LIST">
                {selectedZone ? <ZoneDetail zone={selectedZone} /> : <EmptyState icon={Map} title="No zone selected" description="Select a reported zone to inspect its measurements." compact />}
              </Panel>
              <Panel title={`Zones · ${activeFloor.zones.length}`} className="zone-list-panel">
                {activeFloor.zones.length > 0 ? (
                  <div className="zone-list">
                    {activeFloor.zones.map((zone) => (
                      <button type="button" key={zone.id} className={selectedZone?.id === zone.id ? 'zone-list-item zone-list-item-active' : 'zone-list-item'} onClick={() => setSelectedZoneId(zone.id)}>
                        <span className={`zone-list-swatch congestion-bg-${zone.congestion.toLowerCase().replaceAll(' ', '-')}`} aria-hidden="true" />
                        <span><strong>{zone.name}</strong><small>{titleCase(zone.kind)} · {titleCase(zone.congestion)}</small></span>
                        <span className="zone-list-count"><UsersRound size={12} aria-hidden="true" /><DataValue value={zone.occupancy} formatter={(value) => formatInteger(Number(value))} reason="No zone occupancy measurement was reported." /></span>
                      </button>
                    ))}
                  </div>
                ) : <EmptyState title="No zones reported" description="This floor has no zone polygons in the local API response." compact />}
              </Panel>
              <Panel title="Entrances & stairs" eyebrow="REPORTED PORTALS">
                {activeFloor.portals.length > 0 ? <div className="portal-list">{activeFloor.portals.map((portal) => <div key={portal.id}><span><DirectionIcon direction={portal.direction} /></span><div><strong>{portal.name}</strong><small>{portal.id} · {titleCase(portal.direction)}</small></div></div>)}</div> : <p className="muted-copy">No portals were reported for this floor.</p>}
              </Panel>
            </aside>
          </div>
        </>
      ) : <EmptyState icon={Layers3} title="No floor plans available" description="The local API returned no floors. No illustrative geometry has been invented." />}
    </div>
  );
}
