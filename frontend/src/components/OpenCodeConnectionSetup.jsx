import React, { useEffect, useState } from 'react';

const endpoint = (port, path) => `http://localhost:${port}${path}`;

function capabilityLabel(capabilities, name) {
  const state = capabilities?.[name]?.status || 'unknown';
  return state === 'supported' ? 'Supported' : state === 'unsupported' ? 'Unsupported' : 'Unknown';
}

export default function OpenCodeConnectionSetup({ activePort, addLog, onSaved }) {
  const [connections, setConnections] = useState([]);
  const [draft, setDraft] = useState({ name: 'My OpenCode', configured_model: 'openai/gpt-5.5', transport_type: 'cli', executable_path: '' });
  const [detected, setDetected] = useState(null);
  const [tested, setTested] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const load = () => fetch(endpoint(activePort, '/api/provider-connections')).then(r => r.json()).then(d => setConnections(d.connections || [])).catch(() => setMessage('Provider connections are unavailable.'));
  useEffect(() => { load(); }, [activePort]);
  const request = async (path) => {
    setBusy(true); setMessage('');
    try {
      const response = await fetch(endpoint(activePort, path), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(draft) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Request failed');
      return result;
    } catch (error) { setMessage(error.message); return null; } finally { setBusy(false); }
  };
  const detect = async () => { const result = await request('/api/provider-connections/opencode/detect'); if (result) { setDetected(result); if (result.executable_path) setDraft(d => ({ ...d, executable_path: result.executable_path })); setMessage(result.status === 'detected' ? `OpenCode ${result.version || ''} detected.` : 'OpenCode executable was not found.'); } };
  const test = async () => { const result = await request('/api/provider-connections/opencode/test'); if (result) { setTested(result); setMessage(result.status === 'ok' ? 'Text and image probes completed.' : 'Connection test did not pass.'); } };
  const save = async () => {
    if (!tested?.connection) { setMessage('Detect and test the connection before saving.'); return; }
    setBusy(true);
    try {
      const payload = { ...draft, capabilities: tested.connection.capabilities, last_checked_at: tested.connection.last_checked_at };
      const response = await fetch(endpoint(activePort, '/api/provider-connections/opencode'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Save failed');
      setMessage('OpenCode connection saved. No OpenCode secret was requested or stored.'); addLog?.('[OpenCode]: Authenticated bridge connection saved.'); await load(); onSaved?.(result.connection);
    } catch (error) { setMessage(error.message); } finally { setBusy(false); }
  };
  const models = detected?.available_models || tested?.available_models || [];
  const caps = tested?.connection?.capabilities || tested?.capabilities || detected?.capabilities;
  return <section className="provider-setup">
    <div className="provider-heading"><div><strong>OpenCode Authenticated Bridge</strong><p>OpenCode owns its OAuth session. FreelancerStudio never requests, copies, or stores an OpenCode token.</p></div><span className="connection-state">{tested?.health_status === 'available_authenticated' ? 'Connected' : 'Not tested'}</span></div>
    <div className="provider-form"><label>Connection name<input value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} /></label><label>Model<select value={draft.configured_model} onChange={e => setDraft({ ...draft, configured_model: e.target.value })}>{[draft.configured_model, ...models.filter(m => m !== draft.configured_model)].filter(Boolean).map(model => <option key={model}>{model}</option>)}</select></label></div>
    <div className="provider-actions"><button type="button" onClick={detect} disabled={busy}>Detect OpenCode</button><button type="button" onClick={test} disabled={busy || !draft.configured_model}>Test Connection</button><button type="button" className="primary" onClick={save} disabled={busy || !tested}>Save Connection</button></div>
    {(detected || tested) && <div className="capability-grid"><span>Transport <b>opencode run</b></span><span>Authentication <b>Existing OpenCode OAuth Session</b></span><span>Version <b>{detected?.version || '-'}</b></span><span>Text <b>{capabilityLabel(caps, 'text_input')}</b></span><span>Images <b>{capabilityLabel(caps, 'image_input')}{caps?.image_input?.status === 'supported' ? ' and Proven' : ''}</b></span><span>Structured output <b>Strict JSON Validation</b></span><span>Streaming <b>Unsupported</b></span><span>Last checked <b>{tested?.last_checked_at || '-'}</b></span></div>}
    {message && <p className="provider-message" role="status">{message}</p>}
    {connections.filter(c => c.connection_type === 'opencode_bridge').map(c => <div className="saved-connection" key={c.connection_id}><strong>{c.name}</strong><span>{c.capabilities?.image_input?.status === 'supported' ? 'Connected, images proven' : 'Saved, image proof required'}</span><code>{c.configured_model || 'No model selected'}</code></div>)}
  </section>;
}
