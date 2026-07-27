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

export const orderWorkflowApi = {
  createOrder: payload => post('/api/orders', payload),
  getOrder: orderId => requestJson(`/api/orders/${encodeURIComponent(orderId)}`),
  getQuestions: orderId => requestJson(`/api/orders/${encodeURIComponent(orderId)}/questions`),
  answerQuestions: (orderId, answers) => post(`/api/orders/${encodeURIComponent(orderId)}/answers`, { answers }),
  applyDefaults: orderId => post(`/api/orders/${encodeURIComponent(orderId)}/defaults`, { use_recommended_defaults: true }),
  getBrief: orderId => requestJson(`/api/orders/${encodeURIComponent(orderId)}/brief`),
  generateBrief: orderId => post(`/api/orders/${encodeURIComponent(orderId)}/brief`, {}),
  reviseBrief: (orderId, operations) => post(`/api/orders/${encodeURIComponent(orderId)}/brief/revise`, { operations }),
  approveBrief: (orderId, revision, fingerprint = null) => post(`/api/orders/${encodeURIComponent(orderId)}/brief/approve`, { revision, fingerprint }),
  getHandoff: orderId => requestJson(`/api/orders/${encodeURIComponent(orderId)}/handoff`),
  startExecution: orderId => post(`/api/orders/${encodeURIComponent(orderId)}/execution`, { mode: 'fake' }),
  getExecution: orderId => requestJson(`/api/orders/${encodeURIComponent(orderId)}/execution`),
  cancelExecution: orderId => post(`/api/orders/${encodeURIComponent(orderId)}/execution/cancel`, {}),
  getEvents: orderId => requestJson(`/api/orders/${encodeURIComponent(orderId)}/events`),
  getArtifacts: orderId => requestJson(`/api/orders/${encodeURIComponent(orderId)}/artifacts`),
  getResult: orderId => requestJson(`/api/orders/${encodeURIComponent(orderId)}/result`),
};
