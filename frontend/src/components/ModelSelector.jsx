import React, { useState, useEffect } from 'react';

export default function ModelSelector({ activePort, onClose, addLog }) {
    const [agents, setAgents] = useState({});
    const [loading, setLoading] = useState(true);
    const [showCreateForm, setShowCreateForm] = useState(false);
    const [newAgent, setNewAgent] = useState({ name: '', role: 'custom', custom_prompt: '', provider: 'nvidia', model: 'meta/llama-3.3-70b-instruct', use_global: true, save_path: '' });

    const providerModels = {
        openai: ["gpt-5.5", "gpt-5", "gpt-4.1"],
        nvidia: ["meta/llama-3.3-70b-instruct"],
        anthropic: ["claude-sonnet-4-20250514"],
        google: ["gemini-2.0-flash-001"],
        groq: ["llama-3.1-70b-versatile"],
        mistral: ["mistral-large-latest"],
        deepseek: ["deepseek-chat"],
        together: ["meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo"],
        ollama: ["codellama", "llama3.1", "mistral"],
    };

    useEffect(() => {
        fetchAgentsRegistry();
    }, []);

    const fetchAgentsRegistry = () => {
        fetch(`http://localhost:${activePort}/api/agents`)
            .then(res => res.json())
            .then(data => {
                setAgents(data);
                setLoading(false);
            })
            .catch(err => console.error('[API Error Fetching Agents]:', err));
    };

    const handleUpdateAgent = (agentId, updatedFields) => {
        const currentAgent = agents[agentId];
        const newConfig = {
            use_global: updatedFields.use_global !== undefined ? updatedFields.use_global : currentAgent.use_global,
            provider: updatedFields.provider || currentAgent.provider,
            model: updatedFields.model || currentAgent.model,
            temperature: updatedFields.temperature !== undefined ? parseFloat(updatedFields.temperature) : currentAgent.temperature,
            enabled: updatedFields.enabled !== undefined ? updatedFields.enabled : currentAgent.enabled,
            custom_prompt: updatedFields.custom_prompt !== undefined ? updatedFields.custom_prompt : (currentAgent.custom_prompt || ''),
        };

        const fullConfig = {
            ...newConfig,
            save_path: updatedFields.save_path !== undefined ? updatedFields.save_path : (currentAgent.save_path || ''),
        };
        fetch(`http://localhost:${activePort}/api/agents/${agentId}/config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(fullConfig)
        })
            .then(res => res.json())
            .then(data => {
                if (data.status === 'success') {
                    fetchAgentsRegistry();
                    const name = currentAgent.name || agentId;
                    if (updatedFields.enabled !== undefined) {
                        addLog(`[Config]: ${name} ${updatedFields.enabled ? 'enabled' : 'disabled'}.`);
                    } else {
                        addLog(`[Config]: Updated ${name}.`);
                    }
                }
            })
            .catch(err => console.error('[API Error Saving Agent Config]:', err));
    };

    const handleDeleteAgent = (agentId) => {
        const name = agents[agentId]?.name || agentId;
        fetch(`http://localhost:${activePort}/api/agents/${agentId}`, { method: 'DELETE' })
            .then(res => res.json())
            .then(data => {
                if (data.status === 'deleted' || data.status === 'disabled') {
                    fetchAgentsRegistry();
                    addLog(`[Config]: ${name} ${data.status}.`);
                }
            })
            .catch(err => console.error('[API Error Deleting Agent]:', err));
    };

    const handleCreateAgent = (e) => {
        e.preventDefault();
        if (!newAgent.name.trim()) return;
        fetch(`http://localhost:${activePort}/api/agents`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(newAgent)
        })
            .then(res => res.json())
            .then(data => {
                if (data.status === 'created') {
                    fetchAgentsRegistry();
                    addLog(`[Config]: Created new agent "${newAgent.name}" (${data.agent_id}).`);
                    setShowCreateForm(false);
                    setNewAgent({ name: '', role: 'custom', custom_prompt: '', provider: 'nvidia', model: 'meta/llama-3.3-70b-instruct', use_global: true, save_path: '' });
                }
            })
            .catch(err => console.error('[API Error Creating Agent]:', err));
    };

    if (loading) return <div className="text-xs text-slate-500 p-4">Loading Agent Profiles...</div>;

    return (
        <div className="fixed inset-0 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4 z-50 animate-fade-in">
            <div className="bg-slate-900 border border-slate-800 rounded-xl w-full max-w-2xl overflow-hidden shadow-2xl flex flex-col h-[600px]">

                <div className="p-4 border-b border-slate-800 flex justify-between items-center bg-slate-950/40">
                    <h3 className="text-xs font-bold uppercase tracking-widest text-sky-400">AI Staff Intelligence Configuration</h3>
                    <button onClick={onClose} className="text-slate-500 hover:text-slate-200 font-mono text-sm">✕</button>
                </div>

                <div className="flex-1 overflow-y-auto p-5 space-y-4">
                    {Object.entries(agents).map(([id, agent]) => (
                        <AgentCard
                            key={id}
                            id={id}
                            agent={agent}
                            providerModels={providerModels}
                            onUpdate={handleUpdateAgent}
                            onDelete={handleDeleteAgent}
                        />
                    ))}

                    {showCreateForm ? (
                        <form onSubmit={handleCreateAgent} className="bg-indigo-950/40 border border-indigo-500/30 rounded-xl p-4 space-y-3">
                            <h4 className="text-xs font-bold text-indigo-300">Create New Agent</h4>
                            <div className="grid grid-cols-2 gap-3">
                                <div>
                                    <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1">Name</label>
                                    <input type="text" value={newAgent.name} onChange={e => setNewAgent({...newAgent, name: e.target.value})} placeholder="Agent name" required className="w-full bg-slate-900 border border-slate-800 rounded px-2 py-1 text-xs focus:outline-none focus:border-indigo-500 text-slate-300" />
                                </div>
                                <div>
                                    <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1">Role</label>
                                    <input type="text" value={newAgent.role} onChange={e => setNewAgent({...newAgent, role: e.target.value})} placeholder="e.g. analyst, writer" className="w-full bg-slate-900 border border-slate-800 rounded px-2 py-1 text-xs focus:outline-none focus:border-indigo-500 text-slate-300" />
                                </div>
                                <div>
                                    <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1">Provider</label>
                                    <select value={newAgent.provider} onChange={e => setNewAgent({...newAgent, provider: e.target.value, model: providerModels[e.target.value][0]})} className="w-full bg-slate-900 border border-slate-800 rounded px-2 py-1 text-xs focus:outline-none focus:border-indigo-500 text-slate-300">
                                        <option value="nvidia">NVIDIA (Free)</option>
                                        <option value="ollama">Ollama (Free Local)</option>
                                        <option value="openai">OpenAI (Paid)</option>
                                        <option value="anthropic">Claude by Anthropic (Paid)</option>
                                    </select>
                                </div>
                                <div>
                                    <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1">Model</label>
                                    <input type="text" value={newAgent.model} onChange={e => setNewAgent({...newAgent, model: e.target.value})} className="w-full bg-slate-900 border border-slate-800 rounded px-2 py-1 text-xs focus:outline-none focus:border-indigo-500 text-slate-300 font-mono" />
                                </div>
                                <div className="col-span-2">
                                    <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1">Save Path (optional)</label>
                                    <input type="text" value={newAgent.save_path} onChange={e => setNewAgent({...newAgent, save_path: e.target.value})} placeholder="e.g. D:\Projects" className="w-full bg-slate-900 border border-slate-800 rounded px-2 py-1 text-xs focus:outline-none focus:border-indigo-500 text-slate-300 font-mono" />
                                </div>
                            </div>
                            <div>
                                <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1">Custom System Prompt (optional)</label>
                                <textarea value={newAgent.custom_prompt} onChange={e => setNewAgent({...newAgent, custom_prompt: e.target.value})} rows={3} placeholder="Describe the agent's personality, expertise, and response style..." className="w-full bg-slate-900 border border-slate-800 rounded px-2 py-1 text-xs focus:outline-none focus:border-indigo-500 text-slate-300 font-mono resize-none" />
                            </div>
                            <div className="flex justify-end space-x-2 pt-1">
                                <button type="button" onClick={() => setShowCreateForm(false)} className="bg-slate-800 hover:bg-slate-700 text-slate-300 px-3 py-1 rounded text-xs">Cancel</button>
                                <button type="submit" className="bg-indigo-600 hover:bg-indigo-500 text-white px-3 py-1 rounded text-xs font-medium">Create Agent</button>
                            </div>
                        </form>
                    ) : (
                        <button onClick={() => setShowCreateForm(true)} className="w-full border-2 border-dashed border-slate-700 hover:border-indigo-500/50 text-slate-400 hover:text-indigo-400 rounded-xl py-3 text-xs font-medium transition-colors">
                            + Create New Custom Agent
                        </button>
                    )}
                </div>

                <div className="p-3 bg-slate-950/40 border-t border-slate-800 flex justify-end">
                    <button onClick={onClose} className="bg-slate-800 hover:bg-slate-700 text-slate-200 font-medium px-4 py-1.5 rounded-lg text-xs transition-colors">
                        Close Settings
                    </button>
                </div>

            </div>
        </div>
    );
}

