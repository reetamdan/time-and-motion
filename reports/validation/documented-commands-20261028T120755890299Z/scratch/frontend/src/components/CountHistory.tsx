import { useEffect, useMemo, useState } from 'react';
import { BarChart3, ChevronLeft, ChevronRight, CloudOff, History } from 'lucide-react';
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { Camera, CameraCountHistory } from '../types';
import { api, errorMessage } from '../lib/api';
import { formatNumber } from '../lib/format';
import { CHART_CONFIG } from '../design-system/chart';
import { Badge, Button, EmptyState, Segmented } from '../design-system/components';

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

/**
 * Inserts a null point at each source-time segment boundary and wherever buckets are missing (bucket
 * index jumps by more than one bin), so discontinuities and periods without frames are drawn as gaps,
 * never interpolated.
 */
export function historyPoints(history: CameraCountHistory | null): Point[] {
  if (!history) return [];
  const points: Point[] = [];
  let segment: number | null = null;
  let previousBucket: number | null = null;
  for (const sample of history.samples) {
    const span = sample.bucket_span ?? 1;
    const newSegment = segment !== null && sample.source_segment !== segment;
    const missing = !newSegment && previousBucket !== null && sample.bucket_index - previousBucket > span;
    if (newSegment || missing) points.push({ t: null, mean: null, max: null, segment: sample.source_segment, frames: 0 });
    segment = sample.source_segment;
    previousBucket = sample.bucket_index;
    points.push({ t: sample.source_time_s, mean: sample.visible_mean, max: sample.visible_max, segment, frames: sample.frames });
  }
  return points;
}

/** "N persisted samples", or the window actually shown when the run is longer than the window. */
export function historyCoverage(history: CameraCountHistory | null): string {
  if (!history) return '';
  const total = history.total_samples ?? history.samples.length;
  const returned = history.returned_samples ?? history.samples.length;
  const factor = history.downsample_factor ?? 1;
  const segments = history.segments ?? new Set(history.samples.map((sample) => sample.source_segment)).size;
  const parts = [returned < total ? `newest ${returned} of ${total} samples` : `${total} persisted sample${total === 1 ? '' : 's'}`];
  if (factor > 1) parts.push(`${factor} buckets per point`);
  if (segments > 1) parts.push(`${segments} segments`);
  return parts.join(' · ');
}

/** Clamp a source-time window of `width` seconds ending at `end` into [first, last]. */
export function clampWindow(end: number, width: number, first: number, last: number): [number, number] {
  const to = Math.min(Math.max(end, first + width), last);
  return [Math.max(first, to - width), to];
}

const WINDOWS = [{ value: '30', label: '30 s' }, { value: '120', label: '2 min' }, { value: '600', label: '10 min' }] as const;

