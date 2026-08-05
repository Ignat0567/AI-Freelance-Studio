import { useEffect, useRef, useState } from 'react';
import { collaborationApi } from './collaborationApi.js';

export const MESSAGE_POLL_INTERVAL_MS = 2000;

export function useChannelMessages({ channelId, enabled }) {
  const [messages, setMessages] = useState([]);
  const [error, setError] = useState('');
  const requestInFlight = useRef(false);

  useEffect(() => {
    if (!channelId || !enabled) {
      setMessages([]);
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
      try {
        const result = await collaborationApi.getMessages(channelId);
        if (!cancelled) {
          setMessages(result.messages || []);
          setError('');
        }
      } catch (err) {
        if (!cancelled) setError(err.message || 'Could not load messages.');
      } finally {
        requestInFlight.current = false;
      }
      timer = window.setTimeout(poll, MESSAGE_POLL_INTERVAL_MS);
    };

    poll();
    return () => {
      cancelled = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [channelId, enabled]);

  return { messages, error };
}
