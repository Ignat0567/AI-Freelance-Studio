import React, { useState, useEffect, useRef } from 'react';

export default function InfoModal({ activePort, onClose, addLog, appVersion = '', embedded = false }) {
  const scrollBodyRef = useRef(null);
  const checkingRef = useRef(false);
  const checkControllerRef = useRef(null);
  const [components, setComponents] = useState([]);
  const [results, setResults] = useState(null);
  const [summary, setSummary] = useState(null);
  const [checkError, setCheckError] = useState('');
  const [actionMessage, setActionMessage] = useState('');
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(false);
  const [showDiagnostics, setShowDiagnostics] = useState(false);

  useEffect(() => {
    setLoading(true);
    fetch(`http://localhost:${activePort}/api/system/requirements`)
      .then(r => r.json())
      .then(d => setComponents(uniqueRows(d.components || [])))
      .catch(() => setComponents([]))
      .finally(() => setLoading(false));
  }, [activePort]);

  useEffect(() => {
    if (embedded) scrollBodyRef.current?.focus({ preventScroll: true });
  }, [embedded]);

  useEffect(() => () => checkControllerRef.current?.abort(), []);

  const uniqueRows = (rows) => {
    const byId = new Map();
    rows.forEach(row => {
      if (row?.id && !byId.has(row.id)) byId.set(row.id, row);
    });
    return Array.from(byId.values());
  };

  const mergeResults = (incoming) => {
    const checked = new Map(uniqueRows(incoming).map(row => [row.id, row]));
    const base = uniqueRows(components).map(component => ({ ...component, ...checked.get(component.id) }));
    const known = new Set(base.map(row => row.id));
    return [...base, ...uniqueRows(incoming).filter(row => !known.has(row.id))];
  };

  const fallbackSummary = (rows, durationMs) => ({
    total: rows.length,
    installed: rows.filter(row => row.status === 'Installed').length,
    missing: rows.filter(row => row.status === 'Missing').length,
    error: rows.filter(row => row.status === 'Error').length,
    restart_required: rows.filter(row => row.status === 'Restart required').length,
    duration_ms: Math.round(durationMs),
  });

  const runCheck = async () => {
    if (checkingRef.current) return;
    checkingRef.current = true;
    setChecking(true);
    setCheckError('');
    setSummary(null);
    setResults(uniqueRows(components).map(component => ({ ...component, status: 'Checking' })));
    const controller = new AbortController();
    checkControllerRef.current = controller;
    const timeoutMs = window.__CHECK_ALL_TIMEOUT_MS || 20_000;
    const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
    const started = performance.now();
    try {
      const response = await fetch(`http://localhost:${activePort}/api/system/check`, {
        method: 'POST',
        signal: controller.signal,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || 'System checks could not be completed.');
      const rows = mergeResults(data.results || []);
      setResults(rows);
      setSummary(data.summary || fallbackSummary(rows, performance.now() - started));
    } catch (error) {
      const message = error.name === 'AbortError'
        ? 'System checks timed out. You can try again.'
        : error.message || 'System checks could not be completed.';
      setCheckError(message);
      setResults(current => (current || []).map(row => row.status === 'Checking'
        ? { ...row, status: 'Error', error_code: error.name === 'AbortError' ? 'client_timeout' : 'backend_error', message }
        : row));
      setSummary(current => current || fallbackSummary(
        uniqueRows(components).map(component => ({ ...component, status: 'Error' })),
        performance.now() - started,
      ));
      addLog(`[Info]: ${message}`);
    } finally {
      window.clearTimeout(timeout);
      checkControllerRef.current = null;
      checkingRef.current = false;
      setChecking(false);
    }
  };

  const openOfficialAction = async (componentId) => {
    setActionMessage('');
    if (!window.env?.openOfficialDownload) {
      setActionMessage('Official component pages can only be opened from the installed desktop application.');
      return;
    }
    const result = await window.env.openOfficialDownload(componentId);
    setActionMessage(result?.status === 'opened'
      ? 'Official component page opened in your system browser.'
      : result?.message || 'The official component page could not be opened.');
  };

  const statusIcon = (status) => {
    if (status === 'Installed') return 'OK';
    if (status === 'Checking') return '...';
    if (status === 'Restart required') return '!';
    if (status === 'Missing') return '-';
    return 'x';
  };

  const statusColor = (status) => {
    if (status === 'Installed') return 'var(--success)';
    if (status === 'Checking') return 'var(--accent)';
    if (status === 'Missing' || status === 'Restart required') return 'var(--warning)';
    if (status === 'Error') return 'var(--danger)';
    return 'var(--text-muted)';
  };

  const statusLabel = (status) => status === 'Installed' ? 'Detected' : status || 'Not checked';
  const displayRows = uniqueRows(results || components);

  return (
    <div className={embedded ? "info-inline" : "fixed inset-0 z-50 flex items-center justify-center p-4"} style={embedded ? undefined : { backgroundColor: 'rgba(0,0,0,0.6)' }}>
      <div className={embedded ? "info-modal-container info-inline-container w-full rounded-xl" : "info-modal-container w-full max-w-2xl rounded-xl"} style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
        <div className="info-modal-header flex justify-between items-center px-5 py-3" style={{ borderBottom: '1px solid var(--border)' }}>
          <h2 className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>Info</h2>
          {!embedded && <button onClick={onClose} className="text-lg leading-none" style={{ color: 'var(--text-muted)' }}>x</button>}
        </div>

        <div ref={scrollBodyRef} role="region" tabIndex={0} aria-label="Info content" className="info-inline-body flex-1 overflow-y-auto p-5 space-y-4">
          <div className="rounded-xl p-4 space-y-3" style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}>
            <div>
              <h3 className="text-sm font-bold mb-1" style={{ color: 'var(--text-primary)' }}>AI FreelancerStudio</h3>
              <p data-testid="app-version" className="text-xs font-mono mb-2" style={{ color: 'var(--text-muted)' }}>
                {appVersion ? `Version ${appVersion}` : 'Version unavailable'}
              </p>
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
              style={{ backgroundColor: 'var(--accent)', color: 'var(--accent-contrast)', opacity: checking ? 0.6 : 1 }}
            >
              {checking ? 'Checking...' : 'Check All Components'}
            </button>
          </div>}

          {showDiagnostics && actionMessage && (
            <div role="status" className="rounded-lg p-3 text-xs" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
              {actionMessage}
            </div>
          )}

          {showDiagnostics && checkError && (
            <div role="alert" className="rounded-lg p-3 text-xs" style={{ backgroundColor: 'color-mix(in srgb, var(--danger) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--danger) 30%, transparent)', color: 'var(--danger)' }}>
              {checkError}
            </div>
          )}

          {showDiagnostics && summary && (
            <div data-testid="check-summary" className="rounded-lg p-3 text-xs" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
              Installed: <b>{summary.installed || 0}</b> | Missing: <b>{summary.missing || 0}</b> | Errors: <b>{summary.error || 0}</b> | Restart required: <b>{summary.restart_required || 0}</b> | Completed in <b>{summary.duration_ms || 0} ms</b>
            </div>
          )}

          {showDiagnostics && (
            <div className="space-y-2">
              {displayRows.map(r => {
                const status = r.status || 'Not checked';
                const category = r.category || (r.required ? 'Required' : 'Optional');
                const categoryColor = category === 'Required' ? 'var(--danger)' : category === 'Recommended' ? 'var(--accent)' : 'var(--text-muted)';
                const actionLabel = status === 'Installed' ? 'Recheck'
                  : status === 'Restart required' ? 'Detect Again'
                    : status === 'Error' ? 'Recheck'
                      : status === 'Not checked' ? 'Detect'
                        : r.action_label || 'Install Instructions';
                const action = ['Installed', 'Restart required', 'Error', 'Not checked'].includes(status)
                  ? runCheck
                  : () => openOfficialAction(r.id);
                return (
                <div key={r.id} data-component-id={r.id} data-status={status} className="p-3 rounded-lg text-xs" style={{ backgroundColor: 'var(--bg-card)', border: `1px solid color-mix(in srgb, ${statusColor(status)} 25%, transparent)` }}>
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2">
                      <span aria-hidden="true" style={{ color: statusColor(status) }}>{statusIcon(status)}</span>
                      <span className="font-bold" style={{ color: 'var(--text-primary)' }}>{r.name}</span>
                      <span className="px-1.5 py-0.5 rounded text-[9px] font-medium" style={{ backgroundColor: `color-mix(in srgb, ${categoryColor} 15%, transparent)`, color: categoryColor }}>{category}</span>
                    </div>
                    <span className="font-semibold" style={{ color: statusColor(status) }}>{statusLabel(status)}</span>
                  </div>
                  <p className="font-mono mb-1" style={{ color: 'var(--text-secondary)' }}>Version: {r.version || 'Not detected'}</p>
                  <p className="mb-1" style={{ color: 'var(--text-dim)' }}>{r.description}</p>
                  {(r.message || status === 'Restart required') && <p className="mb-1" style={{ color: statusColor(status) }}>{r.message || 'Restart Studio, then Detect Again.'}</p>}
                  {Number.isFinite(r.duration_ms) && <p className="font-mono" style={{ color: 'var(--text-muted)' }}>Checked in {r.duration_ms} ms</p>}
                  {r.installed && !r.up_to_date && r.min_version && (
                    <p className="mt-1" style={{ color: 'var(--warning)' }}>Update needed: minimum {r.min_version}</p>
                  )}
                  <div className="flex gap-2 mt-2">
                    {status !== 'Checking' && (
                      <button
                        onClick={action}
                        disabled={checking}
                        className="px-3 py-1 rounded text-[10px] font-medium transition-all"
                        style={{
                          backgroundColor: status === 'Missing' ? 'var(--accent)' : 'var(--bg-secondary)',
                          color: status === 'Missing' ? 'var(--accent-contrast)' : 'var(--text-secondary)',
                          border: status === 'Missing' ? 'none' : '1px solid var(--border)',
                        }}
                      >
                         {actionLabel}
                      </button>
                    )}
                  </div>
                </div>
              );})}
            </div>
          )}
        </div>

        <div className="info-modal-footer px-5 py-3 flex justify-between items-center" style={{ borderTop: '1px solid var(--border)', backgroundColor: 'var(--bg-secondary)' }}>
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
