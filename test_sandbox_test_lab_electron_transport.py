import json
from pathlib import Path
import re
import subprocess

import pytest


pytestmark = pytest.mark.unit


def _run_node(script):
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


def test_renderer_authorization_policy_strips_security_headers_and_excludes_test_lab():
    output = _run_node(
        r"""
const { authorizeRendererRequest } = require('./frontend/backend-request-policy');
const base = {
  method: 'POST',
  requestHeaders: {
    'X-FreelancerStudio-Token': 'renderer-supplied',
    'X-FreelancerStudio-Transport': 'electron-main',
  },
  webContentsId: 7,
  trustedWebContentsId: 7,
  expectedOrigin: 'http://127.0.0.1:8080',
  backendToken: 'main-memory-token',
};
const ordinary = authorizeRendererRequest({ ...base, url: 'http://127.0.0.1:8080/api/projects' });
const testLab = authorizeRendererRequest({ ...base, url: 'http://127.0.0.1:8080/api/sandbox-test-lab/capabilities' });
const encodedTestLab = authorizeRendererRequest({ ...base, url: 'http://127.0.0.1:8080/api/sandbox%2Dtest%2Dlab/runs' });
const redirected = authorizeRendererRequest({ ...base, url: 'https://evil.example/api/projects' });
const untrusted = authorizeRendererRequest({ ...base, webContentsId: 9, url: 'http://127.0.0.1:8080/api/projects' });
console.log(JSON.stringify({
  ordinaryAuthorized: ordinary['X-FreelancerStudio-Token'] === 'main-memory-token',
  ordinaryOrigin: ordinary.Origin,
  ordinaryTransport: ordinary['X-FreelancerStudio-Transport'] || null,
  testLabToken: testLab['X-FreelancerStudio-Token'] || null,
  testLabTransport: testLab['X-FreelancerStudio-Transport'] || null,
  encodedTestLabToken: encodedTestLab['X-FreelancerStudio-Token'] || null,
  redirectedToken: redirected['X-FreelancerStudio-Token'] || null,
  redirectedTransport: redirected['X-FreelancerStudio-Transport'] || null,
  untrustedToken: untrusted['X-FreelancerStudio-Token'] || null,
}));
"""
    )

    assert output == {
        "ordinaryAuthorized": True,
        "ordinaryOrigin": "http://127.0.0.1:8080",
        "ordinaryTransport": None,
        "testLabToken": None,
        "testLabTransport": None,
        "encodedTestLabToken": None,
        "redirectedToken": None,
        "redirectedTransport": None,
        "untrustedToken": None,
    }


