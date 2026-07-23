import { useEffect, useRef } from 'react';
import { sandboxTestLabApi } from './sandboxTestLabApi.js';

export const NORMAL_POLL_INTERVAL_MS = 1500;
const RETRY_DELAYS_MS = [1500, 3000, 6000, 10000];

export function useSandboxRunMonitor({
  runId,
  terminal,
  enabled,
  onSnapshot,
  onTransportFailure,
  onRecovered,
  onRunNotFound,
}) {
  const callbacks = useRef({ onSnapshot, onTransportFailure, onRecovered, onRunNotFound });
  const requestInFlight = useRef(false);
  callbacks.current = { onSnapshot, onTransportFailure, onRecovered, onRunNotFound };

  useEffect(() => {
    if (!runId || terminal || !enabled) return undefined;
    let cancelled = false;
    let timer = null;
    let failures = 0;

    const poll = async () => {
      if (requestInFlight.current) {
        timer = window.setTimeout(poll, 50);
        return;
      }
      requestInFlight.current = true;
      const result = await sandboxTestLabApi.getRun(runId);
      requestInFlight.current = false;
      if (cancelled) return;
      if (result.ok) {
        const recovered = failures > 0;
        failures = 0;
        callbacks.current.onSnapshot(result.data);
        if (recovered) callbacks.current.onRecovered();
        if (!result.data.terminal) timer = window.setTimeout(poll, NORMAL_POLL_INTERVAL_MS);
        return;
      }
      const code = result.error?.code || 'transport_error';
      if (code === 'run_not_found') {
        callbacks.current.onRunNotFound();
        return;
      }
      failures += 1;
      callbacks.current.onTransportFailure(code, failures);
      const delay = RETRY_DELAYS_MS[Math.min(failures - 1, RETRY_DELAYS_MS.length - 1)];
      timer = window.setTimeout(poll, delay);
    };

    poll();
    return () => {
      cancelled = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [runId, terminal, enabled]);
}
