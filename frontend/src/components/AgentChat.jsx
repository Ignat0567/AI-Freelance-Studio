import React, { useState, useRef, useEffect } from 'react';

const AGENT_META = {
  maya: { name: 'Maya', emoji: '🎯', accent: '#a855f7' },
  alex: { name: 'Alex', emoji: '👔', accent: '#6366f1' },
  codex: { name: 'Codex', emoji: '💻', accent: '#0ea5e9' },
  elena: { name: 'Elena', emoji: '🎨', accent: '#ec4899' },
  bugcatcher: { name: 'BugCatcher', emoji: '🐛', accent: '#10b981' },
  goldie: { name: 'Goldie', emoji: '💰', accent: '#f59e0b' },
  sentinel: { name: 'Sentinel', emoji: '🛡️', accent: '#ef4444' },
  lupa: { name: 'Lupa', emoji: '🔍', accent: '#8b5cf6' },
};

const AGENT_ROLES = {
  maya: 'Business Analyst',
  alex: 'Project Manager',
  codex: 'Software Architect',
  elena: 'UI/UX Designer',
  bugcatcher: 'QA Engineer',
  goldie: 'Financial Advisor',
  sentinel: 'Security Auditor',
  lupa: 'Code Reviewer',
};

function loadHistory(agentId) {
  try {
    const raw = localStorage.getItem(`chat_${agentId}`);
    const parsed = raw ? JSON.parse(raw) : [];
    if (agentId === 'alex') {
      const cleaned = parsed.filter(m => !/дмитр|dmitry|dmitriy|dmitri/i.test(m.content || ''));
      if (cleaned.length !== parsed.length) saveHistory(agentId, cleaned);
      return cleaned;
    }
    return parsed;
  } catch { return []; }
}

function saveHistory(agentId, messages) {
  try {
    localStorage.setItem(`chat_${agentId}`, JSON.stringify(messages));
  } catch { /* quota exceeded, ignore */ }
}

