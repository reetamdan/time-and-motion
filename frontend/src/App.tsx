import { lazy, Suspense, useCallback, useEffect, useState } from 'react';
import { CloudOff } from 'lucide-react';
import type { Camera, ViewId } from './types';
import { useOperations } from './hooks/useOperations';
import { AppShell } from './components/Layout';
import { EmptyState, ErrorState, LoadingState, Panel } from './design-system/components';
import { Overview } from './views/Overview';
import { Cameras } from './views/Cameras';

// Less frequently used views, charts, and the inspector load on demand (Recharts stays out of the first bundle).
const Floors = lazy(() => import('./views/Floors').then((module) => ({ default: module.Floors })));
const Analytics = lazy(() => import('./views/Analytics').then((module) => ({ default: module.Analytics })));
const Alerts = lazy(() => import('./views/Alerts').then((module) => ({ default: module.Alerts })));
const Evidence = lazy(() => import('./views/Evidence').then((module) => ({ default: module.Evidence })));
const System = lazy(() => import('./views/System').then((module) => ({ default: module.System })));
const CameraInspector = lazy(() => import('./components/CameraInspector'));

const VIEWS: ViewId[] = ['overview', 'cameras', 'floors', 'analytics', 'alerts', 'evidence', 'system'];

/** `#evidence?item=<id>` → view "evidence" focused on that record. */
export function parseHash(hash: string): { view: ViewId; item: string | null } {
  const [name, query = ''] = hash.replace(/^#/, '').split('?');
  const view = VIEWS.includes(name as ViewId) ? (name as ViewId) : 'overview';
  return { view, item: new URLSearchParams(query).get('item') };
}

export default function App() {
  const operations = useOperations();
  const [route, setRoute] = useState(() => parseHash(window.location.hash));
  const [cameraId, setCameraId] = useState<string | null>(null);
  const { snapshot } = operations;
  const camera = snapshot?.cameras.find((item) => item.id === cameraId) ?? null;

  useEffect(() => {
    const onHash = () => setRoute(parseHash(window.location.hash));
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const navigate = useCallback((view: ViewId, item: string | null = null) => {
    const hash = item ? `#${view}?item=${encodeURIComponent(item)}` : `#${view}`;
    setRoute({ view, item });
    if (window.location.hash !== hash) window.history.pushState(null, '', hash);
  }, []);
  const openCamera = useCallback((item: Camera) => setCameraId(item.id), []);

  const fallback = <Panel><LoadingState rows={5} /></Panel>;
  let content;
  if (!snapshot) {
    content = operations.loading ? (
      <Panel eyebrow="Aegis Ops · local runtime" title="Connecting to facility operations">
        <p style={{ marginBottom: 12 }}>Waiting for real source, analytics and readiness state from the local API.</p><LoadingState rows={3} />
      </Panel>
    ) : <EmptyState icon={CloudOff} title="Facility snapshot unavailable" description={operations.probeErrors.snapshot ?? 'No snapshot received. Nothing is simulated in its place.'} />;
  } else {
    content = (
      <Suspense fallback={fallback}>
        {operations.probeErrors.snapshot ? <ErrorState title="Showing the last received snapshot" message={operations.probeErrors.snapshot} /> : null}
        {route.view === 'overview' ? <Overview snapshot={snapshot} onOpenCamera={openCamera} onViewChange={(view) => navigate(view)} versions={operations.versions} /> : null}
        {route.view === 'cameras' ? <Cameras snapshot={snapshot} onOpenCamera={openCamera} /> : null}
        {route.view === 'floors' ? <Floors snapshot={snapshot} /> : null}
        {route.view === 'analytics' ? <Analytics snapshot={snapshot} associations={operations.associations} associationsError={operations.probeErrors.associations} /> : null}
        {route.view === 'alerts' ? <Alerts snapshot={snapshot} onAcknowledge={operations.acknowledgeAlert} /> : null}
        {route.view === 'evidence' ? <Evidence focusId={route.item} /> : null}
        {route.view === 'system' ? <System snapshot={snapshot} readiness={operations.readiness} environment={operations.environment} versions={operations.versions}
          health={operations.health} connection={operations.connection} errors={operations.probeErrors} onRefresh={operations.refreshAll} /> : null}
      </Suspense>
    );
  }

  return (
    <AppShell view={route.view} onViewChange={(view) => navigate(view)} snapshot={snapshot} connection={operations.connection} lastUpdate={operations.lastUpdate}
      pendingAction={operations.pendingAction} onAction={operations.runAction} onRefresh={operations.refreshAll}
      replayMode={operations.replayMode} onReplayMode={operations.setReplayMode} actionError={operations.actionError} onDismissError={operations.clearActionError}>
      {content}
      {camera ? (
        <Suspense fallback={null}>
          <CameraInspector key={camera.id} camera={camera} onClose={() => setCameraId(null)}
            onOpenEvidence={(groupId) => { setCameraId(null); navigate('evidence', groupId); }} />
        </Suspense>
      ) : null}
    </AppShell>
  );
}
