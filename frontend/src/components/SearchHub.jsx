import React from 'react';

export default function SearchHub({
    searchQuery, setSearchQuery, suggestions, setSuggestions,
    searchResults, handleSearchTrigger, handleClaimJob
}) {
    const platformSearchUrls = {
    'Upwork': (t) => `https://www.upwork.com/search/jobs/?q=${encodeURIComponent(t)}`,
    'Freelancer': (t) => `https://www.freelancer.com/jobs/?keyword=${encodeURIComponent(t)}`,
    'Habr Freelance': (t) => `https://freelance.habr.com/tasks?q=${encodeURIComponent(t)}`,
  };

  return (
        <section className="flex-[1.1] flex flex-col bg-slate-900 rounded-xl border border-slate-800 p-5 shadow-lg overflow-hidden">
            <h2 className="text-sm font-bold uppercase tracking-wider text-slate-400 mb-4">Command Center: Find Orders</h2>

            {/* SEARCH BOX CONTROLS */}
            <div className="relative mb-6">
                <div className="flex space-x-2">
                    <input
                        type="text"
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && handleSearchTrigger()}
                        placeholder="Type 'up' for Upwork or query keywords..."
                        className="flex-1 bg-slate-950 border border-slate-800 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-sky-500 transition-colors placeholder-slate-600"
                    />
                    <button
                        onClick={handleSearchTrigger}
                        className="bg-sky-600 hover:bg-sky-500 text-white font-medium px-5 py-2.5 rounded-lg text-sm transition-all shadow-md active:scale-95"
                    >
                        Search
                    </button>
                </div>

                {/* COMPONENT AUTOCOMPLETE POPUPS */}
                {suggestions.length > 0 && (
                    <div className="absolute top-full left-0 right-0 mt-1 bg-slate-950 border border-slate-800 rounded-lg shadow-2xl z-50 overflow-hidden">
                        {suggestions.map((p) => (
                            <button
                                key={p.code}
                                onClick={() => {
                                    setSearchQuery(p.code + ' ');
                                    setSuggestions([]);
                                }}
                                className="w-full text-left px-4 py-2 text-xs hover:bg-slate-900 text-sky-400 font-medium transition-colors border-b border-slate-900/50 last:border-none"
                            >
                                ⚡ Filter by platform: <span className="text-slate-200 font-bold ml-1">{p.name}</span>
                            </button>
                        ))}
                    </div>
                )}
            </div>

            {/* RENDER DYNAMIC CARD LOOPS */}
            <div className="flex-1 overflow-y-auto space-y-4 pr-1">
                {searchResults.length === 0 ? (
                    <div className="h-full flex flex-col items-center justify-center text-center p-6 border border-dashed border-slate-800 rounded-xl">
                        <p className="text-xs text-slate-600 uppercase font-semibold tracking-wider">No Active Feeds Loaded</p>
                        <p className="text-[11px] text-slate-700 mt-1">Execute a search to pull active tasks on-demand.</p>
                    </div>
                ) : (
                    searchResults.map((job, idx) => (
                        <div key={idx} className="bg-slate-950 border border-slate-800/80 rounded-xl p-4 flex flex-col hover:border-slate-700 transition-colors shadow-sm">
                            <div className="flex justify-between items-start mb-2">
                                <span className="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-slate-900 border border-slate-800 text-sky-400 flex items-center gap-1">
                                    {job.platform}
                                    {job.mock && <span className="text-[8px] opacity-50 font-normal">(demo)</span>}
                                </span>
                                <span className="text-xs font-bold text-emerald-400 font-mono">{job.budget}</span>
                            </div>
                            <h3 className="text-xs font-bold text-slate-200 mb-1 line-clamp-1">{job.title}</h3>
                            <p className="text-[11px] text-slate-500 mb-4 line-clamp-2 leading-relaxed">{job.description}</p>

                            <div className="flex space-x-2 mt-auto">
                                <button
                                    onClick={() => {
                                        const urlFn = platformSearchUrls[job.platform];
                                        const hasRealUrl = job.url && !job.url.endsWith('/jobs') && !job.url.endsWith('/tasks');
                                        window.open(hasRealUrl ? job.url : (urlFn ? urlFn(job.title) : job.url), '_blank');
                                    }}
                                    className="flex-1 text-center bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-300 font-medium py-1.5 rounded-md text-xs transition-colors"
                                >
                                    View Source
                                </button>
                                <button
                                    onClick={() => handleClaimJob(job)}
                                    className="flex-1 bg-indigo-600 hover:bg-indigo-500 text-white font-medium py-1.5 rounded-md text-xs transition-colors active:scale-98 shadow-sm"
                                >
                                    Claim Order
                                </button>
                            </div>
                        </div>
                    ))
                )}
            </div>
        </section>
    );
}