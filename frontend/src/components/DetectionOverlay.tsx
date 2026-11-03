import { useEffect, useRef, type RefObject } from 'react';
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
    if (distance < bestDistance) {
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

function mergeFrames(buffer: DetectionFrame[], incoming: DetectionFrame[]): DetectionFrame[] {
  if (incoming.length === 0) return buffer;
  const merged = buffer.concat(incoming);
  return merged.length > MAX_BUFFERED_FRAMES ? merged.slice(merged.length - MAX_BUFFERED_FRAMES) : merged;
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
  const framesRef = useRef<DetectionFrame[]>([]);
  const sizeRef = useRef<[number, number] | null>(null);
  const epochRef = useRef<string | null>(null);
  const modeRef = useRef(camera.replay_mode);
  modeRef.current = camera.replay_mode;

  useEffect(() => {
    if (epochRef.current !== camera.epoch) {
      epochRef.current = camera.epoch;
      framesRef.current = [];
    }
  }, [camera.epoch]);

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

    const poll = async () => {
      const last = framesRef.current[framesRef.current.length - 1];
      try {
        const response: CameraDetections = await api.cameraDetections(camera.id, last?.frame_number, controller.signal);
        if (cancelled) return;
        if (response.camera_epoch !== epochRef.current) framesRef.current = [];
        if (response.frame_width && response.frame_height) sizeRef.current = [response.frame_width, response.frame_height];
        framesRef.current = mergeFrames(framesRef.current, response.frames);
        const video = videoRef.current;
        const newest = framesRef.current[framesRef.current.length - 1];
        if (video && newest && video.readyState >= 1) {
          const target = syncTarget(modeRef.current, video.currentTime, newest.file_position_s);
          if (modeRef.current === 'unpaced') {
            if (!video.paused) video.pause();
          } else if (video.paused) {
            void video.play().catch(() => undefined);
          }
          if (target !== null && Number.isFinite(video.duration) && target <= video.duration) video.currentTime = target;
        }
      } catch {
        // The overlay is best-effort; the camera's own state reports any real failure.
      }
      if (!cancelled) timer = window.setTimeout(poll, DETECTION_POLL_MS);
    };

    const render = () => {
      const video = videoRef.current;
      const canvas = canvasRef.current;
      if (video && canvas) draw(canvas, video, nearestFrame(framesRef.current, video.currentTime), sizeRef.current, detail);
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
  }, [active, camera.id, videoRef, canvasRef, detail]);
}
