import { lazy, Suspense, useCallback, useEffect, useState } from 'react';
import { AlertTriangle, ShieldCheck, X } from 'lucide-react';
import type { Camera, ViewId } from './types';
import { useOperations } from './hooks/useOperations';
import { AppShell } from './components/Layout';
import { CameraDetail } from './components/Camera';
import { SetupBanner } from './components/SetupBanner';
import { ErrorNotice, LoadingBlock } from './components/ui';
import { Overview } from './views/Overview';
import { Cameras } from './views/Cameras';
import { Floors } from './views/Floors';
import { Alerts } from './views/Alerts';
import { System } from './views/System';

const Analytics = lazy(() => import('./views/Analytics').then((module) => ({ default: module.Analytics })));
const VIEWS: ViewId[] = ['overview', 'cameras', 'floors', 'analytics', 'alerts', 'system'];

function initialView(): ViewId {
  const hash = window.location.hash.replace('#', '') as ViewId;
  return VIEWS.includes(hash) ? hash : 'overview';
}

export default function App() {
  const operations = useOperations();
  const [view, setView] = useState<ViewId>(initialView);
  const [selectedCameraId, setSelectedCameraId] = useState<string | null>(null);
  const { snapshot } = operations;
  const selectedCamera = snapshot?.cameras.find((camera) => camera.id === selectedCameraId) ?? null;

  useEffect(() => {
    const onHashChange = () => setView(initialView());
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  const navigate = useCallback((next: ViewId) => {
    setView(next);
    if (window.location.hash !== `#${next}`) window.history.pushState(null, '', `#${next}`);
  }, []);

  const openCamera = useCallback((camera: Camera) => setSelectedCameraId(camera.id), []);
  const closeCamera = useCallback(() => setSelectedCameraId(null), []);

  return (
    <AppShell
      view={view}
      onViewChange={navigate}
      snapshot={snapshot}
      connection={operations.connection}
      lastUpdate={operations.lastUpdate}
      pendingAction={operations.pendingAction}
      onAction={operations.runAction}
      onRefresh={operations.refreshAll}
    >
      {operations.loading && !snapshot ? (
        <div className="initial-loading" role="status">
          <span><ShieldCheck size={28} aria-hidden="true" /></span>
          <p className="eyebrow">AEGIS OPS / LOCAL RUNTIME</p>
          <h1>Connecting to facility operations</h1>
          <p>Waiting for actual source, analytics, and readiness state.</p>
          <LoadingBlock rows={3} />
        </div>
      ) : (
        <>
          {operations.probeErrors.snapshot && snapshot ? <div className="stale-notice"><ErrorNotice title="Showing the last received snapshot" message={operations.probeErrors.snapshot} /></div> : null}
          {view === 'overview' ? <SetupBanner snapshot={snapshot} readiness={operations.readiness} onOpenSystem={() => navigate('system')} /> : null}

          {view === 'overview' ? <Overview snapshot={snapshot} onOpenCamera={openCamera} onViewChange={navigate} snapshotError={operations.probeErrors.snapshot} /> : null}
          {view === 'cameras' ? <Cameras snapshot={snapshot} onOpenCamera={openCamera} snapshotError={operations.probeErrors.snapshot} /> : null}
          {view === 'floors' ? <Floors snapshot={snapshot} snapshotError={operations.probeErrors.snapshot} /> : null}
          {view === 'analytics' ? <Suspense fallback={<div className="panel"><LoadingBlock rows={5} /></div>}><Analytics snapshot={snapshot} associations={operations.associations} associationsError={operations.probeErrors.associations} /></Suspense> : null}
          {view === 'alerts' ? <Alerts snapshot={snapshot} onAcknowledge={operations.acknowledgeAlert} /> : null}
          {view === 'system' ? <System snapshot={snapshot} readiness={operations.readiness} environment={operations.environment} versions={operations.versions} health={operations.health} connection={operations.connection} errors={operations.probeErrors} onRefresh={operations.refreshAll} /> : null}
        </>
      )}

      {operations.actionError ? (
        <div className="toast toast-error" role="alert">
          <AlertTriangle size={18} aria-hidden="true" />
          <div><strong>Run control failed</strong><p>{operations.actionError}</p></div>
          <button type="button" className="icon-button" onClick={operations.clearActionError} aria-label="Dismiss run control error"><X size={16} /></button>
        </div>
      ) : null}
      {selectedCamera ? <CameraDetail key={selectedCamera.id} camera={selectedCamera} onClose={closeCamera} /> : null}
    </AppShell>
  );
}
