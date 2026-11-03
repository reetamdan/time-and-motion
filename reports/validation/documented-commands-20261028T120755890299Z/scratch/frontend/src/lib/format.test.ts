import { describe, expect, it } from 'vitest';
import {
  formatGap,
  formatMilliseconds,
  formatNumber,
  formatPercent,
  formatRelativeTime,
  formatSeconds,
  formatTimestamp,
  formatUnknown,
  reasonOr,
  titleCase,
  UNAVAILABLE,
} from './format';

describe('measurement formatting', () => {
  it('keeps unavailable values distinct from real zeroes', () => {
    expect(formatNumber(null)).toBe(UNAVAILABLE);
    expect(formatNumber(undefined)).toBe(UNAVAILABLE);
    expect(formatNumber(Number.NaN)).toBe(UNAVAILABLE);
    expect(formatNumber(0)).toBe('0');
    expect(formatPercent(0)).toBe('0%');
    expect(formatPercent(null)).toBe(UNAVAILABLE);
  });

  it('formats durations without inventing a fallback value', () => {
    expect(formatMilliseconds(null)).toBe(UNAVAILABLE);
    expect(formatMilliseconds(450)).toBe('450 ms');
    expect(formatMilliseconds(1200)).toBe('1.2 s');
    expect(formatMilliseconds(60_000)).toBe('1 min');
    expect(formatSeconds(45)).toBe('45 s');
    expect(formatSeconds(90)).toBe('1.5 min');
  });

  it('handles timestamps and relative update labels', () => {
    expect(formatTimestamp('not-a-date')).toBe(UNAVAILABLE);
    expect(formatTimestamp('2024-10-24T10:00:00Z', 'UTC')).toContain('2024');
    expect(formatTimestamp('2024-10-24T10:00:00Z', 'UTC')).toContain('10:00:00');
    const now = new Date('2026-01-01T00:00:30Z').getTime();
    expect(formatRelativeTime('2026-01-01T00:00:00Z', now)).toBe('30 s ago');
    expect(formatRelativeTime('2026-01-01T00:01:00Z', now)).toBe('just now');
  });
});

describe('semantic formatting', () => {
  it('normalizes statuses and preserves UNKNOWN semantics', () => {
    expect(titleCase('CALIBRATION WARNING')).toBe('Calibration Warning');
    expect(titleCase('source_camera_id')).toBe('Source Camera Id');
    expect(formatUnknown(null)).toBe('UNKNOWN');
    expect(formatUnknown('')).toBe('UNKNOWN');
    expect(formatUnknown(0)).toBe('0');
  });

  it('uses a real supplied reason before the generic explanation', () => {
    expect(reasonOr(null, 'Source clip is not installed.')).toBe('Source clip is not installed.');
    expect(reasonOr('', null)).toBe('No measurement was reported by the local API.');
  });

  it('formats reported history gaps when boundaries exist', () => {
    expect(formatGap({ from: '2026-01-01T00:00:00Z', to: '2026-01-01T00:05:00Z' })).toContain('→');
    expect(formatGap({ unexpected: true })).toContain('boundaries were not supplied');
  });
});
