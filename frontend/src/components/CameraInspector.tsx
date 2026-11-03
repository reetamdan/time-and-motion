import { useState } from 'react';
import { Activity, CheckCircle2, Crosshair, FileImage, Gauge, Timer } from 'lucide-react';
import type { Camera, EvidenceMarkerResult } from '../types';
import { api, errorMessage } from '../lib/api';
import { formatFps, formatInteger, formatMilliseconds, formatNumber, formatTimestamp, reasonOr, titleCase } from '../lib/format';
import { Badge, Button, Callout, DataValue, Drawer, KeyValue, KeyValueList, Reason } from '../design-system/components';
import { CalibrationBadge, HealthBadge } from './status';
import { CameraPreview, cameraFreshness, markerEligibility, streamOutcomeLabel } from './Camera';
import { CountHistory } from './CountHistory';

function EvidenceMarker({ camera, onOpenEvidence }: { camera: Camera; onOpenEvidence: (groupId: string) => void }) {
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<EvidenceMarkerResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const blocked = markerEligibility(camera);
  const mark = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
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
    <section className="inspector-section" aria-labelledby="evidence-marker-title">
      <h3 id="evidence-marker-title"><FileImage size={15} aria-hidden="true" />Operator evidence marker</h3>
      <p className="ds-footnote" style={{ marginTop: 0 }}>Saves the decoded source frame at the current source position and a stream-copied clip around it. This is a manual operator record, not an analytics detection.</p>
      <div className="marker-row">
        <input type="text" placeholder="Optional note" maxLength={500} value={note} onChange={(event) => setNote(event.target.value)}
          aria-label="Evidence marker note" disabled={busy || Boolean(blocked)} />
        <Button variant="primary" onClick={() => void mark()} loading={busy} disabled={busy || Boolean(blocked)} data-testid="mark-evidence">
          {busy ? 'Extracting frame and clip…' : 'Mark evidence'}
        </Button>
      </div>
      {busy ? <p className="ds-footnote" role="status">Decoding the source frame and copying the clip with ffmpeg; this takes a few seconds.</p> : null}
      {blocked ? <Reason>{blocked}</Reason> : null}
      {error ? <Callout tone="danger" role="alert" title="Evidence not saved"><p>{error}</p></Callout> : null}
      {result ? (
        <div data-testid="evidence-marker-result">
          <Callout tone="info" icon={CheckCircle2} role="status" title={`Saved at source ${formatNumber(result.snapshot.source_marked_s ?? 0, 2)} s`}
            actions={<Button size="sm" onClick={() => onOpenEvidence(result.group_id)}>Open in Evidence</Button>}>
            <p>Frame + clip {formatNumber(result.clip.source_start_s ?? 0, 2)}–{formatNumber(result.clip.source_end_s ?? 0, 2)} s (keyframe-aligned start). Epoch {result.snapshot.camera_epoch?.slice(0, 8)}.</p>
          </Callout>
        </div>
      ) : null}
    </section>
  );
}

