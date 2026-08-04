import { useEffect, useRef, useState } from 'react';
import { sandboxTestLabApi } from './sandboxTestLabApi.js';

export const FRAME_POLL_INTERVAL_MS = 1500;

export function useSandboxLiveFrame({ runId, enabled }) {
  const [frame, setFrame] = useState(null);
  const requestInFlight = useRef(false);

  useEffect(() => {
    if (!runId || !enabled) {
      setFrame(null);
      return undefined;
    }
    let cancelled = false;
    let timer = null;

    const poll = async () => {
      if (requestInFlight.current) {
        timer = window.setTimeout(poll, 50);
        return;
      }
      requestInFlight.current = true;
      const result = await sandboxTestLabApi.getFrame(runId);
      requestInFlight.current = false;
      if (cancelled) return;
      if (result.ok && result.data.frame_base64) {
        setFrame({
          capturedAt: result.data.captured_at,
          dataUrl: `data:image/png;base64,${result.data.frame_base64}`,
        });
      }
      timer = window.setTimeout(poll, FRAME_POLL_INTERVAL_MS);
    };

    poll();
    return () => {
      cancelled = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [runId, enabled]);

  return frame;
}
