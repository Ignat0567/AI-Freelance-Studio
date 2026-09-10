import React, { useEffect, useRef, useState } from 'react';
import { orderWorkflowApi } from './orderWorkflowApi.js';
import { STAGE_PROGRESS, formatLabel, isTerminalExecution } from './orderWorkflowState.js';

export const CODING_BACKENDS = [
  { id: 'ollama', label: 'Local Ollama', detail: 'Grok writes a spec. Qwen 14B writes the files on this machine.' },
  { id: 'grok', label: 'Grok subscription', detail: 'Grok CLI writes the project files using your grok.com login.' },
  { id: 'claude_code', label: 'Claude Code subscription', detail: 'Claude Code CLI writes the project files using your Claude login.' },
  { id: 'opencode_bridge', label: 'OpenCode', detail: 'OpenCode writes the project files through the saved bridge connection.' },
  { id: 'openrouter', label: 'OpenRouter API', detail: 'OpenRouter writes the project files using the saved OpenRouter API key. Pick the model on this screen.' },
];

function ExecutionChoicePanel({ choice, setChoice, disabled }) {
  const selected = CODING_BACKENDS.find(item => item.id === choice.coding_backend) || CODING_BACKENDS[0];
  const connections = choice.connections || [];
  const models = choice.models || [];
  return (
    <section className="ow-callout" aria-labelledby="ow-run-choice-title">
      <div>
        <span className="fs-eyebrow">This run</span>
        <h3 id="ow-run-choice-title">Provider, model, and coding worker</h3>
        <p>Saved in Settings. Change them here if this order should use a different subscription than the last one.</p>
      </div>
      <div className="ow-grid two">
        <label>Provider connection
          <select value={choice.connection_id} disabled={disabled} onChange={event => setChoice(current => ({ ...current, connection_id: event.target.value, model: '' }))}>
            <option value="">{connections.length ? 'Select provider connection' : 'No provider connections yet'}</option>
            {connections.map(item => (
              <option key={item.connection_id} value={item.connection_id}>{item.display_name || item.name || item.provider}</option>
            ))}
          </select>
        </label>
        <label>Model
          <select value={choice.model} disabled={disabled || !choice.connection_id} onChange={event => setChoice(current => ({ ...current, model: event.target.value }))}>
            <option value="">{choice.connection_id ? 'Select model' : 'Select a provider first'}</option>
            {models.map(item => (
              <option key={item.id} value={item.id}>{item.id}</option>
            ))}
          </select>
        </label>
      </div>
      <label>Who writes the project files
        <select value={choice.coding_backend} disabled={disabled} onChange={event => setChoice(current => ({ ...current, coding_backend: event.target.value }))}>
          {CODING_BACKENDS.map(item => (
            <option key={item.id} value={item.id}>{item.label}</option>
          ))}
        </select>
      </label>
      <p className="ow-note">{selected.detail}</p>
    </section>
  );
}

function ReadinessPill({ label, ready, unavailable }) {
  return <div className={ready ? 'ready' : unavailable ? 'unavailable' : 'blocked'}><span>{label}</span><strong>{ready ? 'Ready' : unavailable ? 'Unavailable' : 'Blocked'}</strong></div>;
}

function ExecutionReadinessPanel({ readiness }) {
  const checks = readiness?.checks || [];
  const liveReady = Boolean(readiness?.production_live_ready);
  return (
    <section className="ow-readiness" aria-labelledby="ow-readiness-title">
      <div>
        <span className="fs-eyebrow">Execution Readiness</span>
        <h3 id="ow-readiness-title">Can Studio execute this order?</h3>
        <p>Live build writes a real project with Grok specs and local Ollama coding, then runs QA. Simulation is a fake executor for UI checks only. Production dry-run prepares a package without writing files.</p>
        <p>Checks: AI provider, Model, Workspace, QA tools, Live execution opt-in.</p>
        <p>Environment checklist: Grok CLI (`grok login`), Ollama (`ollama serve` and `ollama pull qwen2.5-coder:14b`), Start Docker Desktop, Node 20+, disk, Live execution opt-in.</p>
      </div>
      <div className="ow-readiness-modes">
        <ReadinessPill label="Live execution" ready={liveReady} unavailable={!liveReady} />
        <ReadinessPill label="Production dry-run" ready={readiness?.production_dry_run_ready} />
        <ReadinessPill label="Simulation mode" ready={readiness?.simulation_ready} />
      </div>
      <div className="ow-readiness-checks">{checks.map(check => (
        <article key={check.code} className={check.status}>
          <b>{check.label}</b>
          <span>{formatLabel(check.status)} - {check.message}</span>
          {check.action && check.status === 'blocked' && <em>Fix: {check.action}</em>}
        </article>
      ))}</div>
      {readiness?.blockers?.length > 0 && <div className="ow-callout warning"><strong>Action suggestions</strong>{readiness.blockers.map(item => <p key={item.code}>{item.message} <b>{item.action}</b></p>)}<p>Open Settings from the sidebar and enable Live coding execution, then confirm Grok CLI and Ollama are ready.</p></div>}
    </section>
  );
}

