import { describe, expect, it } from 'vitest';
import { parseHash } from './App';
import { cameraFreshness } from './components/Camera';
import { clampWindow } from './components/CountHistory';
import { zoneClass } from './components/FloorPlan';
import { cameraVisibleTotal, pollKey } from './views/Overview';
import type { Camera, Snapshot, Zone } from './types';

const camera = (patch: Partial<Camera>): Camera => ({
  id: 'cam-01', name: 'c', floor_id: 'floor-1', enabled: true, health: 'CALIBRATION WARNING', calibration_status: 'MISSING',
  calibration_revision: null, source_type: 'file', source_label: 'clip', epoch: 'e', visible_count: 5, source_fps: 30,
  analytics_fps: 30, frame_age_ms: null, frame_age_basis: null, dropped_frames: 0, last_good_age_ms: 100,
  unavailable_reason: null, preview_url: null, ...patch,
});

describe('routing', () => {
  it('parses evidence deep links and falls back to overview', () => {
    expect(parseHash('#evidence?item=abc%2F1')).toEqual({ view: 'evidence', item: 'abc/1' });
    expect(parseHash('#alerts')).toEqual({ view: 'alerts', item: null });
    expect(parseHash('#nope')).toEqual({ view: 'overview', item: null });
  });
});

describe('camera freshness and totals', () => {
  it('treats old or missing values as stale, never as zero', () => {
    expect(cameraFreshness(camera({}))).toBe('live');
    expect(cameraFreshness(camera({ last_good_age_ms: 4000 }))).toBe('stale');
    expect(cameraFreshness(camera({ visible_count: null }))).toBe('stale');
    expect(cameraFreshness(camera({ epoch: null }))).toBe('idle');
  });

  it('sums only fresh cameras and is unavailable when none is fresh', () => {
    expect(cameraVisibleTotal([camera({}), camera({ id: 'b', visible_count: 3 })])).toEqual({ total: 8, fresh: 2, stale: 0 });
    expect(cameraVisibleTotal([camera({ last_good_age_ms: 9000 })])).toEqual({ total: null, fresh: 0, stale: 1 });
    expect(cameraVisibleTotal([camera({ visible_count: 0 })]).total).toBe(0); // a real observed zero stays zero
  });
});

describe('history range window', () => {
  it('clamps a source-time window inside the recorded range', () => {
    expect(clampWindow(100, 30, 0, 50)).toEqual([20, 50]);
    expect(clampWindow(-5, 30, 0, 50)).toEqual([0, 30]);
    expect(clampWindow(40, 120, 0, 50)).toEqual([0, 50]);
  });
});

describe('floor plan colours', () => {
  const zone = (patch: Partial<Zone>): Zone => ({ id: 'z', floor_id: 'f', name: 'z', kind: 'dining', polygon: [], capacity: null,
    occupancy: null, congestion: 'UNAVAILABLE', queue_size: null, ...patch });
  it('never colours an unmeasured zone as normal', () => {
    expect(zoneClass(zone({}))).toBe('c-gray');
    expect(zoneClass(zone({ occupancy: 3, congestion: 'NORMAL' }))).toBe('c-teal');
    expect(zoneClass(zone({ occupancy: 30, congestion: 'CONGESTED' }))).toBe('c-amber');
  });
});

describe('poll key', () => {
  it('changes with the run and with time buckets only', () => {
    const snapshot = (run: string | null, at: string) => ({ run_id: run, run_status: 'RUNNING', generated_at: at }) as Snapshot;
    expect(pollKey(snapshot('r', '2026-10-27T00:00:01Z'))).toBe(pollKey(snapshot('r', '2026-10-27T00:00:04Z')));
    expect(pollKey(snapshot('r', '2026-10-27T00:00:01Z'))).not.toBe(pollKey(snapshot('r', '2026-10-27T00:00:06Z')));
    expect(pollKey(snapshot('r', '2026-10-27T00:00:01Z'))).not.toBe(pollKey(snapshot('s', '2026-10-27T00:00:01Z')));
  });
});
