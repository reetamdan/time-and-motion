import { useEffect, type RefObject } from 'react';
import { api } from '../lib/api';
import type { Camera, CameraDetections, DetectionFrame } from '../types';

/** How often the overlay asks the backend for new tracked boxes while a camera is live. */
export const DETECTION_POLL_MS = 200;
/** Frames kept in the browser for matching against the video's current time (~10 s at 30 fps). */
const MAX_BUFFERED_FRAMES = 300;
/** A buffered frame further than this from the video time is not drawn (the boxes would be misplaced). */
export const MATCH_TOLERANCE_S = 0.2;
/** Paced replay: re-seek the preview when it drifts this far from the pipeline position. */
export const PACED_RESYNC_S = 0.6;
/** Boxes not refreshed by a successful poll within this time are cleared (matches the snapshot stale threshold). */
export const DETECTION_STALE_MS = 1500;
/** A detections request that takes longer than this is abandoned, so a hung request cannot stall polling. */
export const DETECTION_REQUEST_TIMEOUT_MS = 3000;

export interface RequestSignal { signal: AbortSignal; dispose: () => void }

/** One request's signal: aborts when `cancel` aborts or after `timeoutMs`. Call `dispose()` when the request
 *  settles. It removes the listener on the long-lived `cancel` signal and the timer, so a long-running poll
 *  loop does not accumulate one listener per request. Plain AbortController + setTimeout works in every browser
 *  (AbortSignal.any needs Chrome 116 / Firefox 124 / Safari 17.4). */
export function requestSignal(cancel: AbortSignal, timeoutMs = DETECTION_REQUEST_TIMEOUT_MS): RequestSignal {
  const controller = new AbortController();
  const abort = () => controller.abort();
  const timer = setTimeout(abort, timeoutMs);
  const dispose = () => { clearTimeout(timer); cancel.removeEventListener('abort', abort); };
  if (cancel.aborted) { dispose(); abort(); } else cancel.addEventListener('abort', abort, { once: true });
  return { signal: controller.signal, dispose };
}

export interface Rect { x: number; y: number; width: number; height: number }

/** The area a video occupies inside its element under `object-fit: contain`. */
export function containRect(elementWidth: number, elementHeight: number, videoWidth: number, videoHeight: number): Rect {
  if (elementWidth <= 0 || elementHeight <= 0 || videoWidth <= 0 || videoHeight <= 0) {
    return { x: 0, y: 0, width: Math.max(0, elementWidth), height: Math.max(0, elementHeight) };
  }
  const scale = Math.min(elementWidth / videoWidth, elementHeight / videoHeight);
  const width = videoWidth * scale;
  const height = videoHeight * scale;
  return { x: (elementWidth - width) / 2, y: (elementHeight - height) / 2, width, height };
}

/** The buffered frame whose file position is closest to `time`, if within `tolerance` seconds. */
export function nearestFrame(frames: DetectionFrame[], time: number, tolerance = MATCH_TOLERANCE_S): DetectionFrame | null {
  let best: DetectionFrame | null = null;
  let bestDistance = Infinity;
  for (const frame of frames) {
    if (frame.file_position_s === null) continue;
    const distance = Math.abs(frame.file_position_s - time);
    if (distance <= bestDistance) {
      best = frame;
      bestDistance = distance;
    }
  }
  return bestDistance <= tolerance ? best : null;
}

/** Where the preview should be: null when it is close enough to the pipeline position already. */
export function syncTarget(mode: 'paced' | 'unpaced' | null | undefined, videoTime: number, pipelinePosition: number | null): number | null {
  if (pipelinePosition === null) return null;
  const drift = Math.abs(videoTime - pipelinePosition);
  if (mode === 'unpaced') return drift > 1 / 30 ? pipelinePosition : null;
  return drift > PACED_RESYNC_S ? pipelinePosition : null;
}

/** One camera epoch's display cache. Transport success alone does not make old detections fresh. */
export class DetectionBuffer {
  frames: DetectionFrame[] = [];
  size: [number, number] | null = null;
  private expiresAt = 0;

  constructor(readonly epoch: string | null) {}

  get afterFrame(): number | undefined {
    return this.frames.at(-1)?.frame_number;
  }

  /** `elapsedMs` is the request's round trip: the server's `latest_age_ms` was measured before the response
   *  was sent, so the full elapsed time is subtracted too (conservative: a slow response cannot make old
   *  detections look fresh again). */
  accept(response: CameraDetections, now: number, elapsedMs = 0): boolean {
    if (response.camera_epoch !== this.epoch) {
      this.frames = [];
      this.size = null;
      this.expiresAt = 0;
      return false;
    }
    const after = this.afterFrame ?? -1;
    this.frames = this.frames.concat(response.frames.filter(frame => frame.frame_number > after)).slice(-MAX_BUFFERED_FRAMES);
    if (response.frame_width && response.frame_height) this.size = [response.frame_width, response.frame_height];
    this.expiresAt = response.latest_age_ms === null ? 0
      : now + DETECTION_STALE_MS - response.latest_age_ms - Math.max(0, elapsedMs);
    return this.fresh(now);
  }

