import { useEffect, useState } from 'react';
import {
  Activity,
  Camera as CameraIcon,
  ChevronRight,
  Clock3,
  Crosshair,
  Eye,
  FileImage,
  MapPin,
  VideoOff,
} from 'lucide-react';
import type { Camera, EvidenceMarkerResult } from '../types';
import { api, errorMessage } from '../lib/api';
import {
  formatFps,
  formatInteger,
  formatMilliseconds,
  formatNumber,
  reasonOr,
  titleCase,
} from '../lib/format';
import { CountHistory } from './CountHistory';
import { Modal } from './Modal';
import { Badge, CalibrationBadge, DataValue, HealthBadge, KeyValue, Reason } from './ui';

function previewEligible(camera: Camera): boolean {
  return Boolean(camera.enabled && camera.health !== 'OFFLINE' && camera.preview_url);
}

export function CameraPreview({ camera, detail = false }: { camera: Camera; detail?: boolean }) {
  const [mediaMode, setMediaMode] = useState<'video' | 'image' | 'failed'>('video');

  useEffect(() => {
    setMediaMode('video');
  }, [camera.id, camera.preview_url]);

  const eligible = previewEligible(camera);
  const unavailableReason = !camera.enabled
    ? 'This camera is disabled by the local configuration.'
    : camera.health === 'OFFLINE'
      ? reasonOr(camera.unavailable_reason, 'The camera is offline, so no preview was mounted.')
      : !camera.preview_url
        ? 'The local API did not provide a preview URL.'
        : reasonOr(camera.unavailable_reason, 'The local preview could not be loaded.');
  const showEmpty = !eligible || mediaMode === 'failed';
  const analyticsUnavailable = camera.visible_count === null && camera.analytics_fps === null;

  return (
    <div className={detail ? 'camera-preview camera-preview-detail' : 'camera-preview'}>
      {showEmpty ? (
        <div className="camera-empty">
          <span className="camera-empty-icon"><VideoOff size={25} aria-hidden="true" /></span>
          <div>
            <strong>{camera.enabled ? 'No live preview available' : 'Camera disabled'}</strong>
            <p>{unavailableReason}</p>
          </div>
        </div>
      ) : (
        <>
          {mediaMode === 'video' ? (
            <video
              src={camera.preview_url ?? undefined}
              muted
              autoPlay
              playsInline
              controls={detail}
              preload="metadata"
              aria-label={`${camera.name} local source preview`}
              onError={() => setMediaMode('image')}
            />
          ) : (
            <img
              src={camera.preview_url ?? undefined}
              alt={`${camera.name} local MJPEG source preview`}
              onError={() => setMediaMode('failed')}
            />
          )}
          {analyticsUnavailable ? (
            <span className="source-preview-label">SOURCE PREVIEW · ANALYTICS UNAVAILABLE</span>
          ) : null}
          <span className="preview-local-label"><span aria-hidden="true" />LOCAL SOURCE</span>
        </>
      )}
    </div>
  );
}

function CameraTelemetry({ camera, detail = false }: { camera: Camera; detail?: boolean }) {
  const reason = reasonOr(camera.unavailable_reason);
  return (
    <>
      <dl className={detail ? 'telemetry-grid telemetry-grid-detail' : 'telemetry-grid'}>
        <div><dt>Visible</dt><dd><DataValue value={camera.visible_count} formatter={(value) => formatInteger(Number(value))} reason={reason} /></dd></div>
        <div><dt>Source FPS</dt><dd><DataValue value={camera.source_fps} formatter={(value) => formatFps(Number(value))} reason={reason} /></dd></div>
        <div><dt>Analytics FPS</dt><dd><DataValue value={camera.analytics_fps} formatter={(value) => formatFps(Number(value))} reason={reason} /></dd></div>
        <div><dt>Frame age</dt><dd><DataValue value={camera.frame_age_ms} formatter={(value) => formatMilliseconds(Number(value))} reason={reason} /></dd></div>
        <div><dt>Dropped frames</dt><dd>{formatInteger(camera.dropped_frames)}</dd></div>
        <div><dt>Epoch</dt><dd><DataValue value={camera.epoch} formatter={String} reason={reason} /></dd></div>
      </dl>
      {(camera.visible_count === null || camera.source_fps === null || camera.analytics_fps === null || camera.frame_age_ms === null || camera.epoch === null) ? (
        <Reason>{reason}</Reason>
      ) : null}
    </>
  );
}