export default function ExecutionDashboard({ state, readiness, pending, canStart, canDryRun, canLive, liveConfirm, setLiveConfirm, onStart, onDryRun, onLive, onCancel, onRefresh, onRefreshReadiness, usageSummary }) {
  const execution = state?.execution;
  const progress = execution?.progress ?? STAGE_PROGRESS[execution?.stage] ?? 0;
  const live = Boolean(execution?.live);
  const quota = usageSummary?.last_rate_limit;
  const quotaActive = Boolean(quota?.message) && !quota?.stale;
  const envBlocked = (readiness?.checks || []).some(check => check.status === 'blocked');
  const optInBlocked = (readiness?.checks || []).some(check => check.code === 'live_opt_in' && check.status === 'blocked');
  const [choice, setChoice] = useState({ coding_backend: 'ollama', connection_id: '', model: '', connections: [], models: [] });

  useEffect(() => {
    if (execution) return undefined;
    let cancelled = false;
    Promise.all([orderWorkflowApi.getGlobalAI().catch(() => ({})), orderWorkflowApi.getSystemConfig().catch(() => ({}))]).then(([globalPayload, systemPayload]) => {
      if (cancelled) return;
      const globalAI = globalPayload.global_ai || {};
      const connections = globalPayload.connections || [];
      const mapped = connections.find(item => item.connection_id === globalAI.connection_id) || connections.find(item => item.provider === globalAI.provider) || connections[0];
      const connectionId = mapped?.connection_id || globalAI.connection_id || '';
      const backend = systemPayload.coding_backend || 'ollama';
      setChoice(current => ({
        ...current,
        coding_backend: CODING_BACKENDS.some(item => item.id === backend) ? backend : 'ollama',
        connection_id: connectionId,
        model: globalAI.model || '',
        connections,
      }));
      if (!connectionId) return;
      return orderWorkflowApi.getConnectionModels(connectionId).then(data => {
        if (cancelled) return;
        const models = (data.models || []).filter(item => item?.capabilities?.text_input !== false);
        setChoice(current => ({ ...current, models: current.model && !models.some(item => item.id === current.model) ? [{ id: current.model }, ...models] : models }));
      }).catch(() => {});
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [execution]);

  useEffect(() => {
    if (execution || !choice.connection_id) return undefined;
    let cancelled = false;
    orderWorkflowApi.getConnectionModels(choice.connection_id).then(data => {
      if (cancelled) return;
      const models = (data.models || []).filter(item => item?.capabilities?.text_input !== false);
      setChoice(current => ({
        ...current,
        models: current.model && !models.some(item => item.id === current.model) ? [{ id: current.model }, ...models] : models,
        model: current.model && models.some(item => item.id === current.model) ? current.model : (models[0]?.id || current.model),
      }));
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [choice.connection_id, execution]);

  const launchChoice = {
    coding_backend: choice.coding_backend,
    connection_id: choice.connection_id,
    model: choice.model,
  };

  return (
    <section className="fs-panel ow-card" data-glass aria-labelledby="ow-execution-title">
      <div className="fs-panel-title"><div><span>Execution Dashboard</span><strong id="ow-execution-title">{execution ? (live ? 'Live build' : 'Simulation mode') : 'Ready to build'}</strong></div></div>
      {!execution && <ExecutionReadinessPanel readiness={readiness} />}
      {!execution && quotaActive && (
        <div className="ow-callout warning" role="alert">
          <strong>Provider quota</strong>
          <span>{quota.message}</span>
          <p>Do not start a new order. Wait for the reset, then use Retry this workspace on the result screen.</p>
        </div>
      )}
      {!execution && <ExecutionChoicePanel choice={choice} setChoice={setChoice} disabled={pending} />}
      {!execution && canLive && <div className="ow-callout"><strong>Live build</strong><span>The selected coding worker writes the project, then QA has to pass. This spends subscription time and CPU on this machine.</span></div>}
      {!execution && !canLive && optInBlocked && !quotaActive && <div className="ow-callout warning"><strong>Live execution locked</strong><span>Enable Live coding execution in Settings before starting a real build. Simulation remains available for UI checks.</span></div>}
      {!execution && !canStart && <div className="ow-callout warning" role="alert"><strong>Approval required</strong><span>Approve the current brief and prepare the Alex to Codex handoff before starting execution.</span></div>}
      {execution && (
        <div className="ow-execution-split">
          <div className="ow-execution-main">
            <div className="ow-status-grid"><div><span>Status</span><strong>{formatLabel(execution.status)}</strong></div><div><span>Stage</span><strong>{formatLabel(execution.stage)}</strong></div><div><span>Active agent</span><strong>{execution.active_agent || 'Queued'}</strong></div><div><span>Progress</span><strong>{progress}%</strong></div></div>
            <div className="ow-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow={progress}><span style={{ width: `${progress}%` }} /></div>
            <p className="ow-note" aria-live="polite">{execution.current_activity}</p>
            {execution.blockers?.length > 0 && <div className="ow-callout warning"><strong>Action required</strong>{execution.blockers.map(item => <p key={item.code}>{item.message} <b>{item.action}</b></p>)}</div>}
            <EventTimeline events={execution.events} />
          </div>
          <CodingWorkbench execution={execution} />
        </div>
      )}
      {!execution && canLive && <div className="ow-callout warning"><label><input type="checkbox" checked={liveConfirm} onChange={event => setLiveConfirm(event.target.checked)} /> This will write files in the generated project workspace using the provider and coding worker selected above. Continue?</label></div>}
      <div className="ow-actions">
        <button type="button" className="fs-secondary" onClick={onRefreshReadiness} disabled={pending || !state?.order}>Refresh readiness</button>
        <button type="button" className="fs-secondary" onClick={onRefresh} disabled={pending || !state?.order}>Refresh execution</button>
        {!execution && <button type="button" className="fs-primary" onClick={() => onLive(launchChoice)} disabled={pending || !canLive || !liveConfirm || quotaActive || envBlocked}>{pending ? 'Starting...' : 'Start live build'}</button>}
        {!canLive && !execution && <button type="button" className="fs-secondary" disabled>Live execution locked</button>}
        {!execution && <button type="button" className="fs-secondary" onClick={onStart} disabled={pending || !canStart}>Run simulation</button>}
        {!execution && <button type="button" className="fs-secondary" onClick={onDryRun} disabled={pending || !canDryRun}>Prepare production dry-run</button>}
        {execution && !isTerminalExecution(execution) && <button type="button" className="fs-danger-button" onClick={onCancel} disabled={pending}>Cancel execution</button>}
      </div>
    </section>
  );
}

export function EventTimeline({ events = [] }) {
  return <section className="ow-timeline"><h3>Recent activity</h3>{events.length ? events.slice(-8).map(event => <article key={event.id}><b>{event.agent || 'Studio'}</b><span>{formatLabel(event.stage)} - {event.message}</span></article>) : <p className="fs-empty">No execution events yet.</p>}</section>;
}

export function fileBodiesFromTranscript(transcript) {
  const files = {};
  const pattern = /<<<FILE\s+([^\r\n]+)\s*\r?\n([\s\S]*?)(?:\r?\nFILE>>>|$)/g;
  let match = pattern.exec(transcript || '');
  while (match) {
    files[match[1].trim()] = match[2];
    match = pattern.exec(transcript || '');
  }
  return files;
}

export function CodingWorkbench({ execution }) {
  const transcript = execution?.coding_transcript || '';
  const listed = execution?.coding_files || [];
  const bodies = fileBodiesFromTranscript(transcript);
  const files = listed.length ? listed : Object.keys(bodies);
  const [active, setActive] = useState('');
  const scroller = useRef(null);
  const running = Boolean(execution) && !isTerminalExecution(execution);
  const selected = files.includes(active) ? active : (files[files.length - 1] || '');
  const display = selected && bodies[selected] != null ? bodies[selected] : transcript;

  useEffect(() => {
    if (scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight;
  }, [display]);

  return (
    <section className="ow-coding" aria-labelledby="ow-coding-title">
      <div className="ow-coding-head">
        <div>
          <span className="fs-eyebrow">Coding</span>
          <h3 id="ow-coding-title">{running ? 'Writing project files' : 'Coding output'}</h3>
        </div>
        <strong className={running ? 'ow-coding-pulse' : undefined}>{execution?.active_agent || 'Coder'}</strong>
      </div>
      {files.length > 0 && (
        <div className="ow-coding-files" role="tablist" aria-label="Files being written">
          {files.map(path => (
            <button type="button" key={path} role="tab" aria-selected={path === selected} className={path === selected ? 'active' : undefined} onClick={() => setActive(path)}>{path}</button>
          ))}
        </div>
      )}
      <pre ref={scroller} className={running ? 'ow-coding-stream live' : 'ow-coding-stream'} aria-live="polite">{display || (running ? 'Waiting for the coder to start writing…' : 'No coding output was captured.')}</pre>
    </section>
  );
}
