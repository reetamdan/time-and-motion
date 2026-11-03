import { useEffect, useRef, useState } from 'react';
import { ChevronRight, VideoOff } from 'lucide-react';
import type { Camera } from '../types';
import { formatInteger, formatMilliseconds, reasonOr, titleCase } from '../lib/format';
import { Button } from '../design-system/components';
import { useDetectionOverlay } from './DetectionOverlay';

/** Live values older than this are shown as stale (matches analytics.health.stale_after_ms). */
export const STALE_AFTER_MS = 1500;

function previewEligible(camera: Camera): boolean {
  return Boolean(camera.enabled && camera.health !== 'OFFLINE' && camera.preview_url);
}

export function cameraFreshness(camera: Camera): 'live' | 'stale' | 'idle' {
  if (!camera.epoch) return 'idle';
  if (camera.visible_count === null || (camera.last_good_age_ms !== null && camera.last_good_age_ms > STALE_AFTER_MS)) return 'stale';
  return 'live';
}

export function CameraPreview({ camera, detail = false }: { camera: Camera; detail?: boolean }) {
  const [mode, setMode] = useState<'video' | 'image' | 'failed'>('video');
  useEffect(() => setMode('video'), [camera.id, camera.preview_url]);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const overlayActive = previewEligible(camera) && mode === 'video' && cameraFreshness(camera) === 'live';
  useDetectionOverlay(camera, videoRef, canvasRef, overlayActive, detail);
  const reason = !camera.enabled
    ? 'Disabled in the local configuration.'
    : camera.health === 'OFFLINE'
      ? reasonOr(camera.unavailable_reason, 'The camera is offline; no preview is mounted.')
      : !camera.preview_url ? 'The local API reported no preview URL.' : reasonOr(camera.unavailable_reason, 'The local preview could not be loaded.');
  if (!previewEligible(camera) || mode === 'failed') {
    return (
      <div className="camera-media camera-media-empty">
        <div><VideoOff size={22} aria-hidden="true" style={{ margin: '0 auto', color: 'var(--text-tertiary)' }} />
          <strong>{camera.enabled ? 'No preview available' : 'Camera disabled'}</strong><p>{reason}</p></div>
      </div>
    );
  }
  return (
    <div className="camera-media">
      {mode === 'video' ? (
        <>
          <video ref={videoRef} src={camera.preview_url ?? undefined} muted autoPlay playsInline loop={Boolean(camera.source_loop)}
            controls={detail && !overlayActive} preload="auto"
            aria-label={`${camera.name} local source preview`} onError={() => setMode('image')} />
          <canvas ref={canvasRef} className="detection-overlay" data-active={overlayActive ? 'true' : 'false'}
            aria-label={overlayActive ? 'Tracked person boxes from the local pipeline' : undefined} role={overlayActive ? 'img' : undefined} />
        </>
      ) : (
        <img src={camera.preview_url ?? undefined} alt={`${camera.name} local source preview`} onError={() => setMode('failed')} />
      )}
    </div>
  );
}

function CountOverlay({ camera }: { camera: Camera }) {
  const freshness = cameraFreshness(camera);
  if (freshness === 'idle') return null;
  if (camera.visible_count === null || freshness === 'stale') {
    return (
      <span className="overlay-count overlay-count-unavailable" title={reasonOr(camera.unavailable_reason, 'No current observation')}>
        <span>Camera-visible</span><strong>Unavailable</strong>
      </span>
    );
  }
  return <span className="overlay-count"><span>Camera-visible tracks</span><strong>{formatInteger(camera.visible_count)}</strong></span>;
}

export function CameraTile({ camera, onOpen }: { camera: Camera; onOpen: (camera: Camera) => void }) {
  const freshness = cameraFreshness(camera);
  const healthTone = camera.health === 'HEALTHY' ? '' : camera.health === 'OFFLINE' ? 'overlay-chip-bad' : 'overlay-chip-warn';
  return (
    <article className="camera-tile" data-camera-id={camera.id} data-freshness={freshness}>
      <button type="button" className="camera-hit" onClick={() => onOpen(camera)} aria-label={`Open details for ${camera.name}, ${titleCase(camera.health)}`} />
      <div style={{ position: 'relative' }}>
        <CameraPreview camera={camera} />
        <div className="camera-overlay camera-overlay-top">
          <span className={`overlay-chip ${healthTone}`}><span className={freshness === 'live' ? 'ds-dot ds-dot-live' : 'ds-dot'} aria-hidden="true" />{titleCase(camera.health)}</span>
          {camera.replay_mode ? <span className="overlay-chip">{camera.replay_mode === 'paced' ? 'Paced replay' : 'Unpaced replay'}</span> : null}
        </div>
        <div className="camera-overlay camera-overlay-bottom">
          <CountOverlay camera={camera} />
          {freshness === 'stale' && camera.last_good_age_ms !== null ? <span className="overlay-chip overlay-chip-warn"><span className="ds-dot" aria-hidden="true" />Stale · {formatMilliseconds(camera.last_good_age_ms)}</span> : null}
        </div>
      </div>
      <div className="camera-foot">
        <div className="camera-foot-text">
          <strong>{camera.name}</strong>
          <span>{camera.id} · {titleCase(camera.floor_id)} · {camera.source_label}</span>
        </div>
        <Button size="sm" variant="ghost" tabIndex={-1} aria-hidden="true" onClick={() => onOpen(camera)}>Inspect <ChevronRight size={14} aria-hidden="true" /></Button>
      </div>
    </article>
  );
}

export function streamOutcomeLabel(camera: Camera): string {
  switch (camera.stream_outcome) {
    case 'CLEAN_EOF': return 'Source ended · clean EOF (history complete)';
    case 'ABNORMAL_TERMINATION': return `Source ended abnormally · ${camera.stream_outcome_detail ?? 'no detail'}`;
    case 'CONSUMER_FAILED': return `Processing failed · ${camera.stream_outcome_detail ?? 'no detail'}`;
    default: return !camera.epoch ? 'No active source epoch' : camera.source_loop ? 'Streaming · clip loops continuously' : 'Streaming (no end reported)';
  }
}

/** A marker needs a live epoch that has consumed at least one frame (a real source time). */
export function markerEligibility(camera: Pick<Camera, 'epoch' | 'source_position_s' | 'evidence_available' | 'evidence_unavailable_reason'>): string | null {
  if (camera.evidence_available === false) return camera.evidence_unavailable_reason ?? 'Evidence extraction is unavailable for this source.';
  if (!camera.epoch) return 'No active camera epoch. Start the demo first.';
  if (camera.source_position_s === null || camera.source_position_s === undefined) return 'No frame consumed yet; no source time exists.';
  return null;
}
