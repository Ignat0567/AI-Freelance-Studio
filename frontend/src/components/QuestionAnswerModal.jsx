import React, { useState, useEffect, useCallback } from 'react';

const AGENT_COLORS = {
  alex: '#6366f1', maya: '#a855f7', elena: '#ec4899',
  bugcatcher: '#10b981', codex: '#0ea5e9', goldie: '#f59e0b',
  sentinel: '#ef4444', lupa: '#8b5cf6',
};

export default function QuestionAnswerModal({ activePort, projectId, onClose }) {
  const [questions, setQuestions] = useState([]);
  const [answers, setAnswers] = useState({});
  const [customInputs, setCustomInputs] = useState({});
  const [submitting, setSubmitting] = useState({});

  const fetchQuestions = useCallback(() => {
    if (!projectId) return;
    fetch(`http://localhost:${activePort}/api/projects/${projectId}/questions`)
      .then(r => r.json())
      .then(data => {
        if (data.questions && data.questions.length > 0) {
          setQuestions(data.questions);
          const init = {};
          data.questions.forEach(q => { init[q.id] = ''; });
          setCustomInputs(prev => ({ ...prev, ...init }));
        } else {
          setQuestions([]);
        }
      })
      .catch(() => {});
  }, [activePort, projectId]);

  useEffect(() => {
    fetchQuestions();
    const interval = setInterval(fetchQuestions, 5000);
    return () => clearInterval(interval);
  }, [fetchQuestions]);

  const submitAnswer = async (qId, answer) => {
    if (!answer || answer.trim() === '') return;
    setSubmitting(prev => ({ ...prev, [qId]: true }));
    try {
      await fetch(`http://localhost:${activePort}/api/projects/${projectId}/answer`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question_id: qId, answer: answer.trim() }),
      });
      setAnswers(prev => ({ ...prev, [qId]: answer.trim() }));
    } catch {}
    setSubmitting(prev => ({ ...prev, [qId]: false }));
  };

  const allAnswered = questions.every(q => answers[q.id]);
  if (!questions.length) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ backgroundColor: 'rgba(0,0,0,0.7)' }}>
      <div className="w-full max-w-lg max-h-[80vh] flex flex-col rounded-xl overflow-hidden" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
        <div className="flex justify-between items-center px-5 py-3" style={{ borderBottom: '1px solid var(--border)' }}>
          <h2 className="text-sm font-bold flex items-center gap-2" style={{ color: 'var(--text-primary)' }}>
            <span>💬</span> Agent Questions
          </h2>
          <button onClick={onClose} className="text-lg leading-none" style={{ color: 'var(--text-muted)' }}>✕</button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {questions.map(q => {
            const answered = answers[q.id];
            const agentColor = AGENT_COLORS[q.agent] || '#6366f1';
            return (
              <div key={q.id} className="p-4 rounded-xl" style={{
                backgroundColor: 'var(--bg-card)',
                border: `1px solid ${answered ? '#10b98140' : 'var(--border)'}`,
                opacity: answered ? 0.6 : 1,
              }}>
                <div className="flex items-center gap-2 mb-2">
                  <span className="w-2 h-2 rounded-full" style={{ backgroundColor: agentColor }} />
                  <span className="text-[10px] font-bold uppercase tracking-wider" style={{ color: agentColor }}>
                    {q.agent}
                  </span>
                  {answered && <span className="text-[10px] ml-auto" style={{ color: '#10b981' }}>✓ Answered</span>}
                </div>
                <p className="text-sm mb-3 leading-relaxed" style={{ color: 'var(--text-primary)' }}>
                  {q.question}
                </p>

                {!answered && (
                  <div className="space-y-2">
                    {q.options && q.options.length > 0 ? (
                      <div className="flex flex-wrap gap-2">
                        {q.options.map(opt => (
                          <button
                            key={opt}
                            onClick={() => submitAnswer(q.id, opt)}
                            disabled={submitting[q.id]}
                            className="px-3 py-1.5 rounded-lg text-xs font-medium transition-all"
                            style={{
                              backgroundColor: customInputs[q.id] === opt ? 'var(--accent)' : 'var(--bg-secondary)',
                              color: customInputs[q.id] === opt ? '#fff' : 'var(--text-secondary)',
                              border: '1px solid var(--border)',
                              opacity: submitting[q.id] ? 0.5 : 1,
                            }}
                          >
                            {opt}
                          </button>
                        ))}
                      </div>
                    ) : null}
                    <div className="flex gap-2">
                      <input
                        type="text"
                        placeholder="Enter your answer..."
                        value={customInputs[q.id] || ''}
                        onChange={e => setCustomInputs(prev => ({ ...prev, [q.id]: e.target.value }))}
                        onKeyDown={e => {
                          if (e.key === 'Enter') submitAnswer(q.id, customInputs[q.id]);
                        }}
                        className="flex-1 px-3 py-1.5 rounded-lg text-xs"
                        style={{
                          backgroundColor: 'var(--bg-secondary)',
                          border: '1px solid var(--border)',
                          color: 'var(--text-primary)',
                          outline: 'none',
                        }}
                      />
                      <button
                        onClick={() => submitAnswer(q.id, customInputs[q.id])}
                        disabled={submitting[q.id] || !customInputs[q.id]?.trim()}
                        className="px-4 py-1.5 rounded-lg text-xs font-bold"
                        style={{
                          backgroundColor: 'var(--accent)',
                          color: '#fff',
                          opacity: submitting[q.id] || !customInputs[q.id]?.trim() ? 0.5 : 1,
                        }}
                      >
                        {submitting[q.id] ? '⏳' : 'Send'}
                      </button>
                    </div>
                  </div>
                )}

                {answered && (
                  <div className="px-3 py-2 rounded-lg text-xs" style={{ backgroundColor: '#10b98115', color: '#10b981' }}>
                    Answer: {answered}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <div className="px-4 py-3 flex justify-between items-center" style={{ borderTop: '1px solid var(--border)', backgroundColor: 'var(--bg-secondary)' }}>
          <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
            {questions.filter(q => answers[q.id]).length}/{questions.length} answered
          </span>
          <div className="flex gap-2">
            <button
              onClick={onClose}
              className="px-4 py-1.5 rounded-lg text-xs font-medium"
              style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
            >
              {allAnswered ? 'Done' : 'Minimize'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
