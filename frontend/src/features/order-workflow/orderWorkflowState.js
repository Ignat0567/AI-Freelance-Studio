export const PDF_VOICE_ASSISTANT_EXAMPLE = {
  title: 'PDF Voice Assistant',
  description: 'Create a browser-based voice assistant that allows the user to upload PDF documents, ask questions about their contents by voice or text, receive answers grounded in the documents with page references, and hear the answers spoken aloud.',
  product_type: 'web_app',
  preferred_language: 'ru',
  constraints: '',
};

export const TERMINAL_EXECUTION_STATUSES = new Set(['succeeded', 'failed', 'cancelled']);

export const STAGE_PROGRESS = {
  requirements: 15,
  design: 25,
  planning: 35,
  implementation: 65,
  verification: 85,
  repair: 90,
  packaging: 95,
  completed: 100,
};

export function isTerminalExecution(execution) {
  return Boolean(execution && TERMINAL_EXECUTION_STATUSES.has(execution.status));
}

export function cleanError(error, fallback = 'The order workflow could not complete that action.') {
  const message = error?.message || error?.code || fallback;
  return String(message)
    .replace(/x-freelancerstudio-token\s*:\s*[^\s]+/gi, 'x-freelancerstudio-token: [hidden]')
    .replace(/token\s*[=:]\s*[^\s]+/gi, 'token=[hidden]')
    .replace(/authorization:\s*bearer\s+[^\s]+/gi, 'authorization: [hidden]')
    .slice(0, 220);
}

export function formatLabel(value, fallback = 'Not set') {
  if (value === null || value === undefined || value === '') return fallback;
  return String(value).replaceAll('_', ' ');
}

export function nextStepFromState(state) {
  if (!state?.order) return 'new-order';
  if (state.execution && isTerminalExecution(state.execution)) return 'result';
  if (state.execution) return 'execution';
  if (state.order.status === 'approved' && state.design_preview_required && !state.handoff_ready) return 'brief';
  if (state.order.status === 'approved' || state.handoff_ready) return 'execution';
  if (state.brief) return 'brief';
  if (state.order.status === 'brief_ready' || state.order.status === 'awaiting_approval') return 'brief';
  return 'clarification';
}
