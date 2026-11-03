import type {
  Alert,
  AlertSeverity,
  AlertStatus,
  ApiErrorBody,
  AssociationRecord,
  Camera,
  EnvironmentResponse,
  Floor,
  HealthLive,
  HistoryResponse,
  ReadinessResponse,
  Snapshot,
  SystemMetrics,
  VersionsResponse,
  Zone, CameraCountHistory } from '../types';

const API = '/api/v1';

interface RequestOptions extends RequestInit {
  allowStatuses?: number[];
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;

  constructor(message: string, status = 0, code = 'REQUEST_FAILED', details: Record<string, unknown> = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function isErrorBody(value: unknown): value is ApiErrorBody {
  if (!value || typeof value !== 'object' || !('error' in value)) return false;
  const error = (value as { error?: unknown }).error;
  return Boolean(error && typeof error === 'object' && 'message' in error);
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { allowStatuses = [], headers, ...init } = options;
  let response: Response;

  try {
    response = await fetch(path, {
      ...init,
      headers: {
        Accept: 'application/json',
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
        ...headers,
      },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError(error instanceof Error ? error.message : 'The local API could not be reached.');
  }

  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      if (response.ok) throw new ApiError('The local API returned an invalid JSON response.', response.status, 'INVALID_RESPONSE');
    }
  }

  if (!response.ok && !allowStatuses.includes(response.status)) {
    if (isErrorBody(payload)) {
      throw new ApiError(payload.error.message, response.status, payload.error.code, payload.error.details);
    }
    throw new ApiError(`Local API request failed with HTTP ${response.status}.`, response.status, 'HTTP_ERROR');
  }

  return payload as T;
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined) search.set(key, String(value));
  });
  const encoded = search.toString();
  return encoded ? `?${encoded}` : '';
}

export const api = {
  healthLive: () => request<HealthLive>('/health/live'),
  readiness: () => request<ReadinessResponse>('/health/ready', { allowStatuses: [503] }),
  environment: () => request<EnvironmentResponse>(`${API}/system/environment`),
  metrics: () => request<SystemMetrics>(`${API}/system/metrics`),
  cameras: () => request<Camera[]>(`${API}/cameras`),
  floors: () => request<Floor[]>(`${API}/floors`),
  zones: () => request<Zone[]>(`${API}/zones`),
  snapshot: () => request<Snapshot>(`${API}/facility/snapshot`),
  cameraCountHistory: (cameraId: string) =>
    request<CameraCountHistory>(`${API}/cameras/${encodeURIComponent(cameraId)}/count-history`),
  history: (metric: string, from: string, to: string, limit = 200, signal?: AbortSignal) =>
    request<HistoryResponse>(`${API}/analytics/history${query({ metric, from, to, limit })}`, { signal }),
  alerts: (status: AlertStatus | 'ALL', severity: AlertSeverity | 'ALL', limit = 100, signal?: AbortSignal) =>
    request<Alert[]>(`${API}/alerts${query({
      status: status === 'ALL' ? undefined : status,
      severity: severity === 'ALL' ? undefined : severity,
      limit,
    })}`, { signal }),
  acknowledgeAlert: (id: string) => request<Alert>(`${API}/alerts/${encodeURIComponent(id)}/acknowledge`, { method: 'POST' }),
  associations: (limit = 100) => request<AssociationRecord[]>(`${API}/associations${query({ limit })}`),
  versions: () => request<VersionsResponse>(`${API}/versions`),
  startDemo: () => request<Snapshot>(`${API}/demo/start`, { method: 'POST' }),
  stopDemo: () => request<Snapshot>(`${API}/demo/stop`, { method: 'POST' }),
  resetDemo: () => request<Snapshot>(`${API}/demo/reset`, { method: 'POST' }),
  evidenceUrl: (id: string) => `${API}/evidence/${encodeURIComponent(id)}`,
};

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return 'An unexpected local API error occurred.';
}
