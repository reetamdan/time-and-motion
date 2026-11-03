import { useEffect, useMemo, useState } from 'react';
import { FileImage, FileVideo, FolderSearch, RefreshCw, TriangleAlert } from 'lucide-react';
import type { EvidenceList, EvidenceRecord } from '../types';
import { api, errorMessage } from '../lib/api';
import { formatNumber, formatTimestamp } from '../lib/format';
import { PageHeader } from '../components/PageHeader';
import { Badge, EmptyState, LoadingBlock } from '../components/ui';

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

function EvidenceMedia({ record }: { record: EvidenceRecord }) {
  const [failed, setFailed] = useState(false);
  if (!record.file_available || failed) {
    return (
      <div className="evidence-unavailable" data-testid="evidence-unavailable">
        <TriangleAlert size={16} aria-hidden="true" />
        <span>{record.unavailable_reason ?? 'The local file could not be loaded'}. Nothing is shown in its place.</span>
      </div>
    );
  }
  const url = api.evidenceUrl(record.id);
  return record.media_type.startsWith('image/') ? (
    <img className="evidence-media" src={url} alt={`Snapshot from ${record.camera_id} at ${sourceRange(record)}`}
      data-testid="evidence-image" onError={() => setFailed(true)} />
  ) : (
    <video className="evidence-media" src={url} controls preload="metadata" muted playsInline data-testid="evidence-video"
      onError={() => setFailed(true)} />
  );
}

export function Evidence() {
  const [list, setList] = useState<EvidenceList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.evidence()
      .then((value) => { if (!cancelled) { setList(value); setError(null); } })
      .catch((reason: unknown) => { if (!cancelled) setError(errorMessage(reason)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [refresh]);

  const groups = useMemo(() => groupEvidence(list?.items ?? []), [list]);
  const usage = list?.usage;

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow="OPERATIONS / EVIDENCE"
        title="Local evidence"
        description="Real source frames and stream-copied source clips, each tied to its run, camera epoch, and source time."
        actions={(
          <div className="heading-badges">
            <Badge tone={list?.capability.available ? 'info' : 'warning'}>
              {list ? (list.capability.available ? 'Extraction available' : 'Extraction unavailable') : 'Capability unknown'}
            </Badge>
            {usage ? <Badge tone="muted">{`${usage.records} / ${usage.max_records} records · ${formatNumber(usage.bytes / 1048576, 1)} MiB`}</Badge> : null}
            <button type="button" className="icon-button" onClick={() => setRefresh((value) => value + 1)} aria-label="Refresh evidence" title="Refresh evidence">
              <RefreshCw size={15} className={loading ? 'spin' : ''} />
            </button>
          </div>
        )}
      />
      {list && !list.capability.available ? <p className="panel-footnote">{list.capability.unavailable_reason}</p> : null}
      {loading && !list ? <LoadingBlock rows={4} /> : null}
      {error ? <EmptyState icon={FolderSearch} title="Evidence could not be loaded" description={error} /> : null}
      {!error && list && groups.length === 0 ? (
        <EmptyState icon={FolderSearch} title="No evidence recorded"
          description="Open a camera with an active epoch and use “Mark evidence”. Nothing is generated automatically for this clip." />
      ) : null}
      <div className="evidence-grid">
        {groups.map((group) => {
          const first = group.records[0];
          return (
            <article key={group.id} className="panel evidence-card" data-testid="evidence-card" data-group={group.id}
              data-camera={first.camera_id} data-epoch={first.camera_epoch ?? ''} data-source-marked={first.source_marked_s ?? ''}>
              <header className="evidence-card-header">
                <strong>{first.camera_id}</strong>
                <Badge tone="warning">{first.trigger === 'operator_marker' ? 'Operator marker (manual)' : first.trigger ?? first.kind}</Badge>
              </header>
              <dl className="key-value-grid evidence-facts">
                <div><dt>Marked at</dt><dd data-testid="evidence-source-time">{first.source_marked_s === null ? 'Unavailable' : `source ${formatNumber(first.source_marked_s, 2)} s`}</dd></div>
                <div><dt>Camera epoch</dt><dd><code>{first.camera_epoch?.slice(0, 8) ?? 'Unavailable'}</code></dd></div>
                <div><dt>Run</dt><dd><code>{first.run_id?.slice(0, 8) ?? 'Unavailable'}</code></dd></div>
                <div><dt>Captured</dt><dd>{formatTimestamp(first.created_at)}</dd></div>
              </dl>
              {first.note ? <p className="drawer-copy">“{first.note}”</p> : null}
              {group.records.map((record) => (
                <figure key={record.id} className="evidence-figure" data-testid="evidence-record" data-kind={record.kind}
                  data-source-start={record.source_start_s ?? ''} data-source-end={record.source_end_s ?? ''}>
                  <EvidenceMedia record={record} />
                  <figcaption>
                    {record.media_type.startsWith('image/') ? <FileImage size={13} aria-hidden="true" /> : <FileVideo size={13} aria-hidden="true" />}
                    <span>{record.media_type.startsWith('image/') ? 'Decoded frame' : 'Source clip (stream copy, keyframe-aligned start)'} · {sourceRange(record)}</span>
                  </figcaption>
                </figure>
              ))}
              <p className="panel-footnote">{first.label ?? ''} Source: {String(first.metadata.source_label ?? 'unknown')}. {first.timestamp_basis ?? ''}.</p>
            </article>
          );
        })}
      </div>
    </div>
  );
}