def test_main_transport_uses_only_fixed_requests_and_sanitizes_responses():
    output = _run_node(
        r"""
const http = require('http');
const { EventEmitter } = require('events');
const requests = [];
const runId = '11111111-1111-4111-8111-111111111111';
http.request = (options, callback) => {
  const request = new EventEmitter();
  request.body = '';
  request.write = chunk => { request.body += chunk; };
  request.destroy = () => {};
  request.end = () => {
    requests.push({
      method: options.method,
      path: options.path,
      body: request.body ? JSON.parse(request.body) : null,
      idempotencyKey: options.headers['Idempotency-Key'] || null,
      hasToken: typeof options.headers['X-FreelancerStudio-Token'] === 'string',
      transport: options.headers['X-FreelancerStudio-Transport'],
    });
    const response = new EventEmitter();
    response.statusCode = options.path.endsWith('/runs') ? 202 : 200;
    response.resume = () => {};
    callback(response);
    let payload;
    if (options.path.endsWith('/capabilities')) payload = {
      available: true,
      reasons: [],
      backend_mode: 'loopback',
      operations: { launch: true, cancel: true, status: true, evidence: false },
      secret: 'C:\\private\\token.txt',
    };
    else if (options.path.endsWith('/cancel')) payload = { run_id: runId, status: 'cancelling', accepted: true, secret: 'hidden' };
    else if (options.path.endsWith('/frame')) payload = {
      run_id: runId,
      captured_at: '2026-07-31T12:00:00Z',
      frame_base64: 'aGVsbG8=',
      secret: 'C:\\private\\frame-path.png',
    };
    else if (options.path.endsWith('/input')) payload = { run_id: runId, executed: true, secret: 'hidden' };
    else if (options.method === 'GET') payload = {
      run_id: runId,
      operation: 'production_self_test',
      status: 'running',
      terminal: false,
      progress: { phase: 'running_checks', message: 'C:\\private\\raw detail' },
      result: null,
      errors: ['run_failed', 'private_error'],
      backend_process: 42,
    };
    else payload = { run_id: runId, status: 'queued', backend_run_id: 'private' };
    process.nextTick(() => {
      response.emit('data', Buffer.from(JSON.stringify(payload)));
      response.emit('end');
    });
  };
  return request;
};
const transport = require('./frontend/sandbox-test-lab-transport');
const context = { ready: true, restarting: false, host: '127.0.0.1', port: 8080, token: 'main-memory-token' };
const keyOne = '22222222-2222-4222-8222-222222222222';
const keyTwo = '33333333-3333-4333-8333-333333333333';
(async () => {
  const capabilities = await transport.getSandboxTestLabCapabilities(context);
  const launch = await transport.launchSandboxTestLabRun(context, 'production_self_test', keyOne);
  const retry = await transport.launchSandboxTestLabRun(context, 'production_self_test', keyOne);
  await transport.launchSandboxTestLabRun(context, 'production_self_test', keyTwo);
  const invalid = await transport.launchSandboxTestLabRun(context, 'powershell -Command whoami', keyOne);
  const status = await transport.getSandboxTestLabRun(context, runId);
  const frame = await transport.getSandboxTestLabRunFrame(context, runId);
  const input = await transport.sendSandboxTestLabRunInput(context, runId, { kind: 'click', x: 0.5, y: 0.5 });
  const cancel = await transport.cancelSandboxTestLabRun(context, runId);
  console.log(JSON.stringify({ requests, capabilities, launch, retry, invalid, status, frame, input, cancel }));
})();
"""
    )

    requests = output["requests"]
    assert [(item["method"], item["path"]) for item in requests] == [
        ("GET", "/api/sandbox-test-lab/capabilities"),
        ("POST", "/api/sandbox-test-lab/runs"),
        ("POST", "/api/sandbox-test-lab/runs"),
        ("POST", "/api/sandbox-test-lab/runs"),
        ("GET", "/api/sandbox-test-lab/runs/11111111-1111-4111-8111-111111111111"),
        ("GET", "/api/sandbox-test-lab/runs/11111111-1111-4111-8111-111111111111/frame"),
        ("POST", "/api/sandbox-test-lab/runs/11111111-1111-4111-8111-111111111111/input"),
        ("POST", "/api/sandbox-test-lab/runs/11111111-1111-4111-8111-111111111111/cancel"),
    ]
    assert requests[1]["body"] == {"operation": "production_self_test", "parameters": {}}
    assert requests[-1]["body"] == {"reason": "user_requested"}
    assert requests[1]["idempotencyKey"] == requests[2]["idempotencyKey"]
    assert requests[1]["idempotencyKey"] != requests[3]["idempotencyKey"]
    assert all(item["hasToken"] is True for item in requests)
    assert all(item["transport"] == "electron-main" for item in requests)
    assert output["invalid"]["error"]["code"] == "invalid_launch_request"
    assert output["status"]["data"]["progress"]["message"] == "Sandbox checks are in progress."
    assert output["status"]["data"]["errors"] == ["run_failed"]
    assert output["frame"]["data"] == {
        "run_id": "11111111-1111-4111-8111-111111111111",
        "captured_at": "2026-07-31T12:00:00Z",
        "frame_base64": "aGVsbG8=",
    }
    assert output["input"]["data"] == {
        "run_id": "11111111-1111-4111-8111-111111111111",
        "executed": True,
    }
    assert requests[-2]["body"] == {"kind": "click", "x": 0.5, "y": 0.5, "button": "left"}
    serialized = json.dumps(output).lower()
    assert "private\\" not in serialized
    assert "raw detail" not in serialized
    assert "backend_process" not in serialized
    assert "main-memory-token" not in serialized
    assert "\"secret\"" not in serialized


