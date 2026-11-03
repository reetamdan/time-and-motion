import { describe, expect, it } from 'vitest';
import { groupEvidence, sourceRange } from './Evidence';
import { markerEligibility } from '../components/Camera';
import type { EvidenceRecord } from '../types';

const record = (id: string, group: string | null, media: string, start: number | null, end: number | null): EvidenceRecord => ({
  id, run_id: 'r', camera_id: 'cam-01', camera_epoch: 'e', kind: 'k', media_type: media, label: null, trigger: 'operator_marker',
  group_id: group, note: null, timestamp_basis: null, source_marked_s: 10, source_start_s: start, source_end_s: end,
  checksum: null, size_bytes: 1, created_at: '2026-10-27T00:00:00Z', file_available: true, unavailable_reason: null,
  url: '', metadata: {},
});

describe('evidence presentation', () => {
  it('groups snapshot and clip of one marker, image first', () => {
    const groups = groupEvidence([record('c', 'g1', 'video/mp4', 6, 13), record('s', 'g1', 'image/jpeg', 10, 10), record('x', null, 'image/jpeg', 1, 1)]);
    expect(groups.map((group) => group.records.map((item) => item.id))).toEqual([['s', 'c'], ['x']]);
  });

  it('labels actual source bounds and never invents a time', () => {
    expect(sourceRange(record('a', null, 'image/jpeg', 10, 10))).toBe('source 10 s');
    expect(sourceRange(record('a', null, 'video/mp4', 6.006, 13.5))).toBe('source 6.01–13.5 s');
    expect(sourceRange(record('a', null, 'video/mp4', null, null))).toBe('Source time unavailable');
  });

  it('allows a marker only with a live epoch that has a source time', () => {
    expect(markerEligibility({ epoch: null, source_position_s: null })).toMatch(/No active camera epoch/);
    expect(markerEligibility({ epoch: 'e', source_position_s: null })).toMatch(/No frame consumed/);
    expect(markerEligibility({ epoch: 'e', source_position_s: 3.2 })).toBeNull();
  });
});
