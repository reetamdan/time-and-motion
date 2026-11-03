import { describe, expect, it } from 'vitest';
import { afterEach, vi } from 'vitest';
import { containRect, nearestFrame, syncTarget, PACED_RESYNC_S, DetectionBuffer, DETECTION_STALE_MS, requestSignal } from './DetectionOverlay';
import type { CameraDetections, DetectionFrame } from '../types';

const frame = (frameNumber: number, position: number | null): DetectionFrame => ({
  frame_number: frameNumber, source_time_s: position, file_position_s: position, loop_index: 0, boxes: [[1, 2, 3, 4, '7']],
});

describe('detection overlay geometry and sync', () => {
  it('maps a 16:9 video into a taller element with letterboxing (object-fit: contain)', () => {
    expect(containRect(1600, 1000, 1920, 1080)).toEqual({ x: 0, y: 50, width: 1600, height: 900 });
    expect(containRect(800, 450, 1920, 1080)).toEqual({ x: 0, y: 0, width: 800, height: 450 });
  });

  it('pillarboxes a 4:3 video in a 16:9 element and tolerates unknown sizes', () => {
    expect(containRect(1600, 900, 1200, 900)).toEqual({ x: 200, y: 0, width: 1200, height: 900 });
    expect(containRect(100, 50, 0, 0)).toEqual({ x: 0, y: 0, width: 100, height: 50 });
  });

  it('draws only the frame closest to the video time and only within tolerance', () => {
    const frames = [frame(1, 1.0), frame(2, 1.0334), frame(3, null), frame(4, 1.1)];
    expect(nearestFrame(frames, 1.04)?.frame_number).toBe(2);
    expect(nearestFrame(frames, 5)).toBeNull();
    expect(nearestFrame([], 1)).toBeNull();
  });

  it('matches across a loop wrap by file position, not frame number', () => {
    const frames = [frame(709, 23.65), frame(710, 0.0), frame(711, 0.0334)];
    expect(nearestFrame(frames, 0.01)?.frame_number).toBe(710);
    expect(nearestFrame(frames, 23.6)?.frame_number).toBe(709);
  });

  it('re-seeks paced previews only on real drift and follows unpaced replay frame by frame', () => {
    expect(syncTarget('paced', 10, 10 + PACED_RESYNC_S / 2)).toBeNull();
    expect(syncTarget('paced', 10, 12)).toBe(12);
    expect(syncTarget('unpaced', 10, 10.2)).toBe(10.2);
    expect(syncTarget('unpaced', 10, 10.01)).toBeNull();
    expect(syncTarget('paced', 10, null)).toBeNull();
  });

  it('prefers the newest pass when repeated file positions are equally close', () => {
    expect(nearestFrame([frame(1, 0.1), frame(711, 0.1)], 0.1)?.frame_number).toBe(711);
  });
});

const response = (epoch: string, frames: DetectionFrame[], age: number | null = 0): CameraDetections => ({
  camera_id: 'cam-01', camera_epoch: epoch, replay_mode: 'paced', loop: true, loop_index: 0,
  frame_width: 1920, frame_height: 1080, latest_age_ms: age, box_format: 'xywh', frames,
});

describe('overlay epoch and freshness handling', () => {
  it('rejects late responses from an old epoch and never reuses their high cursor', () => {
    const buffer = new DetectionBuffer('new');
    expect(buffer.accept(response('old', [frame(50000, 1)]), 100)).toBe(false);
    expect(buffer.afterFrame).toBeUndefined();
    expect(buffer.accept(response('new', [frame(0, 0)]), 200)).toBe(true);
    expect(buffer.afterFrame).toBe(0);
    expect(buffer.size).toEqual([1920, 1080]);
  });

  it('expires boxes during failed requests even when the dashboard snapshot stays live', () => {
    const buffer = new DetectionBuffer('epoch');
    buffer.accept(response('epoch', [frame(1, 0)], 500), 100);
    expect(buffer.fresh(100 + DETECTION_STALE_MS - 501)).toBe(true);
    expect(buffer.fresh(100 + DETECTION_STALE_MS - 500)).toBe(false);
    expect(buffer.accept(response('epoch', [], 2000), 2000)).toBe(false);
    expect(buffer.accept(response('epoch', [], null), 2000)).toBe(false);
  });

  it('deduplicates overlapping polls and bounds memory over long runs', () => {
    const buffer = new DetectionBuffer('epoch');
    buffer.accept(response('epoch', Array.from({ length: 200 }, (_, n) => frame(n, n / 30))), 100);
    buffer.accept(response('epoch', Array.from({ length: 200 }, (_, n) => frame(n + 150, n / 30))), 200);
    expect(buffer.frames).toHaveLength(300);
    expect(new Set(buffer.frames.map(f => f.frame_number)).size).toBe(300);
    expect(buffer.frames[0].frame_number).toBe(50);
    expect(buffer.afterFrame).toBe(349);
  });
});

describe('request signal', () => {
  afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

  it('aborts when the poll is cancelled', () => {
    const cancelled = new AbortController();
    const signal = requestSignal(cancelled.signal, 60_000);
    expect(signal.aborted).toBe(false);
    cancelled.abort();
    expect(signal.aborted).toBe(true);
  });

  it('falls back to a manual combination where AbortSignal.any is missing', () => {
    vi.useFakeTimers();
    class LegacySignal { static any = undefined; static timeout = undefined; }
    vi.stubGlobal('AbortSignal', LegacySignal);
    const cancelled = new AbortController();
    const first = requestSignal(cancelled.signal, 1000);
    cancelled.abort();
    expect(first.aborted).toBe(true);
    const timed = requestSignal(new AbortController().signal, 1000);
    vi.advanceTimersByTime(999);
    expect(timed.aborted).toBe(false);
    vi.advanceTimersByTime(2);
    expect(timed.aborted).toBe(true);
  });
});
