import React, { useEffect, useState } from 'react';

const endpoint = (port, path) => `http://localhost:${port}${path}`;

export default function ClaudeCodeConnectionSetup({ activePort, addLog, onSaved }) {
  const [connections, setConnections] = useState([]);
  const [draft, setDraft] = useState({ name: 'My Claude Code', configured_model: 'claude/default', executable_path: '' });
  const [detected, setDetected] = useState(null);
  const [tested, setTested] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const load = () => fetch(endpoint(activePort, '/api/provider-connections')).then(r => r.json()).then(d => setConnections(d.connections || [])).catch(() => setMessage('Provider connections are unavailable.'));
  const applyDetection = (result, announce = true) => {
    setTested(null); setDetected(result);
    if (result.executable_path) setDraft(current => ({ ...current, executable_path: result.executable_path }));
    if (announce) setMessage(result.dependencies?.restart_message || (result.status === 'detected' ? `Claude Code ${result.version || ''} detected.` : 'Claude Code executable was not found.'));
  };
  useEffect(() => {
    load();
    fetch(endpoint(activePort, '/api/provider-connections/claude/detect'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(draft) })
      .then(response => response.json()).then(result => applyDetection(result, false)).catch(() => setMessage('Dependency detection is unavailable.'));
  }, [activePort]);
  const request = async (path) => {
    setBusy(true); setMessage('');
    try {
      const response = await fetch(endpoint(activePort, path), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(draft) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail?.message || result.detail || 'Request failed');
      return result;
    } catch (error) { setMessage(error.message); return null; } finally { setBusy(false); }
  };
  const detect = async () => { const result = await request('/api/provider-connections/claude/detect'); if (result) applyDetection(result); };
  const openOfficialDownload = async (downloadId) => {
    if (!window.env?.openOfficialDownload) { setMessage('Official downloads can only be opened from the installed desktop application.'); return; }
    const result = await window.env.openOfficialDownload(downloadId);
    if (result?.status !== 'opened') { setMessage(result?.message || 'The official download page could not be opened.'); return; }
    setMessage(downloadId === 'nodejs' ? 'Install the Windows LTS version, then return to Studio and select Detect Again.' : 'Install the Claude Code CLI with npm i -g @anthropic-ai/claude-code, run `claude auth login`, then return to Studio and select Detect Again.');
  };
  const test = async () => { const result = await request('/api/provider-connections/claude/test'); if (result) { setTested(result); setMessage(result.message || (result.ready ? 'Claude Code connection is ready.' : 'Connection test did not pass.')); } };
  const repair = async () => { const result = await request('/api/provider-connections/claude/test'); if (result) { setTested(result); setMessage(result.ready ? 'Repair complete. Claude Code CLI is installed and logged in.' : (result.message || 'Repair needs your action.')); } };
  const save = async () => {
    if (!tested?.ready) { setMessage('A successful Test Connection is required before saving.'); return; }
    setBusy(true);
    try {
      const response = await fetch(endpoint(activePort, '/api/provider-connections/claude'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(draft) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail?.message || result.detail || 'Save failed');
      setMessage('Claude Code connection saved. No Anthropic credential was requested or stored.'); addLog?.('[Claude Code]: Authenticated bridge connection saved.'); await load(); onSaved?.(result.connection);
    } catch (error) { setMessage(error.message); } finally { setBusy(false); }
  };
  const models = tested?.available_models || detected?.available_models || [];
  const dependencies = detected?.dependencies?.components || {};
  const nodeMissing = dependencies.node?.installed === false || dependencies.npm?.installed === false;
  const claudeMissing = dependencies.node?.installed === true && dependencies.npm?.installed === true && dependencies.claude?.installed === false;
  const notLoggedIn = tested && tested.ready === false && tested.error_code === 'claude_code_not_logged_in';
  return <section className="provider-setup">
    <div className="provider-heading"><div><strong>Claude Code Authenticated Bridge</strong><p>The official Claude Code CLI owns authentication (`claude auth login`). FreelancerStudio reads only the logged-in status and never requests, copies, or stores your Anthropic credentials.</p></div><span className="connection-state">{tested?.ready ? 'Ready' : tested ? 'Not ready' : 'Not tested'}</span></div>
    <div className="provider-form"><label>Connection name<input value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} /></label><label>Model<select value={draft.configured_model} onChange={e => { setDraft({ ...draft, configured_model: e.target.value }); setTested(null); }}>{[draft.configured_model, ...models.map(m => m.id || m).filter(m => m !== draft.configured_model)].filter(Boolean).map(model => <option key={model}>{model}</option>)}</select></label></div>
    <div className="capability-grid dependency-status"><span>Node.js <b>{dependencies.node?.installed ? 'Installed' : 'Missing'}</b></span><span>npm <b>{dependencies.npm?.installed ? 'Installed' : 'Missing'}</b></span><span>Claude Code <b>{dependencies.claude?.installed ? 'Installed' : 'Missing'}</b></span></div>
    <div className="provider-actions">{nodeMissing && <button type="button" onClick={() => openOfficialDownload('nodejs')} disabled={busy}>Download Node.js</button>}{claudeMissing && <button type="button" onClick={() => openOfficialDownload('claude-code')} disabled={busy}>Download Claude Code</button>}<button type="button" onClick={detect} disabled={busy}>Detect Again</button><button type="button" onClick={repair} disabled={busy || !dependencies.claude?.installed}>Repair automatically</button><button type="button" onClick={test} disabled={busy || !dependencies.claude?.installed}>Test Connection</button><button type="button" className="primary" onClick={save} disabled={busy || !tested?.ready}>Save Connection</button></div>
    {nodeMissing && <p className="provider-message">Node.js is required to install the Claude Code CLI with npm. Download the official Windows LTS installer, install it, restart Studio, and select Detect Again.</p>}
    {claudeMissing && <p className="provider-message">Install the Claude Code CLI (npm i -g @anthropic-ai/claude-code), then return to Studio and select Detect Again.</p>}
    {notLoggedIn && <p className="provider-message">Run `claude auth login` in a terminal to sign in with your Claude subscription, then select Test Connection again.</p>}
    {detected?.dependencies?.restart_recommended && <p className="provider-message">{detected.dependencies.restart_message}</p>}
    {(detected || tested) && <div className="capability-grid"><span>Transport <b>claude -p (stdin)</b></span><span>Authentication <b>Official CLI-owned OAuth</b></span><span>Account <b>{tested?.account || '-'}</b></span><span>Version <b>{detected?.version || '-'}</b></span><span>Last checked <b>{tested?.connection?.last_checked_at || '-'}</b></span></div>}
    {message && <p className="provider-message" role="status">{message}</p>}
    {connections.filter(c => c.connection_type === 'claude_subscription').map(c => <div className="saved-connection" key={c.connection_id}><strong>{c.name || c.display_name}</strong><span>{c.readiness_status === 'ready' ? 'Ready and saved' : 'Retest required'}</span><code>{c.configured_model || 'No model selected'}</code></div>)}
  </section>;
}
