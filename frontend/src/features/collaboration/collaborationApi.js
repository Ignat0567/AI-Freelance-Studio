async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(options.headers || {}),
    },
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const detail = payload?.detail || {};
    const code = detail.code || payload?.code || `http_${response.status}`;
    const message = detail.message || payload?.message || 'The local backend rejected the request.';
    throw { code, message, status: response.status };
  }
  return payload;
}

export const collaborationApi = {
  getChannels: () => requestJson('/api/collaboration/channels'),
  getMessages: (channelId) => requestJson(`/api/collaboration/channels/${channelId}/messages`),
  postMessage: (channelId, { sender, message }) =>
    requestJson(`/api/collaboration/channels/${channelId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ sender, message }),
    }),
  getEvents: (channelId) => requestJson(`/api/collaboration/events${channelId ? `?channel_id=${channelId}` : ''}`),
  getPresence: () => requestJson('/api/collaboration/presence'),
};