export default function CameraInspector({ camera, onClose, onOpenEvidence }: {
  camera: Camera;
  onClose: () => void;
  onOpenEvidence: (groupId: string) => void;
}) {
  const reason = reasonOr(camera.unavailable_reason);
  const freshness = cameraFreshness(camera);
  return (
    <Drawer title={camera.name} eyebrow="Camera inspector" subtitle={`${camera.id} · ${titleCase(camera.floor_id)} · ${camera.source_label}`}
      onClose={onClose} width={560} testId="camera-inspector"
      footer={<span><Activity size={12} aria-hidden="true" style={{ display: 'inline', marginRight: 6 }} />Telemetry and media come from the local API; the browser draws no detections.</span>}>
      <div className="inspector-badges">
        <HealthBadge health={camera.health} />
        <CalibrationBadge status={camera.calibration_status} />
        <Badge tone={freshness === 'live' ? 'good' : freshness === 'stale' ? 'warning' : 'muted'} dot live={freshness === 'live'}>
          {freshness === 'live' ? 'Live values' : freshness === 'stale' ? 'Stale — values unavailable' : 'No active epoch'}
        </Badge>
      </div>
      <div className="inspector-media"><CameraPreview camera={camera} detail /></div>
      <div className="inspector-count" data-testid="detail-visible-count">
        <span>Camera-visible tracked people<br /><small>camera-local · not facility occupancy</small></span>
        <strong>{camera.visible_count === null ? 'Unavailable' : formatNumber(camera.visible_count, 0)}</strong>
      </div>
      {camera.unavailable_reason ? <Reason>{camera.unavailable_reason}</Reason> : null}

      <section className="inspector-section" aria-labelledby="source-title">
        <h3 id="source-title"><Timer size={15} aria-hidden="true" />Source & timeline</h3>
        <KeyValueList>
          <KeyValue label="Source position"><DataValue value={camera.source_position_s} formatter={(value) => `${formatNumber(Number(value), 2)} s · segment ${camera.source_segment ?? 0}`} reason="No current source time (no run, or no frame yet)." /></KeyValue>
          <KeyValue label="Replay mode"><DataValue value={camera.replay_mode ? titleCase(camera.replay_mode) : null} reason="No replay is active." /></KeyValue>
          <KeyValue label="Camera epoch"><DataValue value={camera.epoch ? camera.epoch.slice(0, 8) : null} reason="No active epoch." /></KeyValue>
          <KeyValue label="Stream outcome"><span data-testid="stream-outcome">{streamOutcomeLabel(camera)}</span></KeyValue>
          <KeyValue label="Last good frame"><DataValue value={camera.last_good_age_ms} formatter={(value) => `${formatMilliseconds(Number(value))} ago`} reason={reason} /></KeyValue>
          <KeyValue label="Source discontinuities">{formatInteger(camera.source_discontinuities ?? 0)}</KeyValue>
          {camera.source_type === 'rtsp' ? (
            <KeyValue label="Reconnect">{camera.reconnect_attempt ? `attempt ${camera.reconnect_attempt}${camera.next_reconnect_at ? `, next ${formatTimestamp(camera.next_reconnect_at)}` : ''}` : 'connected / not needed'}</KeyValue>
          ) : null}
        </KeyValueList>
        <p className="ds-footnote" style={{ marginTop: 0 }}>Timestamp basis: {camera.source_time_basis ?? 'source PTS seconds within the camera epoch'}. Receive-time freshness is tracked separately.</p>
      </section>

      <section className="inspector-section" aria-labelledby="rate-title">
        <h3 id="rate-title"><Gauge size={15} aria-hidden="true" />Processing</h3>
        <KeyValueList>
          <KeyValue label="Source rate"><DataValue value={camera.source_fps} formatter={(value) => formatFps(Number(value))} reason={reason} /></KeyValue>
          <KeyValue label="Analytics rate"><DataValue value={camera.analytics_fps} formatter={(value) => formatFps(Number(value))} reason={reason} /></KeyValue>
          <KeyValue label="Backend queue"><DataValue value={camera.backend_queue_ms} formatter={(value) => formatMilliseconds(Number(value))} reason="Not reported" /></KeyValue>
          <KeyValue label="Dropped frames">{formatInteger(camera.dropped_frames)}</KeyValue>
          <KeyValue label="Frames at detector top-k">{camera.frames_at_detection_limit ? `${formatInteger(camera.frames_at_detection_limit)} (counts are lower bounds)` : formatInteger(camera.frames_at_detection_limit ?? 0)}</KeyValue>
          <KeyValue label="History samples">{formatInteger(camera.persisted_count_samples ?? 0)}</KeyValue>
        </KeyValueList>
        {camera.analytics_fps_basis ? <p className="ds-footnote" style={{ marginTop: 0 }}>{camera.analytics_fps_basis}</p> : null}
      </section>

      <section className="inspector-section" aria-labelledby="calibration-title">
        <h3 id="calibration-title"><Crosshair size={15} aria-hidden="true" />Calibration</h3>
        <KeyValueList>
          <KeyValue label="Status">{titleCase(camera.calibration_status)}</KeyValue>
          <KeyValue label="Revision"><DataValue value={camera.calibration_revision} reason="No calibration revision configured." /></KeyValue>
        </KeyValueList>
        {camera.calibration_status !== 'VALID' ? <Reason>Without a validated calibration this camera contributes no floor positions, zone counts, entries/exits, or building figures.</Reason> : null}
      </section>

      <CountHistory camera={camera} />
      <EvidenceMarker camera={camera} onOpenEvidence={onOpenEvidence} />
    </Drawer>
  );
}
