import React, { useState } from 'react';
import { formatLabel } from './orderWorkflowState.js';
import { CodingWorkbench, EventTimeline } from './ExecutionDashboard.jsx';

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

function RevisionRequestForm({ pending, onSubmit }) {
  const [note, setNote] = useState('');
  const submit = event => {
    event.preventDefault();
    const trimmed = note.trim();
    if (!trimmed) return;
    onSubmit(trimmed);
  };
  return (
    <form className="ow-form ow-revision-form" onSubmit={submit}>
      <label htmlFor="ow-revision-note">Request a change to this delivered project</label>
      <textarea
        id="ow-revision-note"
        rows={3}
        value={note}
        onChange={event => setNote(event.target.value)}
        placeholder="e.g. Add a dark mode toggle to the settings screen"
        disabled={pending}
      />
      <div className="ow-actions">
        <button type="submit" className="fs-primary" disabled={pending || !note.trim()}>{pending ? 'Applying revision...' : 'Request change'}</button>
      </div>
    </form>
  );
}

function copyText(value) {
  if (!value || !navigator.clipboard) return;
  navigator.clipboard.writeText(value).catch(() => {});
}

export default function ExecutionResultPanel({ state, pending, onRetry, onRevise, onNewOrder, onBackToBrief, onOpenWorkspace }) {
  const execution = state?.execution;
  const result = execution?.result;
  const delivery = state?.delivery || {};
  const files = delivery.files || {};
  const canRetry = execution?.status === 'failed';
  const canRevise = execution?.status === 'succeeded' && Boolean(onRevise);
  const workspacePath = delivery.workspace_path;
  return (
    <section className="fs-panel ow-card" data-glass aria-labelledby="ow-result-title">
      <div className="fs-panel-title"><div><span>Result</span><strong id="ow-result-title">{formatLabel(execution?.status, 'No result yet')}</strong></div></div>
      {execution?.revised_from && (
        <div className="ow-callout" role="status">
          <strong>This is a revision</strong>
          <span>Built on top of the previously delivered project ({execution.revised_from}), not regenerated from scratch.</span>
        </div>
      )}
      {result?.rate_limit_message && !/session limit|europe\/berlin/i.test(result.rate_limit_message) && (
        <div className="ow-callout warning" role="alert">
          <strong>Rate limited</strong>
          <span>{result.rate_limit_message}</span>
          <p>Once the limit resets, use Retry below -- it reuses this same workspace and does not repeat clarification, brief, or design-preview.</p>
        </div>
      )}
      {result && <div className="ow-result-summary"><p>{result.summary}</p><div className="ow-status-grid"><div><span>Passed</span><strong>{result.test_summary?.passed ?? 0}</strong></div><div><span>Failed</span><strong>{result.test_summary?.failed ?? 0}</strong></div><div><span>Skipped</span><strong>{result.test_summary?.skipped ?? 0}</strong></div><div><span>Repair attempts</span><strong>{result.test_summary?.repair_attempts ?? 0}</strong></div></div></div>}
      {result?.usage && <section className="ow-usage"><h3>Coding agent usage (this execution)</h3><UsageDetails usage={result.usage} /></section>}
      {workspacePath && (
        <section className="ow-artifacts">
          <h3>Delivery folder</h3>
          <article>
            <strong>Project workspace</strong>
            <span><code>{workspacePath}</code></span>
            <div className="ow-actions">
              <button type="button" className="fs-secondary" onClick={() => copyText(workspacePath)}>Copy path</button>
              {onOpenWorkspace && <button type="button" className="fs-primary" onClick={onOpenWorkspace} disabled={pending}>Open folder</button>}
            </div>
          </article>
          <p className="ow-note">
            {files['delivery_report.md'] ? 'delivery_report.md is present. ' : 'delivery_report.md is missing. '}
            {files['delivery_screenshot.png'] ? 'delivery_screenshot.png is present. ' : 'delivery_screenshot.png is missing. '}
            {files['README.md'] ? 'README.md is present.' : 'README.md is missing.'}
          </p>
        </section>
      )}
      {execution?.artifacts?.length > 0 && <section className="ow-artifacts"><h3>Artifacts</h3>{execution.artifacts.map(item => <article key={item.id}><strong>{item.name}</strong><span>{item.summary}</span>{item.simulated && <b>Simulated artifact</b>}</article>)}</section>}
      {result?.warnings?.length > 0 && <div className="ow-callout warning"><strong>Warnings</strong>{result.warnings.map(item => <p key={item}>{item}</p>)}</div>}
      {(execution?.coding_transcript || execution?.coding_files?.length) ? <CodingWorkbench execution={execution} /> : null}
      <EventTimeline events={execution?.events || []} />
      {canRevise && <RevisionRequestForm pending={pending} onSubmit={onRevise} />}
      <div className="ow-actions">
        {canRetry && <button type="button" className="fs-primary" onClick={onRetry} disabled={pending}>{pending ? 'Retrying...' : 'Retry this workspace'}</button>}
        <button type="button" className="fs-secondary" onClick={onBackToBrief}>Revise brief</button>
        <button type="button" className="fs-primary" onClick={onNewOrder}>Create another order</button>
      </div>
    </section>
  );
}
