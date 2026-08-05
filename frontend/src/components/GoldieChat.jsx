import React, { useState, useRef, useEffect } from 'react';

export default function GoldieChat({ activePort, onClose, addLog, project }) {
    const [messages, setMessages] = useState([]);
    const [input, setInput] = useState('');
    const [loading, setLoading] = useState(false);
    const scrollRef = useRef(null);

    useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [messages]);

    const sendMessage = (e) => {
        e.preventDefault();
        if (!input.trim() || loading) return;

        const userMsg = input.trim();
        setInput('');
        setMessages(prev => [...prev, { role: 'user', content: userMsg }]);
        setLoading(true);

        const chatHistory = messages.map(m => ({ role: m.role, content: m.content }));

        fetch(`http://localhost:${activePort}/api/agents/goldie/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: userMsg, chat_history: chatHistory })
        })
            .then(res => res.json())
            .then(data => {
                setMessages(prev => [...prev, { role: 'assistant', content: data.reply || data.response || 'No response' }]);
                setLoading(false);
            })
            .catch(err => {
                setMessages(prev => [...prev, { role: 'assistant', content: 'Connection error. Is the backend running?' }]);
                setLoading(false);
            });
    };

    const requestAnalysis = () => {
        if (!project) {
            addLog('[Goldie]: No active project to analyze.');
            return;
        }
        setLoading(true);
        setMessages(prev => [...prev, {
            role: 'user',
            content: `Analyze the finances for project: ${project.title}`
        }]);

        fetch(`http://localhost:${activePort}/api/agents/goldie/analyze`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project_id: project.project_id || project.id })
        })
            .then(res => res.json())
            .then(data => {
                setMessages(prev => [...prev, { role: 'assistant', content: data.analysis || 'Analysis failed.' }]);
                addLog('[Goldie]: Financial analysis complete.');
                setLoading(false);
            })
            .catch(() => {
                setMessages(prev => [...prev, { role: 'assistant', content: 'Analysis request failed.' }]);
                setLoading(false);
            });
    };

    const doSearch = () => {
        const q = input.trim();
        if (!q) return;
        setMessages(prev => [...prev, { role: 'user', content: `Search web for: ${q}` }]);
        setInput('');
        setLoading(true);

        fetch(`http://localhost:${activePort}/api/agents/goldie/search`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query: q })
        })
            .then(res => res.json())
            .then(data => {
                const results = data.results || [];
                if (results.length === 0 || results[0].title === 'No results') {
                    setMessages(prev => [...prev, { role: 'assistant', content: `No web results found for "${q}".` }]);
                } else {
                    let reply = `**Web search results for "${data.query}":**\n\n`;
                    results.forEach((r, i) => {
                        reply += `${i + 1}. **${r.title}**\n   ${r.snippet}\n   ${r.url}\n\n`;
                    });
                    setMessages(prev => [...prev, { role: 'assistant', content: reply }]);
                }
                setLoading(false);
            })
            .catch(() => {
                setMessages(prev => [...prev, { role: 'assistant', content: 'Web search failed.' }]);
                setLoading(false);
            });
    };

    return (
        <div className="fixed inset-0 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4 z-50">
            <div className="bg-slate-900 border border-slate-800 rounded-xl w-full max-w-2xl shadow-2xl flex flex-col h-[600px]">
                <div className="p-4 border-b border-slate-800 flex justify-between items-center bg-slate-950/40">
                    <div className="flex items-center space-x-3">
                        <div className="w-8 h-8 rounded-full bg-amber-500/20 border border-amber-500/40 flex items-center justify-center text-sm">
                            <span className="text-amber-400 font-bold">$</span>
                        </div>
                        <div>
                            <h3 className="text-xs font-bold uppercase tracking-widest text-amber-400">Goldie — Financial Advisor</h3>
                            <p className="text-[10px] text-slate-500">Senior financial analyst & strategic consultant</p>
                        </div>
                    </div>
                    <button onClick={onClose} className="text-slate-500 hover:text-slate-200 font-mono text-sm">✕</button>
                </div>

                <div className="flex-1 overflow-y-auto p-4 space-y-3" ref={scrollRef}>
                    {messages.length === 0 && (
                        <div className="text-center text-slate-600 text-xs py-8 space-y-2">
                            <p className="text-lg">💰</p>
                            <p className="font-semibold text-slate-500">Ask Goldie anything about project finances.</p>
                            <p className="text-slate-600">Examples:</p>
                            <div className="flex flex-wrap gap-2 justify-center mt-2">
                                <button onClick={() => setInput('What should I charge for this project?')} className="bg-slate-800 hover:bg-slate-700 text-slate-400 px-3 py-1 rounded text-[10px] border border-slate-700 transition-colors">
                                    What should I charge?
                                </button>
                                <button onClick={() => setInput('How do I estimate costs for a web development project?')} className="bg-slate-800 hover:bg-slate-700 text-slate-400 px-3 py-1 rounded text-[10px] border border-slate-700 transition-colors">
                                    Cost estimation tips
                                </button>
                                {project && (
                                    <button onClick={requestAnalysis} className="bg-amber-600/20 hover:bg-amber-600/30 text-amber-400 px-3 py-1 rounded text-[10px] border border-amber-600/30 transition-colors">
                                        Analyze current project
                                    </button>
                                )}
                            </div>
                        </div>
                    )}
                    {messages.map((msg, i) => (
                        <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                            <div className={`max-w-[80%] rounded-xl px-4 py-2.5 text-xs leading-relaxed ${
                                msg.role === 'user'
                                    ? 'bg-indigo-600/20 border border-indigo-500/30 text-slate-200'
                                    : 'bg-slate-800/60 border border-slate-700/50 text-slate-300'
                            }`}>
                                <div className="whitespace-pre-wrap break-words">{msg.content}</div>
                            </div>
                        </div>
                    ))}
                    {loading && (
                        <div className="flex justify-start">
                            <div className="bg-slate-800/60 border border-slate-700/50 rounded-xl px-4 py-2.5 text-xs text-slate-400">
                                <span className="animate-pulse">Goldie is thinking...</span>
                            </div>
                        </div>
                    )}
                </div>

                <form onSubmit={sendMessage} className="p-3 border-t border-slate-800 bg-slate-950/40">
                    <div className="flex space-x-2">
                        <input
                            type="text"
                            value={input}
                            onChange={(e) => setInput(e.target.value)}
                            placeholder="Ask Goldie about finances, pricing, or market research..."
                            className="flex-1 bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs focus:outline-none focus:border-amber-500 text-slate-300 placeholder-slate-700"
                            disabled={loading}
                        />
                        <button
                            type="button"
                            onClick={doSearch}
                            disabled={loading || !input.trim()}
                            className="bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-400 px-3 py-2 rounded-lg text-xs transition-colors disabled:opacity-40"
                            title="Search web"
                        >
                            🌐
                        </button>
                        <button
                            type="submit"
                            disabled={loading || !input.trim()}
                            className="bg-amber-600 hover:bg-amber-500 text-white font-semibold px-4 py-2 rounded-lg text-xs transition-colors disabled:opacity-40"
                        >
                            Send
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
}
