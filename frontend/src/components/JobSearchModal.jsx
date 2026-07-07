import React, { useState } from 'react';

export default function JobSearchModal({ activePort, onClose, addLog }) {
  const [tab, setTab] = useState('search'); // search | import
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const [selectedPlatform, setSelectedPlatform] = useState('');

  const [jobDesc, setJobDesc] = useState('');
  const [generating, setGenerating] = useState(false);
  const [proposal, setProposal] = useState(null);
  const [step, setStep] = useState('input');
  const [clientAnswers, setClientAnswers] = useState('');
  const [refinedSpec, setRefinedSpec] = useState(null);

  const platformSearchUrls = {
    'Upwork': (t) => `https://www.upwork.com/search/jobs/?q=${encodeURIComponent(t)}`,
    'Freelancer': (t) => `https://www.freelancer.com/jobs/?keyword=${encodeURIComponent(t)}`,
    'Habr Freelance': (t) => `https://freelance.habr.com/tasks?q=${encodeURIComponent(t)}`,
  };

  const platforms = [
    { code: '', name: 'All Platforms' },
    { code: 'upwork', name: 'Upwork' },
    { code: 'freelancer', name: 'Freelancer' },
    { code: 'habr', name: 'Habr Freelance' },
  ];

  const handleSearch = async () => {
    const q = (selectedPlatform ? selectedPlatform + ' ' : '') + searchQuery;
    if (!q.trim()) return;
    setSearching(true);
    try {
      const r = await fetch(`http://localhost:${activePort}/api/jobs/search?q=${encodeURIComponent(q)}`);
      const data = await r.json();
      setSearchResults(data.results || []);
      addLog(`[Jobs]: Found ${(data.results || []).length} jobs.`);
    } catch (e) { addLog(`[Jobs]: Search error — ${e.message}`); }
    finally { setSearching(false); }
  };

  const handleClaim = async (job) => {
    try {
      const r = await fetch(`http://localhost:${activePort}/api/projects/claim`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: job.title, platform: job.platform, description: job.description, budget: job.budget, url: job.url }),
      });
      const data = await r.json();
      addLog(`[Jobs]: Project "${data.title || job.title}" claimed. Starting pipeline...`);
      window.dispatchEvent(new CustomEvent('project-created', { detail: data }));
      onClose();
    } catch (e) { addLog(`[Jobs]: Claim error — ${e.message}`); }
  };

  const handleGenerateProposal = async () => {
    const desc = jobDesc.trim();
    if (!desc) { addLog('[Jobs]: Paste a job description first.'); return; }
    setGenerating(true);
    try {
      const r = await fetch(`http://localhost:${activePort}/api/proposals/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_description: desc }),
      });
      const data = await r.json();
      if (data.error) { addLog(`[Jobs]: ${data.error}`); return; }
      setProposal(data);
      setStep('proposal');
      addLog('[Jobs]: Proposal generated.');
    } catch (e) { addLog(`[Jobs]: Error — ${e.message}`); }
    finally { setGenerating(false); }
  };

  const handleRefine = async () => {
    if (!clientAnswers.trim()) return;
    try {
      const r = await fetch(`http://localhost:${activePort}/api/proposals/refine`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          original_job: jobDesc,
          client_answers: clientAnswers,
        }),
      });
      const data = await r.json();
      setRefinedSpec(data);
      setStep('done');
      addLog('[Jobs]: Spec refined.');
    } catch (e) { addLog(`[Jobs]: Refine error — ${e.message}`); }
  };

  const handleCreateFromSpec = () => {
    window.dispatchEvent(new CustomEvent('create-from-spec', { detail: { spec: refinedSpec || proposal } }));
    onClose();
  };

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div className="bg-slate-900 rounded-2xl border border-slate-700 w-full max-w-2xl max-h-[85vh] flex flex-col shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-800">
          <h2 className="text-base font-bold text-slate-200">Find Work</h2>
          <div className="flex items-center gap-2">
            <button onClick={() => setTab('search')} className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${tab === 'search' ? 'bg-sky-600 text-white' : 'bg-slate-800 text-slate-400 hover:text-slate-200'}`}>Search Platforms</button>
            <button onClick={() => setTab('import')} className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${tab === 'import' ? 'bg-emerald-600 text-white' : 'bg-slate-800 text-slate-400 hover:text-slate-200'}`}>Import from URL</button>
            <button onClick={onClose} className="ml-2 text-slate-500 hover:text-slate-300 text-lg">&times;</button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-6">
          {tab === 'search' ? (
            <div className="space-y-4">
              <div className="flex gap-2 flex-wrap">
                {platforms.map((p) => (
                  <button key={p.code} onClick={() => setSelectedPlatform(p.code)}
                    className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all border ${selectedPlatform === p.code ? 'bg-sky-600 border-sky-500 text-white' : 'bg-slate-800 border-slate-700 text-slate-400 hover:border-slate-600'}`}>
                    {p.name}
                  </button>
                ))}
              </div>
              <div className="flex gap-2">
                <input value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                  placeholder="Search for jobs..." className="flex-1 bg-slate-950 border border-slate-800 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-sky-500 transition-colors placeholder-slate-600" />
                <button onClick={handleSearch} disabled={searching}
                  className="bg-sky-600 hover:bg-sky-500 disabled:opacity-50 text-white font-medium px-5 py-2.5 rounded-lg text-sm transition-all">
                  {searching ? '...' : 'Search'}
                </button>
              </div>
              <div className="space-y-3">
                {searchResults.length === 0 ? (
                  <div className="text-center py-8 text-slate-600 text-xs">No results. Try searching above.</div>
                ) : searchResults.map((job, i) => (
                  <div key={i} className="bg-slate-950 border border-slate-800/80 rounded-xl p-4 hover:border-slate-700 transition-colors">
                    <div className="flex justify-between items-start mb-2">
                      <span className="px-2 py-0.5 rounded text-[10px] font-bold uppercase bg-slate-900 border border-slate-800 text-sky-400 flex items-center gap-1">{job.platform}{job.mock && <span className="text-[8px] opacity-50 font-normal">(demo)</span>}</span>
                      <span className="text-xs font-bold text-emerald-400 font-mono">{job.budget}</span>
                    </div>
                    <h3 className="text-xs font-bold text-slate-200 mb-1">{job.title}</h3>
                    <p className="text-[11px] text-slate-500 mb-3 line-clamp-2">{job.description}</p>
                    <div className="flex gap-2">
                      <button onClick={() => {
                        const urlFn = platformSearchUrls[job.platform];
                        const hasRealUrl = job.url && !job.url.endsWith('/jobs') && !job.url.endsWith('/tasks');
                        window.open(hasRealUrl ? job.url : (urlFn ? urlFn(job.title) : job.url), '_blank');
                      }} className="flex-1 text-center bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-300 py-1.5 rounded-md text-xs">View</button>
                      <button onClick={() => handleClaim(job)} className="flex-1 bg-indigo-600 hover:bg-indigo-500 text-white py-1.5 rounded-md text-xs active:scale-98">Claim</button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="space-y-4">
              {step === 'input' && (
                <>
                  <p className="text-xs text-slate-400">Paste a job description or URL from any freelance platform:</p>
                  <textarea value={jobDesc} onChange={(e) => setJobDesc(e.target.value)} rows={8}
                    placeholder="Paste job description here..."
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg px-4 py-3 text-sm focus:outline-none focus:border-sky-500 transition-colors placeholder-slate-600 resize-none" />
                  <button onClick={handleGenerateProposal} disabled={generating || !jobDesc.trim()}
                    className="w-full bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white font-medium py-2.5 rounded-lg text-sm transition-all">
                    {generating ? 'Generating proposal...' : 'Generate Proposal'}
                  </button>
                </>
              )}
              {step === 'proposal' && proposal && (
                <div className="space-y-4">
                  <div className="bg-slate-950 border border-slate-800 rounded-xl p-4 space-y-2">
                    <h3 className="text-sm font-bold text-slate-200">Generated Proposal</h3>
                    <p className="text-xs text-slate-400 whitespace-pre-wrap">{proposal.proposal}</p>
                    {(proposal.clarifying_questions || proposal.questions || []).length > 0 && (
                      <div className="mt-3"><p className="text-xs font-semibold text-sky-400 mb-1">Clarifying Questions:</p>
                        {(proposal.clarifying_questions || proposal.questions || []).map((q, i) => <p key={i} className="text-[11px] text-slate-500">- {q}</p>)}</div>
                    )}
                    {proposal.timeline && <p className="text-xs text-emerald-400">Timeline: {proposal.timeline}</p>}
                    {proposal.budget && <p className="text-xs text-emerald-400">Budget: {proposal.budget}</p>}
                  </div>
                  <textarea value={clientAnswers} onChange={(e) => setClientAnswers(e.target.value)} rows={4}
                    placeholder="Paste client's answers to your questions..."
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg px-4 py-3 text-sm focus:outline-none focus:border-sky-500 transition-colors placeholder-slate-600 resize-none" />
                  <div className="flex gap-2">
                    <button onClick={() => setStep('input')} className="flex-1 bg-slate-800 hover:bg-slate-700 text-slate-300 py-2 rounded-lg text-xs">Back</button>
                    <button onClick={handleRefine} disabled={!clientAnswers.trim()} className="flex-1 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white py-2 rounded-lg text-xs">Refine Spec</button>
                  </div>
                </div>
              )}
              {step === 'done' && (
                <div className="space-y-4">
                  <div className="bg-slate-950 border border-slate-800 rounded-xl p-4">
                    <h3 className="text-sm font-bold text-slate-200 mb-2">Final Specification</h3>
                    <pre className="text-xs text-slate-400 whitespace-pre-wrap max-h-60 overflow-y-auto">{JSON.stringify(refinedSpec || proposal, null, 2)}</pre>
                  </div>
                  <div className="flex gap-2">
                    <button onClick={() => setStep('proposal')} className="flex-1 bg-slate-800 hover:bg-slate-700 text-slate-300 py-2 rounded-lg text-xs">Back</button>
                    <button onClick={handleCreateFromSpec} className="flex-1 bg-indigo-600 hover:bg-indigo-500 text-white py-2 rounded-lg text-xs active:scale-98">Create Project &amp; Start</button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
