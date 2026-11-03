import { useEffect, useMemo, useRef, useState } from 'react';
import { Expand, FileImage, FileVideo, FolderSearch, RefreshCw, TriangleAlert } from 'lucide-react';
import type { EvidenceRecord } from '../types';
import { api } from '../lib/api';
import { formatNumber, formatTimestamp } from '../lib/format';
import { useResource } from '../hooks/useResource';
import { Badge, Button, ChipGroup, Drawer, EmptyState, ErrorState, IconButton, KeyValue, KeyValueList, LoadingState, Meter, Panel } from '../design-system/components';
import { PageHead } from '../components/Layout';

/** Groups snapshot + clip records captured together (same marker), newest first. */
export function groupEvidence(items: EvidenceRecord[]): Array<{ id: string; records: EvidenceRecord[] }> {
  const groups = new Map<string, EvidenceRecord[]>();
  for (const item of items) {
    const key = item.group_id ?? item.id;
    groups.set(key, [...(groups.get(key) ?? []), item]);
  }
  return [...groups.entries()].map(([id, records]) => ({
    id,
    records: [...records].sort((a, b) => (a.media_type.startsWith('image') ? -1 : 1) - (b.media_type.startsWith('image') ? -1 : 1)),
  }));
}

export function sourceRange(record: EvidenceRecord): string {
  if (record.source_start_s === null) return 'Source time unavailable';
  if (record.source_end_s === null || record.source_end_s === record.source_start_s) return `source ${formatNumber(record.source_start_s, 2)} s`;
  return `source ${formatNumber(record.source_start_s, 2)}–${formatNumber(record.source_end_s, 2)} s`;
}

function EvidenceMedia({ record, large = false }: { record: EvidenceRecord; large?: boolean }) {
  const [failed, setFailed] = useState(false);
  if (!record.file_available || failed) {
    return (
      <div className="evidence-unavailable" data-testid={large ? 'evidence-unavailable-large' : 'evidence-unavailable'}>
        <TriangleAlert size={16} aria-hidden="true" />
        <span>{record.unavailable_reason ?? 'The local file could not be loaded'}. Nothing is shown in its place.</span>
      </div>
    );
  }
  const url = api.evidenceUrl(record.id);
  return record.media_type.startsWith('image/') ? (
    <img className="evidence-media" src={url} alt={`Decoded frame from ${record.camera_id} at ${sourceRange(record)}`}
      data-testid={large ? 'evidence-image-large' : 'evidence-image'} onError={() => setFailed(true)} />
  ) : (
    <video className="evidence-media" src={url} controls preload="metadata" muted playsInline
      data-testid={large ? 'evidence-video-large' : 'evidence-video'} onError={() => setFailed(true)} />
  );
}

function Provenance({ first }: { first: EvidenceRecord }) {
  return (
    <KeyValueList>
      <KeyValue label="Marked at"><span data-testid="evidence-source-time">{first.source_marked_s === null ? 'Unavailable' : `source ${formatNumber(first.source_marked_s, 2)} s`}</span></KeyValue>
      <KeyValue label="Camera / epoch"><code>{first.camera_id} · {first.camera_epoch?.slice(0, 8) ?? 'unavailable'}</code></KeyValue>
      <KeyValue label="Run"><code>{first.run_id?.slice(0, 8) ?? 'unavailable'}</code></KeyValue>
      <KeyValue label="Captured">{formatTimestamp(first.created_at)}</KeyValue>
    </KeyValueList>
  );
}

function Viewer({ group, onClose }: { group: { id: string; records: EvidenceRecord[] }; onClose: () => void }) {
  const first = group.records[0];
  return (
    <Drawer title={`Evidence · ${first.camera_id}`} eyebrow="Evidence viewer" subtitle={sourceRange(first)} onClose={onClose} width={900} testId="evidence-viewer">
      <div className="evidence-viewer">
        {group.records.map((record) => (
          <figure key={record.id} className="evidence-figure">
            <EvidenceMedia record={record} large />
            <figcaption>{record.media_type.startsWith('image/') ? 'Decoded source frame' : 'Source clip (stream copy, keyframe-aligned start)'} · {sourceRange(record)}</figcaption>
          </figure>
        ))}
        <Provenance first={first} />
        <KeyValueList>
          {group.records.map((record) => (
            <KeyValue key={record.id} label={`${record.kind} checksum`}><code className="hash">{record.checksum ? `${record.checksum.slice(0, 24)}…` : 'not recorded'}</code></KeyValue>
          ))}
          <KeyValue label="Timestamp basis">{first.timestamp_basis ?? 'not reported'}</KeyValue>
          <KeyValue label="Trigger">{first.trigger === 'operator_marker' ? 'Operator marker (manual)' : first.trigger ?? first.kind}</KeyValue>
        </KeyValueList>
        {first.note ? <p>“{first.note}”</p> : null}
      </div>
    </Drawer>
  );
}

