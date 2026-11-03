import { useState } from 'react';
import { Layers3, Map as MapIcon } from 'lucide-react';
import type { Snapshot } from '../types';
import { api } from '../lib/api';
import { formatInteger, titleCase } from '../lib/format';
import { useResource } from '../hooks/useResource';
import { Badge, Callout, ChipGroup, DataValue, EmptyState, KeyValue, KeyValueList, Panel, Reason } from '../design-system/components';
import { FloorPlan, zoneClass } from '../components/FloorPlan';
import { PageHead } from '../components/Layout';
import { QualityBadge } from '../components/status';
import { pollKey } from './Overview';

export function Floors({ snapshot }: { snapshot: Snapshot }) {
  const [floorId, setFloorId] = useState(snapshot.floors[0]?.id ?? '');
  const [zoneId, setZoneId] = useState<string | null>(null);
  const [coverage, setCoverage] = useState(true);
  const matrix = useResource(() => api.capabilities(), pollKey(snapshot)).data;
  const floor = snapshot.floors.find((item) => item.id === floorId) ?? snapshot.floors[0];
  if (!floor) return <EmptyState icon={Layers3} title="No floors configured" description="The local API returned no floor geometry; none is invented." />;
  const floorCapability = matrix?.floors?.[floor.id]?.visible_in_zones;
  const illustrative = floorCapability ? floorCapability.fixture_status !== 'SURVEYED'
    : snapshot.notices.some((notice) => notice.title.toLowerCase().includes('illustrative'));
  const zone = floor.zones.find((item) => item.id === zoneId) ?? null;
  const zoneCapability = zone ? matrix?.zones[zone.id] : undefined;

  return (
    <>
      <PageHead title="Floors" description="Per-floor zones, portals, and configured camera coverage. Zone and floor figures are calibrated facility analytics; camera-local counts never appear here." />
      <Panel variant="tight">
        <div className="toolbar">
          <span className="toolbar-label">Floor</span>
          <ChipGroup label="Select floor" value={floor.id} onChange={(value) => { setFloorId(value); setZoneId(null); }}
            options={snapshot.floors.map((item) => ({ value: item.id, label: `L${item.level} · ${item.name}` }))} />
          <label className="toolbar" style={{ gap: 6, fontSize: 'var(--text-sm)' }}><input type="checkbox" checked={coverage} onChange={(event) => setCoverage(event.target.checked)} />Show camera coverage</label>
        </div>
      </Panel>
      {illustrative ? (
        <Callout tone="warning" title="Illustrative layout — not surveyed geometry">
          <p>Walls, zones and coverage are placeholder fixtures. They are not measured, and no figure on this page uses them as real positions until a surveyed plan and camera calibrations are supplied.</p>
        </Callout>
      ) : null}
      <div className="floor-layout">
        <Panel variant="flush">
          <FloorPlan floor={floor} illustrative={illustrative} selectedZoneId={zoneId} onSelectZone={(item) => setZoneId(item.id)} showCoverage={coverage} />
        </Panel>
        <div className="stack">
          <Panel eyebrow={`Level ${floor.level}`} title={floor.name} actions={<QualityBadge quality={floor.quality} />}>
            <KeyValueList>
              <KeyValue label="Visible in zones"><DataValue value={floor.visible_occupancy} formatter={(value) => formatInteger(Number(value))} reason={floorCapability?.reason ?? 'Not measured'} /></KeyValue>
              <KeyValue label="Estimated"><DataValue value={floor.estimated_occupancy} formatter={(value) => formatInteger(Number(value))} reason="No floor estimate is produced without full boundary coverage" /></KeyValue>
            </KeyValueList>
            {floorCapability?.reason ? <Reason>{floorCapability.reason}</Reason> : null}
            <p className="ds-footnote">{floorCapability?.aggregation ?? 'De-duplicated union of zone memberships: a person in two overlapping zones counts once.'}</p>
          </Panel>
          <Panel eyebrow="Zones" title={`${floor.zones.length} on this floor`}>
            {floor.zones.length ? (
              <div className="zone-list">
                {floor.zones.map((item) => (
                  <button key={item.id} type="button" className="zone-item" aria-pressed={zoneId === item.id} onClick={() => setZoneId(item.id)}>
                    <svg width="12" height="12" aria-hidden="true"><rect className={zoneClass(item)} width="12" height="12" rx="3" /></svg>
                    <span><strong>{item.name}</strong><small>{titleCase(item.kind)} · {titleCase(item.congestion)}</small></span>
                    <DataValue value={item.kind === 'queue' ? item.queue_size : item.occupancy} formatter={(value) => formatInteger(Number(value))}
                      reason={matrix?.zones[item.id]?.occupancy.reason ?? matrix?.zones[item.id]?.occupancy.current_reason ?? 'Not measured'} />
                  </button>
                ))}
              </div>
            ) : <EmptyState icon={MapIcon} title="No zones" description="No zone polygons are configured for this floor." compact />}
          </Panel>
          {zone ? (
            <Panel eyebrow={`${titleCase(zone.kind)} zone`} title={zone.name} actions={<Badge tone={zone.congestion === 'UNAVAILABLE' ? 'muted' : zone.congestion === 'NORMAL' ? 'good' : 'warning'}>{titleCase(zone.congestion)}</Badge>}>
              <KeyValueList>
                <KeyValue label="Visible"><DataValue value={zone.occupancy} formatter={(value) => formatInteger(Number(value))} reason={zoneCapability?.occupancy.reason ?? 'Not measured'} /></KeyValue>
                <KeyValue label="Capacity"><DataValue value={zone.capacity} formatter={(value) => formatInteger(Number(value))} reason="No capacity configured" /></KeyValue>
                {zone.kind === 'queue' ? <KeyValue label="Queue"><DataValue value={zone.queue_size} formatter={(value) => formatInteger(Number(value))} reason={zoneCapability?.queue?.reason ?? 'Not measured'} /></KeyValue> : null}
                <KeyValue label="Zone id"><code>{zone.id}</code></KeyValue>
              </KeyValueList>
              {zoneCapability && !zoneCapability.occupancy.available ? <Reason>{zoneCapability.occupancy.reason}</Reason> : null}
              {zoneCapability?.occupancy.available && !zoneCapability.occupancy.current ? <Reason>Configured but not current: {zoneCapability.occupancy.current_reason}</Reason> : null}
            </Panel>
          ) : null}
          <Panel eyebrow="Portals" title="Stairs & entrances">
            {floor.portals.length ? (
              <KeyValueList stack>{floor.portals.map((portal) => <KeyValue key={portal.id} label={portal.id}>{portal.name} · {titleCase(portal.direction)}</KeyValue>)}</KeyValueList>
            ) : <p className="ds-footnote">No portals on this floor.</p>}
            {matrix ? <p className="ds-footnote">{matrix.stair_association.available ? 'Stair association runnable.' : `Stair association unavailable: ${matrix.stair_association.reason}`} Identity continuity: {matrix.stair_association.identity_continuity ?? 'UNKNOWN without re-identification evidence'}.</p> : null}
          </Panel>
        </div>
      </div>
    </>
  );
}
