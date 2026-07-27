import React from 'react';
import { formatLabel } from './orderWorkflowState.js';
import { EventTimeline } from './ExecutionDashboard.jsx';

export default function ExecutionResultPanel({ state, onNewOrder, onBackToBrief }) {
  const execution = state?.execution;
  const result = execution?.result;
  return (
    <section className="fs-panel ow-card" aria-labelledby="ow-result-title">
      <div className="fs-panel-title"><div><span>Result</span><strong id="ow-result-title">{formatLabel(execution?.status, 'No result yet')}</strong></div></div>
      {result && <div className="ow-result-summary"><p>{result.summary}</p><div className="ow-status-grid"><div><span>Passed</span><strong>{result.test_summary?.passed ?? 0}</strong></div><div><span>Failed</span><strong>{result.test_summary?.failed ?? 0}</strong></div><div><span>Skipped</span><strong>{result.test_summary?.skipped ?? 0}</strong></div><div><span>Repair attempts</span><strong>{result.test_summary?.repair_attempts ?? 0}</strong></div></div></div>}
      {execution?.artifacts?.length > 0 && <section className="ow-artifacts"><h3>Artifacts</h3>{execution.artifacts.map(item => <article key={item.id}><strong>{item.name}</strong><span>{item.summary}</span>{item.simulated && <b>Simulated artifact</b>}</article>)}</section>}
      {result?.warnings?.length > 0 && <div className="ow-callout warning"><strong>Warnings</strong>{result.warnings.map(item => <p key={item}>{item}</p>)}</div>}
      <EventTimeline events={execution?.events || []} />
      <div className="ow-actions"><button type="button" className="fs-secondary" onClick={onBackToBrief}>Revise brief</button><button type="button" className="fs-primary" onClick={onNewOrder}>Create another order</button></div>
    </section>
  );
}
