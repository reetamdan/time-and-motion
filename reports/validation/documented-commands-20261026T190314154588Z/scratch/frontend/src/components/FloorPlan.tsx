import type { KeyboardEvent } from 'react';
import { AlertTriangle, ArrowDown, ArrowUp, ArrowUpDown, MapPin, UsersRound } from 'lucide-react';
import type { Floor, Zone } from '../types';
import { formatInteger, reasonOr, titleCase } from '../lib/format';
import { Badge, DataValue, KeyValue, QualityBadge, Reason } from './ui';

export function polygonCentroid(points: [number, number][]): [number, number] {
  if (points.length === 0) return [0, 0];
  const total = points.reduce((sum, point) => [sum[0] + point[0], sum[1] + point[1]], [0, 0]);
  return [total[0] / points.length, total[1] / points.length];
}

function pointsAttribute(points: [number, number][]): string {
  return points.map(([x, y]) => `${x},${y}`).join(' ');
}

function zoneFill(zone: Zone): string {
  if (zone.congestion === 'SEVERELY CONGESTED') return '#442127';
  if (zone.congestion === 'CONGESTED') return '#402b20';
  if (zone.congestion === 'BUSY') return '#34301f';
  if (zone.congestion === 'UNAVAILABLE') return '#151e25';
  if (zone.kind === 'entrance' || zone.kind === 'stairs') return '#123036';
  if (zone.kind === 'queue') return '#152c33';
  if (zone.kind === 'blind') return '#202128';
  return '#15262b';
}

function handleZoneKey(event: KeyboardEvent<SVGPolygonElement>, zone: Zone, onSelect?: (zone: Zone) => void) {
  if (!onSelect || (event.key !== 'Enter' && event.key !== ' ')) return;
  event.preventDefault();
  onSelect(zone);
}

export function FloorPlan({ floor, compact = false, selectedZone, onZoneSelect, showCoverage = true }: {
  floor: Floor;
  compact?: boolean;
  selectedZone?: Zone | null;
  onZoneSelect?: (zone: Zone) => void;
  showCoverage?: boolean;
}) {
  const uncalibrated = floor.cameras.length === 0 || floor.cameras.some((camera) => camera.calibration_status !== 'VALID');
  const hasGeometry = floor.zones.length > 0 || floor.walls.length > 0 || floor.cameras.length > 0 || floor.portals.length > 0;

  return (
    <div className={compact ? 'floor-plan floor-plan-compact' : 'floor-plan'}>
      {uncalibrated ? (
        <div className="uncalibrated-label"><AlertTriangle size={12} aria-hidden="true" />ILLUSTRATIVE PLAN · UNCALIBRATED</div>
      ) : (
        <div className="calibrated-label">CALIBRATED GEOMETRY</div>
      )}
      <svg
        viewBox={`0 0 ${floor.width} ${floor.height}`}
        role="group"
        aria-label={`${floor.name} facility plan, ${uncalibrated ? 'uncalibrated' : 'calibrated'}`}
        preserveAspectRatio="xMidYMid meet"
      >
        <title>{floor.name} — geometry supplied by the local API</title>
        <rect className="plan-background" x="0" y="0" width={floor.width} height={floor.height} rx="8" />
        <g className="plan-grid" aria-hidden="true">
          {Array.from({ length: 9 }, (_, index) => <line key={`v-${index}`} x1={(index + 1) * floor.width / 10} y1="0" x2={(index + 1) * floor.width / 10} y2={floor.height} />)}
          {Array.from({ length: 5 }, (_, index) => <line key={`h-${index}`} x1="0" y1={(index + 1) * floor.height / 6} x2={floor.width} y2={(index + 1) * floor.height / 6} />)}
        </g>

        {showCoverage ? (
          <g className="coverage-layer" aria-label="Camera coverage">
            {floor.cameras.map((camera) => camera.coverage_polygon.length > 1 ? (
              <polygon key={`coverage-${camera.id}`} points={pointsAttribute(camera.coverage_polygon)}>
                <title>{camera.name} coverage polygon</title>
              </polygon>
            ) : null)}
          </g>
        ) : null}

        <g className="zone-layer" aria-label="Facility zones">
          {floor.zones.map((zone) => {
            const [labelX, labelY] = polygonCentroid(zone.polygon);
            const selected = selectedZone?.id === zone.id;
            return (
              <g key={zone.id} className={selected ? 'zone-group zone-selected' : 'zone-group'}>
                <polygon
                  points={pointsAttribute(zone.polygon)}
                  fill={zoneFill(zone)}
                  className={`zone-shape zone-kind-${zone.kind}`}
                  tabIndex={onZoneSelect ? 0 : undefined}
                  role={onZoneSelect ? 'button' : undefined}
                  aria-label={onZoneSelect ? `${zone.name}, ${titleCase(zone.congestion)}, occupancy ${zone.occupancy ?? 'unavailable'}, queue ${zone.queue_size ?? 'unavailable'}` : undefined}
                  onClick={() => onZoneSelect?.(zone)}
                  onKeyDown={(event) => handleZoneKey(event, zone, onZoneSelect)}
                >
                  <title>{zone.name} · {titleCase(zone.kind)} · {titleCase(zone.congestion)}</title>
                </polygon>
                <g className="zone-label" pointerEvents="none">
                  <text x={labelX} y={labelY - (compact ? 0 : 15)} textAnchor="middle" className="zone-name">{zone.name}</text>
                  {!compact ? (
                    <>
                      <text x={labelX} y={labelY + 7} textAnchor="middle" className="zone-measure">
                        {zone.kind === 'queue' ? `Queue ${formatInteger(zone.queue_size)}` : `Visible ${formatInteger(zone.occupancy)}`}
                      </text>
                      <text x={labelX} y={labelY + 27} textAnchor="middle" className={`zone-congestion congestion-${zone.congestion.toLowerCase().replaceAll(' ', '-')}`}>
                        {titleCase(zone.congestion)}
                      </text>
                    </>
                  ) : null}
                </g>
              </g>
            );
          })}
        </g>

        <g className="wall-layer" aria-label="Walls">
          {floor.walls.map((wall, index) => (
            <line key={`wall-${index}`} x1={wall[0][0]} y1={wall[0][1]} x2={wall[1][0]} y2={wall[1][1]} />
          ))}
        </g>

        <g className="portal-layer" aria-label="Entrances and stairs">
          {floor.portals.map((portal) => (
            <g key={portal.id} transform={`translate(${portal.position[0]} ${portal.position[1]})`} className="portal-marker">
              <title>{portal.name} · {portal.direction}</title>
              <rect x="-12" y="-12" width="24" height="24" rx="4" />
              <text x="0" y="5" textAnchor="middle">{portal.direction === 'UP' ? '↑' : portal.direction === 'DOWN' ? '↓' : '↕'}</text>
              {!compact ? <text x="18" y="4" className="portal-name">{portal.name}</text> : null}
            </g>
          ))}
        </g>

        <g className="camera-layer" aria-label="Cameras">
          {floor.cameras.map((camera) => (
            <g key={camera.id} transform={`translate(${camera.position[0]} ${camera.position[1]})`} className={`camera-marker camera-${camera.health.toLowerCase().replaceAll(' ', '-')}`}>
              <title>{camera.name} · {titleCase(camera.health)} · Calibration {titleCase(camera.calibration_status)}</title>
              <circle r="12" />
              <circle r="4" className="camera-marker-core" />
              {!compact ? <text x="17" y="4" className="camera-marker-label">{camera.name}</text> : null}
            </g>
          ))}
        </g>

        {!hasGeometry ? <text x={floor.width / 2} y={floor.height / 2} textAnchor="middle" className="no-geometry">No floor geometry was reported</text> : null}
      </svg>
      {!compact ? (
        <div className="plan-legend" aria-label="Floor plan legend">
          <span><i className="legend-zone" />Zone / reported status</span>
          <span><i className="legend-coverage" />Camera coverage</span>
          <span><i className="legend-wall" />Wall</span>
          <span><i className="legend-portal" />Entrance / stairs</span>
          <span><i className="legend-camera" />Camera</span>
        </div>
      ) : null}
    </div>
  );
}

