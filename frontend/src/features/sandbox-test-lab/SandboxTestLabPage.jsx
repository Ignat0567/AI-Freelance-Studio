import React, { useEffect, useRef, useState } from 'react';
import SandboxTestLabConfirmDialog from './SandboxTestLabConfirmDialog.jsx';
import { createSecureIdempotencyKey, sandboxTestLabApi } from './sandboxTestLabApi.js';
import {
  CANCELLABLE_STATUSES,
  RUN_ERROR_MESSAGES,
  SANDBOX_OPERATIONS,
  TERMINAL_STATUSES,
  canApplyRunSnapshot,
  formatObservedDuration,
  formatObservedTime,
  operationDetails,
  reasonMessage,
  shortRunId,
  statusDetails,
} from './sandboxTestLabModels.js';
import { useSandboxLiveFrame } from './useSandboxLiveFrame.js';
import { useSandboxRunMonitor } from './useSandboxRunMonitor.js';

function CapabilityState({ state, capability, onRefresh }) {
  if (state === 'loading') {
    return <div className="fs-test-lab-state loading" role="status" aria-live="polite"><span className="fs-test-lab-spinner" aria-hidden="true" />Checking protected local availability...</div>;
  }
  if (state === 'available') {
    return (
      <div className="fs-test-lab-state ready" role="status" aria-live="polite">
        <div><span className="fs-test-lab-state-mark" aria-hidden="true">OK</span><div><strong>Sandbox Test Lab is ready</strong><span>Available only through the protected local Studio backend.</span></div></div>
        <button type="button" className="fs-secondary" onClick={onRefresh}>Refresh</button>
      </div>
    );
  }
  const codes = capability?.reasons?.map(reason => reason.code) || [state === 'backend_restarting' ? 'backend_restarting' : 'transport_error'];
  return (
    <div className={`fs-test-lab-state ${state === 'backend_restarting' ? 'reconnecting' : 'unavailable'}`} role="alert">
      <div><span className="fs-test-lab-state-mark" aria-hidden="true">!</span><div><strong>{state === 'backend_restarting' ? 'Reconnecting to the local backend' : 'Sandbox Test Lab is unavailable'}</strong>{codes.map(code => <span key={code}>{reasonMessage(code)}</span>)}</div></div>
      <button type="button" className="fs-secondary" onClick={onRefresh}>Try again</button>
    </div>
  );
}

function OperationSelector({ selected, disabled, onSelect, onLaunch }) {
  return (
    <section className="fs-panel fs-test-lab-selector" aria-labelledby="test-lab-operations-title">
      <div className="fs-panel-title"><div><span>Controlled operations</span><strong id="test-lab-operations-title">Choose one validation workflow</strong></div></div>
      <fieldset className="fs-test-lab-operation-grid" disabled={disabled}>
        <legend className="sr-only">Sandbox Test Lab operation</legend>
        {Object.values(SANDBOX_OPERATIONS).map(operation => (
          <label key={operation.id} className={`fs-test-lab-operation ${selected === operation.id ? 'selected' : ''}`}>
            <input type="radio" name="sandbox-operation" value={operation.id} checked={selected === operation.id} onChange={() => onSelect(operation.id)} />
            <span className="fs-test-lab-operation-mark" aria-hidden="true">{operation.short}</span>
            <span><strong>{operation.name}</strong><small>{operation.description}</small></span>
            <i aria-hidden="true" />
          </label>
        ))}
      </fieldset>
      <div className="fs-test-lab-launch-row">
        <p>{selected ? `${operationDetails(selected).name} is selected.` : 'Select one operation to continue.'}</p>
        <button type="button" className="fs-primary" disabled={disabled || !selected} onClick={onLaunch}>Review and launch</button>
      </div>
    </section>
  );
}

