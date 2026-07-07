import React, { useState } from 'react';

export default function ProjectChat({ activePort, projectId, chatHistory, onUpdateHistory, onApprove, onClose }) {
    const [messageText, setMessageText] = useState('');
    const [sending, setSending] = useState(false);

    const handleSendMessage = () => {
        if (!messageText.trim() || sending) return;
        setSending(true);

        // Optimistically push the user message into view local history
        const updatedHistory = [...chatHistory, { role: 'user', content: messageText }];
        onUpdateHistory(updatedHistory);
        const textToSend = messageText;
        setMessageText('');

        fetch(`http://localhost:${activePort}/api/projects/${projectId}/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: textToSend, chat_history: chatHistory })
        })
            .then(res => res.json())
            .then(data => {
                if (data.status === 'success' && data.chat_history) {
                    onUpdateHistory(data.chat_history);
                } else if (data.response) {
                    onUpdateHistory([...updatedHistory, { role: 'assistant', content: data.response }]);
                }
                setSending(false);
            })
            .catch(err => {
                console.error('[API Error Sending Chat Message]:', err);
                setSending(false);
            });
    };

    return (
        <div className="fixed inset-0 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4 z-50 animate-fade-in">
            <div className="bg-slate-900 border border-slate-800 rounded-xl w-full max-w-xl overflow-hidden shadow-2xl flex flex-col h-[520px]">

                {/* PANEL HEADER CONTROLS */}
                <div className="p-4 border-b border-slate-800 flex justify-between items-center bg-slate-950/40">
                    <div className="flex items-center space-x-2">
                        <div className="w-2 h-2 rounded-full bg-sky-400 animate-pulse" />
                        <h3 className="text-xs font-bold uppercase tracking-widest text-sky-400">💬 Briefing Room: Specification Clarification</h3>
                    </div>
                    <button onClick={onClose} className="text-slate-500 hover:text-slate-200 font-mono text-sm">✕</button>
                </div>

                {/* MESSAGES WATERFALL SCROLL LAYER */}
                <div className="flex-1 overflow-y-auto p-4 space-y-3 bg-slate-950/20">
                    {chatHistory.map((msg, idx) => (
                        <div
                            key={idx}
                            className={`flex flex-col max-w-[85%] rounded-xl p-3 text-xs leading-relaxed ${msg.role === 'user'
                                    ? 'bg-sky-600/20 border border-sky-500/30 text-sky-200 ml-auto'
                                    : 'bg-slate-800/80 border border-slate-700/50 text-slate-300'
                                }`}
                        >
                            <span className="text-[9px] font-bold uppercase tracking-wider mb-1 text-slate-500">
                                {msg.role === 'user' ? 'You (Director)' : 'Maya (Project Manager)'}
                            </span>
                            <p className="whitespace-pre-wrap">{msg.content}</p>
                        </div>
                    ))}
                    {sending && (
                        <div className="text-[10px] text-slate-500 italic animate-pulse">Maya is compiling requirements...</div>
                    )}
                </div>

                {/* ACTION CONTROLS INPUT BOARD FOOTER */}
                <div className="p-4 border-t border-slate-800 bg-slate-950/40 space-y-3">
                    <div className="flex space-x-2">
                        <input
                            type="text"
                            value={messageText}
                            onChange={(e) => setMessageText(e.target.value)}
                            onKeyDown={(e) => e.key === 'Enter' && handleSendMessage()}
                            placeholder="Provide extra project specifics to Maya..."
                            className="flex-1 bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs focus:outline-none focus:border-sky-500 text-slate-300 placeholder-slate-600"
                        />
                        <button
                            onClick={handleSendMessage}
                            className="bg-sky-600 hover:bg-sky-500 text-white font-medium px-4 py-2 rounded-lg text-xs transition-colors"
                        >
                            Send
                        </button>
                    </div>

                    <div className="flex justify-between items-center pt-1">
                        <button
                            onClick={onClose}
                            className="text-slate-400 hover:text-slate-200 text-xs font-medium transition-colors"
                        >
                            Minimize Chat
                        </button>
                        <button
                            onClick={onApprove}
                            className="bg-emerald-600 hover:bg-emerald-500 text-white font-bold px-5 py-2 rounded-lg text-xs transition-all shadow-md active:scale-95"
                        >
                            🤝 Approve Specification & Start Team Assembly
                        </button>
                    </div>
                </div>

            </div>
        </div>
    );
}
