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

const post = (path, body = {}) => requestJson(path, { method: 'POST', body: JSON.stringify(body) });

export const knowledgeBaseApi = {
  ask: payload => post('/api/embeddings/ask', payload),
};