function RunView({ run, reconnecting, cancellationPending, cancellationNote, liveFrame, onCancel, onReturn, onRunAgain }) {
  const headingRef = useRef(null);
  useEffect(() => { headingRef.current?.focus(); }, []);
  const operation = operationDetails(run.operation);
  const status = statusDetails(run.status);
  const duration = formatObservedDuration(run.startedAt, run.finishedAt);
  const canCancel = CANCELLABLE_STATUSES.has(run.status) && !cancellationPending;
  return (
    <section className={`fs-panel fs-test-lab-run ${status.tone}`} aria-labelledby="test-lab-run-title">
      <div className="fs-test-lab-run-heading">
        <div><span className="fs-eyebrow">Current controlled run</span><h2 ref={headingRef} id="test-lab-run-title" tabIndex={-1}>{operation?.name || 'Sandbox operation'}</h2></div>
        <span className={`fs-status ${status.tone}`}>{status.label}</span>
      </div>
      {reconnecting && <div className="fs-test-lab-reconnecting" role="status" aria-live="polite">Reconnecting to the local backend. The operation will not be relaunched.</div>}
      {liveFrame && (
        <div className="fs-test-lab-live-frame">
          <img src={liveFrame.dataUrl} alt="Live Sandbox session preview" />
          <span className="fs-test-lab-live-frame-caption">Live preview -- updates every few seconds</span>
        </div>
      )}
      <div className="fs-test-lab-progress" role="status" aria-live="polite" aria-atomic="true">
        <span className="fs-test-lab-progress-mark" aria-hidden="true">{run.terminal ? 'DONE' : 'LIVE'}</span>
        <div><strong>{run.progress?.message || 'Waiting for a sanitized status update.'}</strong><span>Phase: {String(run.progress?.phase || 'not_started').replaceAll('_', ' ')}</span></div>
      </div>
      <dl className="fs-test-lab-facts">
        <div><dt>Run ID</dt><dd title={run.run_id}>{shortRunId(run.run_id)}</dd></div>
        <div><dt>Launch observed</dt><dd>{formatObservedTime(run.createdAt)}</dd></div>
        <div><dt>Start observed</dt><dd>{formatObservedTime(run.startedAt)}</dd></div>
        <div><dt>Finish observed</dt><dd>{formatObservedTime(run.finishedAt)}</dd></div>
        {duration && <div><dt>Observed duration</dt><dd>{duration}</dd></div>}
      </dl>
      {run.errors?.length > 0 && <div className="fs-test-lab-errors" role="alert"><strong>Sanitized run details</strong>{run.errors.map(code => <p key={code}>{RUN_ERROR_MESSAGES[code] || 'The run returned a protected failure code.'}</p>)}</div>}
      {cancellationNote && <p className="fs-test-lab-cancel-note" role="status">Cancellation was requested. The current Sandbox operation may need to reach a safe interruption point before it stops.</p>}
      {run.result?.manual_close_required && <p className="fs-test-lab-cancel-note warning">The controlled session may require manual closing.</p>}
      <div className="fs-test-lab-run-actions">
        {canCancel && <button type="button" className="fs-danger-button" onClick={onCancel}>Cancel run</button>}
        {cancellationPending && <button type="button" className="fs-danger-button" disabled>Requesting cancellation...</button>}
        {run.terminal && <><button type="button" className="fs-secondary" onClick={onReturn}>Return to operations</button><button type="button" className="fs-primary" onClick={onRunAgain}>Run again</button></>}
      </div>
    </section>
  );
}

