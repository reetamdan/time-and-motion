import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import {
  Activity,
  BarChart3,
  BellRing,
  Cctv,
  FolderSearch,
  LayoutDashboard,
  LoaderCircle,
  LockKeyhole,
  Map as MapIcon,
  Menu,
  Play,
  Radio,
  RefreshCw,
  RotateCcw,
  Settings2,
  ShieldCheck,
  Square,
  Wifi,
  WifiOff,
  X,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { ConnectionState, DemoAction, RunStatus, Snapshot, ViewId } from '../types';
import { formatClock, formatRelativeTime, formatTimestamp, titleCase } from '../lib/format';
import { Modal } from './Modal';
import { Badge, cx } from './ui';

const NAV_ITEMS: Array<{ id: ViewId; label: string; icon: LucideIcon }> = [
  { id: 'overview', label: 'Overview', icon: LayoutDashboard },
  { id: 'cameras', label: 'Live cameras', icon: Cctv },
  { id: 'floors', label: 'Floor plans', icon: MapIcon },
  { id: 'analytics', label: 'Analytics', icon: BarChart3 },
  { id: 'alerts', label: 'Alerts', icon: BellRing },
  { id: 'evidence', label: 'Evidence', icon: FolderSearch },
  { id: 'system', label: 'System', icon: Settings2 },
];

const CONNECTION_COPY: Record<ConnectionState, { label: string; detail: string; tone: 'good' | 'info' | 'warning' | 'danger'; icon: LucideIcon }> = {
  live: { label: 'Live connection', detail: 'WebSocket · local stream', tone: 'good', icon: Wifi },
  connecting: { label: 'Connecting', detail: 'Opening local live channel', tone: 'info', icon: Radio },
  reconnecting: { label: 'Reconnecting', detail: 'REST fallback active · 10 s', tone: 'warning', icon: RefreshCw },
  polling: { label: 'REST polling', detail: 'Live channel unavailable · 10 s', tone: 'info', icon: Radio },
  offline: { label: 'API offline', detail: 'No current local connection', tone: 'danger', icon: WifiOff },
};

function useNow(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}

function RunStatusBadge({ status }: { status?: RunStatus }) {
  const tone = status === 'RUNNING' ? 'good' : status === 'STOP_FAILED' ? 'danger' : status === 'DEGRADED' || status === 'STARTING' ? 'warning' : 'muted';
  return <span className="run-status-badge" data-run-status={status ?? ''}><Badge tone={tone} icon={Activity}>{status ? titleCase(status) : 'Run status unavailable'}</Badge></span>;
}

function TopBar({ snapshot, connection, lastUpdate, pendingAction, onAction, onRefresh }: {
  snapshot: Snapshot | null;
  connection: ConnectionState;
  lastUpdate: string | null;
  pendingAction: DemoAction | null;
  onAction: (action: DemoAction) => Promise<boolean>;
  onRefresh: () => Promise<void>;
}) {
  const now = useNow();
  const [refreshing, setRefreshing] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);
  const runStatus = snapshot?.run_status;
  const startDisabled = !snapshot || Boolean(pendingAction) || runStatus === 'RUNNING' || runStatus === 'STARTING' || runStatus === 'DEGRADED' || runStatus === 'STOP_FAILED';
  const stopDisabled = !snapshot || Boolean(pendingAction) || (runStatus !== 'RUNNING' && runStatus !== 'STARTING' && runStatus !== 'DEGRADED' && runStatus !== 'STOP_FAILED');
  const localZone = Intl.DateTimeFormat().resolvedOptions().timeZone;

  const refresh = async () => {
    setRefreshing(true);
    await onRefresh();
    setRefreshing(false);
  };

  const reset = async () => {
    const succeeded = await onAction('reset');
    if (succeeded) setConfirmReset(false);
  };

  return (
    <header className="topbar">
      <div className="topbar-facility">
        <div className="facility-icon"><ShieldCheck size={18} aria-hidden="true" /></div>
        <div>
          <div className="facility-status-line"><span>Local facility</span><RunStatusBadge status={runStatus} /></div>
          <p>{snapshot?.run_id ? <code>RUN {snapshot.run_id}</code> : 'No active run identifier'}</p>
        </div>
      </div>

      <div className="topbar-time" aria-label="Current UTC and local time">
        <div className="clock-item">
          <span>UTC</span>
          <strong>{formatClock(now, 'UTC')}</strong>
          <small>{new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(now)}</small>
        </div>
        <div className="clock-divider" />
        <div className="clock-item" title={localZone}>
          <span>LOCAL</span>
          <strong>{formatClock(now)}</strong>
          <small>{new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }).format(now)}</small>
        </div>
      </div>

      <div className="topbar-actions">
        <div className="last-update" title={lastUpdate ? formatTimestamp(lastUpdate, 'UTC') : 'No snapshot timestamp has been received.'}>
          <span>LAST UPDATE</span>
          <strong>{lastUpdate ? formatRelativeTime(lastUpdate, now.getTime()) : 'Unavailable'}</strong>
        </div>
        <button type="button" className="icon-button refresh-button" onClick={() => void refresh()} disabled={refreshing} aria-label="Refresh all local API data" title="Refresh all local API data">
          <RefreshCw size={16} className={refreshing ? 'spin' : ''} aria-hidden="true" />
        </button>
        <div className="run-controls" aria-label="Run controls">
          <button type="button" className="button button-primary" onClick={() => void onAction('start')} disabled={startDisabled} title={!snapshot ? 'Waiting for a facility snapshot from the local API.' : runStatus === 'RUNNING' ? 'A run is already active.' : 'Start local sources and analytics'}>
            {pendingAction === 'start' ? <LoaderCircle size={15} className="spin" aria-hidden="true" /> : <Play size={15} aria-hidden="true" />}
            <span>{pendingAction === 'start' ? 'Starting…' : 'Start demo'}</span>
          </button>
          <button type="button" className="button button-secondary" onClick={() => void onAction('stop')} disabled={stopDisabled} title={stopDisabled ? 'Stop is available while a run is active.' : 'Stop the active run'}>
            {pendingAction === 'stop' ? <LoaderCircle size={15} className="spin" aria-hidden="true" /> : <Square size={14} aria-hidden="true" />}
            <span>{pendingAction === 'stop' ? 'Stopping…' : 'Stop'}</span>
          </button>
          <button type="button" className="button button-quiet" onClick={() => setConfirmReset(true)} disabled={!snapshot || Boolean(pendingAction)} title="Stop and clear run-scoped analytics, alerts, and history">
            <RotateCcw size={15} aria-hidden="true" />
            <span>Reset</span>
          </button>
        </div>
      </div>
      <span className="sr-only" role="status">{connection === 'live' ? 'Live local connection active.' : `Connection state: ${CONNECTION_COPY[connection].label}`}</span>

      {confirmReset ? (
        <Modal title="Reset run-scoped data?" subtitle="This action calls the local reset endpoint." onClose={() => setConfirmReset(false)} footer={
          <div className="modal-action-row">
            <button type="button" className="button button-secondary" onClick={() => setConfirmReset(false)} disabled={pendingAction === 'reset'}>Cancel</button>
            <button type="button" className="button button-danger" onClick={() => void reset()} disabled={pendingAction === 'reset'}>
              {pendingAction === 'reset' ? <LoaderCircle size={15} className="spin" aria-hidden="true" /> : <RotateCcw size={15} aria-hidden="true" />}
              {pendingAction === 'reset' ? 'Resetting…' : 'Reset run data'}
            </button>
          </div>
        }>
          <div className="reset-confirmation">
            <AlertResetIcon />
            <p>The active run will stop and its analytics, alerts, and history will be cleared. Configurations, models, and calibration are preserved.</p>
          </div>
        </Modal>
      ) : null}
    </header>
  );
}

