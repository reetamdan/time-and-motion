import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import {
  BarChart3, BellRing, Cctv, FolderSearch, LayoutDashboard, LockKeyhole, Map as MapIcon, Menu, Moon, Play,
  Radio, RefreshCw, RotateCcw, Settings2, ShieldAlert, ShieldCheck, Square, Sun, Wifi, WifiOff, X,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { ActionFailure, ConnectionState, DemoAction, ReplayMode, Snapshot, ViewId } from '../types';
import { formatRelativeTime, formatTimestamp } from '../lib/format';
import { Button, Callout, Dialog, Drawer, IconButton, Segmented, cx } from '../design-system/components';
import { RunStatusPill } from './status';

export const NAV_ITEMS: Array<{ id: ViewId; label: string; icon: LucideIcon }> = [
  { id: 'overview', label: 'Overview', icon: LayoutDashboard },
  { id: 'cameras', label: 'Cameras', icon: Cctv },
  { id: 'floors', label: 'Floors', icon: MapIcon },
  { id: 'analytics', label: 'Analytics', icon: BarChart3 },
  { id: 'alerts', label: 'Alerts', icon: BellRing },
  { id: 'evidence', label: 'Evidence', icon: FolderSearch },
  { id: 'system', label: 'System', icon: Settings2 },
];

const CONNECTION: Record<ConnectionState, { label: string; tone: string; icon: LucideIcon }> = {
  live: { label: 'Live connection', tone: 'connection-live', icon: Wifi },
  connecting: { label: 'Connecting', tone: 'connection-warn', icon: Radio },
  reconnecting: { label: 'Reconnecting', tone: 'connection-warn', icon: RefreshCw },
  polling: { label: 'REST polling', tone: 'connection-warn', icon: Radio },
  offline: { label: 'API offline', tone: 'connection-down', icon: WifiOff },
};

type Theme = 'dark' | 'light';

function readTheme(): Theme {
  try { return window.localStorage.getItem('aegis-theme') === 'light' ? 'light' : 'dark'; } catch { return 'dark'; }
}

function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(readTheme);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { window.localStorage.setItem('aegis-theme', theme); } catch { /* per-viewer preference only */ }
  }, [theme]);
  return [theme, () => setTheme((current) => (current === 'dark' ? 'light' : 'dark'))];
}

function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}

function NavList({ view, onNavigate, openAlerts }: { view: ViewId; onNavigate: (view: ViewId) => void; openAlerts?: number }) {
  return (
    <>
      {NAV_ITEMS.map(({ id, label, icon: Icon }) => (
        <button key={id} type="button" className="nav-item" aria-current={view === id ? 'page' : undefined} onClick={() => onNavigate(id)} title={label}
          aria-label={id === 'alerts' && openAlerts ? `${label}, ${openAlerts} open` : label}>
          <Icon size={18} strokeWidth={1.9} aria-hidden="true" />
          <span className="nav-label">{label}</span>
          {id === 'alerts' && openAlerts ? <span className="nav-count" aria-hidden="true">{openAlerts}</span> : null}
        </button>
      ))}
    </>
  );
}

