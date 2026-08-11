import React from 'react';
import { formatLabel } from './orderWorkflowState.js';
import { EventTimeline } from './ExecutionDashboard.jsx';

function UsageDetails({ usage }) {
  if (!usage) return null;
  return (
    <div className="ow-status-grid">
      <div><span>Cost</span><strong>${usage.total_cost_usd.toFixed(4)}</strong></div>
      <div><span>Input tokens</span><strong>{usage.input_tokens.toLocaleString()}</strong></div>
      <div><span>Output tokens</span><strong>{usage.output_tokens.toLocaleString()}</strong></div>
      <div><span>Cache read</span><strong>{usage.cache_read_input_tokens.toLocaleString()}</strong></div>
    </div>
  );
}

export default function ExecutionResultPanel({ state, pending, onRetry, onNewOrder, onBackToBrief }) {
  const execution = state?.execution;
  const result = execution?.result;
  const canRetry = execution?.status === 'failed';
  return (
    <section className="fs-panel ow-card" aria-labelledby="ow-result-title">
      <div className="fs-panel-title"><div><span>Result</span><strong id="ow-result-title">{formatLabel(execution?.status, 'No result yet')}</strong></div></div>
      {result?.rate_limit_message && (
        <div className="ow-callout warning" role="alert">
          <strong>Rate limited</strong>
          <span>{result.rate_limit_message}</span>
          <p>Once the limit resets, use Retry below -- it reuses this same workspace and does not repeat clarification, brief, or design-preview.</p>
        </div>
      )}
      {result && <div className="ow-result-summary"><p>{result.summary}</p><div className="ow-status-grid"><div><span>Passed</span><strong>{result.test_summary?.passed ?? 0}</strong></div><div><span>Failed</span><strong>{result.test_summary?.failed ?? 0}</strong></div><div><span>Skipped</span><strong>{result.test_summary?.skipped ?? 0}</strong></div><div><span>Repair attempts</span><strong>{result.test_summary?.repair_attempts ?? 0}</strong></div></div></div>}
      {result?.usage && <section className="ow-usage"><h3>Coding agent usage (this execution)</h3><UsageDetails usage={result.usage} /></section>}
      {execution?.artifacts?.length > 0 && <section className="ow-artifacts"><h3>Artifacts</h3>{execution.artifacts.map(item => <article key={item.id}><strong>{item.name}</strong><span>{item.summary}</span>{item.simulated && <b>Simulated artifact</b>}</article>)}</section>}
      {result?.warnings?.length > 0 && <div className="ow-callout warning"><strong>Warnings</strong>{result.warnings.map(item => <p key={item}>{item}</p>)}</div>}
      <EventTimeline events={execution?.events || []} />
      <div className="ow-actions">
        {canRetry && <button type="button" className="fs-primary" onClick={onRetry} disabled={pending}>{pending ? 'Retrying...' : 'Retry execution'}</button>}
        <button type="button" className="fs-secondary" onClick={onBackToBrief}>Revise brief</button>
        <button type="button" className="fs-primary" onClick={onNewOrder}>Create another order</button>
      </div>
    </section>
  );
}
