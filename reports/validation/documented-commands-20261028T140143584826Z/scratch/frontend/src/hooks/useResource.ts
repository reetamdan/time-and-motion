import { useCallback, useEffect, useState } from 'react';
import { errorMessage } from '../lib/api';

/** One local API resource, re-fetched when `key` changes or `reload()` is called. Errors are kept, never replaced by defaults. */
export function useResource<T>(load: (() => Promise<T>) | null, key: unknown) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(load));
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (!load) { setLoading(false); return; }
    let cancelled = false;
    setLoading(true);
    load()
      .then((value) => { if (!cancelled) { setData(value); setError(null); } })
      .catch((reason: unknown) => { if (!cancelled) setError(errorMessage(reason)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);
  return { data, error, loading, reload };
}
