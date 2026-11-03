import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError, errorMessage } from '../lib/api';
import { applyLiveEvent, isLiveEvent, MAX_RECONNECT_ATTEMPTS, reconnectDelay } from '../lib/live';
import type {
  ActionFailure,
  Alert,
  AssociationRecord,
  ConnectionState,
  DemoAction,
  EnvironmentResponse,
  HealthLive,
  ReadinessResponse,
  ReplayMode,
  Snapshot,
  VersionsResponse,
} from '../types';

export interface HealthProbe extends HealthLive {
  rttMs: number;
}

type ProbeName = 'snapshot' | 'readiness' | 'environment' | 'versions' | 'associations' | 'health';
type ProbeErrors = Partial<Record<ProbeName, string>>;

export function useOperations() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [connection, setConnection] = useState<ConnectionState>('connecting');
  const [readiness, setReadiness] = useState<ReadinessResponse | null>(null);
  const [environment, setEnvironment] = useState<EnvironmentResponse | null>(null);
  const [versions, setVersions] = useState<VersionsResponse | null>(null);
  const [associations, setAssociations] = useState<AssociationRecord[]>([]);
  const [health, setHealth] = useState<HealthProbe | null>(null);
  const [probeErrors, setProbeErrors] = useState<ProbeErrors>({});
  const [lastUpdate, setLastUpdate] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [pendingAction, setPendingAction] = useState<DemoAction | null>(null);
  const [actionError, setActionError] = useState<ActionFailure | null>(null);
  const [replayMode, setReplayMode] = useState<ReplayMode>('paced');
  const snapshotRef = useRef<Snapshot | null>(null);
  const socketLiveRef = useRef(false);
  const mountedRef = useRef(true);

  const setProbeError = useCallback((name: ProbeName, message?: string) => {
    if (!mountedRef.current) return;
    setProbeErrors((current) => {
      const next = { ...current };
      if (message) next[name] = message;
      else delete next[name];
      return next;
    });
  }, []);

  const commitSnapshot = useCallback((next: Snapshot) => {
    if (!mountedRef.current) return;
    snapshotRef.current = next;
    setSnapshot(next);
    setLastUpdate(next.generated_at);
  }, []);

  const fetchSnapshot = useCallback(async (): Promise<boolean> => {
    try {
      const next = await api.snapshot();
      commitSnapshot(next);
      setProbeError('snapshot');
      if (!socketLiveRef.current && mountedRef.current) setConnection('polling');
      return true;
    } catch (error) {
      setProbeError('snapshot', errorMessage(error));
      if (!socketLiveRef.current && mountedRef.current) setConnection('offline');
      return false;
    }
  }, [commitSnapshot, setProbeError]);

  const fetchReadiness = useCallback(async () => {
    try {
      const next = await api.readiness();
      if (mountedRef.current) setReadiness(next);
      setProbeError('readiness');
    } catch (error) {
      setProbeError('readiness', errorMessage(error));
    }
  }, [setProbeError]);

  const fetchEnvironment = useCallback(async () => {
    try {
      const next = await api.environment();
      if (mountedRef.current) setEnvironment(next);
      setProbeError('environment');
    } catch (error) {
      setProbeError('environment', errorMessage(error));
    }
  }, [setProbeError]);

  const fetchVersions = useCallback(async () => {
    try {
      const next = await api.versions();
      if (mountedRef.current) setVersions(next);
      setProbeError('versions');
    } catch (error) {
      setProbeError('versions', errorMessage(error));
    }
  }, [setProbeError]);

  const fetchAssociations = useCallback(async () => {
    try {
      const next = await api.associations();
      if (mountedRef.current) setAssociations(Array.isArray(next) ? next : []);
      setProbeError('associations');
    } catch (error) {
      setProbeError('associations', errorMessage(error));
    }
  }, [setProbeError]);

  const fetchHealth = useCallback(async () => {
    const started = performance.now();
    try {
      const next = await api.healthLive();
      if (mountedRef.current) setHealth({ ...next, rttMs: Math.max(0, performance.now() - started) });
      setProbeError('health');
    } catch (error) {
      setProbeError('health', errorMessage(error));
    }
  }, [setProbeError]);

  useEffect(() => {
    mountedRef.current = true;
    setLoading(true);
    void Promise.allSettled([
      fetchSnapshot(),
      fetchReadiness(),
      fetchEnvironment(),
      fetchVersions(),
      fetchAssociations(),
      fetchHealth(),
    ]).finally(() => {
      if (mountedRef.current) setLoading(false);
    });

    const systemPoll = window.setInterval(() => {
      void fetchReadiness();
      void fetchHealth();
      void fetchAssociations();
    }, 60_000);

    return () => {
      mountedRef.current = false;
      window.clearInterval(systemPoll);
    };
  }, [fetchAssociations, fetchEnvironment, fetchHealth, fetchReadiness, fetchSnapshot, fetchVersions]);

  useEffect(() => {
    let stopped = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | undefined;
    let attempts = 0;

    const connect = () => {
      if (stopped) return;
      setConnection(attempts === 0 ? 'connecting' : 'reconnecting');
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const url = `${protocol}//${window.location.host}/api/v1/live`;

      try {
        socket = new WebSocket(url);
      } catch {
        setConnection(snapshotRef.current ? 'polling' : 'offline');
        return;
      }

      socket.onopen = () => {
        attempts = 0;
        socketLiveRef.current = true;
        if (!stopped) setConnection('live');
      };

      socket.onmessage = (message: MessageEvent<string>) => {
        try {
          const parsed: unknown = JSON.parse(message.data);
          if (!isLiveEvent(parsed)) return;
          const current = snapshotRef.current;
          const next = applyLiveEvent(current, parsed);
          if (!next || next === current || !mountedRef.current) return;
          snapshotRef.current = next;
          setSnapshot(next);
          setProbeError('snapshot');
          if (parsed.type === 'metrics') setLastUpdate(parsed.data.timestamp);
          else if (parsed.data.generated_at) setLastUpdate(parsed.data.generated_at);
        } catch {
          // Malformed live events are ignored; REST polling remains the source of truth.
        }
      };

      socket.onerror = () => {
        socket?.close();
      };

      socket.onclose = () => {
        socketLiveRef.current = false;
        if (stopped) return;
        if (attempts < MAX_RECONNECT_ATTEMPTS) {
          const delay = reconnectDelay(attempts);
          attempts += 1;
          setConnection('reconnecting');
          reconnectTimer = window.setTimeout(connect, delay);
        } else {
          setConnection(snapshotRef.current ? 'polling' : 'offline');
          void fetchSnapshot();
        }
      };
    };

    connect();
    const fallbackPoll = window.setInterval(() => {
      if (!socketLiveRef.current) void fetchSnapshot();
    }, 10_000);

    return () => {
      stopped = true;
      socketLiveRef.current = false;
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
      window.clearInterval(fallbackPoll);
      socket?.close();
    };
  }, [fetchSnapshot, setProbeError]);

  const runAction = useCallback(async (action: DemoAction): Promise<boolean> => {
    setPendingAction(action);
    setActionError(null);
    try {
      const next = action === 'start'
        ? await api.startDemo(replayMode)
        : action === 'stop'
          ? await api.stopDemo()
          : await api.resetDemo();
      commitSnapshot(next);
      await Promise.allSettled([fetchReadiness(), fetchAssociations(), fetchHealth()]);
      return true;
    } catch (error) {
      if (mountedRef.current) {
        setActionError({
          action, message: errorMessage(error),
          code: error instanceof ApiError ? error.code : 'REQUEST_FAILED',
          status: error instanceof ApiError ? error.status : 0,
        });
        // A refused action (e.g. 409 CLEANUP_UNCONFIRMED) means the displayed state may be stale.
        void fetchSnapshot();
      }
      return false;
    } finally {
      if (mountedRef.current) setPendingAction(null);
    }
  }, [commitSnapshot, fetchAssociations, fetchHealth, fetchReadiness, fetchSnapshot, replayMode]);

  const acknowledgeAlert = useCallback(async (id: string): Promise<Alert> => {
    const updated = await api.acknowledgeAlert(id);
    const current = snapshotRef.current;
    if (current) {
      const next = {
        ...current,
        alerts: current.alerts.map((alert) => alert.id === id ? updated : alert),
      };
      snapshotRef.current = next;
      setSnapshot(next);
    }
    return updated;
  }, []);

  const refreshAll = useCallback(async () => {
    await Promise.allSettled([
      fetchSnapshot(),
      fetchReadiness(),
      fetchEnvironment(),
      fetchVersions(),
      fetchAssociations(),
      fetchHealth(),
    ]);
  }, [fetchAssociations, fetchEnvironment, fetchHealth, fetchReadiness, fetchSnapshot, fetchVersions]);

  return {
    snapshot,
    connection,
    readiness,
    environment,
    versions,
    associations,
    health,
    probeErrors,
    lastUpdate,
    loading,
    pendingAction,
    actionError,
    replayMode,
    setReplayMode,
    clearActionError: () => setActionError(null),
    runAction,
    acknowledgeAlert,
    refreshAll,
  };
}

export type Operations = ReturnType<typeof useOperations>;
