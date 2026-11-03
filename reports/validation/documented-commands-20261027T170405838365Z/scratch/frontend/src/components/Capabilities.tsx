import { useEffect, useState } from 'react';
import { CheckCircle2, Fingerprint, Layers, XCircle } from 'lucide-react';
import type { ArtifactsResponse, CapabilityEntry, CapabilityMatrix } from '../types';
import { api, errorMessage } from '../lib/api';
import { titleCase } from '../lib/format';
import { Badge, EmptyState, Panel } from './ui';

const CAMERA_FEATURES: Array<[keyof CapabilityMatrix['cameras'][string], string]> = [
  ['camera_local_counts', 'Camera-local counts'],
  ['image_line_crossings', 'Image-line crossings'],
  ['floor_positions', 'Floor positions'],
  ['facility_crossings', 'Entries / exits'],
];

function Cell({ entry }: { entry: CapabilityEntry | undefined }) {
  if (!entry) return <td>–</td>;
  return (
    <td data-available={entry.available ? 'true' : 'false'}>
      {entry.available
        ? <span className="capability-yes"><CheckCircle2 size={14} aria-hidden="true" />Available</span>
        : <span className="capability-no"><XCircle size={14} aria-hidden="true" />{entry.reason ?? 'Unavailable'}</span>}
    </td>
  );
}

/** Which analytics run now, and the exact reason each one that does not is unavailable. */
export function CapabilityPanel({ refreshKey }: { refreshKey: unknown }) {
  const [matrix, setMatrix] = useState<CapabilityMatrix | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    api.capabilities()
      .then((value) => { if (!cancelled) { setMatrix(value); setError(null); } })
      .catch((reason: unknown) => { if (!cancelled) setError(errorMessage(reason)); });
    return () => { cancelled = true; };
  }, [refreshKey]);

  return (
    <Panel title="Analytics capabilities" eyebrow="GET /API/V1/SYSTEM/CAPABILITIES" action={<Badge tone="neutral" icon={Layers}>Calibration-gated</Badge>}>
      {error ? <EmptyState title="Capabilities unavailable" description={error} compact /> : null}
      {matrix ? (
        <div className="table-scroll" data-testid="capability-matrix">
          <table className="data-table">
            <thead><tr><th>Camera</th>{CAMERA_FEATURES.map(([, label]) => <th key={label}>{label}</th>)}</tr></thead>
            <tbody>
              {Object.entries(matrix.cameras).map(([id, camera]) => (
                <tr key={id}><td><strong>{id}</strong><small className="table-subline">{camera.running ? 'Running' : camera.enabled ? 'Enabled, not running' : 'Disabled'}</small></td>
                  {CAMERA_FEATURES.map(([key]) => <Cell key={key} entry={camera[key] as CapabilityEntry} />)}</tr>
              ))}
            </tbody>
          </table>
          <table className="data-table">
            <thead><tr><th>Zone</th><th>Occupancy</th><th>Congestion</th><th>Queue</th></tr></thead>
            <tbody>
              {Object.entries(matrix.zones).map(([id, zone]) => (
                <tr key={id}><td><strong>{id}</strong><small className="table-subline">{titleCase(zone.kind)}</small></td>
                  <Cell entry={zone.occupancy} /><Cell entry={zone.congestion} />{zone.queue ? <Cell entry={zone.queue} /> : <td>Not a queue zone</td>}</tr>
              ))}
              <tr><td><strong>Building estimate</strong></td><Cell entry={matrix.building_estimate} /><td /><td /></tr>
              <tr><td><strong>Stair association</strong></td><Cell entry={matrix.stair_association} /><td /><td /></tr>
            </tbody>
          </table>
        </div>
      ) : null}
    </Panel>
  );
}

/** Recorded expected identity vs recomputed current hash; MATCH only when both exist and agree. */
export function ArtifactPanel({ refreshKey }: { refreshKey: unknown }) {
  const [data, setData] = useState<ArtifactsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    api.artifacts()
      .then((value) => { if (!cancelled) { setData(value); setError(null); } })
      .catch((reason: unknown) => { if (!cancelled) setError(errorMessage(reason)); });
    return () => { cancelled = true; };
  }, [refreshKey]);
  const capability = data?.capability?.cached;
  return (
    <Panel title="Runtime artifact identity" eyebrow="GET /API/V1/SYSTEM/ARTIFACTS" action={<Badge tone="neutral" icon={Fingerprint}>Expected vs current</Badge>}>
      {error ? <EmptyState title="Artifact identities unavailable" description={error} compact /> : null}
      {data && data.artifacts.length === 0 ? <EmptyState title="No artifact identities" description="This perception adapter reports no runtime artifacts." compact /> : null}
      {data && data.artifacts.length > 0 ? (
        <div className="table-scroll" data-testid="artifact-identity">
          <table className="data-table">
            <thead><tr><th>Artifact</th><th>Status</th><th>Expected (recorded)</th><th>Current (recomputed)</th></tr></thead>
            <tbody>
              {data.artifacts.map((item) => (
                <tr key={item.name}><td><strong>{item.name}</strong></td>
                  <td><Badge tone={item.status === 'MATCH' ? 'good' : 'danger'}>{item.status}</Badge></td>
                  <td><code>{item.expected_sha256 ? `${item.expected_sha256.slice(0, 16)}…` : 'none recorded'}</code></td>
                  <td><code>{item.current_sha256 ? `${item.current_sha256.slice(0, 16)}…` : 'unavailable'}</code></td></tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {data?.capability ? (
        <p className="panel-footnote">
          Container capability: {capability ? `${capability.available ? 'validated' : 'failed'} ${capability.validated_at_utc ?? ''} (probe ${capability.probe_seconds ?? '–'} s)` : 'not probed yet'}
          {data.capability.last_invalidation ? ` · last invalidated: ${data.capability.last_invalidation.reason}` : ''}
        </p>
      ) : null}
    </Panel>
  );
}