export default function SandboxTestLabPage({ active }) {
  const [capabilityState, setCapabilityState] = useState('loading');
  const [capability, setCapability] = useState(null);
  const [selectedOperation, setSelectedOperation] = useState('');
  const [confirmationOpen, setConfirmationOpen] = useState(false);
  const [launchPending, setLaunchPending] = useState(false);
  const [launchError, setLaunchError] = useState('');
  const [launchRetryAvailable, setLaunchRetryAvailable] = useState(false);
  const [currentRun, setCurrentRun] = useState(null);
  const [trackingLost, setTrackingLost] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [cancelPending, setCancelPending] = useState(false);
  const [cancellationNote, setCancellationNote] = useState(false);
  const mounted = useRef(true);
  const capabilityRequest = useRef(0);
  const capabilityInFlight = useRef(false);
  const launchIntent = useRef(null);
  const cancelRequest = useRef(0);
  const currentRunRef = useRef(null);
  const loadedOnce = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  currentRunRef.current = currentRun;

  const loadCapabilities = async () => {
    if (capabilityInFlight.current) return;
    capabilityInFlight.current = true;
    const requestId = ++capabilityRequest.current;
    setCapabilityState('loading');
    const result = await sandboxTestLabApi.getCapabilities();
    capabilityInFlight.current = false;
    if (!mounted.current || requestId !== capabilityRequest.current) return;
    loadedOnce.current = true;
    if (result.ok) {
      setCapability(result.data);
      setCapabilityState(result.data.available ? 'available' : 'unavailable');
      return;
    }
    const code = result.error?.code || 'transport_error';
    if (code === 'network_bind_disallowed') {
      setCapability({ available: false, reasons: [{ code }] });
      setCapabilityState('unavailable');
    } else {
      setCapability({ available: false, reasons: [{ code }] });
      setCapabilityState(code === 'backend_restarting' ? 'backend_restarting' : 'transport_error');
    }
  };

  useEffect(() => {
    if (active && !loadedOnce.current) loadCapabilities();
  }, [active]);

  const applySnapshot = snapshot => {
    if (snapshot.terminal) {
      cancelRequest.current += 1;
      setCancelPending(false);
    }
    setCurrentRun(current => {
      if (
        !current
        || current.run_id !== snapshot.run_id
        || current.terminal
        || !canApplyRunSnapshot(current.status, snapshot.status)
      ) return current;
      const now = new Date().toISOString();
      const startedAt = current.startedAt || (snapshot.status === 'queued' ? null : now);
      return {
        ...current,
        ...snapshot,
        createdAt: current.createdAt,
        startedAt,
        finishedAt: snapshot.terminal ? (current.finishedAt || now) : null,
      };
    });
    setReconnecting(false);
  };

  useSandboxRunMonitor({
    runId: currentRun?.run_id,
    terminal: currentRun?.terminal,
    enabled: Boolean(active && currentRun && !trackingLost),
    onSnapshot: applySnapshot,
    onTransportFailure: () => setReconnecting(true),
    onRecovered: loadCapabilities,
    onRunNotFound: () => {
      setTrackingLost(true);
      setReconnecting(false);
      launchIntent.current = null;
      loadCapabilities();
    },
  });

  const liveFrame = useSandboxLiveFrame({
    runId: currentRun?.run_id,
    enabled: Boolean(
      active && currentRun && !trackingLost
      && currentRun.operation === 'interactive_session' && currentRun.status === 'running',
    ),
  });

  const selectOperation = operation => {
    if (!Object.hasOwn(SANDBOX_OPERATIONS, operation)) return;
    setSelectedOperation(operation);
    setLaunchError('');
    setLaunchRetryAvailable(false);
    launchIntent.current = null;
  };

  const performLaunch = async () => {
    if (launchPending || !selectedOperation || !Object.hasOwn(SANDBOX_OPERATIONS, selectedOperation)) return;
    if (!launchIntent.current || launchIntent.current.operation !== selectedOperation) {
      try {
        launchIntent.current = { operation: selectedOperation, key: createSecureIdempotencyKey() };
      } catch {
        setLaunchError('backend_security_unavailable');
        return;
      }
    }
    setLaunchPending(true);
    setLaunchError('');
    const intent = launchIntent.current;
    const result = await sandboxTestLabApi.launchRun(intent.operation, intent.key);
    if (!mounted.current || launchIntent.current !== intent) return;
    setLaunchPending(false);
    if (!result.ok) {
      const code = result.error?.code || 'transport_error';
      setLaunchError(code);
      const retryable = ['transport_error', 'backend_restarting', 'backend_security_unavailable'].includes(code);
      setLaunchRetryAvailable(retryable);
      if (!retryable) launchIntent.current = null;
      setConfirmationOpen(false);
      if (code === 'backend_restarting') setCapabilityState('backend_restarting');
      return;
    }
    const now = new Date().toISOString();
    setCurrentRun({
      run_id: result.data.run_id,
      operation: selectedOperation,
      status: result.data.status,
      terminal: TERMINAL_STATUSES.has(result.data.status),
      progress: { phase: 'not_started', message: 'The run is queued.' },
      result: null,
      errors: [],
      createdAt: now,
      startedAt: null,
      finishedAt: null,
    });
    setTrackingLost(false);
    setReconnecting(false);
    setCancellationNote(false);
    setLaunchRetryAvailable(false);
    launchIntent.current = null;
    setConfirmationOpen(false);
  };

  const cancelRun = async () => {
    if (!currentRun || !CANCELLABLE_STATUSES.has(currentRun.status) || cancelPending) return;
    setCancelPending(true);
    const runId = currentRun.run_id;
    const requestId = ++cancelRequest.current;
    const result = await sandboxTestLabApi.cancelRun(runId);
    if (
      !mounted.current
      || requestId !== cancelRequest.current
      || currentRunRef.current?.run_id !== runId
    ) return;
    if (result.ok) {
      const terminalCancellation = result.data.status === 'cancelled';
      if (terminalCancellation) {
        const snapshot = await sandboxTestLabApi.getRun(runId);
        if (
          !mounted.current
          || requestId !== cancelRequest.current
          || currentRunRef.current?.run_id !== runId
        ) return;
        if (snapshot.ok) {
          applySnapshot(snapshot.data);
          return;
        }
      }
      setCancelPending(false);
      setCancellationNote(!terminalCancellation && result.data.accepted);
      setCurrentRun(current => {
        if (!current || current.run_id !== runId || current.terminal) return current;
        if (terminalCancellation) {
          const now = new Date().toISOString();
          return {
            ...current,
            status: 'cancelled',
            terminal: true,
            progress: { phase: 'complete', message: 'The run is complete.' },
            result: { outcome: 'cancelled', manual_close_required: false },
            finishedAt: current.finishedAt || now,
          };
        }
        return { ...current, status: result.data.status };
      });
      return;
    }
    const code = result.error?.code || 'transport_error';
    if (code === 'run_already_terminal') {
      const snapshot = await sandboxTestLabApi.getRun(runId);
      if (
        !mounted.current
        || requestId !== cancelRequest.current
        || currentRunRef.current?.run_id !== runId
      ) return;
      if (snapshot.ok) applySnapshot(snapshot.data);
      else setCancelPending(false);
    } else if (code === 'run_not_found') {
      setCancelPending(false);
      setTrackingLost(true);
      loadCapabilities();
    } else {
      setCancelPending(false);
      setReconnecting(true);
    }
  };

  const resetRun = clearSelection => {
    cancelRequest.current += 1;
    setCancelPending(false);
    setCurrentRun(null);
    setTrackingLost(false);
    setReconnecting(false);
    setCancellationNote(false);
    setLaunchError('');
    setLaunchRetryAvailable(false);
    launchIntent.current = null;
    if (clearSelection) setSelectedOperation('');
  };

  const runAgain = () => {
    resetRun(false);
    setConfirmationOpen(true);
  };

  const selectorDisabled = capabilityState !== 'available' || launchPending || Boolean(currentRun && !currentRun.terminal);

  return (
    <div className="fs-test-lab-page" data-testid="sandbox-test-lab-page">
      <section className="fs-test-lab-intro">
        <div><span className="fs-eyebrow">Protected local validation</span><h1>Sandbox Test Lab</h1><p>Run a supported production-style check, or open a direct interactive session, through Studio's loopback-only control boundary. No commands, paths, or environment values are accepted here.</p></div>
        <div className="fs-test-lab-security-note"><strong>Local boundary</strong><span>Authorization remains inside Electron main and is never shown on this page.</span></div>
      </section>

      <CapabilityState state={capabilityState} capability={capability} onRefresh={loadCapabilities} />

      {trackingLost ? (
        <section className="fs-panel fs-test-lab-lost" role="alert">
          <span className="fs-eyebrow">Tracking unavailable</span>
          <h2>The backend no longer recognizes this run</h2>
          <p>{reasonMessage('run_not_found')}</p>
          <button type="button" className="fs-secondary" onClick={() => resetRun(false)}>Return to operations</button>
        </section>
      ) : currentRun ? (
        <RunView
          run={currentRun}
          reconnecting={reconnecting}
          cancellationPending={cancelPending}
          cancellationNote={cancellationNote}
          liveFrame={liveFrame}
          onCancel={cancelRun}
          onReturn={() => resetRun(true)}
          onRunAgain={runAgain}
        />
      ) : (
        <OperationSelector selected={selectedOperation} disabled={selectorDisabled} onSelect={selectOperation} onLaunch={() => setConfirmationOpen(true)} />
      )}

      {launchError && !currentRun && (
        <div className="fs-test-lab-request-error" role="alert">
          <div><strong>Launch was not confirmed</strong><span>{reasonMessage(launchError)}</span></div>
          {launchRetryAvailable && <button type="button" className="fs-secondary" disabled={launchPending} onClick={performLaunch}>{launchPending ? 'Retrying...' : 'Retry launch request'}</button>}
        </div>
      )}

      <p className="fs-test-lab-limits">Detailed evidence access and run history are not available in this version.</p>

      {confirmationOpen && operationDetails(selectedOperation) && (
        <SandboxTestLabConfirmDialog operation={operationDetails(selectedOperation)} pending={launchPending} onConfirm={performLaunch} onClose={() => !launchPending && setConfirmationOpen(false)} />
      )}
    </div>
  );
}
