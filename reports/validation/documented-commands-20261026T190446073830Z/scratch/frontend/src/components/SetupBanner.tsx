import { AlertTriangle, ArrowRight, Wrench } from 'lucide-react';
import type { ReadinessResponse, Snapshot } from '../types';
import { titleCase } from '../lib/format';

export function SetupBanner({ snapshot, readiness, onOpenSystem }: {
  snapshot: Snapshot | null;
  readiness: ReadinessResponse | null;
  onOpenSystem: () => void;
}) {
  const issues = new Set<string>();
  readiness?.checks.filter((check) => !check.ready).forEach((check) => {
    issues.add(`${titleCase(check.name)}: ${check.detail}`);
  });
  snapshot?.notices.forEach((notice) => issues.add(`${notice.title}: ${notice.message}`));
  snapshot?.cameras.filter((camera) => camera.enabled && camera.health === 'OFFLINE').forEach((camera) => {
    if (camera.unavailable_reason) issues.add(`${camera.name}: ${camera.unavailable_reason}`);
  });

  if (issues.size === 0) return null;
  const visibleIssues = Array.from(issues).slice(0, 2);

  return (
    <section className="setup-banner" aria-labelledby="setup-banner-title">
      <span className="setup-banner-icon"><Wrench size={18} aria-hidden="true" /></span>
      <div className="setup-banner-copy">
        <div className="setup-banner-title-row">
          <h2 id="setup-banner-title">Complete local setup</h2>
          <span><AlertTriangle size={12} aria-hidden="true" />{issues.size} readiness issue{issues.size === 1 ? '' : 's'}</span>
        </div>
        <p>The console remains operational; missing sources or perception runtime stay visibly unavailable.</p>
        <ul>
          {visibleIssues.map((issue) => <li key={issue}>{issue}</li>)}
          {issues.size > visibleIssues.length ? <li>Plus {issues.size - visibleIssues.length} more issue{issues.size - visibleIssues.length === 1 ? '' : 's'} in System.</li> : null}
        </ul>
      </div>
      <button type="button" className="button button-secondary button-small" onClick={onOpenSystem}>Review setup <ArrowRight size={14} aria-hidden="true" /></button>
    </section>
  );
}
