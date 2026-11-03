export const UNAVAILABLE = '—';
export const DEFAULT_UNAVAILABLE_REASON = 'No measurement was reported by the local API.';

const numberFormatter = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 });
const integerFormatter = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });

export function formatNumber(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return UNAVAILABLE;
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: digits }).format(value);
}

export function formatInteger(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return UNAVAILABLE;
  return integerFormatter.format(value);
}

export function formatPercent(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isFinite(value) ? UNAVAILABLE : `${numberFormatter.format(value)}%`;
}

export function formatFps(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isFinite(value) ? UNAVAILABLE : `${numberFormatter.format(value)} fps`;
}

export function formatMilliseconds(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return UNAVAILABLE;
  if (Math.abs(value) < 1000) return `${integerFormatter.format(value)} ms`;
  if (Math.abs(value) < 60_000) return `${formatNumber(value / 1000, 1)} s`;
  return `${formatNumber(value / 60_000, 1)} min`;
}

export function formatSeconds(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return UNAVAILABLE;
  if (value < 60) return `${integerFormatter.format(value)} s`;
  return `${formatNumber(value / 60, 1)} min`;
}

export function formatTimestamp(value: string | null | undefined, timeZone?: string): string {
  if (!value) return UNAVAILABLE;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return UNAVAILABLE;
  return new Intl.DateTimeFormat('en-GB', {
    dateStyle: 'medium',
    timeStyle: 'medium',
    timeZone,
  }).format(date);
}

export function formatClock(date: Date, timeZone?: string): string {
  return new Intl.DateTimeFormat('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    timeZone,
  }).format(date);
}

export function formatRelativeTime(value: string | null | undefined, now = Date.now()): string {
  if (!value) return UNAVAILABLE;
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return UNAVAILABLE;
  const seconds = Math.max(0, Math.round((now - timestamp) / 1000));
  if (seconds < 5) return 'just now';
  if (seconds < 60) return `${seconds} s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.floor(hours / 24)} d ago`;
}

export function formatChartTime(value: number | string, rangeHours = 24): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return UNAVAILABLE;
  return new Intl.DateTimeFormat('en-GB', rangeHours > 24
    ? { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false }
    : { hour: '2-digit', minute: '2-digit', hour12: false }).format(date);
}

export function titleCase(value: string | null | undefined): string {
  if (!value) return 'UNKNOWN';
  return value.toLowerCase().replace(/[_-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function reasonOr(...reasons: Array<string | null | undefined>): string {
  return reasons.find((reason) => Boolean(reason?.trim())) ?? DEFAULT_UNAVAILABLE_REASON;
}

export function formatUnknown(value: unknown): string {
  if (value === null || value === undefined || value === '') return 'UNKNOWN';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
  return JSON.stringify(value);
}

export function formatMeasurement(value: unknown): string {
  if (value === null || value === undefined) return UNAVAILABLE;
  if (typeof value === 'number') return formatNumber(value);
  if (typeof value === 'string' || typeof value === 'boolean') return String(value);
  return JSON.stringify(value);
}

export function formatGap(gap: unknown): string {
  if (typeof gap === 'string') return gap;
  if (gap && typeof gap === 'object') {
    const record = gap as Record<string, unknown>;
    const from = record.from ?? record.start ?? record.started_at;
    const to = record.to ?? record.end ?? record.ended_at;
    if (typeof from === 'string' && typeof to === 'string') {
      return `${formatTimestamp(from)} → ${formatTimestamp(to)}`;
    }
  }
  return 'A time gap was reported; its boundaries were not supplied.';
}

export function formatBytes(value: number | null | undefined, unit = 'MiB'): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return UNAVAILABLE;
  return `${integerFormatter.format(value)} ${unit}`;
}
