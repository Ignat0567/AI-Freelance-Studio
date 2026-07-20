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
  const applyDetection = (result, announce = true) => {
    setTested(null); setDetected(result);
    if (result.executable_path) setDraft(current => ({ ...current, executable_path: result.executable_path }));
    if (announce) setMessage(result.dependencies?.restart_message || (result.status === 'detected' ? `OpenCode ${result.version || ''} detected.` : 'OpenCode executable was not found.'));
  };
  useEffect(() => {
    load();
    fetch(endpoint(activePort, '/api/provider-connections/opencode/detect'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(draft) })
      .then(response => response.json()).then(result => applyDetection(result, false)).catch(() => setMessage('Dependency detection is unavailable.'));
  }, [activePort]);
  const request = async (path) => {
    setBusy(true); setMessage('');
    try {
      const response = await fetch(endpoint(activePort, path), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(draft) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Request failed');
      return result;
    } catch (error) { setMessage(error.message); return null; } finally { setBusy(false); }
  };
  const detect = async () => { const result = await request('/api/provider-connections/opencode/detect'); if (result) applyDetection(result); };
  const openOfficialDownload = async (downloadId) => {
    if (!window.env?.openOfficialDownload) { setMessage('Official downloads can only be opened from the installed desktop application.'); return; }
    const result = await window.env.openOfficialDownload(downloadId);
    if (result?.status !== 'opened') { setMessage(result?.message || 'The official download page could not be opened.'); return; }
    setMessage(downloadId === 'nodejs' ? 'Install the Windows LTS version, then return to Studio and select Detect Again.' : 'Install OpenCode using the official Windows download or run npm i -g opencode-ai. Then return to Studio and select Detect Again.');
  };
  const test = async () => { const result = await request('/api/provider-connections/opencode/test'); if (result) { setTested(result); setMessage(result.message || (result.ready ? 'OpenCode connection is ready.' : 'Connection test did not pass.')); } };
  const save = async () => {
    if (!tested?.ready || !tested?.connection) { setMessage('A successful Test Connection is required before saving.'); return; }
    setBusy(true);
    try {
      const payload = { ...draft };
      const response = await fetch(endpoint(activePort, '/api/provider-connections/opencode'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Save failed');
      setMessage('OpenCode connection saved. No OpenCode secret was requested or stored.'); addLog?.('[OpenCode]: Authenticated bridge connection saved.'); await load(); onSaved?.(result.connection);
    } catch (error) { setMessage(error.message); } finally { setBusy(false); }
  };
  const models = tested?.available_models || detected?.available_models || [];
  const caps = tested?.connection?.capabilities || tested?.capabilities || detected?.capabilities;
  const dependencies = detected?.dependencies?.components || {};
  const nodeMissing = dependencies.node?.installed === false || dependencies.npm?.installed === false;
  const opencodeMissing = dependencies.node?.installed === true && dependencies.npm?.installed === true && dependencies.opencode?.installed === false;
  return <section className="provider-setup">
    <div className="provider-heading"><div><strong>OpenCode Authenticated Bridge</strong><p>OpenCode owns provider authentication. FreelancerStudio reads only provider/auth-type labels returned by the CLI and never requests, copies, or stores credentials.</p></div><span className="connection-state">{tested?.ready ? 'Ready' : tested ? 'Not ready' : 'Not tested'}</span></div>
    <div className="provider-form"><label>Connection name<input value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} /></label><label>Model<select value={draft.configured_model} onChange={e => { setDraft({ ...draft, configured_model: e.target.value }); setTested(null); }}>{[draft.configured_model, ...models.filter(m => m !== draft.configured_model)].filter(Boolean).map(model => <option key={model}>{model}</option>)}</select></label></div>
    <div className="capability-grid dependency-status"><span>Node.js <b>{dependencies.node?.installed ? 'Installed' : 'Missing'}</b></span><span>npm <b>{dependencies.npm?.installed ? 'Installed' : 'Missing'}</b></span><span>OpenCode <b>{dependencies.opencode?.installed ? 'Installed' : 'Missing'}</b></span></div>
    <div className="provider-actions">{nodeMissing && <button type="button" onClick={() => openOfficialDownload('nodejs')} disabled={busy}>Download Node.js</button>}{opencodeMissing && <button type="button" onClick={() => openOfficialDownload('opencode')} disabled={busy}>Download OpenCode</button>}<button type="button" onClick={detect} disabled={busy}>Detect Again</button><button type="button" onClick={test} disabled={busy || !draft.configured_model || !dependencies.opencode?.installed}>Test Connection</button><button type="button" className="primary" onClick={save} disabled={busy || !tested?.ready}>Save Connection</button></div>
    {nodeMissing && <p className="provider-message">Node.js is required to install OpenCode with npm. Download the official Windows LTS installer, install it, restart Studio, and select Detect Again.</p>}
    {opencodeMissing && <p className="provider-message">Download OpenCode from the official website or install it with npm. Then return to Studio and select Detect Again.</p>}
    {detected?.dependencies?.restart_recommended && <p className="provider-message">{detected.dependencies.restart_message}</p>}
    {tested?.checks && <div className="capability-grid">{Object.entries(tested.checks).map(([name, check]) => <span key={name}>{name.replace('_', ' ')} <b>{check.status}{check.error_code ? `: ${check.error_code}` : ''}</b></span>)}</div>}
    {(detected || tested) && <div className="capability-grid"><span>Transport <b>opencode run</b></span><span>Authentication <b>OpenCode-owned provider flow</b></span><span>Version <b>{detected?.version || '-'}</b></span><span>Text <b>{capabilityLabel(caps, 'text_input')}</b></span><span>Images <b>{capabilityLabel(caps, 'image_input')}{caps?.image_input?.status === 'supported' ? ' and Proven' : ''}</b></span><span>Structured output <b>Strict JSON Validation</b></span><span>Streaming <b>Unsupported</b></span><span>Last checked <b>{tested?.connection?.last_checked_at || '-'}</b></span></div>}
    {message && <p className="provider-message" role="status">{message}</p>}
    {connections.filter(c => ['opencode_bridge', 'opencode_oauth_bridge'].includes(c.connection_type)).map(c => <div className="saved-connection" key={c.connection_id}><strong>{c.name}</strong><span>{c.readiness_status === 'ready' ? 'Ready and saved' : 'Retest required'}</span><code>{c.configured_model || 'No model selected'}</code></div>)}
  </section>;
}
