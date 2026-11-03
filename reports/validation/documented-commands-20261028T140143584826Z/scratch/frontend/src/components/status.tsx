import { AlertTriangle, Check, CheckCheck, CheckCircle2, CircleOff, Crosshair, Eye, Info, Sigma, XCircle, BellRing } from 'lucide-react';
import type { AlertSeverity, AlertStatus, Calibration, Health, Quality, RunStatus } from '../types';
import { titleCase } from '../lib/format';
import { Badge } from '../design-system/components';
import type { Tone } from '../design-system/components';

export function runStatusTone(status?: RunStatus): Tone {
  if (status === 'RUNNING') return 'good';
  if (status === 'STOP_FAILED') return 'danger';
  if (status === 'DEGRADED' || status === 'STARTING') return 'warning';
  return 'muted';
}

/** The run status; data-run-status carries the raw value for automation and is present at every width. */
export function RunStatusPill({ status }: { status?: RunStatus }) {
  return (
    <span className="run-status-pill" data-run-status={status ?? ''} role="status" aria-live="polite">
      <span className={status === 'RUNNING' || status === 'STARTING' ? 'ds-dot ds-dot-live' : 'ds-dot'} aria-hidden="true" />
      {status ? titleCase(status) : 'Run status unavailable'}
    </span>
  );
}

export function HealthBadge({ health }: { health: Health }) {
  if (health === 'HEALTHY') return <Badge tone="good" icon={CheckCircle2}>Healthy</Badge>;
  if (health === 'OFFLINE') return <Badge tone="danger" icon={XCircle}>Offline</Badge>;
  return <Badge tone="warning" icon={AlertTriangle}>{titleCase(health)}</Badge>;
}

export function CalibrationBadge({ status }: { status: Calibration }) {
  return status === 'VALID'
    ? <Badge tone="good" icon={Crosshair}>Calibrated</Badge>
    : <Badge tone="warning" icon={AlertTriangle}>{`Calibration ${titleCase(status).toLowerCase()}`}</Badge>;
}

export function QualityBadge({ quality }: { quality: Quality }) {
  const Icon = quality === 'OBSERVED' ? Eye : quality === 'ESTIMATED' ? Sigma : CircleOff;
  return <Badge tone={quality === 'OBSERVED' ? 'good' : quality === 'ESTIMATED' ? 'info' : 'muted'} icon={Icon}>{titleCase(quality)}</Badge>;
}

export function SeverityBadge({ severity }: { severity: AlertSeverity }) {
  return <Badge tone={severity === 'INFO' ? 'info' : severity === 'WARNING' ? 'warning' : 'danger'} icon={severity === 'INFO' ? Info : AlertTriangle}>{titleCase(severity)}</Badge>;
}

export function AlertStatusBadge({ status }: { status: AlertStatus }) {
  const Icon = status === 'OPEN' ? BellRing : status === 'ACKNOWLEDGED' ? CheckCheck : Check;
  return <Badge tone={status === 'OPEN' ? 'warning' : status === 'ACKNOWLEDGED' ? 'info' : 'good'} icon={Icon}>{titleCase(status)}</Badge>;
}

/** Detector acceptance as recorded in configuration; never upgraded by the UI. */
export function AcceptanceBadge({ acceptance }: { acceptance: string | null | undefined }) {
  if (acceptance === 'accepted') return <Badge tone="good">Accepted baseline</Badge>;
  if (acceptance === 'provisional_smoke') return <Badge tone="warning" title="Runs real detections but has not been evaluated against labelled data">Provisional · unevaluated</Badge>;
  if (acceptance === 'candidate_unaccepted') return <Badge tone="accent">Candidate · unaccepted</Badge>;
  return <Badge tone="muted">Acceptance unknown</Badge>;
}