function AgentCard({ id, agent, providerModels, onUpdate, onDelete }) {
    const [editingPrompt, setEditingPrompt] = useState(false);
    const [promptDraft, setPromptDraft] = useState(agent.custom_prompt || '');
    const [savePathDraft, setSavePathDraft] = useState(agent.save_path || '');

    const handleSavePrompt = () => {
        onUpdate(id, { custom_prompt: promptDraft });
        setEditingPrompt(false);
    };

    const handleSavePath = () => {
        onUpdate(id, { save_path: savePathDraft });
    };

    return (
        <div className={`bg-slate-950 border rounded-xl p-4 space-y-3 ${agent.enabled === false ? 'border-red-900/40 opacity-60' : 'border-slate-800/80'}`}>
            <div className="flex justify-between items-center border-b border-slate-900 pb-2">
                <div className="flex items-center space-x-2">
                    <h4 className="text-xs font-bold text-slate-200">{agent.name}</h4>
                    {agent.builtin === false && (
                        <span className="text-[9px] bg-purple-900/60 text-purple-300 px-1.5 py-0.5 rounded font-bold uppercase">Custom</span>
                    )}
                    {agent.enabled === false && (
                        <span className="text-[9px] bg-red-900/60 text-red-300 px-1.5 py-0.5 rounded font-bold uppercase">Disabled</span>
                    )}
                </div>
                <div className="flex items-center space-x-2">
                    <SliderSwitch
                        checked={agent.enabled !== false}
                        onChange={(value) => onUpdate(id, { enabled: value })}
                        label="Active"
                        accent="#10b981"
                    />
                    <SliderSwitch
                        checked={!!agent.use_global}
                        onChange={(value) => onUpdate(id, { use_global: value })}
                        label="Global"
                        accent="#0ea5e9"
                    />
                    {agent.builtin === false && (
                        <button onClick={() => { if (confirm(`Delete ${agent.name}?`)) onDelete(id); }} className="text-red-500 hover:text-red-400 text-xs px-1" title="Delete agent">✕</button>
                    )}
                </div>
            </div>

            <div className="grid grid-cols-3 gap-3">
                <div>
                    <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1">Provider</label>
                    <select
                        disabled={agent.use_global}
                        value={agent.use_global ? agent.active_provider : agent.provider}
                        onChange={(e) => onUpdate(id, { provider: e.target.value, model: providerModels[e.target.value][0] })}
                        className="w-full bg-slate-900 border border-slate-800 rounded px-2 py-1 text-xs focus:outline-none focus:border-sky-500 text-slate-300 disabled:opacity-40"
                    >
                        <option value="nvidia">NVIDIA (Free)</option>
                        <option value="ollama">Ollama (Free Local)</option>
                        <option value="openai">OpenAI (Paid)</option>
                        <option value="anthropic">Claude by Anthropic (Paid)</option>
                    </select>
                </div>
                <div className="col-span-2">
                    <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1">Model</label>
                    <input
                        type="text"
                        disabled={agent.use_global}
                        value={agent.use_global ? agent.active_model : agent.model}
                        onChange={(e) => onUpdate(id, { model: e.target.value })}
                        placeholder="Enter any model string..."
                        className="w-full bg-slate-900 border border-slate-800 rounded px-2 py-1 text-xs focus:outline-none focus:border-sky-500 text-slate-300 font-mono disabled:opacity-40"
                    />
                </div>
            </div>

            <div>
                <div className="flex justify-between items-center mb-1">
                    <label className="block text-[10px] text-slate-500 uppercase font-semibold">Save Path</label>
                    {savePathDraft !== (agent.save_path || '') && (
                        <button onClick={handleSavePath} className="text-[10px] bg-sky-600 hover:bg-sky-500 text-white px-2 py-0.5 rounded">Save Path</button>
                    )}
                </div>
                <input type="text" value={savePathDraft} onChange={e => setSavePathDraft(e.target.value)}
                    placeholder="e.g. D:\Projects (leave empty for default: generated_projects/)"
                    className="w-full bg-slate-900/60 border border-slate-800/60 rounded px-2 py-1 text-[11px] focus:outline-none focus:border-sky-500 text-slate-300 font-mono" />
                {agent.save_path && (
                    <p className="text-[10px] text-slate-600 mt-0.5">Current: {agent.save_path}</p>
                )}
            </div>

            <div>
                <div className="flex justify-between items-center mb-1">
                    <label className="block text-[10px] text-slate-500 uppercase font-semibold">System Prompt</label>
                    {!editingPrompt && agent.custom_prompt && (
                        <button onClick={() => { setPromptDraft(agent.custom_prompt); setEditingPrompt(true); }} className="text-[10px] text-sky-500 hover:text-sky-400">Edit</button>
                    )}
                </div>
                {editingPrompt ? (
                    <div className="space-y-1">
                        <textarea value={promptDraft} onChange={e => setPromptDraft(e.target.value)} rows={3} className="w-full bg-slate-900 border border-slate-800 rounded px-2 py-1 text-[11px] focus:outline-none focus:border-sky-500 text-slate-300 font-mono resize-none" />
                        <div className="flex space-x-2">
                            <button onClick={handleSavePrompt} className="bg-sky-600 hover:bg-sky-500 text-white px-2 py-0.5 rounded text-[10px]">Save</button>
                            <button onClick={() => { setPromptDraft(agent.custom_prompt || ''); setEditingPrompt(false); }} className="bg-slate-800 hover:bg-slate-700 text-slate-300 px-2 py-0.5 rounded text-[10px]">Cancel</button>
                            {agent.custom_prompt && (
                                <button onClick={() => { onUpdate(id, { custom_prompt: '' }); setPromptDraft(''); setEditingPrompt(false); }} className="text-red-400 hover:text-red-300 text-[10px] ml-auto">Reset to default</button>
                            )}
                        </div>
                    </div>
                ) : (
                    <div className="text-[11px] text-slate-500 font-mono bg-slate-950/60 rounded px-2 py-1 border border-slate-800/50 min-h-[1.5em] cursor-pointer" onClick={() => { setPromptDraft(agent.custom_prompt || ''); setEditingPrompt(true); }}>
                        {agent.custom_prompt ? (
                            <span className="text-slate-400 line-clamp-2">{agent.custom_prompt}</span>
                        ) : (
                            <span className="text-slate-600 italic">Default prompt — click to customize</span>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}

function SliderSwitch({ checked, onChange, label, accent }) {
    return (
        <label className="flex items-center space-x-1.5 text-[10px] cursor-pointer text-slate-400 select-none">
            <span
                role="switch"
                aria-checked={checked}
                tabIndex={0}
                onClick={(e) => { e.preventDefault(); onChange(!checked); }}
                onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        onChange(!checked);
                    }
                }}
                className="transition-all"
                style={{
                    position: 'relative',
                    display: 'inline-flex',
                    alignItems: 'center',
                    width: 38,
                    height: 22,
                    minWidth: 38,
                    borderRadius: 999,
                    padding: 2,
                    backgroundColor: checked ? accent : 'rgba(71,85,105,0.45)',
                    boxShadow: checked ? `0 0 14px ${accent}55` : 'inset 0 0 0 1px rgba(148,163,184,0.25)',
                    cursor: 'pointer',
                }}
            >
                <span
                    className="transition-transform shadow-md"
                    style={{
                        display: 'block',
                        width: 18,
                        height: 18,
                        borderRadius: 999,
                        backgroundColor: '#fff',
                        transform: checked ? 'translateX(16px)' : 'translateX(0)',
                    }}
                />
            </span>
            <span>{label}</span>
        </label>
    );
}