def test_sanitize_outgoing_input_action_enforces_the_closed_vocabulary():
    output = _run_node(
        r"""
const { sanitizeOutgoingInputAction } = require('./frontend/sandbox-test-lab-transport');
console.log(JSON.stringify({
  click: sanitizeOutgoingInputAction({ kind: 'click', x: 0.25, y: 0.75 }),
  clickExplicitButton: sanitizeOutgoingInputAction({ kind: 'click', x: 0, y: 1, button: 'right' }),
  clickOutOfRange: sanitizeOutgoingInputAction({ kind: 'click', x: 1.5, y: 0.5 }),
  clickBadButton: sanitizeOutgoingInputAction({ kind: 'click', x: 0.5, y: 0.5, button: 'middle' }),
  clickNonNumeric: sanitizeOutgoingInputAction({ kind: 'click', x: '0.5', y: 0.5 }),
  type: sanitizeOutgoingInputAction({ kind: 'type', text: 'hello' }),
  typeEmpty: sanitizeOutgoingInputAction({ kind: 'type', text: '' }),
  typeTooLong: sanitizeOutgoingInputAction({ kind: 'type', text: 'x'.repeat(501) }),
  typeControlChar: sanitizeOutgoingInputAction({ kind: 'type', text: 'a\nb' }),
  key: sanitizeOutgoingInputAction({ kind: 'key', key: 'enter' }),
  keyUnknown: sanitizeOutgoingInputAction({ kind: 'key', key: 'f1' }),
  unknownKind: sanitizeOutgoingInputAction({ kind: 'scroll' }),
  notAnObject: sanitizeOutgoingInputAction('click'),
  nullAction: sanitizeOutgoingInputAction(null),
}));
"""
    )

    assert output["click"] == {"kind": "click", "x": 0.25, "y": 0.75, "button": "left"}
    assert output["clickExplicitButton"] == {"kind": "click", "x": 0, "y": 1, "button": "right"}
    assert output["clickOutOfRange"] is None
    assert output["clickBadButton"] is None
    assert output["clickNonNumeric"] is None
    assert output["type"] == {"kind": "type", "text": "hello"}
    assert output["typeEmpty"] is None
    assert output["typeTooLong"] is None
    assert output["typeControlChar"] is None
    assert output["key"] == {"kind": "key", "key": "enter"}
    assert output["keyUnknown"] is None
    assert output["unknownKind"] is None
    assert output["notAnObject"] is None
    assert output["nullAction"] is None


def test_sanitize_input_result_accepts_only_the_known_shape():
    output = _run_node(
        r"""
const { sanitizeInputResult } = require('./frontend/sandbox-test-lab-transport');
const runId = '11111111-1111-4111-8111-111111111111';
console.log(JSON.stringify({
  executed: sanitizeInputResult({ run_id: runId, executed: true }),
  notExecuted: sanitizeInputResult({ run_id: runId, executed: false }),
  badRunId: sanitizeInputResult({ run_id: 'not-a-uuid', executed: true }),
  badExecuted: sanitizeInputResult({ run_id: runId, executed: 'yes' }),
  missing: sanitizeInputResult(null),
}));
"""
    )

    assert output["executed"] == {"run_id": "11111111-1111-4111-8111-111111111111", "executed": True}
    assert output["notExecuted"] == {"run_id": "11111111-1111-4111-8111-111111111111", "executed": False}
    assert output["badRunId"] is None
    assert output["badExecuted"] is None
    assert output["missing"] is None


def test_send_input_rejects_invalid_run_id_or_action_without_a_network_call():
    output = _run_node(
        r"""
let called = false;
const http = require('http');
http.request = () => { called = true; throw new Error('should not be called'); };
const transport = require('./frontend/sandbox-test-lab-transport');
const context = { ready: true, restarting: false, host: '127.0.0.1', port: 8080, token: 'main-memory-token' };
(async () => {
  const badRunId = await transport.sendSandboxTestLabRunInput(context, 'not-a-uuid', { kind: 'key', key: 'enter' });
  const badAction = await transport.sendSandboxTestLabRunInput(
    context,
    '11111111-1111-4111-8111-111111111111',
    { kind: 'key', key: 'f1' },
  );
  console.log(JSON.stringify({ called, badRunId, badAction }));
})();
"""
    )

    assert output["called"] is False
    assert output["badRunId"]["error"]["code"] == "run_not_found"
    assert output["badAction"]["error"]["code"] == "invalid_input_request"


