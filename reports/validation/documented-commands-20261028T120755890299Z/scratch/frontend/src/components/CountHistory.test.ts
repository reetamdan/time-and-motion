import { describe, expect, it } from 'vitest';
import { historyPoints } from './CountHistory';
import type { CameraCountHistory } from '../types';

const sample = (segment: number, t: number, mean: number) => ({
  camera_epoch: 'e', source_segment: segment, bucket_index: Math.floor(t * 2), source_time_s: t, frame_number: null,
  visible_count: Math.round(mean), visible_mean: mean, visible_max: Math.round(mean) + 1, frames: 15, replay_mode: 'paced',
  received_at_utc: '2026-10-26T00:00:00.000Z', count_basis: 'unit',
});

describe('historyPoints', () => {
  it('inserts a null gap at each source-time segment boundary and never invents zeros', () => {
    const history: CameraCountHistory = {
      camera_id: 'cam-01', run_id: 'r', camera_epoch: 'e', time_basis: 'source', receive_time_basis: 'receive',
      sample_seconds: 0.5, unavailable_reason: null,
      samples: [sample(0, 0, 3), sample(0, 0.5, 4), sample(1, 0, 2)],
    };
    const points = historyPoints(history);
    expect(points.map((point) => point.mean)).toEqual([3, 4, null, 2]);
    expect(points[2].t).toBeNull();
  });

  it('returns no points without history', () => {
    expect(historyPoints(null)).toEqual([]);
  });
});

import { historyScope } from './CountHistory';

describe('historyScope', () => {
  const history = (epoch: string | null): CameraCountHistory => ({
    camera_id: 'cam-01', run_id: 'r', camera_epoch: epoch, time_basis: 's', receive_time_basis: 'r',
    sample_seconds: 0.5, unavailable_reason: null, samples: [],
  });
  it('labels history from the live epoch as current', () => {
    expect(historyScope(history('abcdef0123'), { epoch: 'abcdef0123' }).kind).toBe('live');
  });
  it('never labels a stopped or replaced epoch as current', () => {
    expect(historyScope(history('abcdef0123'), { epoch: null })).toEqual({ kind: 'previous', label: 'Previous epoch abcdef01 · not live' });
    expect(historyScope(history('abcdef0123'), { epoch: 'ffff' }).kind).toBe('previous');
    expect(historyScope(history(null), { epoch: 'ffff' }).kind).toBe('none');
  });
});

import { historyCoverage } from './CountHistory';

describe('long-run history', () => {
  const base = (samples: ReturnType<typeof sample>[], extra: Partial<CameraCountHistory> = {}): CameraCountHistory => ({
    camera_id: 'cam-01', run_id: 'r', camera_epoch: 'e', time_basis: 's', receive_time_basis: 'r',
    sample_seconds: 0.5, unavailable_reason: null, samples, ...extra,
  });

  it('draws a gap where buckets are missing (stale period) instead of interpolating', () => {
    const points = historyPoints(base([sample(0, 0, 3), sample(0, 0.5, 3), sample(0, 5, 1)]));
    expect(points.map((point) => point.mean)).toEqual([3, 3, null, 1]);
  });

  it('treats consecutive downsampled bins as continuous but gaps beyond a bin span as missing', () => {
    const bin = (bucket: number) => ({ ...sample(0, bucket / 2, 2), bucket_index: bucket, bucket_span: 4, buckets_present: 4 });
    expect(historyPoints(base([bin(0), bin(4), bin(8)])).map((p) => p.mean)).toEqual([2, 2, 2]);
    expect(historyPoints(base([bin(0), bin(4), bin(20)])).map((p) => p.mean)).toEqual([2, 2, null, 2]);
  });

  it('says when only the newest window of a longer history is shown', () => {
    expect(historyCoverage(base([], { total_samples: 5000, returned_samples: 2400, downsample_factor: 4, segments: 2 })))
      .toBe('newest 2400 of 5000 samples · 4 buckets per point · 2 segments');
    expect(historyCoverage(base([sample(0, 0, 1)], { total_samples: 1, returned_samples: 1 }))).toBe('1 persisted sample');
  });
});