export function CameraTile({ camera, onOpen }: { camera: Camera; onOpen: (camera: Camera) => void }) {
  return (
    <article className="camera-tile">
      <button type="button" className="camera-hit-area" onClick={() => onOpen(camera)} aria-label={`Open details for ${camera.name}, ${titleCase(camera.health)}`} />
      <span className="camera-tile-header">
        <span>
          <span className="camera-name"><CameraIcon size={15} aria-hidden="true" />{camera.name}</span>
          <span className="camera-subline"><span>{titleCase(camera.source_type)}</span><span>·</span><span>{camera.source_label}</span></span>
        </span>
        <ChevronRight size={16} className="camera-open-icon" aria-hidden="true" />
      </span>
      <CameraPreview camera={camera} />
      <span className="camera-badges">
        <Badge tone="neutral" icon={CameraIcon}>{camera.id}</Badge>
        <Badge tone="muted" icon={MapPin}>{titleCase(camera.floor_id)}</Badge>
        <HealthBadge health={camera.health} compact />
        <CalibrationBadge status={camera.calibration_status} />
      </span>
      <CameraTelemetry camera={camera} />
      <span className="camera-detail-cue">Inspect camera <ChevronRight size={12} aria-hidden="true" /></span>
    </article>
  );
}

export function streamOutcomeLabel(camera: Camera): string {
  switch (camera.stream_outcome) {
    case 'CLEAN_EOF':
      return 'Source ended · clean EOF (history complete)';
    case 'ABNORMAL_TERMINATION':
      return `Source ended abnormally · ${camera.stream_outcome_detail ?? 'no detail'}`;
    case 'CONSUMER_FAILED':
      return `Processing failed · ${camera.stream_outcome_detail ?? 'no detail'}`;
    default:
      return camera.epoch ? 'Streaming (no end reported)' : 'No active source epoch';
  }
}

/** A marker needs a live epoch that has consumed at least one frame (a real source time). */
export function markerEligibility(camera: Pick<Camera, 'epoch' | 'source_position_s'>): string | null {
  if (!camera.epoch) return 'No active camera epoch. Start the demo first.';
  if (camera.source_position_s === null || camera.source_position_s === undefined) return 'No frame consumed yet; no source time exists.';
  return null;
}

function EvidenceMarker({ camera }: { camera: Camera }) {
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<EvidenceMarkerResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const blocked = markerEligibility(camera);
  const mark = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.markEvidence(camera.id, note.trim() || undefined));
      setNote('');
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="drawer-section" aria-labelledby="evidence-marker-title">
      <h3 id="evidence-marker-title"><FileImage size={15} aria-hidden="true" />Operator evidence marker</h3>
      <p className="drawer-copy">Saves the real source frame at the current source position and a stream-copied source clip around it. This is a manual operator record, not an analytics detection.</p>
      <div className="evidence-marker-row">
        <input type="text" className="text-input" placeholder="Optional note" maxLength={500} value={note}
          onChange={(event) => setNote(event.target.value)} aria-label="Evidence marker note" disabled={busy || Boolean(blocked)} />
        <button type="button" className="button button-secondary button-small" onClick={mark} disabled={busy || Boolean(blocked)}
          data-testid="mark-evidence">{busy ? 'Extracting…' : 'Mark evidence'}</button>
      </div>
      {blocked ? <p className="panel-footnote">{blocked}</p> : null}
      {error ? <p className="panel-footnote form-error" role="alert">{error}</p> : null}
      {result ? (
        <p className="panel-footnote" data-testid="evidence-marker-result">
          Saved at source {formatNumber(result.snapshot.source_marked_s ?? 0, 2)} s · clip {formatNumber(result.clip.source_start_s ?? 0, 2)}–{formatNumber(result.clip.source_end_s ?? 0, 2)} s (keyframe-aligned). See Evidence.
        </p>
      ) : null}
    </section>
  );
}

