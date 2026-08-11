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
  listOrders: () => requestJson('/api/orders'),
  getOrder: orderId => requestJson(`/api/orders/${encodeURIComponent(orderId)}`),
  getQuestions: orderId => requestJson(`/api/orders/${encodeURIComponent(orderId)}/questions`),
  answerQuestions: (orderId, answers) => post(`/api/orders/${encodeURIComponent(orderId)}/answers`, { answers }),
  applyDefaults: orderId => post(`/api/orders/${encodeURIComponent(orderId)}/defaults`, { use_recommended_defaults: true }),
  runAutopilot: orderId => post(`/api/orders/${encodeURIComponent(orderId)}/autopilot`, {}),
  generateProposal: orderId => post(`/api/orders/${encodeURIComponent(orderId)}/proposal`, {}),
  getBrief: orderId => requestJson(`/api/orders/${encodeURIComponent(orderId)}/brief`),
  generateBrief: orderId => post(`/api/orders/${encodeURIComponent(orderId)}/brief`, {}),
  reviseBrief: (orderId, operations) => post(`/api/orders/${encodeURIComponent(orderId)}/brief/revise`, { operations }),
  approveBrief: (orderId, revision, fingerprint = null) => post(`/api/orders/${encodeURIComponent(orderId)}/brief/approve`, { revision, fingerprint }),
  getDesignPreview: orderId => requestJson(`/api/orders/${encodeURIComponent(orderId)}/design-preview`),
  generateDesignPreview: (orderId, revision_note = null) => post(`/api/orders/${encodeURIComponent(orderId)}/design-preview`, { revision_note }),
  reviseDesignPreview: (orderId, note) => post(`/api/orders/${encodeURIComponent(orderId)}/design-preview/revise`, { note }),
  approveDesignPreview: (orderId, preview_id, brief_version) => post(`/api/orders/${encodeURIComponent(orderId)}/design-preview/approve`, { preview_id, brief_version }),
  getHandoff: orderId => requestJson(`/api/orders/${encodeURIComponent(orderId)}/handoff`),
  getReadiness: (orderId, mode = 'production') => requestJson(`/api/orders/${encodeURIComponent(orderId)}/readiness?mode=${encodeURIComponent(mode)}`),
  startExecution: (orderId, mode = 'fake', live = false) => post(`/api/orders/${encodeURIComponent(orderId)}/execution`, { mode, live }),
  getExecution: orderId => requestJson(`/api/orders/${encodeURIComponent(orderId)}/execution`),
  cancelExecution: orderId => post(`/api/orders/${encodeURIComponent(orderId)}/execution/cancel`, {}),
  retryExecution: orderId => post(`/api/orders/${encodeURIComponent(orderId)}/execution/retry`, {}),
  getEvents: orderId => requestJson(`/api/orders/${encodeURIComponent(orderId)}/events`),
  getArtifacts: orderId => requestJson(`/api/orders/${encodeURIComponent(orderId)}/artifacts`),
  getResult: orderId => requestJson(`/api/orders/${encodeURIComponent(orderId)}/result`),
  getUsageSummary: () => requestJson('/api/orders/usage-summary'),
};
