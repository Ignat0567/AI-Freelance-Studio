const http = require('http');

const ALLOWED_OPERATIONS = new Set(['production_self_test', 'production_screenshot', 'interactive_session']);
const ALLOWED_STATUSES = new Set([
    'queued',
    'preparing',
    'launching',
    'running',
    'cancelling',
    'succeeded',
    'failed',
    'cancelled',
    'infrastructure_error',
]);
const ALLOWED_PHASES = new Set([
    'not_started',
    'preparing',
    'launching',
    'running_checks',
    'collecting_evidence',
    'cancelling',
    'complete',
]);
const ALLOWED_REASON_CODES = new Set([
    'test_lab_disabled',
    'sandbox_capability_unavailable',
    'job_service_unavailable',
]);
const ALLOWED_ERROR_CODES = new Set([
    'network_bind_disallowed',
    'test_lab_unavailable',
    'job_service_unavailable',
    'launch_rejected',
    'idempotency_conflict',
    'run_not_found',
    'run_already_terminal',
    'invalid_launch_request',
    'invalid_cancel_request',
    'internal_error',
]);
const ALLOWED_RUN_ERRORS = new Set(['run_failed', 'run_timed_out', 'run_interrupted']);
const MAX_RESPONSE_BYTES = 256 * 1024;
// The frame endpoint returns a base64-encoded downscaled screenshot, which can comfortably
// exceed the 256 KB cap used by every other (tiny JSON) response.
const MAX_FRAME_RESPONSE_BYTES = 1024 * 1024;
const FRAME_BASE64_PATTERN = /^[A-Za-z0-9+/]+={0,2}$/;
const REQUEST_TIMEOUT_MS = 10_000;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

const PHASE_MESSAGES = Object.freeze({
    not_started: 'The run is queued.',
    preparing: 'The Sandbox run is being prepared.',
    launching: 'Windows Sandbox is launching.',
    running_checks: 'Sandbox checks are in progress.',
    collecting_evidence: 'Sandbox evidence is being validated.',
    cancelling: 'Cancellation is in progress.',
    complete: 'The run is complete.',
});

function isCanonicalUuid(value) {
    return typeof value === 'string' && UUID_PATTERN.test(value);
}

function errorResult(code, status = 0) {
    return { ok: false, status, error: { code } };
}

function backendContextError(context) {
    if (context?.restarting) return errorResult('backend_restarting');
    if (
        !context?.ready
        || !['127.0.0.1', '::1'].includes(context.host)
        || !Number.isInteger(context.port)
        || context.port < 1
        || context.port > 65535
        || typeof context.token !== 'string'
        || !context.token
    ) {
        return errorResult('backend_security_unavailable');
    }
    return null;
}

function sanitizeCapabilities(payload) {
    if (!payload || typeof payload !== 'object' || typeof payload.available !== 'boolean') return null;
    if (payload.backend_mode !== 'loopback' || !payload.operations || typeof payload.operations !== 'object') return null;
    const reasons = Array.isArray(payload.reasons)
        ? payload.reasons
            .map(item => item && ALLOWED_REASON_CODES.has(item.code) ? { code: item.code } : null)
            .filter(Boolean)
        : [];
    return {
        available: payload.available,
        reasons,
        backend_mode: 'loopback',
        operations: {
            launch: payload.operations.launch === true,
            cancel: payload.operations.cancel === true,
            status: payload.operations.status === true,
            evidence: false,
        },
    };
}

function sanitizeLaunch(payload) {
    if (!payload || !isCanonicalUuid(payload.run_id) || !ALLOWED_STATUSES.has(payload.status)) return null;
    return { run_id: payload.run_id, status: payload.status };
}

function sanitizeRun(payload) {
    if (
        !payload
        || !isCanonicalUuid(payload.run_id)
        || !ALLOWED_OPERATIONS.has(payload.operation)
        || !ALLOWED_STATUSES.has(payload.status)
        || typeof payload.terminal !== 'boolean'
        || !payload.progress
        || !ALLOWED_PHASES.has(payload.progress.phase)
    ) return null;
    const terminalStatuses = new Set(['succeeded', 'failed', 'cancelled', 'infrastructure_error']);
    if (payload.terminal !== terminalStatuses.has(payload.status)) return null;
    const errors = Array.isArray(payload.errors)
        ? payload.errors.filter(code => ALLOWED_RUN_ERRORS.has(code))
        : [];
    let result = null;
    if (payload.result !== null && payload.result !== undefined) {
        const outcomes = new Set(['succeeded', 'failed', 'cancelled', 'infrastructure_error']);
        if (!outcomes.has(payload.result.outcome) || typeof payload.result.manual_close_required !== 'boolean') return null;
        result = {
            outcome: payload.result.outcome,
            manual_close_required: payload.result.manual_close_required,
        };
    }
    if (payload.terminal !== Boolean(result) || (result && result.outcome !== payload.status)) return null;
    return {
        run_id: payload.run_id,
        operation: payload.operation,
        status: payload.status,
        terminal: payload.terminal,
        progress: {
            phase: payload.progress.phase,
            message: PHASE_MESSAGES[payload.progress.phase],
        },
        result,
        errors,
    };
}

function sanitizeCancel(payload) {
    if (
        !payload
        || !isCanonicalUuid(payload.run_id)
        || !ALLOWED_STATUSES.has(payload.status)
        || typeof payload.accepted !== 'boolean'
    ) return null;
    return { run_id: payload.run_id, status: payload.status, accepted: payload.accepted };
}

