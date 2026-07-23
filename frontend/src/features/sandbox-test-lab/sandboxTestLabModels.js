export const SANDBOX_OPERATIONS = Object.freeze({
  production_self_test: Object.freeze({
    id: 'production_self_test',
    short: 'SELF',
    name: 'Production Self-Test',
    description: 'Validates the packaged Studio workflow inside the controlled Windows Sandbox environment.',
  }),
  production_screenshot: Object.freeze({
    id: 'production_screenshot',
    short: 'SHOT',
    name: 'Production Screenshot',
    description: 'Runs the supported screenshot-oriented validation workflow in Windows Sandbox.',
  }),
});

export const TERMINAL_STATUSES = new Set([
  'succeeded',
  'failed',
  'cancelled',
  'infrastructure_error',
]);

export const CANCELLABLE_STATUSES = new Set([
  'queued',
  'preparing',
  'launching',
  'running',
]);

export const STATUS_DETAILS = Object.freeze({
  queued: Object.freeze({ label: 'Queued', tone: 'active' }),
  preparing: Object.freeze({ label: 'Preparing', tone: 'active' }),
  launching: Object.freeze({ label: 'Launching', tone: 'active' }),
  running: Object.freeze({ label: 'Running', tone: 'active' }),
  cancelling: Object.freeze({ label: 'Cancelling', tone: 'warning' }),
  succeeded: Object.freeze({ label: 'Succeeded', tone: 'success' }),
  failed: Object.freeze({ label: 'Failed', tone: 'danger' }),
  cancelled: Object.freeze({ label: 'Cancelled', tone: 'warning' }),
  infrastructure_error: Object.freeze({ label: 'Infrastructure error', tone: 'danger' }),
});

const ACTIVE_STATUS_RANK = Object.freeze({
  queued: 0,
  preparing: 1,
  launching: 2,
  running: 3,
  cancelling: 4,
});

export const REASON_MESSAGES = Object.freeze({
  network_bind_disallowed: 'Sandbox Test Lab is disabled while the Studio backend is accessible over the network. Switch the backend to loopback-only mode to use it.',
  test_lab_disabled: 'Sandbox Test Lab is disabled in this Studio configuration.',
  sandbox_capability_unavailable: 'Windows Sandbox is not available with the required capability on this computer.',
  job_service_unavailable: 'The Sandbox Test Lab execution service is not configured or ready.',
  test_lab_unavailable: 'Sandbox Test Lab is not currently available.',
  backend_security_unavailable: 'The trusted local Studio connection is not available.',
  backend_restarting: 'The local Studio backend is reconnecting. This run will not be relaunched automatically.',
  backend_restarted: 'The local backend instance changed, so this launch cannot be retried safely. Review the operation and start a new intentional run.',
  transport_error: 'The trusted Studio transport could not reach the local backend.',
  untrusted_renderer: 'Sandbox Test Lab requests are available only from the trusted Studio window.',
  launch_rejected: 'Another Sandbox Test Lab run is active. Wait for it to finish before starting a new run.',
  idempotency_conflict: 'This launch request could not be safely retried. Return to the operation selector and start again.',
  run_not_found: 'The backend restarted or no longer has tracking state for this run. The operation was not relaunched.',
  run_already_terminal: 'The run finished before cancellation could be applied. The final server result is shown.',
  internal_error: 'The local Test Lab service could not complete the request.',
  invalid_launch_request: 'The selected operation could not be launched safely.',
  invalid_cancel_request: 'The cancellation request was rejected.',
});

export const RUN_ERROR_MESSAGES = Object.freeze({
  run_failed: 'The controlled validation did not complete successfully.',
  run_timed_out: 'The controlled validation exceeded its allowed runtime.',
  run_interrupted: 'The backend stopped before the run reached a recorded terminal result.',
});

export function reasonMessage(code) {
  return REASON_MESSAGES[code] || 'Sandbox Test Lab is unavailable for a protected local reason.';
}

export function operationDetails(operation) {
  return SANDBOX_OPERATIONS[operation] || null;
}

export function statusDetails(status) {
  return STATUS_DETAILS[status] || Object.freeze({ label: 'Unknown', tone: 'idle' });
}

export function canApplyRunSnapshot(currentStatus, nextStatus) {
  if (TERMINAL_STATUSES.has(nextStatus)) return true;
  if (TERMINAL_STATUSES.has(currentStatus)) return false;
  if (currentStatus === 'cancelling') return nextStatus === 'cancelling';
  const currentRank = ACTIVE_STATUS_RANK[currentStatus];
  const nextRank = ACTIVE_STATUS_RANK[nextStatus];
  return Number.isInteger(currentRank) && Number.isInteger(nextRank) && nextRank >= currentRank;
}

export function shortRunId(runId) {
  if (typeof runId !== 'string' || runId.length < 13) return 'Unavailable';
  return `${runId.slice(0, 8)}...${runId.slice(-4)}`;
}

export function formatObservedTime(value) {
  if (!value) return 'Not yet';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Unavailable' : date.toLocaleString();
}

export function formatObservedDuration(startedAt, finishedAt) {
  if (!startedAt || !finishedAt) return '';
  const duration = Math.max(0, new Date(finishedAt).getTime() - new Date(startedAt).getTime());
  if (!Number.isFinite(duration)) return '';
  const seconds = Math.round(duration / 1000);
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}