export function ZoneDetail({ zone }: { zone: Zone }) {
  return (
    <div className="zone-detail">
      <div className="zone-detail-heading">
        <span className="zone-detail-icon"><MapPin size={17} aria-hidden="true" /></span>
        <div><p className="eyebrow">{titleCase(zone.kind)} zone</p><h3>{zone.name}</h3></div>
      </div>
      <Badge tone={zone.congestion === 'NORMAL' ? 'good' : zone.congestion === 'UNAVAILABLE' ? 'muted' : 'warning'}>
        {titleCase(zone.congestion)}
      </Badge>
      <dl className="key-value-grid zone-detail-values">
        <KeyValue label="Visible occupancy"><DataValue value={zone.occupancy} formatter={(value) => formatInteger(Number(value))} reason="No zone occupancy measurement was reported." /></KeyValue>
        <KeyValue label="Capacity"><DataValue value={zone.capacity} formatter={(value) => formatInteger(Number(value))} reason="No capacity is configured for this zone." /></KeyValue>
        <KeyValue label="Queue size"><DataValue value={zone.queue_size} formatter={(value) => formatInteger(Number(value))} reason="No queue measurement was reported for this zone." /></KeyValue>
        <KeyValue label="Zone ID"><code>{zone.id}</code></KeyValue>
      </dl>
      {zone.occupancy === null ? <Reason>No zone occupancy measurement was reported by the local API.</Reason> : null}
      {zone.congestion === 'UNAVAILABLE' ? <Reason>Congestion is unavailable because the API did not report a usable zone classification.</Reason> : null}
    </div>
  );
}

export function FloorOccupancy({ floor }: { floor: Floor }) {
  return (
    <div className="floor-occupancy-row">
      <QualityBadge quality={floor.quality} />
      <span><UsersRound size={13} aria-hidden="true" />Visible <DataValue value={floor.visible_occupancy} formatter={(value) => formatInteger(Number(value))} reason="No visible floor occupancy was reported." /></span>
      <span>Estimated <DataValue value={floor.estimated_occupancy} formatter={(value) => formatInteger(Number(value))} reason="No estimated floor occupancy was reported." /></span>
      {floor.quality === 'UNAVAILABLE' ? <span className="floor-quality-reason">{reasonOr(undefined, 'Floor occupancy is unavailable because no perception measurement was reported.')}</span> : null}
    </div>
  );
}

export function DirectionIcon({ direction }: { direction: 'UP' | 'DOWN' | 'BOTH' }) {
  return direction === 'UP' ? <ArrowUp size={14} aria-hidden="true" /> : direction === 'DOWN' ? <ArrowDown size={14} aria-hidden="true" /> : <ArrowUpDown size={14} aria-hidden="true" />;
}