export default function AgentChat({ agentId, activePort, onClose, addLog, project }) {
  const meta = AGENT_META[agentId] || { name: agentId, emoji: '🤖', accent: '#6366f1' };
  const role = AGENT_ROLES[agentId] || 'AI Assistant';
  const [messages, setMessages] = useState(() => loadHistory(agentId));
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [fastMode, setFastMode] = useState(() => localStorage.getItem('agent_chat_fast_mode') !== 'false');
  const [useProjectContext, setUseProjectContext] = useState(() => localStorage.getItem('agent_chat_project_context') === 'true');
  const scrollRef = useRef(null);
  const prevAgentRef = useRef(agentId);
  const messagesRef = useRef(messages);

  useEffect(() => { messagesRef.current = messages; }, [messages]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  useEffect(() => {
    return () => {
      const prevId = prevAgentRef.current;
      if (prevId) saveHistory(prevId, messagesRef.current);
    };
  }, []);

  useEffect(() => {
    const prevId = prevAgentRef.current;
    if (prevId && prevId !== agentId) {
      saveHistory(prevId, messages);
    }
    prevAgentRef.current = agentId;
    setMessages(loadHistory(agentId));
  }, [agentId]);

  const clearHistory = () => {
    setMessages([]);
    localStorage.removeItem(`chat_${agentId}`);
    addLog(`[${meta.name}]: Chat history cleared.`);
  };

  const IMAGE_EXT_RE = /\.(png|jpg|jpeg|gif|webp|bmp|tiff?|svg)\b/i;

  const sendMessage = (e) => {
    e.preventDefault();
    if (!input.trim() || loading) return;

    const userMsg = input.trim();

    if (IMAGE_EXT_RE.test(userMsg)) {
      setMessages(prev => [...prev, { role: 'user', content: userMsg }, { role: 'assistant', content: `This AI model only supports text. You mentioned "${userMsg.match(IMAGE_EXT_RE)[0]}", which looks like an image file. Please describe what you see in text instead.` }]);
      setInput('');
      return;
    }

    setInput('');
    setMessages(prev => [...prev, { role: 'user', content: userMsg }]);
    setLoading(true);

    const historyLimit = fastMode ? 6 : 12;
    const chatHistory = messages
      .slice(-historyLimit)
      .map(m => ({ role: m.role, content: m.content }));

    const payload = {
      message: userMsg,
      chat_history: chatHistory,
      fast_mode: fastMode,
      use_project_context: useProjectContext,
      history_limit: historyLimit,
    };
    if (useProjectContext && project && project.project_id) {
      payload.project_id = project.project_id;
    }
    fetch(`http://localhost:${activePort}/api/agents/${agentId}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then(data => {
        setMessages(prev => [...prev, { role: 'assistant', content: data.reply || data.response || 'No response' }]);
        setLoading(false);
      })
      .catch(err => {
        setMessages(prev => [...prev, { role: 'assistant', content: `Connection error: ${err.message}. Is the backend running?` }]);
        setLoading(false);
      });
  };

  const handleSuggestion = (text) => {
    setInput(text);
  };

  const toggleFastMode = () => {
    setFastMode(prev => {
      const next = !prev;
      localStorage.setItem('agent_chat_fast_mode', String(next));
      return next;
    });
  };

  const toggleProjectContext = () => {
    setUseProjectContext(prev => {
      const next = !prev;
      localStorage.setItem('agent_chat_project_context', String(next));
      return next;
    });
  };

  return (
    <div className="fixed inset-0 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4 z-50">
      <div className="bg-slate-900 border border-slate-800 rounded-xl w-full max-w-2xl shadow-2xl flex flex-col h-[600px]">
        <div className="p-4 border-b border-slate-800 flex justify-between items-center bg-slate-950/40">
          <div className="flex items-center space-x-3">
            <div
              className="w-8 h-8 rounded-full flex items-center justify-center text-sm"
              style={{
                backgroundColor: `${meta.accent}20`,
                borderColor: `${meta.accent}40`,
                border: '1px solid',
              }}
            >
              <span style={{ color: meta.accent }} className="font-bold">{meta.emoji}</span>
            </div>
            <div>
              <h3 className="text-xs font-bold uppercase tracking-widest" style={{ color: meta.accent }}>{meta.name}</h3>
              <p className="text-[10px] text-slate-500">{role}</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <TogglePill checked={fastMode} onChange={toggleFastMode} label="Fast" color={meta.accent} />
            {project?.project_id && (
              <TogglePill checked={useProjectContext} onChange={toggleProjectContext} label="Context" color="#10b981" />
            )}
            {messages.length > 0 && (
              <button onClick={clearHistory} className="text-[10px] text-slate-600 hover:text-red-400 px-2 py-1 rounded border border-slate-800 hover:border-red-500/30 transition-colors" title="Clear chat history">
                🗑 Clear
              </button>
            )}
            <button onClick={onClose} className="text-slate-500 hover:text-slate-200 font-mono text-sm">✕</button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-3" ref={scrollRef}>
          {messages.length === 0 && (
            <div className="text-center text-slate-600 text-xs py-8 space-y-2">
              <p className="text-lg">{meta.emoji}</p>
              <p className="font-semibold text-slate-500">Ask {meta.name} anything about your project.</p>
              <p className="text-slate-600">Examples:</p>
              <div className="flex flex-wrap gap-2 justify-center mt-2">
                {agentId === 'alex' && (
                  <button onClick={() => handleSuggestion('Summarize the current project status.')} className="bg-slate-800 hover:bg-slate-700 text-slate-400 px-3 py-1 rounded text-[10px] border border-slate-700 transition-colors">
                    Summarize project status
                  </button>
                )}
                {agentId === 'maya' && (
                  <button onClick={() => handleSuggestion('What are the key requirements for this project?')} className="bg-slate-800 hover:bg-slate-700 text-slate-400 px-3 py-1 rounded text-[10px] border border-slate-700 transition-colors">
                    List key requirements
                  </button>
                )}
                {agentId === 'codex' && (
                  <button onClick={() => handleSuggestion('What tech stack do you recommend?')} className="bg-slate-800 hover:bg-slate-700 text-slate-400 px-3 py-1 rounded text-[10px] border border-slate-700 transition-colors">
                    Recommend tech stack
                  </button>
                )}
                {agentId === 'elena' && (
                  <button onClick={() => handleSuggestion('Suggest a color palette for the UI.')} className="bg-slate-800 hover:bg-slate-700 text-slate-400 px-3 py-1 rounded text-[10px] border border-slate-700 transition-colors">
                    Suggest color palette
                  </button>
                )}
                {agentId === 'bugcatcher' && (
                  <button onClick={() => handleSuggestion('What test strategy should I use?')} className="bg-slate-800 hover:bg-slate-700 text-slate-400 px-3 py-1 rounded text-[10px] border border-slate-700 transition-colors">
                    Recommend test strategy
                  </button>
                )}
                {agentId === 'goldie' && (
                  <button onClick={() => handleSuggestion('What should I charge for this project?')} className="bg-slate-800 hover:bg-slate-700 text-slate-400 px-3 py-1 rounded text-[10px] border border-slate-700 transition-colors">
                    What should I charge?
                  </button>
                )}
                {agentId === 'sentinel' && (
                  <button onClick={() => handleSuggestion('Check my code for security vulnerabilities.')} className="bg-slate-800 hover:bg-slate-700 text-slate-400 px-3 py-1 rounded text-[10px] border border-slate-700 transition-colors">
                    Scan for vulnerabilities
                  </button>
                )}
                {agentId === 'lupa' && (
                  <button onClick={() => handleSuggestion('Review my code for quality and best practices.')} className="bg-slate-800 hover:bg-slate-700 text-slate-400 px-3 py-1 rounded text-[10px] border border-slate-700 transition-colors">
                    Review code quality
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
                <span className="animate-pulse">{meta.name} is thinking...</span>
              </div>
            </div>
          )}
        </div>

        <form onSubmit={sendMessage} className="p-3 border-t border-slate-800 bg-slate-950/40">
          <div className="flex items-center justify-between mb-2 text-[10px] text-slate-600">
            <span>{fastMode ? 'Fast mode: short prompt, limited history' : 'Detailed mode: fuller prompt and history'}</span>
            {project?.project_id && <span>{useProjectContext ? 'Project context on' : 'Project context off'}</span>}
          </div>
          <div className="flex space-x-2">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={`Ask ${meta.name} anything...`}
              className="flex-1 bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs focus:outline-none focus:border-indigo-500 text-slate-300 placeholder-slate-700"
              disabled={loading}
            />
            <button
              type="submit"
              disabled={loading || !input.trim()}
              className="bg-indigo-600 hover:bg-indigo-500 text-white font-semibold px-4 py-2 rounded-lg text-xs transition-colors disabled:opacity-40"
            >
              Send
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function TogglePill({ checked, onChange, label, color }) {
  return (
    <button
      type="button"
      onClick={onChange}
      className="px-2 py-1 rounded-full border text-[10px] font-semibold transition-all"
      style={{
        backgroundColor: checked ? `${color}22` : 'rgba(15,23,42,0.7)',
        borderColor: checked ? `${color}66` : 'rgba(51,65,85,0.9)',
        color: checked ? color : '#64748b',
      }}
      title={checked ? `${label} enabled` : `${label} disabled`}
    >
      {label}: {checked ? 'On' : 'Off'}
    </button>
  );
}