/** Persistent run status, connection, replay mode, and Start/Stop/Reset at every breakpoint. */
function RunBar({ snapshot, connection, lastUpdate, pendingAction, onAction, onRefresh, replayMode, onReplayMode, onOpenNav }: {
  snapshot: Snapshot | null;
  connection: ConnectionState;
  lastUpdate: string | null;
  pendingAction: DemoAction | null;
  onAction: (action: DemoAction) => Promise<boolean>;
  onRefresh: () => Promise<void>;
  replayMode: ReplayMode;
  onReplayMode: (mode: ReplayMode) => void;
  onOpenNav: () => void;
}) {
  const now = useNow();
  const [refreshing, setRefreshing] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);
  const status = snapshot?.run_status;
  const active = status === 'RUNNING' || status === 'STARTING' || status === 'DEGRADED';
  const startDisabled = !snapshot || Boolean(pendingAction) || active || status === 'STOP_FAILED';
  const stopDisabled = !snapshot || Boolean(pendingAction) || !(active || status === 'STOP_FAILED');
  const connectionCopy = CONNECTION[connection];
  const ConnectionIcon = connectionCopy.icon;

  const refresh = async () => { setRefreshing(true); await onRefresh(); setRefreshing(false); };
  const reset = async () => { if (await onAction('reset')) setConfirmReset(false); };

  return (
    <div className="runbar">
      <div className="runbar-mobile">
        <IconButton label="Open navigation" icon={Menu} onClick={onOpenNav} />
        <span className="brand-mark" style={{ width: 28, height: 28, borderRadius: 8 }}><ShieldCheck size={16} aria-hidden="true" /></span>
      </div>
      <div className="runbar-status">
        <RunStatusPill status={status} />
        <div className="run-meta">
          <span>{snapshot?.run_id ? <>Run <code>{snapshot.run_id.slice(0, 8)}</code></> : 'No active run'}</span>
          <span title={lastUpdate ? formatTimestamp(lastUpdate, 'UTC') : undefined}>Updated {lastUpdate ? formatRelativeTime(lastUpdate, now) : 'never'}</span>
        </div>
      </div>
      <span className="runbar-spacer" />
      <div className="runbar-signal">
        <span className={cx('connection-pill', connectionCopy.tone)} role="status"><ConnectionIcon size={14} aria-hidden="true" />{connectionCopy.label}</span>
        <IconButton label="Refresh all local API data" icon={RefreshCw} spinning={refreshing} disabled={refreshing} onClick={() => void refresh()} />
      </div>
      <div className="runbar-controls" aria-label="Run controls" role="group">
        <Segmented<ReplayMode> label="Replay mode for the next start" value={replayMode} onChange={onReplayMode} disabled={active || Boolean(pendingAction)}
          options={[
            { value: 'paced', label: 'Paced', title: 'Release frames on the source timeline (operations view)' },
            { value: 'unpaced', label: 'Unpaced', title: 'Process as fast as the GPU allows (evaluation; not live presentation)' },
          ]} />
        <Button variant="primary" icon={Play} loading={pendingAction === 'start'} disabled={startDisabled} onClick={() => void onAction('start')}
          title={status === 'STOP_FAILED' ? 'Start is blocked until cleanup of the previous run is confirmed' : active ? 'A run is already active' : `Start a ${replayMode} run`}>
          Start demo
        </Button>
        <Button icon={Square} loading={pendingAction === 'stop'} disabled={stopDisabled} onClick={() => void onAction('stop')}
          title={stopDisabled ? 'Stop is available while a run is active or cleanup is unconfirmed' : 'Stop the run and release its containers'}>
          Stop
        </Button>
        <Button variant="ghost" icon={RotateCcw} disabled={!snapshot || Boolean(pendingAction)} onClick={() => setConfirmReset(true)}
          title="Stop and clear run-scoped analytics, alerts, and history">Reset</Button>
      </div>
      {confirmReset ? (
        <Dialog title="Reset run-scoped data?" subtitle="Calls POST /api/v1/demo/reset" onClose={() => setConfirmReset(false)} actions={(
          <>
            <Button onClick={() => setConfirmReset(false)} disabled={pendingAction === 'reset'}>Cancel</Button>
            <Button variant="danger" icon={RotateCcw} loading={pendingAction === 'reset'} disabled={pendingAction === 'reset'} onClick={() => void reset()}>Reset run data</Button>
          </>
        )}>
          <p>The active run stops and its analytics, alerts, and count history are cleared. Configuration, models, calibration files, and evidence files are kept. Reset is refused while container cleanup is unconfirmed.</p>
        </Dialog>
      ) : null}
    </div>
  );
}

/** STOP_FAILED / refused-action state, shown under the run bar at every width. */
function CleanupBanner({ snapshot, onAction, pendingAction }: { snapshot: Snapshot | null; onAction: (action: DemoAction) => Promise<boolean>; pendingAction: DemoAction | null }) {
  if (snapshot?.run_status !== 'STOP_FAILED') return null;
  const failures = snapshot.alerts.filter((alert) => alert.stable_key.startsWith('camera-cleanup-failed:') && alert.status !== 'RESOLVED');
  const cameras = snapshot.cameras.filter((camera) => camera.unavailable_reason?.startsWith('Stop not confirmed'));
  return (
    <div className="cleanup-banner" role="alert" data-testid="cleanup-banner">
      <ShieldAlert size={18} aria-hidden="true" />
      <div>
        <strong>Stop not confirmed — owned containers may still be running</strong>
        <p>{cameras.length ? cameras.map((camera) => `${camera.id}: ${camera.unavailable_reason}`).join(' · ')
          : failures.length ? failures.map((alert) => alert.message).join(' · ') : 'Cleanup of the previous run could not be confirmed.'} Start and Reset stay blocked until a retry confirms release.</p>
      </div>
      <Button variant="danger" size="sm" icon={Square} loading={pendingAction === 'stop'} disabled={Boolean(pendingAction)} onClick={() => void onAction('stop')}>Retry cleanup</Button>
    </div>
  );
}

