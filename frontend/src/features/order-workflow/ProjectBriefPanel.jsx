import React, { useState } from 'react';

function ListSection({ title, items }) {
  return <section className="ow-brief-section"><h3>{title}</h3>{items?.length ? <ul>{items.map(item => <li key={item}>{item}</li>)}</ul> : <p className="fs-empty">None listed.</p>}</section>;
}

export default function ProjectBriefPanel({ state, pending, onGenerate, onApprove, onRevise, onBack }) {
  const brief = state?.brief;
  const [revisionText, setRevisionText] = useState('');
  if (!brief) {
    return <section className="fs-panel ow-card"><div className="fs-panel-title"><div><span>Project Brief</span><strong>Ready to generate</strong></div></div><p className="ow-note">Alex has enough information to create the structured brief.</p><div className="ow-actions"><button type="button" className="fs-secondary" onClick={onBack}>Back to questions</button><button type="button" className="fs-primary" onClick={onGenerate} disabled={pending}>Generate project brief</button></div></section>;
  }
  return (
    <section className="fs-panel ow-card" aria-labelledby="ow-brief-title">
      <div className="fs-panel-title"><div><span>Project Brief</span><strong id="ow-brief-title">Revision {brief.revision}</strong></div></div>
      <div className="ow-brief-grid">
        <section className="ow-brief-section wide"><h3>Goal</h3><p>{brief.goal}</p></section>
        <ListSection title="Target users" items={brief.target_users} />
        <ListSection title="Core features" items={brief.core_features} />
        <ListSection title="Non-goals" items={brief.non_goals} />
        <ListSection title="Assumptions" items={brief.assumptions} />
        <ListSection title="Technical constraints" items={brief.technical_constraints} />
        <ListSection title="UI requirements" items={brief.ui_requirements} />
        <section className="ow-brief-section"><h3>Recommended stack</h3><p>{brief.recommended_stack?.frontend} / {brief.recommended_stack?.backend} / {brief.recommended_stack?.storage}</p></section>
        <ListSection title="Acceptance criteria" items={brief.acceptance_criteria} />
        <section className="ow-brief-section"><h3>Elena design choice</h3><p>{String(brief.elena_design_choice).replaceAll('_', ' ')}</p>{brief.elena_design_concept && <p>Concept: {brief.elena_design_concept.visual_direction}</p>}</section>
      </div>
      <div className="ow-revision-box"><label>Request changes: add one requirement<input value={revisionText} onChange={event => setRevisionText(event.target.value)} placeholder="Example: Keyboard shortcut for push-to-talk" /></label><button type="button" className="fs-secondary" disabled={!revisionText.trim() || pending} onClick={() => { onRevise(revisionText); setRevisionText(''); }}>Request changes</button></div>
      {state?.handoff_ready && <div className="ow-callout success"><strong>Alex to Codex handoff is ready.</strong><span>Execution can begin after explicit approval.</span></div>}
      <div className="ow-actions"><button type="button" className="fs-secondary" onClick={onBack}>Back to answers</button><button type="button" className="fs-primary" onClick={() => onApprove(brief)} disabled={pending || state?.approval?.approved}>{state?.approval?.approved ? 'Brief approved' : 'Approve brief'}</button></div>
    </section>
  );
}