export function CameraDetail({ camera, onClose }: { camera: Camera; onClose: () => void }) {
  const reason = reasonOr(camera.unavailable_reason);
  return (
    <Modal
      title={camera.name}
      subtitle={`${camera.id} · ${titleCase(camera.floor_id)} · ${camera.source_label}`}
      onClose={onClose}
      variant="drawer"
      footer={<span className="modal-footnote"><Activity size={13} aria-hidden="true" />Telemetry and media are supplied by the local API.</span>}
    >
      <div className="camera-detail-badges">
        <HealthBadge health={camera.health} />
        <CalibrationBadge status={camera.calibration_status} />
        <Badge tone={camera.enabled ? 'info' : 'muted'}>{camera.enabled ? 'Source enabled' : 'Source disabled'}</Badge>
      </div>
      <CameraPreview camera={camera} detail />
      {camera.unavailable_reason ? <Reason>{camera.unavailable_reason}</Reason> : null}

      <section className="drawer-section" aria-labelledby="live-telemetry-title">
        <h3 id="live-telemetry-title"><Eye size={15} aria-hidden="true" />Live telemetry</h3>
        <CameraTelemetry camera={camera} detail />
      </section>

      <section className="drawer-section" aria-labelledby="source-details-title">
        <h3 id="source-details-title"><Crosshair size={15} aria-hidden="true" />Source & calibration</h3>
        <dl className="key-value-grid">
          <KeyValue label="Source type">{titleCase(camera.source_type)}</KeyValue>
          <KeyValue label="Source label">{camera.source_label}</KeyValue>
          <KeyValue label="Calibration revision"><DataValue value={camera.calibration_revision} reason={reasonOr(camera.unavailable_reason, 'No calibration revision was reported.')} /></KeyValue>
          <KeyValue label="Last good frame"><DataValue value={camera.last_good_age_ms} formatter={(value) => formatMilliseconds(Number(value))} reason={reason} /></KeyValue>
          <KeyValue label="Frame age basis"><DataValue value={camera.frame_age_basis} reason="The API did not provide a frame-age basis." /></KeyValue>
          <KeyValue label="Replay mode"><DataValue value={camera.replay_mode ? titleCase(camera.replay_mode) : null} reason="No replay is active." /></KeyValue>
          <KeyValue label="Source position"><DataValue value={camera.source_position_s} formatter={(value) => `${formatNumber(Number(value), 2)} s · segment ${camera.source_segment ?? 0}`} reason="No current source time (no run or stale metadata)." /></KeyValue>
          <KeyValue label="Stream outcome"><span data-testid="stream-outcome">{streamOutcomeLabel(camera)}</span></KeyValue>
          <KeyValue label="Analytics rate basis"><DataValue value={camera.analytics_fps_basis} reason="No replay is active." /></KeyValue>
          <KeyValue label="Preview endpoint"><code>{camera.preview_url ?? 'Unavailable'}</code></KeyValue>
        </dl>
      </section>

      <section className="drawer-section" aria-labelledby="camera-health-title">
        <h3 id="camera-health-title"><Clock3 size={15} aria-hidden="true" />Operational interpretation</h3>
        <p className="drawer-copy">
          {camera.health === 'OFFLINE'
            ? reasonOr(camera.unavailable_reason, 'The source is offline. No analytics are inferred from this camera.')
            : camera.calibration_status !== 'VALID'
              ? 'Source media may be visible, but spatial measurements remain illustrative until calibration is valid.'
              : 'Only reported source and analytics telemetry is shown. No detections or tracks are synthesized in the browser.'}
        </p>
        <div className="detail-number" data-testid="detail-visible-count"><span>Reported visible people</span><strong>{camera.visible_count === null ? 'Unavailable' : formatNumber(camera.visible_count, 0)}</strong></div>
      </section>

      <CountHistory camera={camera} />
      <EvidenceMarker camera={camera} />
    </Modal>
  );
}