function AlertResetIcon() {
  return <span className="reset-icon"><RotateCcw size={22} aria-hidden="true" /></span>;
}

export function AppShell({ children, view, onViewChange, snapshot, connection, lastUpdate, pendingAction, onAction, onRefresh }: {
  children: ReactNode;
  view: ViewId;
  onViewChange: (view: ViewId) => void;
  snapshot: Snapshot | null;
  connection: ConnectionState;
  lastUpdate: string | null;
  pendingAction: DemoAction | null;
  onAction: (action: DemoAction) => Promise<boolean>;
  onRefresh: () => Promise<void>;
}) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const connectionCopy = CONNECTION_COPY[connection];
  const ConnectionIcon = connectionCopy.icon;
  const openAlerts = snapshot?.alerts.filter((alert) => alert.status === 'OPEN').length;

  const navigate = (next: ViewId) => {
    onViewChange(next);
    setMobileOpen(false);
    window.scrollTo({ top: 0, behavior: 'auto' });
  };

  return (
    <div className="app-shell">
      <a href="#main-content" className="skip-link">Skip to main content</a>
      {mobileOpen ? <button type="button" className="mobile-nav-scrim" onClick={() => setMobileOpen(false)} aria-label="Close navigation" /> : null}
      <aside className={cx('sidebar', mobileOpen && 'sidebar-open')}>
        <div className="brand">
          <div className="brand-mark"><ShieldCheck size={24} aria-hidden="true" /><span /></div>
          <div className="brand-wordmark"><strong>Aegis</strong><span>OPS</span></div>
          <button type="button" className="icon-button mobile-nav-close" onClick={() => setMobileOpen(false)} aria-label="Close navigation"><X size={18} /></button>
        </div>
        <div className="nav-section-label">OPERATIONS</div>
        <nav className="primary-nav" aria-label="Primary navigation">
          {NAV_ITEMS.map(({ id, label, icon: Icon }) => (
            <button key={id} type="button" className={cx('nav-item', view === id && 'nav-item-active')} onClick={() => navigate(id)} aria-current={view === id ? 'page' : undefined} title={label}>
              <span className="nav-icon"><Icon size={18} strokeWidth={1.8} aria-hidden="true" /></span>
              <span className="nav-label">{label}</span>
              {id === 'alerts' && openAlerts ? <span className="nav-count" aria-label={`${openAlerts} open alerts`}>{openAlerts}</span> : null}
              {view === id ? <span className="nav-active-rail" aria-hidden="true" /> : null}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="privacy-status">
            <LockKeyhole size={15} aria-hidden="true" />
            <div><strong>LOCAL ONLY</strong><span>Private by architecture</span></div>
          </div>
          <div className={cx('connection-status', `connection-${connectionCopy.tone}`)} role="status">
            <ConnectionIcon size={15} aria-hidden="true" />
            <div><strong>{connectionCopy.label}</strong><span>{connectionCopy.detail}</span></div>
          </div>
          <p className="privacy-note">No cloud media or hosted telemetry.</p>
        </div>
      </aside>

      <div className="app-workspace">
        <div className="mobile-topline">
          <button type="button" className="icon-button" onClick={() => setMobileOpen(true)} aria-label="Open navigation" aria-expanded={mobileOpen}><Menu size={20} /></button>
          <div className="mobile-brand"><ShieldCheck size={19} /><strong>Aegis Ops</strong></div>
          <RunStatusBadge status={snapshot?.run_status} />
        </div>
        <TopBar snapshot={snapshot} connection={connection} lastUpdate={lastUpdate} pendingAction={pendingAction} onAction={onAction} onRefresh={onRefresh} />
        <main id="main-content" className="main-content" tabIndex={-1}>{children}</main>
      </div>
    </div>
  );
}
