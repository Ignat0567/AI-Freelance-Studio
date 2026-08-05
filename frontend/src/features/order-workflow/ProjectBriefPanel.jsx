import React, { useState } from 'react';

function ListSection({ title, items }) {
  return <section className="ow-brief-section"><h3>{title}</h3>{items?.length ? <ul>{items.map(item => <li key={item}>{item}</li>)}</ul> : <p className="fs-empty">None listed.</p>}</section>;
}

function DesignPreviewPanel({ preview, required, approved, pending, onGenerate, onApprove, onRevise }) {
  const [note, setNote] = useState('');
  if (!required) return null;
  if (!preview) {
    return <section className="ow-design-preview"><div><span className="fs-eyebrow">Elena Design Preview</span><h3>Preview not generated</h3><p>Generate an approximate visual direction before implementation starts.</p></div><button type="button" className="fs-secondary" disabled={pending} onClick={onGenerate}>Generate preview</button></section>;
  }
  return (
    <section className="ow-design-preview" aria-labelledby="ow-design-preview-title">
      <div className="ow-design-head"><div><span className="fs-eyebrow">Elena Design Preview</span><h3 id="ow-design-preview-title">{preview.concept_name}</h3><p>Layout type: <strong>{preview.layout_type}</strong> · {preview.visual_direction}</p></div><strong>{preview.approved ? 'Approved' : 'Needs review'}</strong></div>
      <div className="ow-preview-screens">{preview.screens?.map(screen => <article key={screen.screen_id}><h4>{screen.name}</h4><p>{screen.purpose}</p><div className="ow-region-list">{screen.layout?.regions?.map(region => <span key={region}>{region}</span>)}</div><ListSection title="Components" items={screen.components} /><ListSection title="States" items={screen.states} /></article>)}</div>
      <div className="ow-brief-grid"><ListSection title="User flows" items={preview.user_flows} /><ListSection title="Empty states" items={preview.empty_states} /><ListSection title="Error states" items={preview.error_states} /><ListSection title="Accessibility notes" items={preview.accessibility_notes} /><ListSection title="Implementation notes for Codex" items={preview.implementation_notes} /><ListSection title="Revision notes" items={preview.revision_notes} /></div>
      <div className="ow-revision-box"><label>Request revision note<input value={note} onChange={event => setNote(event.target.value)} placeholder="Example: Make source evidence more prominent" /></label><button type="button" className="fs-secondary" disabled={!note.trim() || pending} onClick={() => { onRevise(note); setNote(''); }}>Regenerate preview</button></div>
      <div className="ow-actions"><button type="button" className="fs-primary" disabled={pending || !approved || preview.approved} onClick={() => onApprove(preview)}>{preview.approved ? 'Preview approved' : 'Approve preview'}</button></div>
    </section>
  );
}

function downloadTextFile(filename, text) {
  const blob = new Blob([text], { type: 'text/markdown' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function ClientProposalSection({ proposal, pending, error, onGenerate }) {
  return (
    <section className="ow-brief-section wide">
      <h3>Client Proposal</h3>
      <div className="ow-actions"><button type="button" className="fs-secondary" onClick={onGenerate} disabled={pending}>{pending ? 'Generating proposal...' : 'Generate proposal'}</button></div>
      {error && <p className="ow-note">{error}</p>}
      {proposal && (
        <>
          <pre className="ow-proposal-text">{proposal.proposal_markdown}</pre>
          <div className="ow-actions"><button type="button" className="fs-secondary" onClick={() => downloadTextFile('proposal.md', proposal.proposal_markdown)}>Download as .md</button></div>
        </>
      )}
    </section>
  );
}

export default function ProjectBriefPanel({ state, pending, onGenerate, onApprove, onRevise, onBack, onGeneratePreview, onApprovePreview, onRevisePreview, proposal, proposalPending, proposalError, onGenerateProposal }) {
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
      <DesignPreviewPanel preview={state?.design_preview} required={state?.design_preview_required} approved={state?.approval?.approved} pending={pending} onGenerate={onGeneratePreview} onApprove={onApprovePreview} onRevise={onRevisePreview} />
      <ClientProposalSection proposal={proposal} pending={proposalPending} error={proposalError} onGenerate={onGenerateProposal} />
      {state?.handoff_ready && <div className="ow-callout success"><strong>Alex to Codex handoff is ready.</strong><span>Execution can begin after explicit approval and Elena preview approval.</span></div>}
      <div className="ow-actions"><button type="button" className="fs-secondary" onClick={onBack}>Back to answers</button><button type="button" className="fs-primary" onClick={() => onApprove(brief)} disabled={pending || state?.approval?.approved}>{state?.approval?.approved ? 'Brief approved' : 'Approve brief'}</button></div>
    </section>
  );
}