export function Evidence({ focusId }: { focusId: string | null }) {
  const [refresh, setRefresh] = useState(0);
  const [camera, setCamera] = useState('ALL');
  const [open, setOpen] = useState<string | null>(null);
  const { data, error, loading } = useResource(() => api.evidence(undefined, 200), refresh);
  const groups = useMemo(() => groupEvidence(data?.items ?? []), [data]);
  const cameras = [...new Set(groups.map((group) => group.records[0].camera_id ?? 'unknown'))];
  const shown = groups.filter((group) => camera === 'ALL' || group.records[0].camera_id === camera);
  const focused = focusId ? groups.find((group) => group.id === focusId || group.records.some((record) => record.id === focusId)) : undefined;
  const focusRef = useRef<HTMLElement | null>(null);
  useEffect(() => { focusRef.current?.scrollIntoView({ block: 'center' }); }, [focused?.id]);
  const usage = data?.usage;
  const viewing = groups.find((group) => group.id === open);

  return (
    <>
      <PageHead title="Evidence" description="Decoded source frames and stream-copied source clips, each bound to its run, camera epoch, and source time. Files are local; retention removes the oldest first."
        actions={<IconButton label="Refresh evidence" icon={RefreshCw} spinning={loading} onClick={() => setRefresh((value) => value + 1)} />} />
      <Panel variant="tight">
        <div className="toolbar" style={{ justifyContent: 'space-between' }}>
          <div className="toolbar">
            <span className="toolbar-label">Camera</span>
            <ChipGroup label="Filter evidence by camera" value={camera} onChange={setCamera}
              options={[{ value: 'ALL', label: 'All', count: groups.length }, ...cameras.map((value) => ({ value, label: value, count: groups.filter((group) => group.records[0].camera_id === value).length }))]} />
            {data ? <Badge tone={data.capability.available ? 'good' : 'warning'} title={data.capability.unavailable_reason ?? undefined}>{data.capability.available ? 'Extraction available' : 'Extraction unavailable'}</Badge> : null}
          </div>
          {usage ? (
            <div className="toolbar" aria-label="Retention usage">
              <div className="usage-bar"><span>{usage.records} / {usage.max_records} files</span><Meter value={usage.records} max={usage.max_records} /></div>
              <div className="usage-bar"><span>{formatNumber(usage.bytes / 1048576, 1)} / {formatNumber(usage.max_bytes / 1048576, 0)} MiB · kept ≤ {usage.max_age_days} d</span><Meter value={usage.bytes} max={usage.max_bytes} /></div>
            </div>
          ) : null}
        </div>
        {data && !data.capability.available ? <p className="ds-footnote">{data.capability.unavailable_reason}</p> : null}
      </Panel>
      {loading && !data ? <LoadingState rows={4} /> : null}
      {error ? <ErrorState title="Evidence could not be loaded" message={error} /> : null}
      {focusId && data && !focused ? <ErrorState title="Linked evidence not found" message={`No record ${focusId} is stored. It may have been removed by retention.`} /> : null}
      {!error && data && shown.length === 0 ? (
        <EmptyState icon={FolderSearch} title="No evidence recorded" description="Open a camera with an active epoch and use “Mark evidence”. Nothing is generated automatically for this clip." />
      ) : null}
      <div className="evidence-grid">
        {shown.map((group) => {
          const first = group.records[0];
          const highlighted = focused?.id === group.id;
          return (
            <article key={group.id} ref={highlighted ? (node) => { focusRef.current = node; } : undefined}
              className="ds-panel ds-panel-tight evidence-card" data-testid="evidence-card" data-group={group.id} data-highlighted={highlighted ? 'true' : 'false'}
              data-camera={first.camera_id} data-epoch={first.camera_epoch ?? ''} data-source-marked={first.source_marked_s ?? ''}>
              <div className="alert-row-head">
                <strong style={{ color: 'var(--text-primary)' }}>{first.camera_id}</strong>
                <div className="toolbar">
                  <Badge tone="accent">{first.trigger === 'operator_marker' ? 'Operator marker' : first.trigger ?? first.kind}</Badge>
                  <Button size="sm" variant="ghost" icon={Expand} onClick={() => setOpen(group.id)}>View</Button>
                </div>
              </div>
              <div className="evidence-figures">
                {group.records.map((record) => (
                  <figure key={record.id} className="evidence-figure" data-testid="evidence-record" data-kind={record.kind}
                    data-source-start={record.source_start_s ?? ''} data-source-end={record.source_end_s ?? ''}>
                    <EvidenceMedia record={record} />
                    <figcaption>{record.media_type.startsWith('image/') ? <FileImage size={13} aria-hidden="true" /> : <FileVideo size={13} aria-hidden="true" />}
                      <span>{record.media_type.startsWith('image/') ? 'Frame' : 'Clip'} · {sourceRange(record)}</span></figcaption>
                  </figure>
                ))}
              </div>
              <Provenance first={first} />
              {first.note ? <p>“{first.note}”</p> : null}
            </article>
          );
        })}
      </div>
      {viewing ? <Viewer group={viewing} onClose={() => setOpen(null)} /> : null}
    </>
  );
}
