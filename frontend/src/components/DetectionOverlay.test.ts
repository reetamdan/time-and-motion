import { describe, expect, it } from 'vitest';
import { containRect, nearestFrame, syncTarget, PACED_RESYNC_S } from './DetectionOverlay';
import type { DetectionFrame } from '../types';

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
});