function ActionError({ failure, onDismiss }: { failure: ActionFailure; onDismiss: () => void }) {
  const cleanup = failure.code === 'CLEANUP_UNCONFIRMED';
  return (
    <div className="ds-toast">
      <Callout tone="danger" role="alert" testId="action-error"
        title={cleanup ? `${failure.action === 'start' ? 'Start' : 'Reset'} refused (HTTP ${failure.status} ${failure.code})` : `Run control failed${failure.status ? ` (HTTP ${failure.status})` : ''}`}
        actions={<IconButton label="Dismiss run control error" icon={X} onClick={onDismiss} />}>
        <p>{failure.message}</p>
        {cleanup ? <p>Use “Retry cleanup” or Stop until the previous run’s containers are confirmed released.</p> : null}
      </Callout>
    </div>
  );
}

export function AppShell({ children, view, onViewChange, snapshot, connection, lastUpdate, pendingAction, onAction, onRefresh,
  replayMode, onReplayMode, actionError, onDismissError }: {
  children: ReactNode;
  view: ViewId;
  onViewChange: (view: ViewId) => void;
  snapshot: Snapshot | null;
  connection: ConnectionState;
  lastUpdate: string | null;
  pendingAction: DemoAction | null;
  onAction: (action: DemoAction) => Promise<boolean>;
  onRefresh: () => Promise<void>;
  replayMode: ReplayMode;
  onReplayMode: (mode: ReplayMode) => void;
  actionError: ActionFailure | null;
  onDismissError: () => void;
}) {
  const [navOpen, setNavOpen] = useState(false);
  const [theme, toggleTheme] = useTheme();
  const openAlerts = snapshot?.alerts.filter((alert) => alert.status === 'OPEN').length;
  const navigate = (next: ViewId) => { onViewChange(next); setNavOpen(false); window.scrollTo({ top: 0 }); };

  return (
    <div className="shell">
      <a href="#main-content" className="ds-skip-link">Skip to main content</a>
      <aside className="sidebar" aria-label="Aegis Ops">
        <div className="brand">
          <span className="brand-mark"><ShieldCheck size={19} aria-hidden="true" /></span>
          <span className="brand-name"><strong>Aegis Ops</strong><span>Local operations</span></span>
        </div>
        <nav className="nav" aria-label="Primary navigation"><NavList view={view} onNavigate={navigate} openAlerts={openAlerts} /></nav>
        <div className="sidebar-foot">
          <button type="button" className="nav-item" onClick={toggleTheme} aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`} title="Theme">
            {theme === 'dark' ? <Sun size={18} aria-hidden="true" /> : <Moon size={18} aria-hidden="true" />}<span className="nav-label theme-label">{theme === 'dark' ? 'Light theme' : 'Dark theme'}</span>
          </button>
          <div className="sidebar-note" title="No cloud media, no hosted telemetry, no model key">
            <LockKeyhole size={15} aria-hidden="true" />
            <div><strong>Local only</strong>No cloud media or hosted telemetry.</div>
          </div>
        </div>
      </aside>

      <div className="workspace">
        <header className="runbar-wrap">
          <RunBar snapshot={snapshot} connection={connection} lastUpdate={lastUpdate} pendingAction={pendingAction} onAction={onAction}
            onRefresh={onRefresh} replayMode={replayMode} onReplayMode={onReplayMode} onOpenNav={() => setNavOpen(true)} />
          <CleanupBanner snapshot={snapshot} onAction={onAction} pendingAction={pendingAction} />
        </header>
        <main id="main-content" className="main" tabIndex={-1}>{children}</main>
      </div>

      {navOpen ? (
        <Drawer title="Aegis Ops" eyebrow="Navigation" side="left" width={300} onClose={() => setNavOpen(false)}>
          <nav className="mobile-nav" aria-label="Primary navigation (mobile)"><NavList view={view} onNavigate={navigate} openAlerts={openAlerts} /></nav>
          <Button variant="ghost" icon={theme === 'dark' ? Sun : Moon} onClick={toggleTheme}>{theme === 'dark' ? 'Light theme' : 'Dark theme'}</Button>
        </Drawer>
      ) : null}
      {actionError ? <ActionError failure={actionError} onDismiss={onDismissError} /> : null}
    </div>
  );
}

export function PageHead({ title, description, actions }: { title: string; description: string; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div><h1>{title}</h1><p>{description}</p></div>
      {actions ? <div className="page-head-actions">{actions}</div> : null}
    </div>
  );
}