export function CountHistory({ camera }: { camera: Camera }) {
  const [recent, setRecent] = useState<CameraCountHistory | null>(null);
  const [ranged, setRanged] = useState<CameraCountHistory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<'recent' | 'range'>('recent');
  const [width, setWidth] = useState<'30' | '120' | '600'>('120');
  const [windowEnd, setWindowEnd] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.cameraCountHistory(camera.id)
      .then((value) => { if (!cancelled) { setRecent(value); setError(null); } })
      .catch((reason: unknown) => { if (!cancelled) setError(errorMessage(reason)); });
    return () => { cancelled = true; };
    // Refetch when the live snapshot reports newly persisted samples or a new epoch.
  }, [camera.id, camera.epoch, camera.persisted_count_samples]);

  const first = recent?.first_source_s ?? null;
  const last = recent?.last_source_s ?? null;
  const span = Number(width);
  const bounds = mode === 'range' && first !== null && last !== null ? clampWindow(windowEnd ?? last, span, first, last) : null;

  useEffect(() => {
    if (!bounds || !recent?.camera_epoch) { setRanged(null); return; }
    let cancelled = false;
    api.cameraCountHistoryRange(camera.id, recent.camera_epoch, bounds[0], bounds[1])
      .then((value) => { if (!cancelled) { setRanged(value); setError(null); } })
      .catch((reason: unknown) => { if (!cancelled) setError(errorMessage(reason)); });
    return () => { cancelled = true; };
  }, [camera.id, recent?.camera_epoch, bounds?.[0], bounds?.[1]]);

  const history = mode === 'range' ? ranged : recent;
  const points = useMemo(() => historyPoints(history), [history]);
  const scope = historyScope(recent, camera);
  const step = (direction: -1 | 1) => {
    if (!bounds) return;
    setWindowEnd(bounds[1] + direction * span);
  };

  return (
    <section className="inspector-section" aria-labelledby="count-history-title" data-testid="count-history"
      data-samples={history?.samples.length ?? 0} data-total-samples={recent?.total_samples ?? recent?.samples.length ?? 0}
      data-epoch={recent?.camera_epoch ?? ''} data-scope={scope.kind} data-window={mode}>
      <h3 id="count-history-title"><BarChart3 size={15} aria-hidden="true" />Camera-visible track history</h3>
      <div className="inspector-badges">
        {recent?.camera_epoch ? <Badge tone={scope.kind === 'live' ? 'info' : 'muted'}>{scope.label}</Badge> : null}
        <Badge tone="muted">Source time · {recent ? `${recent.sample_seconds} s buckets` : 'loading'}</Badge>
      </div>
      <div className="history-toolbar">
        <Segmented<'recent' | 'range'> label="History window" value={mode} onChange={(next) => { setMode(next); setWindowEnd(null); }}
          options={[{ value: 'recent', label: 'Newest' }, { value: 'range', label: 'Browse range' }]} disabled={first === null} />
        {mode === 'range' ? (
          <div className="toolbar">
            <Segmented<'30' | '120' | '600'> label="Range width" value={width} onChange={setWidth} options={WINDOWS.map((item) => ({ ...item }))} />
            <Button size="sm" icon={ChevronLeft} disabled={!bounds || bounds[0] <= (first ?? 0)} onClick={() => step(-1)} aria-label="Earlier window">Earlier</Button>
            <Button size="sm" disabled={!bounds || bounds[1] >= (last ?? 0)} onClick={() => step(1)} aria-label="Later window">Later <ChevronRight size={14} aria-hidden="true" /></Button>
          </div>
        ) : null}
      </div>
      <div className="history-meta">
        <span>{mode === 'range' && bounds ? `Showing source ${formatNumber(bounds[0], 1)}–${formatNumber(bounds[1], 1)} s of ${formatNumber(first, 1)}–${formatNumber(last, 1)} s` : historyCoverage(recent)}</span>
      </div>
      {error ? <EmptyState icon={CloudOff} title="History unavailable" description={error} compact /> : null}
      {!error && history && points.length === 0 ? (
        <EmptyState icon={History} title="No persisted samples" description={history.unavailable_reason ?? 'No samples in this window.'} compact />
      ) : null}
      {!error && points.length > 0 ? (
        <div className="history-chart" role="img" aria-label="Mean and maximum tracked people per source-time bucket. Discontinuities and missing buckets are gaps.">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={points} margin={{ top: 10, right: 12, left: 0, bottom: 4 }}>
              <CartesianGrid {...CHART_CONFIG.grid} />
              <XAxis dataKey="t" type="number" domain={['dataMin', 'dataMax']} tickFormatter={(value: number) => `${formatNumber(value, 0)} s`} {...CHART_CONFIG.axis} minTickGap={36} />
              <YAxis {...CHART_CONFIG.axis} width={34} allowDecimals={false} domain={[0, 'auto']} />
              <Tooltip
                cursor={{ stroke: 'var(--border-hover)', strokeDasharray: '3 3' }}
                content={({ active, payload }) => {
                  const point = payload?.[0]?.payload as Point | undefined;
                  if (!active || !point || point.t === null) return null;
                  return (
                    <div className="chart-tooltip" style={CHART_CONFIG.tooltipStyle}>
                      <time>Source {formatNumber(point.t, 2)} s · segment {point.segment}</time>
                      <strong>Mean {point.mean === null ? 'Unavailable' : formatNumber(point.mean, 1)}</strong>
                      <span>Max {point.max === null ? 'Unavailable' : formatNumber(point.max, 0)} · {point.frames} frames</span>
                    </div>
                  );
                }}
              />
              <Line type="linear" dataKey="max" stroke="var(--chart-series-2)" strokeWidth={1} strokeDasharray="4 3" {...CHART_CONFIG.line} />
              <Line type="linear" dataKey="mean" stroke="var(--chart-series-1)" strokeWidth={2} {...CHART_CONFIG.line} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : null}
      <div className="inspector-badges"><Badge tone="muted">Solid · mean per bucket</Badge><Badge tone="muted">Dashed · max per bucket</Badge></div>
      <p className="ds-footnote">{recent?.time_basis ?? 'Source time basis unavailable'}. Counts are camera-local tracked detections from the selected detector profile, not calibrated facility occupancy.</p>
    </section>
  );
}
