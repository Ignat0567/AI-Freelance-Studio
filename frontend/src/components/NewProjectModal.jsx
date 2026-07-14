import React, { useState } from 'react';

export default function NewProjectModal({ onCreate, onClose }) {
    const [title, setTitle] = useState('');
    const [desc, setDesc] = useState('');
    const [mode, setMode] = useState('mvp');

    const handleSubmit = (e) => {
        e.preventDefault();
        if (!title.trim() || !desc.trim()) return;
        onCreate(title, desc, mode);
    };

    return (
        <div className="fixed inset-0 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4 z-50 animate-fade-in">
            <div className="bg-slate-900 border border-slate-800 rounded-xl w-full max-w-md overflow-hidden shadow-2xl">

                {/* MODAL HEADER */}
                <div className="p-4 border-b border-slate-800 flex justify-between items-center bg-slate-950/40">
                    <h3 className="text-xs font-bold uppercase tracking-widest text-indigo-400">📝 Initialize New Manual Project</h3>
                    <button onClick={onClose} className="text-slate-500 hover:text-slate-200 font-mono text-sm">✕</button>
                </div>

                {/* INPUT FORM */}
                <form onSubmit={handleSubmit} className="p-5 space-y-4">
                    <div>
                        <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1">Project Name</label>
                        <input
                            type="text"
                            required
                            value={title}
                            onChange={(e) => setTitle(e.target.value)}
                            placeholder="e.g., Weather Telegram Bot"
                            className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs focus:outline-none focus:border-indigo-500 text-slate-300 placeholder-slate-700"
                        />
                    </div>

                    <div>
                        <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1">Initial Requirements / Description</label>
                        <textarea
                            required
                            rows={4}
                            value={desc}
                            onChange={(e) => setDesc(e.target.value)}
                            placeholder="Describe what the application should do in detail..."
                            className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs focus:outline-none focus:border-indigo-500 text-slate-300 placeholder-slate-700 resize-none"
                        />
                    </div>

                    <div>
                        <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1">Project Mode</label>
                        <select
                            value={mode}
                            onChange={(e) => setMode(e.target.value)}
                            className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs focus:outline-none focus:border-indigo-500 text-slate-300"
                        >
                            <option value="prototype">Prototype - fast validation with local/mocked integrations</option>
                            <option value="manual">Manual - continue while selected setup/verification is manual</option>
                            <option value="mvp">MVP - usable minimum product with verified core flows</option>
                        </select>
                        <p className="mt-1 text-[10px] text-slate-500">Default is MVP. The selected mode becomes part of the project contract.</p>
                    </div>

                    {/* FORM ACTIONS */}
                    <div className="flex justify-end space-x-2 pt-2 border-t border-slate-800/60">
                        <button
                            type="button"
                            onClick={onClose}
                            className="bg-slate-800 hover:bg-slate-700 text-slate-300 px-4 py-2 rounded-lg text-xs font-medium transition-colors"
                        >
                            Cancel
                        </button>
                        <button
                            type="submit"
                            className="bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2 rounded-lg text-xs font-bold transition-all active:scale-95 shadow-md"
                        >
                            Create Project
                        </button>
                    </div>
                </form>

            </div>
        </div>
    );
}
