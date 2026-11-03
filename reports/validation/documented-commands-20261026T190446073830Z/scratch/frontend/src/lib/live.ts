import type { LiveEvent, Snapshot } from '../types';

export const MAX_RECONNECT_ATTEMPTS = 5;

export function reconnectDelay(attempt: number): number {
  const safeAttempt = Math.max(0, Math.floor(attempt));
  return Math.min(15_000, 1000 * 2 ** safeAttempt);
}

export function applyLiveEvent(current: Snapshot | null, event: LiveEvent): Snapshot | null {
  if (event.type === 'snapshot') return event.data;
  if (!current) return current;

  if (event.type === 'metrics') {
    return {
      ...current,
      system_metrics: event.data,
      sequence: event.sequence ?? current.sequence,
      watermark: event.watermark ?? current.watermark,
    };
  }

  return {
    ...current,
    ...event.data,
    kpis: event.data.kpis ? { ...current.kpis, ...event.data.kpis } : current.kpis,
    system_metrics: event.data.system_metrics ?? current.system_metrics,
    sequence: event.sequence ?? event.data.sequence ?? current.sequence,
    watermark: event.watermark ?? event.data.watermark ?? current.watermark,
  };
}

export function isLiveEvent(value: unknown): value is LiveEvent {
  if (!value || typeof value !== 'object' || !('type' in value) || !('data' in value)) return false;
  const type = (value as { type?: unknown }).type;
  return type === 'snapshot' || type === 'metrics' || type === 'delta';
}

export function mergeAssociations<T extends { id?: string | null; association_id?: string | null }>(
  current: T[],
  incoming: T[],
): T[] {
  const next = [...current];
  incoming.forEach((record) => {
    const id = record.id ?? record.association_id;
    const index = id
      ? next.findIndex((candidate) => (candidate.id ?? candidate.association_id) === id)
      : -1;
    if (index >= 0) next[index] = { ...next[index], ...record };
    else next.unshift(record);
  });
  return next.slice(0, 100);
}
