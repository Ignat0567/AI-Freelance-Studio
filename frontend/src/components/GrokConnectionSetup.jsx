import React, { useEffect, useState } from 'react';

const endpoint = (port, path) => `http://localhost:${port}${path}`;

export default function GrokConnectionSetup({ activePort, addLog, onSaved }) {
  const [connections, setConnections] = useState([]);
  const [draft, setDraft] = useState({ name: 'My Grok', configured_model: 'grok/grok-4.6', executable_path: '' });
  const [detected, setDetected] = useState(null);
  const [tested, setTested] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const load = () => fetch(endpoint(activePort, '/api/provider-connections')).then(r => r.json()).then(d => setConnections(d.connections || [])).catch(() => setMessage('Provider connections are unavailable.'));
  const applyDetection = (result, announce = true) => {
    setTested(null); setDetected(result);
    if (result.executable_path) setDraft(current => ({ ...current, executable_path: result.executable_path }));
    if (announce) setMessage(result.status === 'detected' ? `Grok CLI ${result.version || ''} detected.` : 'Grok CLI was not found.');
  };
  useEffect(() => {
    load();
    fetch(endpoint(activePort, '/api/provider-connections/grok/detect'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(draft) })
      .then(response => response.json()).then(result => applyDetection(result, false)).catch(() => setMessage('Grok detection is unavailable.'));
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
  const detect = async () => { const result = await request('/api/provider-connections/grok/detect'); if (result) applyDetection(result); };
  const test = async () => { const result = await request('/api/provider-connections/grok/test'); if (result) { setTested(result); setMessage(result.message || (result.ready ? 'Grok connection is ready.' : 'Connection test did not pass.')); } };
  const save = async () => {
    if (!tested?.ready) { setMessage('A successful Test Connection is required before saving.'); return; }
    setBusy(true);
    try {
      const response = await fetch(endpoint(activePort, '/api/provider-connections/grok'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(draft) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail?.message || result.detail || 'Save failed');
      setMessage('Grok connection saved. The grok.com login stays in the official CLI; Studio did not store a key.'); addLog?.('[Grok]: Subscription connection saved for planners, Elena, and BugCatcher.'); await load(); onSaved?.(result.connection);
    } catch (error) { setMessage(error.message); } finally { setBusy(false); }
  };
  const models = tested?.available_models || detected?.available_models || [];
  const grokMissing = detected && !detected.executable_path;
  const notLoggedIn = tested && tested.ready === false && tested.error_code === 'grok_not_logged_in';
  return <section className="provider-setup">
    <div className="provider-heading"><div><strong>Grok Subscription (planners, Elena, BugCatcher)</strong><p>Uses the same <code>grok</code> command as PowerShell — your grok.com login, not a paid xAI API key. Studio never stores the session. Grok writes briefs and coding specs; local Ollama writes the project files.</p></div><span className="connection-state">{tested?.ready ? 'Ready' : tested ? 'Not ready' : 'Not tested'}</span></div>
    <div className="provider-form"><label>Connection name<input value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} /></label><label>Model<select value={draft.configured_model} onChange={e => { setDraft({ ...draft, configured_model: e.target.value }); setTested(null); }}>{[draft.configured_model, ...models.map(m => m.id || m).filter(m => m !== draft.configured_model)].filter(Boolean).map(model => <option key={model}>{model}</option>)}</select></label></div>
    <div className="capability-grid dependency-status"><span>Grok CLI <b>{detected?.executable_path ? 'Installed' : 'Missing'}</b></span><span>Login <b>{tested?.ready ? 'Logged in' : tested ? 'Not logged in' : 'Not tested'}</b></span></div>
    <div className="provider-actions"><button type="button" onClick={detect} disabled={busy}>Detect Again</button><button type="button" onClick={test} disabled={busy || grokMissing}>Test Connection</button><button type="button" className="primary" onClick={save} disabled={busy || !tested?.ready}>Save Connection</button></div>
    {grokMissing && <p className="provider-message">Install Grok Build TUI so the same `grok` command you use in PowerShell is on PATH, then select Detect Again.</p>}
    {notLoggedIn && <p className="provider-message">Run `grok login` in a terminal, then select Test Connection again.</p>}
    {(detected || tested) && <div className="capability-grid"><span>Transport <b>grok --prompt-file (plan mode)</b></span><span>Authentication <b>Official CLI-owned grok.com login</b></span><span>Account <b>{tested?.account || '-'}</b></span><span>Version <b>{detected?.version || '-'}</b></span></div>}
    {message && <p className="provider-message" role="status">{message}</p>}
    {connections.filter(c => c.connection_type === 'grok_subscription').map(c => <div className="saved-connection" key={c.connection_id}><strong>{c.name || c.display_name}</strong><span>{c.readiness_status === 'ready' ? 'Ready and saved' : 'Retest required'}</span><code>{c.configured_model || 'No model selected'}</code></div>)}
  </section>;
}
