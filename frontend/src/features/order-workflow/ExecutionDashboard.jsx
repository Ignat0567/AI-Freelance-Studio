import React from 'react';
import { STAGE_PROGRESS, formatLabel, isTerminalExecution } from './orderWorkflowState.js';

export default function ExecutionDashboard({ state, pending, canStart, onStart, onCancel, onRefresh }) {
  const execution = state?.execution;
  const progress = execution?.progress ?? STAGE_PROGRESS[execution?.stage] ?? 0;
  return (
    <section className="fs-panel ow-card" aria-labelledby="ow-execution-title">
      <div className="fs-panel-title"><div><span>Execution Dashboard</span><strong id="ow-execution-title">Simulation mode</strong></div></div>
      {!execution && <div className="ow-callout"><strong>Fake executor for MVP validation</strong><span>This will create simulated artifacts only. It will not run production OpenCode execution.</span></div>}
      {!execution && !canStart && <div className="ow-callout warning" role="alert"><strong>Approval required</strong><span>Approve the current brief and prepare the Alex to Codex handoff before starting execution.</span></div>}
      {execution && <>
        <div className="ow-status-grid"><div><span>Status</span><strong>{formatLabel(execution.status)}</strong></div><div><span>Stage</span><strong>{formatLabel(execution.stage)}</strong></div><div><span>Active agent</span><strong>{execution.active_agent || 'Queued'}</strong></div><div><span>Progress</span><strong>{progress}%</strong></div></div>
        <div className="ow-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow={progress}><span style={{ width: `${progress}%` }} /></div>
        <p className="ow-note" aria-live="polite">{execution.current_activity}</p>
        {execution.blockers?.length > 0 && <div className="ow-callout warning"><strong>Action required</strong>{execution.blockers.map(item => <p key={item.code}>{item.message} <b>{item.action}</b></p>)}</div>}
        <EventTimeline events={execution.events} />
      </>}
      <div className="ow-actions"><button type="button" className="fs-secondary" onClick={onRefresh} disabled={pending || !state?.order}>Refresh</button>{!execution && <button type="button" className="fs-primary" onClick={onStart} disabled={pending || !canStart}>{pending ? 'Starting...' : 'Start fake execution'}</button>}{execution && !isTerminalExecution(execution) && <button type="button" className="fs-danger-button" onClick={onCancel} disabled={pending}>Cancel execution</button>}</div>
    </section>
  );
}

export function EventTimeline({ events = [] }) {
  return <section className="ow-timeline"><h3>Recent activity</h3>{events.length ? events.slice(-8).map(event => <article key={event.id}><b>{event.agent || 'Studio'}</b><span>{formatLabel(event.stage)} - {event.message}</span></article>) : <p className="fs-empty">No execution events yet.</p>}</section>;
}