  fresh(now: number): boolean {
    return now < this.expiresAt;
  }
}

function draw(canvas: HTMLCanvasElement, video: HTMLVideoElement, frame: DetectionFrame | null,
  frameSize: [number, number] | null, detail: boolean): void {
  const ratio = window.devicePixelRatio || 1;
  const width = Math.round(canvas.clientWidth * ratio);
  const height = Math.round(canvas.clientHeight * ratio);
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const context = canvas.getContext('2d');
  if (!context) return;
  context.clearRect(0, 0, width, height);
  if (!frame || !frameSize || frame.boxes.length === 0) return;
  // Pipeline pixels -> normalized frame -> the displayed video rectangle.
  const area = containRect(width, height, video.videoWidth || frameSize[0], video.videoHeight || frameSize[1]);
  const sx = area.width / frameSize[0];
  const sy = area.height / frameSize[1];
  context.lineWidth = (detail ? 2 : 1.25) * ratio;
  context.strokeStyle = 'rgba(52, 211, 153, 0.95)';
  context.fillStyle = 'rgba(52, 211, 153, 0.95)';
  context.font = `${Math.round(10 * ratio)}px ui-monospace, monospace`;
  for (const [x, y, w, h, trackId] of frame.boxes) {
    const left = area.x + x * sx;
    const top = area.y + y * sy;
    context.strokeRect(left, top, w * sx, h * sy);
    if (detail) context.fillText(trackId, left + 2 * ratio, Math.max(area.y + 10 * ratio, top - 3 * ratio));
  }
}

/**
 * Draws the backend's tracked person boxes over the source preview and keeps the preview on the
 * pipeline's position (paced: plays along and re-seeks on drift; unpaced: follows frame by frame).
 * Display only: every count still comes from the backend analytics.
 */
export function useDetectionOverlay(camera: Camera, videoRef: RefObject<HTMLVideoElement | null>,
  canvasRef: RefObject<HTMLCanvasElement | null>, active: boolean, detail: boolean): void {
  useEffect(() => {
    const video = videoRef.current;
    if (video) video.loop = Boolean(camera.source_loop);
  }, [videoRef, camera.source_loop]);

  useEffect(() => {
    if (!active) {
      const canvas = canvasRef.current;
      canvas?.getContext('2d')?.clearRect(0, 0, canvas.width, canvas.height);
      return undefined;
    }
    let cancelled = false;
    let timer: number | undefined;
    let animation: number | undefined;
    const controller = new AbortController();
    // Effect-local state prevents a late response from a previous epoch repopulating a new cache.
    const buffer = new DetectionBuffer(camera.epoch);

    const poll = async () => {
      const request = requestSignal(controller.signal);
      const sentAt = performance.now();
      try {
        const response: CameraDetections = await api.cameraDetections(camera.id, buffer.afterFrame,
          request.signal, camera.epoch ?? undefined);
        if (cancelled) return;
        const now = performance.now();
        const fresh = buffer.accept(response, now, now - sentAt);
        const video = videoRef.current;
        const newest = buffer.frames.at(-1);
        if (fresh && video && newest && video.readyState >= 1) {
          const target = syncTarget(camera.replay_mode, video.currentTime, newest.file_position_s);
          if (camera.replay_mode === 'unpaced') {
            if (!video.paused) video.pause();
          } else if (video.paused) {
            void video.play().catch(() => undefined);
          }
          if (target !== null && Number.isFinite(video.duration) && target <= video.duration) video.currentTime = target;
        }
      } catch {
        // The overlay is best-effort; the camera's own state reports any real failure.
      } finally {
        request.dispose();
      }
      if (!cancelled) timer = window.setTimeout(poll, DETECTION_POLL_MS);
    };

    const render = () => {
      const video = videoRef.current;
      const canvas = canvasRef.current;
      if (video && canvas) draw(canvas, video,
        buffer.fresh(performance.now()) ? nearestFrame(buffer.frames, video.currentTime) : null, buffer.size, detail);
      animation = window.requestAnimationFrame(render);
    };

    void poll();
    animation = window.requestAnimationFrame(render);
    return () => {
      cancelled = true;
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
      if (animation !== undefined) window.cancelAnimationFrame(animation);
      const canvas = canvasRef.current;
      canvas?.getContext('2d')?.clearRect(0, 0, canvas.width, canvas.height);
    };
  }, [active, camera.id, camera.epoch, camera.replay_mode, videoRef, canvasRef, detail]);
}
