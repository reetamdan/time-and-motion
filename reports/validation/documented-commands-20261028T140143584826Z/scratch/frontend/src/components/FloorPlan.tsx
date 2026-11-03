import type { KeyboardEvent } from 'react';
import { AlertTriangle } from 'lucide-react';
import type { Floor, Zone } from '../types';
import { formatInteger, titleCase } from '../lib/format';

export function polygonCentroid(points: [number, number][]): [number, number] {
  if (points.length === 0) return [0, 0];
  const total = points.reduce((sum, point) => [sum[0] + point[0], sum[1] + point[1]], [0, 0]);
  return [total[0] / points.length, total[1] / points.length];
}

const points = (polygon: [number, number][]) => polygon.map(([x, y]) => `${x},${y}`).join(' ');

/** Zone colour ramp (design-system .c-* classes): unavailable is gray, never "normal" green. */
export function zoneClass(zone: Zone): string {
  if (zone.congestion === 'SEVERELY CONGESTED') return 'c-red';
  if (zone.congestion === 'CONGESTED' || zone.congestion === 'BUSY') return 'c-amber';
  if (zone.congestion === 'UNAVAILABLE' && zone.occupancy === null) return 'c-gray';
  return zone.kind === 'stairs' || zone.kind === 'entrance' ? 'c-purple' : 'c-teal';
}

export function FloorPlan({ floor, illustrative, selectedZoneId, onSelectZone, showCoverage = true }: {
  floor: Floor;
  illustrative: boolean;
  selectedZoneId?: string | null;
  onSelectZone?: (zone: Zone) => void;
  showCoverage?: boolean;
}) {
  const key = (event: KeyboardEvent<SVGPolygonElement>, zone: Zone) => {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelectZone?.(zone); }
  };
  return (
    <div className="floor-plan" data-testid="floor-plan" data-illustrative={illustrative ? 'true' : 'false'}>
      {illustrative ? <span className="plan-watermark"><AlertTriangle size={12} aria-hidden="true" />ILLUSTRATIVE LAYOUT · NOT SURVEYED</span> : null}
      <svg viewBox={`0 0 ${floor.width} ${floor.height}`} role="group" aria-label={`${floor.name} plan${illustrative ? ' (illustrative, not surveyed)' : ''}`}>
        <rect x="0" y="0" width={floor.width} height={floor.height} fill="var(--plan-bg)" />
        <g className="plan-grid" aria-hidden="true">
          {Array.from({ length: 9 }, (_, i) => <line key={`v${i}`} x1={(i + 1) * floor.width / 10} y1="0" x2={(i + 1) * floor.width / 10} y2={floor.height} />)}
          {Array.from({ length: 5 }, (_, i) => <line key={`h${i}`} x1="0" y1={(i + 1) * floor.height / 6} x2={floor.width} y2={(i + 1) * floor.height / 6} />)}
        </g>
        {showCoverage ? floor.cameras.map((camera) => camera.coverage_polygon.length > 2
          ? <polygon key={`cov-${camera.id}`} className="plan-coverage" points={points(camera.coverage_polygon)}><title>{camera.name} coverage (configured)</title></polygon> : null) : null}
        {floor.zones.map((zone) => {
          const [x, y] = polygonCentroid(zone.polygon);
          return (
            <g key={zone.id}>
              <polygon className={`plan-zone ${zoneClass(zone)}`} points={points(zone.polygon)} data-selected={selectedZoneId === zone.id ? 'true' : 'false'}
                tabIndex={onSelectZone ? 0 : undefined} role={onSelectZone ? 'button' : undefined}
                aria-label={`${zone.name}: ${zone.occupancy === null ? 'occupancy unavailable' : `${zone.occupancy} visible`}, ${titleCase(zone.congestion)}`}
                onClick={() => onSelectZone?.(zone)} onKeyDown={(event) => key(event, zone)}>
                <title>{zone.name}</title>
              </polygon>
              <text x={x} y={y - 6} textAnchor="middle" className="plan-label" pointerEvents="none">{zone.name}</text>
              <text x={x} y={y + 14} textAnchor="middle" className="plan-sub" pointerEvents="none">
                {zone.kind === 'queue' ? `Queue ${zone.queue_size === null ? '—' : formatInteger(zone.queue_size)}` : `Visible ${zone.occupancy === null ? '—' : formatInteger(zone.occupancy)}`}
              </text>
            </g>
          );
        })}
        {floor.walls.map((wall, index) => <line key={`w${index}`} className="plan-wall" x1={wall[0][0]} y1={wall[0][1]} x2={wall[1][0]} y2={wall[1][1]} />)}
        {floor.portals.map((portal) => (
          <g key={portal.id} className="plan-portal" transform={`translate(${portal.position[0]} ${portal.position[1]})`}>
            <title>{portal.name} · {portal.direction}</title>
            <rect x="-13" y="-13" width="26" height="26" rx="6" />
            <text y="5" textAnchor="middle">{portal.direction === 'UP' ? '↑' : portal.direction === 'DOWN' ? '↓' : '↕'}</text>
          </g>
        ))}
        {floor.cameras.map((camera) => (
          <g key={camera.id} className="plan-camera" data-health={camera.health} transform={`translate(${camera.position[0]} ${camera.position[1]})`}>
            <title>{camera.name} · {titleCase(camera.health)} · calibration {titleCase(camera.calibration_status)}</title>
            <circle r="10" />
            <text x="15" y="5" className="plan-sub">{camera.id}</text>
          </g>
        ))}
      </svg>
      <div className="plan-legend" aria-label="Legend">
        <span><i style={{ background: '#5DCAA5' }} />Zone (measured)</span>
        <span><i style={{ background: '#6f6e78' }} />Zone unavailable</span>
        <span><i style={{ background: '#EF9F27' }} />Busy / congested</span>
        <span><i style={{ background: 'transparent', border: '1px dashed var(--color-lilac)' }} />Configured camera coverage</span>
        <span>↕ Stair / portal</span>
      </div>
    </div>
  );
}
