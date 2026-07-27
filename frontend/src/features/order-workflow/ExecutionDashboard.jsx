import React from 'react';
import { STAGE_PROGRESS, formatLabel, isTerminalExecution } from './orderWorkflowState.js';

function ReadinessPill({ label, ready, unavailable }) {
  return <div className={ready ? 'ready' : unavailable ? 'unavailable' : 'blocked'}><span>{label}</span><strong>{ready ? 'Ready' : unavailable ? 'Unavailable' : 'Blocked'}</strong></div>;
}

function ExecutionReadinessPanel({ readiness }) {
  const checks = readiness?.checks || [];
  return (
    <section className="ow-readiness" aria-labelledby="ow-readiness-title">
      <div><span className="fs-eyebrow">Execution Readiness</span><h3 id="ow-readiness-title">Can Studio execute this order?</h3><p>Simulation is local fake execution. Production dry-run only prepares a package. Live execution is not enabled.</p><p>Checks: OpenCode, AI provider, Model, Workspace, QA tools, Live execution opt-in.</p></div>
      <div className="ow-readiness-modes"><ReadinessPill label="Simulation mode" ready={readiness?.simulation_ready} /><ReadinessPill label="Production dry-run" ready={readiness?.production_dry_run_ready} /><ReadinessPill label="Live execution" ready={readiness?.production_live_ready} unavailable={!readiness?.production_live_ready} /></div>
      <div className="ow-readiness-checks">{checks.map(check => <article key={check.code} className={check.status}><b>{check.label}</b><span>{formatLabel(check.status)} - {check.message}</span></article>)}</div>
      {readiness?.blockers?.length > 0 && <div className="ow-callout warning"><strong>Action suggestions</strong>{readiness.blockers.map(item => <p key={item.code}>{item.message} <b>{item.action}</b></p>)}<p>Open Settings from the sidebar and configure OpenCode/provider, model, workspace, and QA commands.</p></div>}
    </section>
  );
}

export default function ExecutionDashboard({ state, readiness, pending, canStart, canDryRun, canLive, liveConfirm, setLiveConfirm, onStart, onDryRun, onLive, onCancel, onRefresh, onRefreshReadiness }) {
  const execution = state?.execution;
  const progress = execution?.progress ?? STAGE_PROGRESS[execution?.stage] ?? 0;
  return (
    <section className="fs-panel ow-card" aria-labelledby="ow-execution-title">
      <div className="fs-panel-title"><div><span>Execution Dashboard</span><strong id="ow-execution-title">Simulation mode</strong></div></div>
      <ExecutionReadinessPanel readiness={readiness} />
      {!execution && <div className="ow-callout"><strong>Fake executor for MVP validation</strong><span>This will create simulated artifacts only. It will not run production OpenCode execution.</span></div>}
      {!execution && !canStart && <div className="ow-callout warning" role="alert"><strong>Approval required</strong><span>Approve the current brief and prepare the Alex to Codex handoff before starting execution.</span></div>}
      {execution && <>
        <div className="ow-status-grid"><div><span>Status</span><strong>{formatLabel(execution.status)}</strong></div><div><span>Stage</span><strong>{formatLabel(execution.stage)}</strong></div><div><span>Active agent</span><strong>{execution.active_agent || 'Queued'}</strong></div><div><span>Progress</span><strong>{progress}%</strong></div></div>
        <div className="ow-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow={progress}><span style={{ width: `${progress}%` }} /></div>
        <p className="ow-note" aria-live="polite">{execution.current_activity}</p>
        {execution.blockers?.length > 0 && <div className="ow-callout warning"><strong>Action required</strong>{execution.blockers.map(item => <p key={item.code}>{item.message} <b>{item.action}</b></p>)}</div>}
        <EventTimeline events={execution.events} />
      </>}
      {!execution && canLive && <div className="ow-callout warning"><label><input type="checkbox" checked={liveConfirm} onChange={event => setLiveConfirm(event.target.checked)} /> This will ask OpenCode to create files in the generated project workspace. Continue?</label></div>}
      <div className="ow-actions"><button type="button" className="fs-secondary" onClick={onRefreshReadiness} disabled={pending || !state?.order}>Refresh readiness</button><button type="button" className="fs-secondary" onClick={onRefresh} disabled={pending || !state?.order}>Refresh execution</button>{!execution && <button type="button" className="fs-primary" onClick={onStart} disabled={pending || !canStart}>{pending ? 'Starting...' : 'Run simulation'}</button>}{!execution && <button type="button" className="fs-secondary" onClick={onDryRun} disabled={pending || !canDryRun}>Prepare production dry-run</button>}{!execution && <button type="button" className="fs-danger-button" onClick={onLive} disabled={pending || !canLive || !liveConfirm}>Start live OpenCode execution</button>}{!canLive && <button type="button" className="fs-secondary" disabled>Live execution locked</button>}{execution && !isTerminalExecution(execution) && <button type="button" className="fs-danger-button" onClick={onCancel} disabled={pending}>Cancel execution</button>}</div>
    </section>
  );
}

export function EventTimeline({ events = [] }) {
  return <section className="ow-timeline"><h3>Recent activity</h3>{events.length ? events.slice(-8).map(event => <article key={event.id}><b>{event.agent || 'Studio'}</b><span>{formatLabel(event.stage)} - {event.message}</span></article>) : <p className="fs-empty">No execution events yet.</p>}</section>;
}
