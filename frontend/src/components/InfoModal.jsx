import React, { useState, useEffect } from 'react';

const COLORS = {
  ok: '#10b981',
  warn: '#f59e0b',
  fail: '#ef4444',
  muted: '#64748b',
};

export default function InfoModal({ activePort, onClose, addLog, embedded = false }) {
  const [components, setComponents] = useState([]);
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(false);
  const [installing, setInstalling] = useState(null);
  const [showDiagnostics, setShowDiagnostics] = useState(false);

  useEffect(() => {
    setLoading(true);
    fetch(`http://localhost:${activePort}/api/system/requirements`)
      .then(r => r.json())
      .then(d => setComponents(d.components || []))
      .catch(() => setComponents([]))
      .finally(() => setLoading(false));
  }, [activePort]);

  const runCheck = () => {
    setChecking(true);
    setResults(null);
    fetch(`http://localhost:${activePort}/api/system/check`, { method: 'POST' })
      .then(r => r.json())
      .then(d => setResults(d.results || []))
      .catch(() => addLog('[Info]: Check failed'))
      .finally(() => setChecking(false));
  };

  const runInstall = (compId) => {
    setInstalling(compId);
    fetch(`http://localhost:${activePort}/api/system/install/${compId}`, { method: 'POST' })
      .then(r => r.json())
      .then(d => {
        addLog(`[Info]: ${compId} — ${d.message}`);
        runCheck();
      })
      .catch(e => addLog(`[Info]: Install error — ${e.message}`))
      .finally(() => setInstalling(null));
  };

  const statusIcon = (ok, upToDate) => {
    if (ok && upToDate) return '✅';
    if (ok && !upToDate) return '⚠️';
    return '❌';
  };

  const statusColor = (ok, upToDate) => {
    if (ok && upToDate) return COLORS.ok;
    if (ok && !upToDate) return COLORS.warn;
    return COLORS.fail;
  };

  return (
    <div className={embedded ? "info-inline" : "fixed inset-0 z-50 flex items-center justify-center p-4"} style={embedded ? undefined : { backgroundColor: 'rgba(0,0,0,0.6)' }}>
      <div className={embedded ? "info-inline-container w-full flex flex-col rounded-xl overflow-hidden" : "w-full max-w-2xl max-h-[85vh] flex flex-col rounded-xl overflow-hidden"} style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
        <div className="flex justify-between items-center px-5 py-3" style={{ borderBottom: '1px solid var(--border)' }}>
          <h2 className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>Info</h2>
          {!embedded && <button onClick={onClose} className="text-lg leading-none" style={{ color: 'var(--text-muted)' }}>x</button>}
        </div>

        <div className="info-inline-body flex-1 overflow-y-auto p-5 space-y-4">
          <div className="rounded-xl p-4 space-y-3" style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}>
            <div>
              <h3 className="text-sm font-bold mb-1" style={{ color: 'var(--text-primary)' }}>AI FreelancerStudio v1.0</h3>
              <p className="text-xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                A local AI production studio for turning project ideas and freelance tasks into generated software projects.
                It coordinates planning, design, coding, review, QA, project files, credentials, and delivery tools in one workspace.
              </p>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
              <div className="rounded-lg p-3" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
                <div className="font-semibold mb-1" style={{ color: 'var(--text-primary)' }}>Workspace</div>
                <div style={{ color: 'var(--text-muted)' }}>Local project generation, completed project access, file browser, GitHub export, and resume support.</div>
              </div>
              <div className="rounded-lg p-3" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
                <div className="font-semibold mb-1" style={{ color: 'var(--text-primary)' }}>AI Staff</div>
                <div style={{ color: 'var(--text-muted)' }}>Configurable agents for planning, design, coding, QA, security, review, finance, and project assistance.</div>
              </div>
              <div className="rounded-lg p-3" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
                <div className="font-semibold mb-1" style={{ color: 'var(--text-primary)' }}>Privacy</div>
                <div style={{ color: 'var(--text-muted)' }}>Settings are stored locally. API keys are not saved in Studio settings; set the matching provider environment variable to persist a key.</div>
              </div>
              <div className="rounded-lg p-3" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
                <div className="font-semibold mb-1" style={{ color: 'var(--text-primary)' }}>Runtime</div>
                <div style={{ color: 'var(--text-muted)' }}>Backend port: {activePort}. Platform: {navigator.platform}.</div>
              </div>
            </div>
          </div>

          <button
            onClick={() => setShowDiagnostics(prev => !prev)}
            className="px-4 py-2 rounded-lg text-xs font-bold transition-all"
            style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
          >
            {showDiagnostics ? 'Hide Diagnostics' : 'Show Diagnostics'}
          </button>

          {showDiagnostics && loading && <p className="text-xs" style={{ color: 'var(--text-muted)' }}>Loading components...</p>}

          {showDiagnostics && <div className="flex gap-2 mb-4">
            <button
              onClick={runCheck}
              disabled={checking}
              className="px-4 py-2 rounded-lg text-xs font-bold transition-all"
              style={{ backgroundColor: 'var(--accent)', color: '#fff', opacity: checking ? 0.6 : 1 }}
            >
              {checking ? 'Checking...' : 'Check All Components'}
            </button>
          </div>}

          {showDiagnostics && results ? (
            <div className="space-y-2">
              {results.map(r => (
                <div key={r.id} className="p-3 rounded-lg text-xs" style={{ backgroundColor: 'var(--bg-card)', border: `1px solid ${statusColor(r.installed, r.up_to_date)}40` }}>
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2">
                      <span>{statusIcon(r.installed, r.up_to_date)}</span>
                      <span className="font-bold" style={{ color: 'var(--text-primary)' }}>{r.name}</span>
                      {r.required && <span className="px-1.5 py-0.5 rounded text-[9px] font-medium" style={{ backgroundColor: 'color-mix(in srgb, #ef4444 15%, transparent)', color: '#ef4444' }}>required</span>}
                    </div>
                    {r.version && <span className="font-mono" style={{ color: 'var(--text-secondary)' }}>{r.version}</span>}
                  </div>
                  <p className="mb-1" style={{ color: 'var(--text-dim)' }}>{r.description}</p>
                  {r.path && <p className="font-mono" style={{ color: 'var(--text-muted)' }}>{r.path}</p>}
                  {r.installed && !r.up_to_date && r.min_version && (
                    <p className="mt-1" style={{ color: COLORS.warn }}>Update needed: minimum {r.min_version}</p>
                  )}
                  <div className="flex gap-2 mt-2">
                    {!r.installed && (
                      <button
                        onClick={() => runInstall(r.id)}
                        disabled={installing === r.id}
                        className="px-3 py-1 rounded text-[10px] font-medium transition-all"
                        style={{
                          backgroundColor: r.can_auto_install ? 'var(--accent)' : 'var(--bg-secondary)',
                          color: r.can_auto_install ? '#fff' : 'var(--text-secondary)',
                          border: r.can_auto_install ? 'none' : '1px solid var(--border)',
                          opacity: installing === r.id ? 0.6 : 1,
                        }}
                      >
                         {installing === r.id ? 'Installing...' : r.can_auto_install ? 'Auto Install' : 'Info'}
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ) : showDiagnostics ? (
            <div className="space-y-2">
              {components.map(c => (
                <div key={c.id} className="p-3 rounded-lg text-xs" style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}>
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-bold" style={{ color: 'var(--text-primary)' }}>{c.name}</span>
                    {c.required && <span className="px-1.5 py-0.5 rounded text-[9px] font-medium" style={{ backgroundColor: 'color-mix(in srgb, #ef4444 15%, transparent)', color: '#ef4444' }}>required</span>}
                    {!c.required && <span className="px-1.5 py-0.5 rounded text-[9px] font-medium" style={{ backgroundColor: 'color-mix(in srgb, #64748b 15%, transparent)', color: '#64748b' }}>optional</span>}
                  </div>
                  <p style={{ color: 'var(--text-dim)' }}>{c.description}</p>
                </div>
              ))}
            </div>
          ) : null}
        </div>

        <div className="px-5 py-3 flex justify-between items-center" style={{ borderTop: '1px solid var(--border)', backgroundColor: 'var(--bg-secondary)' }}>
          <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
            AI FreelancerStudio | Local workspace application
          </span>
          {!embedded && <button
            onClick={onClose}
            className="px-4 py-1.5 rounded-lg text-xs font-medium"
            style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
          >
            Close
          </button>}
        </div>
      </div>
    </div>
  );
}
