function unavailableTransport() {
  return { ok: false, status: 0, error: { code: 'backend_security_unavailable' } };
}

function transport() {
  const candidate = globalThis.window?.env?.sandboxTestLab;
  return candidate && typeof candidate === 'object' ? candidate : null;
}

async function invoke(method, ...args) {
  const client = transport();
  if (!client || typeof client[method] !== 'function') return unavailableTransport();
  try {
    const result = await client[method](...args);
    if (!result || typeof result !== 'object' || typeof result.ok !== 'boolean') {
      return unavailableTransport();
    }
    return result;
  } catch {
    return { ok: false, status: 0, error: { code: 'transport_error' } };
  }
}

export function createSecureIdempotencyKey() {
  const secureCrypto = globalThis.crypto;
  if (typeof secureCrypto?.randomUUID === 'function') return secureCrypto.randomUUID();
  if (typeof secureCrypto?.getRandomValues !== 'function') {
    throw new Error('Secure UUID generation is unavailable');
  }
  const bytes = secureCrypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map(value => value.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export const sandboxTestLabApi = Object.freeze({
  getCapabilities: () => invoke('getCapabilities'),
  launchRun: (operation, idempotencyKey, projectName) => invoke('launchRun', operation, idempotencyKey, projectName),
  getRun: runId => invoke('getRun', runId),
  getFrame: runId => invoke('getFrame', runId),
  sendInput: (runId, action) => invoke('sendInput', runId, action),
  cancelRun: runId => invoke('cancelRun', runId),
});
