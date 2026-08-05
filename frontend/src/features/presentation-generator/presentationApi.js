async function parseErrorBody(response) {
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  const detail = payload?.detail || {};
  const code = detail.code || payload?.code || `http_${response.status}`;
  const message = detail.message || payload?.message || 'The local backend rejected the request.';
  return { code, message, status: response.status };
}

export const presentationApi = {
  async generate(topic) {
    const response = await fetch('/api/presentation/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ topic }),
    });
    if (!response.ok) {
      throw await parseErrorBody(response);
    }
    return response.blob();
  },
};
