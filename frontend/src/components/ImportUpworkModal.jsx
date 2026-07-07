import React, { useState } from 'react';

export default function ImportUpworkModal({ activePort, onClose, addLog }) {
  const [step, setStep] = useState('input'); // input | proposal | refine | done
  const [jobUrl, setJobUrl] = useState('');
  const [jobDesc, setJobDesc] = useState('');
  const [generating, setGenerating] = useState(false);
  const [proposal, setProposal] = useState(null);
  const [clientAnswers, setClientAnswers] = useState('');
  const [refining, setRefining] = useState(false);
  const [refinedSpec, setRefinedSpec] = useState(null);
  const [copied, setCopied] = useState(false);

  const handleGenerate = async () => {
    const desc = jobDesc.trim() || jobUrl.trim();
    if (!desc) { addLog('[Upwork]: Paste the job description first.'); return; }
    setGenerating(true);
    try {
      const r = await fetch(`http://localhost:${activePort}/api/proposals/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_description: desc }),
      });
      const data = await r.json();
      if (data.error) { addLog(`[Upwork]: ${data.error}`); return; }
      setProposal(data);
      setStep('proposal');
      addLog('[Upwork]: Proposal generated successfully.');
    } catch (e) { addLog(`[Upwork]: Error — ${e.message}`); }
    finally { setGenerating(false); }
  };

  const handleRefine = async () => {
    if (!clientAnswers.trim()) { addLog('[Upwork]: Paste the client response first.'); return; }
    setRefining(true);
    try {
      const r = await fetch(`http://localhost:${activePort}/api/proposals/refine`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          original_job: jobDesc.trim() || jobUrl.trim(),
          client_answers: clientAnswers.trim(),
        }),
      });
      const data = await r.json();
      if (data.error) { addLog(`[Upwork]: ${data.error}`); return; }
      setRefinedSpec(data);
      setStep('done');
      addLog('[Upwork]: Spec refined. Ready for development.');
    } catch (e) { addLog(`[Upwork]: Error — ${e.message}`); }
    finally { setRefining(false); }
  };

  const copyToClipboard = (text) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const createProjectFromSpec = () => {
    if (!refinedSpec?.refined_spec) return;
    onClose();
    // Signal parent to create a project with this spec
    window.dispatchEvent(new CustomEvent('create-from-spec', {
      detail: { spec: refinedSpec.refined_spec },
    }));
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ backgroundColor: 'rgba(0,0,0,0.6)' }}>
      <div className="w-full max-w-2xl max-h-[85vh] flex flex-col rounded-xl overflow-hidden" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
        <div className="flex justify-between items-center px-5 py-3" style={{ borderBottom: '1px solid var(--border)' }}>
          <h2 className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>
            {step === 'input' ? '📥 Import from Upwork' :
             step === 'proposal' ? '💼 Proposal Ready' :
             step === 'refine' ? '📝 Client Response' :
             '✅ Spec Complete'}
          </h2>
          <button onClick={onClose} className="text-lg leading-none" style={{ color: 'var(--text-muted)' }}>✕</button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-4">

          {/* Step 1: Input */}
          {step === 'input' && (
            <>
              <p className="text-xs" style={{ color: 'var(--text-dim)' }}>
                Paste the Upwork job URL or the full job description below. Goldie (Sales Agent) will analyze it and generate a tailored proposal + clarifying questions.
              </p>
              <input
                type="text"
                placeholder="Upwork job URL (optional)"
                value={jobUrl}
                onChange={e => setJobUrl(e.target.value)}
                className="w-full bg-[var(--bg-secondary)] border border-[var(--border)] rounded-lg px-3 py-2 text-xs font-mono focus:outline-none"
                style={{ color: 'var(--text-primary)' }}
              />
              <textarea
                placeholder="Paste the full job description here..."
                value={jobDesc}
                onChange={e => setJobDesc(e.target.value)}
                rows={12}
                className="w-full bg-[var(--bg-secondary)] border border-[var(--border)] rounded-lg px-3 py-2 text-xs font-mono focus:outline-none resize-none"
                style={{ color: 'var(--text-primary)' }}
              />
              <button
                onClick={handleGenerate}
                disabled={generating}
                className="w-full py-3 rounded-lg text-sm font-bold transition-all"
                style={{ backgroundColor: generating ? 'var(--text-muted)' : 'var(--accent)', color: '#fff', opacity: generating ? 0.6 : 1 }}
              >
                {generating ? '🔄 Goldie is analyzing...' : '🚀 Generate Proposal'}
              </button>
            </>
          )}

          {/* Step 2: Proposal */}
          {step === 'proposal' && proposal && (
            <>
              <div className="p-3 rounded-lg" style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}>
                <div className="text-xs font-bold mb-2" style={{ color: 'var(--text-primary)' }}>📋 Project Summary</div>
                <p className="text-xs" style={{ color: 'var(--text-dim)' }}>{proposal.summary}</p>
              </div>

              <div className="p-3 rounded-lg" style={{ backgroundColor: 'color-mix(in srgb, var(--warning) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--warning) 30%, transparent)' }}>
                <div className="text-xs font-bold mb-2" style={{ color: 'var(--warning)' }}>❓ Clarifying Questions for Client</div>
                {(proposal.clarifying_questions || []).map((q, i) => (
                  <div key={i} className="flex items-start gap-2 mb-1 text-xs" style={{ color: 'var(--text-dim)' }}>
                    <span>{i + 1}.</span>
                    <span>{q}</span>
                    <button onClick={() => copyToClipboard(q)} className="text-[9px] shrink-0 px-1 py-0.5 rounded" style={{ backgroundColor: 'var(--bg-secondary)', color: 'var(--text-muted)' }}>📋</button>
                  </div>
                ))}
              </div>

              <div className="p-3 rounded-lg" style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}>
                <div className="flex justify-between items-center mb-2">
                  <span className="text-xs font-bold" style={{ color: 'var(--text-primary)' }}>📝 Draft Proposal</span>
                  <button onClick={() => copyToClipboard(proposal.proposal)} className="text-[10px] px-2 py-1 rounded" style={{ backgroundColor: 'var(--bg-secondary)', color: 'var(--text-muted)' }}>
                    {copied ? '✅ Copied!' : 'Copy'}
                  </button>
                </div>
                <p className="text-xs whitespace-pre-wrap" style={{ color: 'var(--text-dim)' }}>{proposal.proposal}</p>
              </div>

              <div className="flex gap-2 text-xs" style={{ color: 'var(--text-dim)' }}>
                <span>💡 Tech: {(proposal.suggested_tech_stack || []).join(', ')}</span>
                <span>⏱ {proposal.estimated_timeline}</span>
                <span>💰 {proposal.estimated_budget}</span>
              </div>

              <div className="flex gap-3 pt-2">
                <button onClick={() => setStep('refine')} className="flex-1 py-2.5 rounded-lg text-xs font-bold" style={{ backgroundColor: 'var(--accent)', color: '#fff' }}>
                  📨 Client Replied — Enter Response
                </button>
                <button onClick={() => setStep('input')} className="px-4 py-2.5 rounded-lg text-xs" style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                  Edit
                </button>
              </div>
            </>
          )}

          {/* Step 3: Client Response */}
          {step === 'refine' && (
            <>
              <p className="text-xs" style={{ color: 'var(--text-dim)' }}>
                Paste the client's response here. Maya (Analyst) will extract requirements and generate a structured spec.
              </p>
              <div className="p-3 rounded-lg text-xs" style={{ backgroundColor: 'color-mix(in srgb, var(--accent) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--accent) 30%, transparent)', color: 'var(--text-dim)' }}>
                <strong>Original Job:</strong>
                <p className="mt-1 text-[10px]">{(jobDesc || jobUrl).substring(0, 300)}...</p>
              </div>
              <textarea
                placeholder="Paste the client's full response (answers to your questions, additional requirements, etc.)"
                value={clientAnswers}
                onChange={e => setClientAnswers(e.target.value)}
                rows={10}
                className="w-full bg-[var(--bg-secondary)] border border-[var(--border)] rounded-lg px-3 py-2 text-xs font-mono focus:outline-none resize-none"
                style={{ color: 'var(--text-primary)' }}
              />
              <button
                onClick={handleRefine}
                disabled={refining}
                className="w-full py-3 rounded-lg text-sm font-bold transition-all"
                style={{ backgroundColor: refining ? 'var(--text-muted)' : 'var(--accent)', color: '#fff', opacity: refining ? 0.6 : 1 }}
              >
                {refining ? '🔄 Maya is analyzing...' : '📐 Generate Structured Spec'}
              </button>
            </>
          )}

          {/* Step 4: Done */}
          {step === 'done' && refinedSpec && (
            <>
              <div className="p-3 rounded-lg" style={{ backgroundColor: refinedSpec.ready_for_development ? 'color-mix(in srgb, var(--success) 15%, transparent)' : 'color-mix(in srgb, var(--warning) 15%, transparent)', border: '1px solid color-mix(in srgb, var(--success) 30%, transparent)' }}>
                <div className="flex items-center gap-2 mb-2">
                  <span>{refinedSpec.ready_for_development ? '✅' : '⚠️'}</span>
                  <span className="text-xs font-bold" style={{ color: 'var(--text-primary)' }}>
                    {refinedSpec.ready_for_development ? 'Ready for Development!' : 'Needs More Info'}
                  </span>
                </div>
              </div>

              <div className="p-3 rounded-lg" style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}>
                <div className="flex justify-between items-center mb-2">
                  <span className="text-xs font-bold" style={{ color: 'var(--text-primary)' }}>📄 Refined Specification</span>
                  <button onClick={() => copyToClipboard(refinedSpec.refined_spec)} className="text-[10px] px-2 py-1 rounded" style={{ backgroundColor: 'var(--bg-secondary)', color: 'var(--text-muted)' }}>
                    {copied ? '✅ Copied!' : 'Copy'}
                  </button>
                </div>
                <pre className="text-xs whitespace-pre-wrap font-mono" style={{ color: 'var(--text-dim)', maxHeight: '300px', overflow: 'auto' }}>{refinedSpec.refined_spec}</pre>
              </div>

              {refinedSpec.remaining_questions && refinedSpec.remaining_questions.length > 0 && (
                <div className="p-3 rounded-lg" style={{ backgroundColor: 'color-mix(in srgb, var(--warning) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--warning) 30%, transparent)' }}>
                  <div className="text-xs font-bold mb-2" style={{ color: 'var(--warning)' }}>❓ Remaining Questions</div>
                  {refinedSpec.remaining_questions.map((q, i) => (
                    <div key={i} className="flex items-start gap-2 mb-1 text-xs" style={{ color: 'var(--text-dim)' }}>
                      <span>{i + 1}.</span>
                      <span>{q}</span>
                    </div>
                  ))}
                </div>
              )}

              <div className="flex gap-3 pt-2">
                {refinedSpec.ready_for_development && (
                  <button onClick={createProjectFromSpec} className="flex-1 py-2.5 rounded-lg text-xs font-bold" style={{ backgroundColor: 'var(--success)', color: '#fff' }}>
                    🚀 Create Project & Start Development
                  </button>
                )}
                {refinedSpec.remaining_questions?.length > 0 && (
                  <button onClick={() => setStep('refine')} className="flex-1 py-2.5 rounded-lg text-xs font-bold" style={{ backgroundColor: 'var(--accent)', color: '#fff' }}>
                    📨 Add More Client Answers
                  </button>
                )}
              </div>
            </>
          )}

        </div>

        <div className="px-5 py-3 flex justify-between items-center" style={{ borderTop: '1px solid var(--border)', backgroundColor: 'var(--bg-secondary)' }}>
          <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
            {step === 'input' ? 'Step 1/4: Paste job → Generate' :
             step === 'proposal' ? 'Step 2/4: Send proposal → Wait for reply' :
             step === 'refine' ? 'Step 3/4: Paste reply → Refine spec' :
             'Step 4/4: Done! Build the project'}
          </span>
          <button onClick={onClose} className="px-4 py-1.5 rounded-lg text-xs font-medium" style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