def test_launch_project_name_is_wired_through_and_validated_before_any_network_call():
    output = _run_node(
        r"""
const requests = [];
const http = require('http');
http.request = (options, callback) => {
  const { EventEmitter } = require('events');
  const request = new EventEmitter();
  request.write = () => {};
  request.destroy = () => {};
  request.end = () => {
    requests.push({ path: options.path, body: JSON.parse(request.body || '{}') });
    const response = new EventEmitter();
    response.statusCode = 202;
    response.resume = () => {};
    callback(response);
    process.nextTick(() => {
      response.emit('data', Buffer.from(JSON.stringify({ run_id: '11111111-1111-4111-8111-111111111111', status: 'queued' })));
      response.emit('end');
    });
  };
  request.body = '';
  const originalWrite = request.write;
  request.write = chunk => { request.body += chunk; };
  return request;
};
const transport = require('./frontend/sandbox-test-lab-transport');
const context = { ready: true, restarting: false, host: '127.0.0.1', port: 8080, token: 'main-memory-token' };
(async () => {
  const withProject = await transport.launchSandboxTestLabRun(
    context, 'interactive_session', '22222222-2222-4222-8222-222222222222', 'demo-project',
  );
  const withoutProject = await transport.launchSandboxTestLabRun(
    context, 'production_self_test', '33333333-3333-4333-8333-333333333333', null,
  );
  const wrongOperation = await transport.launchSandboxTestLabRun(
    context, 'production_self_test', '44444444-4444-4444-8444-444444444444', 'demo-project',
  );
  const badFormat = await transport.launchSandboxTestLabRun(
    context, 'interactive_session', '55555555-5555-4555-8555-555555555555', '../escape',
  );
  console.log(JSON.stringify({ requests, withProject, withoutProject, wrongOperation, badFormat }));
})();
"""
    )

    assert [item["body"] for item in output["requests"]] == [
        {"operation": "interactive_session", "parameters": {"project_name": "demo-project"}},
        {"operation": "production_self_test", "parameters": {}},
    ]
    assert output["withProject"]["ok"] is True
    assert output["withoutProject"]["ok"] is True
    assert output["wrongOperation"]["error"]["code"] == "invalid_launch_request"
    assert output["badFormat"]["error"]["code"] == "invalid_launch_request"


def test_preload_and_ipc_surface_are_narrow_and_token_free():
    preload = Path("frontend/preload.js").read_text(encoding="utf-8")
    main_source = Path("frontend/main.js").read_text(encoding="utf-8")
    package = json.loads(Path("frontend/package.json").read_text(encoding="utf-8"))

    for method in ("getCapabilities", "launchRun", "getRun", "getFrame", "sendInput", "cancelRun"):
        assert method in preload
    assert "authenticatedFetch" not in preload
    assert "fetch:" not in preload
    assert "url" not in preload.lower()
    assert "method" not in preload.lower()
    assert "Token" not in preload
    assert "token" not in preload
    assert "isAllowedDownloadSender(event, mainWindow?.webContents, expectedRendererOrigin)" in main_source
    assert "untrusted_renderer" in main_source
    assert "sandbox-test-lab-capabilities" in main_source
    assert "sandbox-test-lab-launch" in main_source
    assert "sandbox-test-lab-status" in main_source
    assert "sandbox-test-lab-frame" in main_source
    assert "sandbox-test-lab-input" in main_source
    assert "sandbox-test-lab-cancel" in main_source
    assert "existingInstance !== instanceId" in main_source
    assert "backend_restarted" in main_source
    assert "backend-request-policy.js" in package["build"]["files"]
    assert "sandbox-test-lab-transport.js" in package["build"]["files"]


def test_renderer_feature_has_fixed_operations_secure_uuid_and_no_executor_inputs():
    feature = Path("frontend/src/features/sandbox-test-lab")
    source = "\n".join(path.read_text(encoding="utf-8") for path in feature.rglob("*.js*"))

    assert "production_self_test" in source
    assert "production_screenshot" in source
    assert "crypto.randomUUID" not in source
    assert "secureCrypto.randomUUID" in source
    assert "secureCrypto.getRandomValues" in source
    assert "Math.random" not in source
    assert "localStorage" not in source
    assert "sessionStorage" not in source
    assert "document.cookie" not in source
    assert "console." not in source
    assert "authenticatedFetch" not in source
    assert not re.search(r'<(?:input|textarea)[^>]+(?:command|powershell|executable|environment|path)', source, re.I)
    assert "requestInFlight.current" in source
    assert "if (cancelled) return" in source
    assert "window.clearTimeout(timer)" in source
    assert "canApplyRunSnapshot(current.status, snapshot.status)" in source
