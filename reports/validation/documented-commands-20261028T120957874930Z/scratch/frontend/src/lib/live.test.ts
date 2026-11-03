import { describe, expect, it } from 'vitest';
import type { Snapshot, SystemMetrics } from '../types';
import { applyLiveEvent, isLiveEvent, MAX_RECONNECT_ATTEMPTS, mergeAssociations, reconnectDelay } from './live';

const metrics: SystemMetrics = {
  timestamp: '2026-01-01T00:00:00Z',
  scope: 'unavailable',
  gpu_name: null,
  gpu_utilization: null,
  vram_used_mib: null,
  vram_total_mib: null,
  gpu_temperature_c: null,
  nvdec_utilization: null,
  cpu_utilization: null,
  ram_used_mb: null,
  ram_total_mb: null,
  process_rss_mb: null,
  unavailable_reason: 'No host probe available',
};

function snapshot(): Snapshot {
  return {
    schema_version: '1.0',
    run_id: null,
    run_status: 'IDLE',
    generated_at: '2026-01-01T00:00:00Z',
    sequence: 4,
    watermark: 8,
    cameras: [],
    floors: [],
    alerts: [],
    kpis: {
      building_expected: null,
      building_visible: null,
      building_estimated: null,
      unresolved_transitions: null,
      discrepancy: null,
      quality: 'UNAVAILABLE',
      entries: null,
      exits: null,
      queue_total: null,
      max_wait_seconds: null,
      stair_up_per_min: null,
      stair_down_per_min: null,
      congested_zones: 0,
      healthy_cameras: 0,
      enabled_cameras: 0,
    },
    associations: [],
    system_metrics: metrics,
    notices: [],
  };
}

describe('live event state', () => {
  it('replaces state for an initial snapshot', () => {
    const initial = snapshot();
    expect(applyLiveEvent(null, { type: 'snapshot', data: initial })).toBe(initial);
  });

  it('updates real metrics and sequence without altering KPI values', () => {
    const current = snapshot();
    const nextMetrics = { ...metrics, timestamp: '2026-01-01T00:00:05Z', cpu_utilization: 12 };
    const next = applyLiveEvent(current, { type: 'metrics', data: nextMetrics, sequence: 5, watermark: 9 });
    expect(next?.system_metrics.cpu_utilization).toBe(12);
    expect(next?.system_metrics.timestamp).toBe('2026-01-01T00:00:05Z');
    expect(next?.sequence).toBe(5);
    expect(next?.watermark).toBe(9);
    expect(next?.kpis).toBe(current.kpis);
  });

  it('merges partial delta KPIs while preserving unrelated fields', () => {
    const current = snapshot();
    const next = applyLiveEvent(current, { type: 'delta', data: { kpis: { building_visible: 3 } }, sequence: 6 });
    expect(next?.kpis.building_visible).toBe(3);
    expect(next?.kpis.quality).toBe('UNAVAILABLE');
    expect(next?.kpis.enabled_cameras).toBe(0);
    expect(next?.sequence).toBe(6);
    expect(next?.system_metrics).toBe(current.system_metrics);
  });

  it('ignores metric events until an initial snapshot exists', () => {
    expect(applyLiveEvent(null, { type: 'metrics', data: metrics })).toBeNull();
  });

  it('validates live envelopes and bounds association merges', () => {
    expect(isLiveEvent({ type: 'snapshot', data: {} })).toBe(true);
    expect(isLiveEvent({ type: 'unknown', data: {} })).toBe(false);
    expect(isLiveEvent(null)).toBe(false);
    const merged = mergeAssociations([{ id: 'a', status: 'OPEN' }], [{ id: 'a', status: 'MATCHED' }, { id: 'b', status: 'OPEN' }]);
    expect(merged).toHaveLength(2);
    expect(merged.find((record) => record.id === 'a')?.status).toBe('MATCHED');
    expect(merged[0].id).toBe('b');
  });
});

describe('reconnect policy', () => {
  it('uses bounded exponential delays', () => {
    expect(reconnectDelay(0)).toBe(1000);
    expect(reconnectDelay(1)).toBe(2000);
    expect(reconnectDelay(4)).toBe(15_000);
    expect(reconnectDelay(20)).toBe(15_000);
    expect(MAX_RECONNECT_ATTEMPTS).toBe(5);
  });
});
