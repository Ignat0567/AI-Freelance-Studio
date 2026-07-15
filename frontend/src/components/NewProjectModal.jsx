import React, { useState } from 'react';

export default function NewProjectModal({ onCreate, onClose }) {
    const [title, setTitle] = useState('');
    const [desc, setDesc] = useState('');
    const [qualityProfile, setQualityProfile] = useState('strict_mvp');
    const [requiredTargets, setRequiredTargets] = useState(['backend', 'web']);
    const [optionalTargets, setOptionalTargets] = useState([]);
    const [strictToggles, setStrictToggles] = useState({
        require_real_e2e: true,
        require_rbac_matrix: true,
        require_security_baseline: true,
        block_on_mandatory_not_verified: true,
    });

    const targetOptions = [
        ['backend', 'Backend'],
        ['web', 'Web'],
        ['manager_web', 'Manager Web'],
        ['android', 'Android'],
        ['ios', 'iOS'],
        ['desktop_windows', 'Windows desktop'],
        ['desktop_macos', 'macOS desktop'],
        ['desktop_linux', 'Linux desktop'],
        ['api_service', 'API service'],
        ['telegram_bot', 'Telegram bot'],
        ['packaged_installer', 'Installer'],
    ];

    const rules = {
        prototype: 'Fast validation of source, startup, and one primary flow. Useful for demos; secondary targets may remain unverified.',
        strict_mvp: 'Usable minimum product. Mandatory features, required targets, E2E, RBAC/security, and unverified criteria block acceptance.',
        production_candidate: 'Strict MVP plus stronger architecture, packaging, expanded security, migrations, and operational readiness evidence.',
        production: 'Release gate with deployment, monitoring, backups, secrets, performance, and approved audit evidence.',
    };

    const strictToggleOptions = [
        ['require_real_e2e', 'Require real E2E'],
        ['require_rbac_matrix', 'Require security/RBAC checks'],
        ['require_security_baseline', 'Require security baseline'],
        ['block_on_mandatory_not_verified', 'Block mandatory unverified criteria'],
    ];

    const toggleTarget = (target, bucket) => {
        const setPrimary = bucket === 'required' ? setRequiredTargets : setOptionalTargets;
        const setOther = bucket === 'required' ? setOptionalTargets : setRequiredTargets;
        setPrimary(prev => prev.includes(target) ? prev.filter(item => item !== target) : [...prev, target]);
        setOther(prev => prev.filter(item => item !== target));
    };

    const handleSubmit = (e) => {
        e.preventDefault();
        if (!title.trim() || !desc.trim()) return;
        onCreate(title, desc, qualityProfile, { requiredTargets, optionalTargets, strictToggles });
    };

    return (
        <div className="fixed inset-0 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4 z-50 animate-fade-in">
            <div className="bg-slate-900 border border-slate-800 rounded-xl w-full max-w-3xl overflow-hidden shadow-2xl new-project-modal-container">

                {/* MODAL HEADER */}
                <div className="p-4 border-b border-slate-800 flex justify-between items-center bg-slate-950/40">
                    <h3 className="text-xs font-bold uppercase tracking-widest text-indigo-400">📝 Initialize New Manual Project</h3>
                    <button onClick={onClose} className="text-slate-500 hover:text-slate-200 font-mono text-sm">✕</button>
                </div>

                {/* INPUT FORM */}
                <form onSubmit={handleSubmit} className="p-5 space-y-5 new-project-modal-body">
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

                    <div className="rounded-xl border border-indigo-500/30 bg-indigo-500/5 p-4 space-y-4" aria-label="Project Quality step">
                        <div>
                            <span className="block text-[10px] text-indigo-300 uppercase font-bold tracking-widest">Project Quality</span>
                            <p className="mt-1 text-[11px] text-slate-400">Choose the acceptance target before generation. Implementation claims are informational; Final Audit owns acceptance.</p>
                        </div>
                        <div>
                        <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1">Quality Profile</label>
                        <select
                            value={qualityProfile}
                            onChange={(e) => setQualityProfile(e.target.value)}
                            className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs focus:outline-none focus:border-indigo-500 text-slate-300"
                        >
                            <option value="prototype">Prototype - technical demonstration</option>
                            <option value="strict_mvp">Strict MVP - usable minimum product</option>
                            <option value="production_candidate">Production candidate - expanded readiness gates</option>
                            <option value="production">Production - release and operations gates</option>
                        </select>
                        <p className="mt-1 text-[10px] text-slate-500">{rules[qualityProfile]}</p>
                        </div>

                        <div>
                        <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-2">Required Targets and Optional Targets</label>
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 max-h-48 overflow-y-auto pr-1">
                            {targetOptions.map(([value, label]) => (
                                <div key={value} className="bg-slate-950 border border-slate-800 rounded-lg p-2">
                                    <div className="text-[11px] text-slate-300 font-medium mb-2">{label}</div>
                                    <div className="flex gap-2">
                                        <button type="button" onClick={() => toggleTarget(value, 'required')} className={`flex-1 rounded px-2 py-1 text-[10px] border ${requiredTargets.includes(value) ? 'border-indigo-400 text-indigo-200 bg-indigo-500/20' : 'border-slate-800 text-slate-500'}`}>Required</button>
                                        <button type="button" onClick={() => toggleTarget(value, 'optional')} className={`flex-1 rounded px-2 py-1 text-[10px] border ${optionalTargets.includes(value) ? 'border-amber-400 text-amber-200 bg-amber-500/20' : 'border-slate-800 text-slate-500'}`}>Optional</button>
                                    </div>
                                </div>
                            ))}
                        </div>
                        <p className="mt-2 text-[10px] text-slate-500">Required targets block completion when missing. Optional targets are reported but do not block acceptance.</p>
                        </div>

                        <div>
                            <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-2">Strict Completion Toggles</label>
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                                {strictToggleOptions.map(([key, label]) => (
                                    <label key={key} className="flex items-center gap-2 rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-[11px] text-slate-300">
                                        <input type="checkbox" checked={!!strictToggles[key]} onChange={event => setStrictToggles(prev => ({ ...prev, [key]: event.target.checked }))} />
                                        <span>{label}</span>
                                    </label>
                                ))}
                            </div>
                        </div>

                        <div className="rounded-lg border border-amber-400/30 bg-amber-400/10 p-3">
                            <strong className="block text-[11px] text-amber-200 uppercase tracking-wide">Completion Policy Summary</strong>
                            <p className="mt-1 text-[11px] text-slate-300">This project will not be accepted until:</p>
                            <ul className="mt-2 grid gap-1 text-[10px] text-slate-400 list-disc pl-4">
                                <li>mandatory features pass</li>
                                <li>required targets are verified</li>
                                <li>required E2E passes</li>
                                <li>required security/RBAC checks pass</li>
                                <li>mandatory unverified criteria are resolved</li>
                            </ul>
                        </div>
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
