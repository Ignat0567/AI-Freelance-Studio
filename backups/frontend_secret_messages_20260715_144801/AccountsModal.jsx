import React, { useState, useEffect } from 'react';

export default function AccountsModal({ activePort, onClose, addLog }) {
  const [accounts, setAccounts] = useState([]);
  const [platforms, setPlatforms] = useState({});
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [newPlatform, setNewPlatform] = useState('github');
  const [newLabel, setNewLabel] = useState('');

  const fetchAccounts = () => {
    fetch(`http://localhost:${activePort}/api/accounts`)
      .then(r => r.json())
      .then(d => {
        setAccounts(d.accounts || []);
        setPlatforms(d.platforms || {});
        setLoading(false);
      })
      .catch(() => setLoading(false));
  };

  useEffect(() => { fetchAccounts(); }, [activePort]);

  const handleAdd = () => {
    setAdding(true);
    const p = platforms[newPlatform] || {};
    fetch(`http://localhost:${activePort}/api/accounts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        platform: newPlatform,
        label: newLabel || `${p.name || newPlatform} account`,
        credentials: {},
      }),
    })
      .then(r => r.json())
      .then(d => {
        addLog(`[Accounts]: ${p.name || newPlatform} connected (id: ${d.id})`);
        setNewLabel('');
        fetchAccounts();
      })
      .catch(e => addLog(`[Accounts]: Error — ${e.message}`))
      .finally(() => setAdding(false));
  };

  const handleRemove = (id, label) => {
    fetch(`http://localhost:${activePort}/api/accounts/${id}`, { method: 'DELETE' })
      .then(() => {
        addLog(`[Accounts]: Removed ${label}`);
        fetchAccounts();
      })
      .catch(e => addLog(`[Accounts]: Error — ${e.message}`));
  };

  const handleSync = (id) => {
    fetch(`http://localhost:${activePort}/api/accounts/${id}/sync`, { method: 'POST' })
      .then(r => r.json())
      .then(d => {
        addLog(`[Accounts]: Synced — ${d.status}`);
        fetchAccounts();
      })
      .catch(e => addLog(`[Accounts]: Error — ${e.message}`));
  };

  const formatTime = (ts) => {
    if (!ts) return 'never';
    return new Date(ts * 1000).toLocaleString();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ backgroundColor: 'rgba(0,0,0,0.6)' }}>
      <div className="w-full max-w-lg max-h-[85vh] flex flex-col rounded-xl overflow-hidden" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
        <div className="flex justify-between items-center px-5 py-3" style={{ borderBottom: '1px solid var(--border)' }}>
          <h2 className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>🔗 Connected Accounts</h2>
          <button onClick={onClose} className="text-lg leading-none" style={{ color: 'var(--text-muted)' }}>✕</button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-3">
          {loading && <p className="text-xs" style={{ color: 'var(--text-muted)' }}>Loading...</p>}

          {accounts.length === 0 && !loading && (
            <p className="text-xs py-8 text-center" style={{ color: 'var(--text-muted)' }}>
              No accounts connected yet. Add your first account below.
            </p>
          )}

          {accounts.map(acc => (
            <div key={acc.id} className="p-3 rounded-lg flex items-center justify-between" style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}>
              <div className="flex items-center gap-3">
                <span className="text-lg">{acc.icon}</span>
                <div>
                  <div className="text-xs font-bold" style={{ color: 'var(--text-primary)' }}>{acc.label}</div>
                  <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                    {acc.platform_name} · {acc.auth_type}
                    {acc.status === 'expired' && <span className="ml-2" style={{ color: '#ef4444' }}>(expired)</span>}
                  </div>
                  <div className="text-[9px]" style={{ color: 'var(--text-muted)' }}>synced: {formatTime(acc.last_sync)}</div>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full ${acc.status === 'connected' ? 'bg-emerald-500' : 'bg-red-500'}`} />
                <button onClick={() => handleSync(acc.id)} className="text-[9px] px-2 py-1 rounded" style={{ backgroundColor: 'var(--bg-secondary)', color: 'var(--text-muted)' }}>sync</button>
                <button onClick={() => handleRemove(acc.id, acc.label)} className="text-[9px] px-2 py-1 rounded" style={{ backgroundColor: 'color-mix(in srgb, #ef4444 15%, transparent)', color: '#ef4444' }}>✕</button>
              </div>
            </div>
          ))}

          <hr className="border-[var(--border)] my-4" />

          <div className="text-xs font-bold mb-2" style={{ color: 'var(--text-primary)' }}>➕ Add Account</div>
          <div className="flex gap-2">
            <select
              value={newPlatform}
              onChange={e => setNewPlatform(e.target.value)}
              className="flex-1 bg-[var(--bg-secondary)] border border-[var(--border)] rounded-lg px-3 py-2 text-xs focus:outline-none"
              style={{ color: 'var(--text-primary)' }}
            >
              {Object.entries(platforms).map(([k, v]) => (
                <option key={k} value={k}>{v.icon} {v.name} ({v.auth_type})</option>
              ))}
            </select>
            <input
              type="text"
              placeholder="Label (optional)"
              value={newLabel}
              onChange={e => setNewLabel(e.target.value)}
              className="flex-1 bg-[var(--bg-secondary)] border border-[var(--border)] rounded-lg px-3 py-2 text-xs focus:outline-none"
              style={{ color: 'var(--text-primary)' }}
            />
            <button
              onClick={handleAdd}
              disabled={adding}
              className="px-4 py-2 rounded-lg text-xs font-bold"
              style={{ backgroundColor: 'var(--accent)', color: '#fff', opacity: adding ? 0.6 : 1 }}
            >
              {adding ? '...' : 'Connect'}
            </button>
          </div>
          <p className="text-[9px] mt-1" style={{ color: 'var(--text-muted)' }}>
            Note: API keys and OAuth tokens are stored locally in studio_config.json. Add keys via Settings → API Keys for providers.
          </p>
        </div>

        <div className="px-5 py-3 flex justify-end" style={{ borderTop: '1px solid var(--border)', backgroundColor: 'var(--bg-secondary)' }}>
          <button onClick={onClose} className="px-4 py-1.5 rounded-lg text-xs font-medium" style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
