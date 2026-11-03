import { useEffect, useMemo, useState } from 'react';
import { BarChart3, CloudOff } from 'lucide-react';
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { Camera, CameraCountHistory } from '../types';
import { api } from '../lib/api';
import { formatNumber } from '../lib/format';
import { Badge, EmptyState } from './ui';

interface Point {
  t: number | null;
  mean: number | null;
  max: number | null;
  segment: number;
  frames: number;
}

export type HistoryScopeKind = 'live' | 'previous' | 'none';

/** Says whether persisted history belongs to the camera's live epoch, so old history is never shown as current. */
export function historyScope(history: CameraCountHistory | null, camera: Pick<Camera, 'epoch'>): { kind: HistoryScopeKind; label: string } {
  if (!history?.camera_epoch) return { kind: 'none', label: 'No recorded epoch' };
  const short = history.camera_epoch.slice(0, 8);
  if (camera.epoch && camera.epoch === history.camera_epoch) return { kind: 'live', label: `Current epoch ${short}` };
  return { kind: 'previous', label: `Previous epoch ${short} · not live` };
}

/** Splits segments with a null point so a source-time discontinuity is never drawn as continuous. */
export function historyPoints(history: CameraCountHistory | null): Point[] {
  if (!history) return [];
  const points: Point[] = [];
  let segment: number | null = null;
  for (const sample of history.samples) {
    if (segment !== null && sample.source_segment !== segment) {
      points.push({ t: null, mean: null, max: null, segment: sample.source_segment, frames: 0 });
    }
    segment = sample.source_segment;
    points.push({ t: sample.source_time_s, mean: sample.visible_mean, max: sample.visible_max, segment, frames: sample.frames });
  }
  return points;
}

export function CountHistory({ camera }: { camera: Camera }) {
  const [history, setHistory] = useState<CameraCountHistory | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.cameraCountHistory(camera.id)
      .then((value) => { if (!cancelled) { setHistory(value); setError(null); } })
      .catch((reason: unknown) => { if (!cancelled) setError(reason instanceof Error ? reason.message : 'History request failed'); });
    return () => { cancelled = true; };
    // Refetch when the live snapshot reports newly persisted samples or a new epoch.
  }, [camera.id, camera.epoch, camera.persisted_count_samples]);

  const points = useMemo(() => historyPoints(history), [history]);
  const scope = historyScope(history, camera);
  const segments = new Set(history?.samples.map((sample) => sample.source_segment) ?? []).size;

  return (
    <section className="drawer-section" aria-labelledby="count-history-title" data-testid="count-history"
      data-samples={history?.samples.length ?? 0} data-epoch={history?.camera_epoch ?? ''} data-scope={scope.kind}>
      <h3 id="count-history-title"><BarChart3 size={15} aria-hidden="true" />Visible-count history</h3>
      {history?.camera_epoch ? <Badge tone={scope.kind === 'live' ? 'info' : 'muted'}>{scope.label}</Badge> : null}
      <div className="history-meta">
        <span>Source time (s) · {history ? `${history.sample_seconds} s buckets` : 'loading'}</span>
        <span>{history ? `${history.samples.length} persisted samples${segments > 1 ? ` · ${segments} segments` : ''}` : ''}</span>
      </div>
      {error ? <EmptyState icon={CloudOff} title="History unavailable" description={error} compact /> : null}
      {!error && history && points.length === 0 ? (
        <EmptyState icon={BarChart3} title="No persisted samples" description={history.unavailable_reason ?? 'No samples for this epoch yet.'} compact />
      ) : null}
      {!error && points.length > 0 ? (
        <div className="history-chart history-chart-compact" role="img" aria-label="Mean and maximum tracked people per source-time bucket. Discontinuities are shown as gaps.">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={points} margin={{ top: 12, right: 12, left: 0, bottom: 4 }}>
              <CartesianGrid stroke="#1b2931" strokeDasharray="3 5" vertical={false} />
              <XAxis dataKey="t" type="number" domain={['dataMin', 'dataMax']} tickFormatter={(value: number) => `${formatNumber(value, 0)} s`} stroke="#60717b" tick={{ fill: '#81919a', fontSize: 11 }} tickLine={false} axisLine={false} minTickGap={36} />
              <YAxis stroke="#60717b" tick={{ fill: '#81919a', fontSize: 11 }} tickLine={false} axisLine={false} width={36} allowDecimals={false} domain={[0, 'auto']} />
              <Tooltip
                cursor={{ stroke: '#38505a', strokeDasharray: '3 3' }}
                content={({ active, payload }) => {
                  const point = payload?.[0]?.payload as Point | undefined;
                  if (!active || !point || point.t === null) return null;
                  return (
                    <div className="chart-tooltip">
                      <time>Source {formatNumber(point.t, 2)} s · segment {point.segment}</time>
                      <strong>Mean {point.mean === null ? 'Unavailable' : formatNumber(point.mean, 1)}</strong>
                      <span>Max {point.max === null ? 'Unavailable' : formatNumber(point.max, 0)} · {point.frames} frames</span>
                    </div>
                  );
                }}
              />
              <Line type="linear" dataKey="max" stroke="#3f6f7a" strokeWidth={1} strokeDasharray="4 3" connectNulls={false} dot={false} isAnimationActive={false} />
              <Line type="linear" dataKey="mean" stroke="#6bd8d1" strokeWidth={2} connectNulls={false} dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : null}
      <div className="camera-detail-badges">
        <Badge tone="muted">Solid · mean per bucket</Badge>
        <Badge tone="muted">Dashed · max per bucket</Badge>
      </div>
      <p className="panel-footnote">
        {history?.time_basis ?? 'Source time basis unavailable'}. Counts are tracked detections from a provisional, unevaluated detector and are not
        calibrated facility occupancy.
      </p>
    </section>
  );
}
