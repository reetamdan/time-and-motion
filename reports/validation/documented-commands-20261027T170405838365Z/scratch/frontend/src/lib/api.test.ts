import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError } from './api';

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('typed local API client', () => {
  it('builds history queries using the contract parameter names', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ metric: 'occupancy', points: [], gaps: [], next_cursor: null }));
    vi.stubGlobal('fetch', fetchMock);

    await api.history('occupancy', '2026-01-01T00:00:00.000Z', '2026-01-01T01:00:00.000Z');

    const url = new URL(String(fetchMock.mock.calls[0][0]), 'http://local.test');
    expect(url.pathname).toBe('/api/v1/analytics/history');
    expect(url.searchParams.get('metric')).toBe('occupancy');
    expect(url.searchParams.get('from')).toBe('2026-01-01T00:00:00.000Z');
    expect(url.searchParams.get('to')).toBe('2026-01-01T01:00:00.000Z');
    expect(url.searchParams.get('limit')).toBe('200');
  });

  it('returns readiness details for the contract HTTP 503 response', async () => {
    const payload = { status: 'not_ready', checks: [{ name: 'sources', ready: false, detail: 'No authorized clips installed' }] };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload, 503)));
    await expect(api.readiness()).resolves.toEqual(payload);
  });

  it('preserves structured API error codes, messages, and status', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ error: { code: 'ALERT_NOT_FOUND', message: 'Unknown alert', details: { id: 'a-1' } } }, 404)));
    const request = api.acknowledgeAlert('a-1');
    await expect(request).rejects.toMatchObject({ status: 404, code: 'ALERT_NOT_FOUND', message: 'Unknown alert' });
    await request.catch((error: unknown) => expect(error).toBeInstanceOf(ApiError));
  });

  it('uses POST and encodes resource identifiers for run controls and alerts', async () => {
    const alert = { id: 'alert/1' };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(alert));
    vi.stubGlobal('fetch', fetchMock);
    await api.acknowledgeAlert('alert/1');
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/alerts/alert%2F1/acknowledge');
    expect(fetchMock.mock.calls[0][1].method).toBe('POST');
  });

  it('does not convert AbortError into a visible API failure', async () => {
    const abort = new DOMException('The request was aborted', 'AbortError');
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(abort));
    await expect(api.snapshot()).rejects.toBe(abort);
  });
});