function sanitizeFrame(payload) {
    if (!payload || !isCanonicalUuid(payload.run_id)) return null;
    if (payload.captured_at !== null && typeof payload.captured_at !== 'string') return null;
    if (payload.frame_base64 !== null && (typeof payload.frame_base64 !== 'string' || !FRAME_BASE64_PATTERN.test(payload.frame_base64))) return null;
    if ((payload.captured_at === null) !== (payload.frame_base64 === null)) return null;
    return { run_id: payload.run_id, captured_at: payload.captured_at, frame_base64: payload.frame_base64 };
}

function responseError(payload, status) {
    const candidate = payload?.error?.code || payload?.detail;
    const code = ALLOWED_ERROR_CODES.has(candidate) ? candidate : 'internal_error';
    return errorResult(code, status);
}

function requestJson(context, specification, sanitizer, maxResponseBytes = MAX_RESPONSE_BYTES) {
    const contextError = backendContextError(context);
    if (contextError) return Promise.resolve(contextError);
    const body = specification.body === null ? null : JSON.stringify(specification.body);
    const headers = {
        Accept: 'application/json',
        'X-FreelancerStudio-Token': context.token,
        'X-FreelancerStudio-Transport': 'electron-main',
    };
    if (body !== null) {
        headers['Content-Type'] = 'application/json';
        headers['Content-Length'] = Buffer.byteLength(body);
    }
    if (specification.idempotencyKey) headers['Idempotency-Key'] = specification.idempotencyKey;

    return new Promise(resolve => {
        let settled = false;
        const finish = result => {
            if (settled) return;
            settled = true;
            resolve(result);
        };
        const request = http.request({
            hostname: context.host,
            port: context.port,
            path: specification.path,
            method: specification.method,
            headers,
            timeout: REQUEST_TIMEOUT_MS,
        }, response => {
            if (response.statusCode >= 300 && response.statusCode < 400) {
                response.resume();
                finish(errorResult('transport_error', response.statusCode));
                return;
            }
            const chunks = [];
            let size = 0;
            response.on('data', chunk => {
                size += chunk.length;
                if (size > maxResponseBytes) {
                    request.destroy();
                    finish(errorResult('backend_security_unavailable'));
                    return;
                }
                chunks.push(chunk);
            });
            response.on('end', () => {
                if (settled) return;
                let payload;
                try {
                    payload = JSON.parse(Buffer.concat(chunks).toString('utf8'));
                } catch {
                    finish(errorResult('backend_security_unavailable', response.statusCode));
                    return;
                }
                if (response.statusCode < 200 || response.statusCode >= 300) {
                    finish(responseError(payload, response.statusCode));
                    return;
                }
                const data = sanitizer(payload);
                finish(data ? { ok: true, status: response.statusCode, data } : errorResult('backend_security_unavailable', response.statusCode));
            });
        });
        request.on('timeout', () => {
            request.destroy();
            finish(errorResult('backend_restarting'));
        });
        request.on('error', () => finish(errorResult('backend_restarting')));
        if (body !== null) request.write(body);
        request.end();
    });
}

function getSandboxTestLabCapabilities(context) {
    return requestJson(
        context,
        { method: 'GET', path: '/api/sandbox-test-lab/capabilities', body: null },
        sanitizeCapabilities,
    );
}

function launchSandboxTestLabRun(context, operation, idempotencyKey) {
    if (!ALLOWED_OPERATIONS.has(operation) || !isCanonicalUuid(idempotencyKey)) {
        return Promise.resolve(errorResult('invalid_launch_request', 400));
    }
    return requestJson(
        context,
        {
            method: 'POST',
            path: '/api/sandbox-test-lab/runs',
            body: { operation, parameters: {} },
            idempotencyKey,
        },
        sanitizeLaunch,
    );
}

function getSandboxTestLabRun(context, runId) {
    if (!isCanonicalUuid(runId)) return Promise.resolve(errorResult('run_not_found', 404));
    return requestJson(
        context,
        { method: 'GET', path: `/api/sandbox-test-lab/runs/${runId}`, body: null },
        sanitizeRun,
    );
}

function getSandboxTestLabRunFrame(context, runId) {
    if (!isCanonicalUuid(runId)) return Promise.resolve(errorResult('run_not_found', 404));
    return requestJson(
        context,
        { method: 'GET', path: `/api/sandbox-test-lab/runs/${runId}/frame`, body: null },
        sanitizeFrame,
        MAX_FRAME_RESPONSE_BYTES,
    );
}

function cancelSandboxTestLabRun(context, runId) {
    if (!isCanonicalUuid(runId)) return Promise.resolve(errorResult('run_not_found', 404));
    return requestJson(
        context,
        {
            method: 'POST',
            path: `/api/sandbox-test-lab/runs/${runId}/cancel`,
            body: { reason: 'user_requested' },
        },
        sanitizeCancel,
    );
}

module.exports = {
    ALLOWED_OPERATIONS,
    ALLOWED_STATUSES,
    isCanonicalUuid,
    sanitizeCapabilities,
    sanitizeLaunch,
    sanitizeRun,
    sanitizeCancel,
    sanitizeFrame,
    getSandboxTestLabCapabilities,
    launchSandboxTestLabRun,
    getSandboxTestLabRun,
    getSandboxTestLabRunFrame,
    cancelSandboxTestLabRun,
};
