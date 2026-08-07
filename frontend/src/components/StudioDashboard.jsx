import React, { useState } from 'react';
import OrderWorkflowPage from '../features/order-workflow/OrderWorkflowPage.jsx';
import SandboxTestLabPage from '../features/sandbox-test-lab/SandboxTestLabPage.jsx';
import KnowledgeBasePage from '../features/knowledge-base/KnowledgeBasePage.jsx';
import MarketplacePage from '../features/marketplace/MarketplacePage.jsx';
import VideoGenerationPage from '../features/video-generation/VideoGenerationPage.jsx';
import PresentationGeneratorPage from '../features/presentation-generator/PresentationGeneratorPage.jsx';
import TeamChatPage from '../features/collaboration/TeamChatPage.jsx';

const navItems = [
  { id: 'overview', label: 'Overview', icon: 'OV' },
  { id: 'create-project', label: 'Create Project', icon: 'CP' },
  { id: 'team', label: 'AI Team', icon: 'AI' },
  { id: 'mobile', label: 'Mobile Preview', icon: 'MB' },
  { id: 'logs', label: 'Logs', icon: 'LG' },
  { id: 'sandbox', label: 'Sandbox Test Lab', icon: 'TL' },
  { id: 'knowledge-base', label: 'Knowledge Base', icon: 'KB' },
  { id: 'marketplace', label: 'Marketplace', icon: 'MP' },
  { id: 'video-generation', label: 'AI Video', icon: 'VD' },
  { id: 'presentation-generator', label: 'AI Presentations', icon: 'PZ' },
  { id: 'team-chat', label: 'Team Chat', icon: 'TC' },
  { id: 'settings', label: 'Settings', icon: 'ST' },
];

function pretty(value, fallback = 'Unavailable') {
  if (value === null || value === undefined || value === '') return fallback;
  return String(value).replace(/_/g, ' ');
}

function statusTone(status) {
  status = String(status || '').trim().toLowerCase().replace(/\s+/g, '_');
  if (['completed', 'passed', 'done'].includes(status)) return 'success';
  if (['working', 'running', 'in_progress'].includes(status)) return 'active';
  if (['failed', 'error'].includes(status)) return 'danger';
  if (['unavailable', 'not_verified', 'incomplete'].includes(status)) return 'warning';
  return 'idle';
}

function getAgentEntries(agents) {
  return Object.entries(agents || {}).map(([id, agent]) => ({ id, ...agent }));
}

function getAgentInitials(agent) {
  if (agent.id === 'opencode') return 'OC';
  const name = agent.name || agent.id || 'AI';
  return name.split(/\s+/).map(part => part[0]).join('').slice(0, 2).toUpperCase();
}

export default function StudioDashboard({
  activePort,
  logs,
  agents,
  statuses,
  settingsContent,
  infoContent,
  onKeyManager,
}) {
  const [activeView, setActiveView] = useState('overview');
  const [sandboxOpened, setSandboxOpened] = useState(false);
  const agentEntries = getAgentEntries(agents);

  const eventRows = (logs || []).slice(0, 7);

  const selectNav = (id) => {
    setActiveView(id);
    if (id === 'sandbox') setSandboxOpened(true);
  };

  const openInfo = () => setActiveView('info');

  const workspaceWide = ['settings', 'info', 'sandbox'].includes(activeView);

  return (
    <div className={`fs-shell ${workspaceWide ? 'workspace-wide' : ''}`}>
      <aside className="fs-sidebar" aria-label="FreelancerStudio navigation">
        <div className="fs-brand">
          <div className="fs-brand-mark" aria-hidden="true">FS</div>
          <div>
            <strong>FreelancerStudio</strong>
            <span>Autonomous AI Workspace</span>
          </div>
        </div>
        <nav className="fs-nav">
          {navItems.map(item => (
            <button
              key={item.id}
              type="button"
              className={activeView === item.id ? 'active' : ''}
              onClick={() => selectNav(item.id)}
              title={item.label}
              aria-current={activeView === item.id ? 'page' : undefined}
            >
              <span className="fs-nav-icon" aria-hidden="true">{item.icon}</span>
              <span>{item.label}</span>
              {item.id === 'logs' && logs?.length > 0 && <b className="fs-count muted">{logs.length}</b>}
            </button>
          ))}
        </nav>
        <button type="button" className="fs-about-button" onClick={openInfo} title="Info">
          <span className="fs-nav-icon" aria-hidden="true">i</span>
          <span>Info</span>
        </button>
      </aside>

      <section className="fs-app">
        <header className="fs-header">
          <div className="fs-project-context">
            <span className="fs-eyebrow">FreelancerStudio</span>
            <strong>Autonomous AI Workspace</strong>
          </div>
          <div className="fs-header-actions">
            <button type="button" className="fs-pill" onClick={onKeyManager} title="Manage API keys">Keys</button>
            <button type="button" className="fs-pill" onClick={() => setActiveView('settings')} title="Open settings">Settings</button>
            <span className="fs-pill status" title="Backend status">Backend {activePort}</span>
          </div>
        </header>

        <div className="fs-body">
          <main className={`fs-workspace ${activeView === 'overview' ? 'fs-overview-workspace' : ''} ${activeView === 'settings' ? 'fs-settings-workspace' : ''} ${activeView === 'info' ? 'fs-info-workspace' : ''}`} tabIndex={0} aria-label="Central workspace content">
            {activeView === 'overview' && (
              <>
                <OverviewHero onNewProject={() => setActiveView('create-project')} />
                <AgentActivity agents={agentEntries} statuses={statuses} />
                <ActivityPanel logs={eventRows} onOpenLogs={() => setActiveView('logs')} />
              </>
            )}
            {activeView === 'create-project' && <OrderWorkflowPage active={activeView === 'create-project'} />}
            {activeView === 'knowledge-base' && <KnowledgeBasePage active={activeView === 'knowledge-base'} />}
            {activeView === 'marketplace' && <MarketplacePage active={activeView === 'marketplace'} />}
            {activeView === 'video-generation' && <VideoGenerationPage active={activeView === 'video-generation'} />}
            {activeView === 'presentation-generator' && <PresentationGeneratorPage active={activeView === 'presentation-generator'} />}
            {activeView === 'team-chat' && <TeamChatPage active={activeView === 'team-chat'} />}
            {activeView === 'team' && <AgentActivity agents={agentEntries} statuses={statuses} expanded />}
            {activeView === 'mobile' && <MobilePreviewPanel activePort={activePort} />}
            {activeView === 'logs' && <LogPanel logs={logs} />}
            {sandboxOpened && <section className="fs-test-lab-host" hidden={activeView !== 'sandbox'}><SandboxTestLabPage active={activeView === 'sandbox'} /></section>}
            {activeView === 'settings' && <section className="fs-panel fs-settings-page">{settingsContent}</section>}
            {activeView === 'info' && <section className="fs-panel fs-info-page">{infoContent}</section>}
            <div className="fs-workspace-bottom-sentinel" data-testid="workspace-bottom-sentinel" aria-hidden="true" />
          </main>
        </div>
      </section>
    </div>
  );
}

function OverviewHero({ onNewProject }) {
  return (
    <section className="fs-hero">
      <div>
        <span className="fs-eyebrow">Workspace Overview</span>
        <h1>Start a project to activate the studio</h1>
        <p>Create an AI order to generate a new project, or use Knowledge Base, Marketplace, AI Video, and AI Presentations from the sidebar.</p>
      </div>
      <div className="fs-hero-actions">
        <button type="button" className="fs-primary" onClick={onNewProject}>Create Project</button>
      </div>
    </section>
  );
}

function AgentActivity({ agents, statuses, expanded = false }) {
  return (
    <section className="fs-panel fs-team-panel">
      <div className="fs-panel-title"><div><span>AI Team</span><strong>{agents.length || 0} configured agents</strong></div></div>
      <div className={`fs-agent-grid ${expanded ? 'expanded' : ''}`}>
        {agents.length ? agents.map(agent => {
          const state = statuses?.[agent.id]?.status || (agent.enabled === false ? 'unavailable' : 'idle');
          return (
            <div className={`fs-agent ${statusTone(state)}`} key={agent.id} title={agent.name || agent.id}>
              <span className="fs-agent-mark" style={{ '--agent-color': agent.color || 'var(--fs-accent)' }}>{getAgentInitials(agent)}</span>
              <span><b>{agent.name || agent.id}</b><small>{statuses?.[agent.id]?.task || agent.display_role || agent.role || pretty(state)}</small></span>
              <i aria-label={state} />
            </div>
          );
        }) : <p className="fs-empty">No configured agents were returned by the backend.</p>}
      </div>
    </section>
  );
}

function ActivityPanel({ logs, onOpenLogs }) {
  return (
    <section className="fs-panel fs-activity fs-activity-panel">
      <div className="fs-panel-title"><div><span>Recent Activity</span><strong>{logs.length ? `${logs.length} latest events` : 'No events'}</strong></div><button type="button" onClick={onOpenLogs}>Open logs</button></div>
      <div className="fs-log-list">
        {logs.length ? logs.map((log, index) => <div className="fs-log-line" key={`${log}-${index}`}><i aria-hidden="true" /><span>{log}</span></div>) : <p className="fs-empty">No recent activity is available.</p>}
      </div>
    </section>
  );
}

function LogPanel({ logs }) {
  return <section className="fs-panel fs-log-panel"><div className="fs-panel-title"><div><span>Logs</span><strong>{logs?.length || 0} events</strong></div></div><div className="fs-full-log">{logs?.length ? logs.map((log, index) => <div key={`${log}-${index}`}>{log}</div>) : <p className="fs-empty">No log entries are available.</p>}</div></section>;
}

const MOBILE_DEVICES = [
  { id: 'iphone16pro', name: 'iPhone 16 Pro', width: 402, height: 874, radius: 48 },
  { id: 'iphone16promax', name: 'iPhone 16 Pro Max', width: 440, height: 956, radius: 52 },
  { id: 'iphone15promax', name: 'iPhone 15 Pro Max', width: 430, height: 932, radius: 50 },
  { id: 'iphoneSE', name: 'iPhone SE 3rd Gen', width: 375, height: 667, radius: 26 },
  { id: 'samsungS25ultra', name: 'Samsung Galaxy S25 Ultra', width: 412, height: 915, radius: 28 },
  { id: 'samsungS24ultra', name: 'Samsung Galaxy S24 Ultra', width: 412, height: 915, radius: 28 },
  { id: 'galaxyZFold6', name: 'Samsung Galaxy Z Fold6', width: 768, height: 960, radius: 24 },
  { id: 'pixel9proxl', name: 'Google Pixel 9 Pro XL', width: 412, height: 915, radius: 36 },
  { id: 'pixel9pro', name: 'Google Pixel 9 Pro', width: 402, height: 874, radius: 36 },
  { id: 'ipadpro11', name: 'iPad Pro 11-inch', width: 834, height: 1194, radius: 28 },
];

function isEmbeddablePreviewUrl(value) {
  const text = String(value || '').trim();
  if (!text) return false;
  try {
    const url = new URL(text);
        return ['http:', 'https:'].includes(url.protocol)
          && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
          && !url.username
          && !url.password;
  } catch {
    return false;
  }
}

function MobilePreviewPanel({ activePort }) {
  const [deviceId, setDeviceId] = useState('iphone16pro');
  const [previewUrl, setPreviewUrl] = useState('');
  const device = MOBILE_DEVICES.find(item => item.id === deviceId) || MOBILE_DEVICES[0];
  const scale = device.width > 500 ? 0.48 : device.width > 430 ? 0.62 : 0.74;
  const canEmbed = isEmbeddablePreviewUrl(previewUrl);
  const hasExternalUrl = previewUrl && !canEmbed;
  return (
    <section className="fs-panel fs-mobile-preview-page">
      <div className="fs-panel-title">
        <div><span>Mobile Preview</span></div>
      </div>
      <AndroidDevicesSection activePort={activePort} />
      <div className="fs-mobile-preview-layout">
        <div className="fs-mobile-controls">
          <label>Device
            <select value={deviceId} onChange={event => setDeviceId(event.target.value)}>
              {MOBILE_DEVICES.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          <label>Preview URL
            <input value={previewUrl} onChange={event => setPreviewUrl(event.target.value)} placeholder="http://localhost:5173 or Expo web URL" />
          </label>
          <p>Use a local running preview URL, for example Vite, Expo Web, React Native Web, Ionic, or Capacitor. External sites can block embedding inside the phone frame.</p>
        </div>
        <div className="fs-phone-stage">
          <div className="fs-phone-frame" style={{ width: device.width * scale, height: device.height * scale, borderRadius: device.radius * scale }}>
            <div className="fs-phone-speaker" />
            {canEmbed ? <iframe title={`${device.name} preview`} src={previewUrl} sandbox="allow-forms allow-scripts" style={{ borderRadius: Math.max(18, device.radius * scale - 10) }} /> : <div className="fs-phone-empty"><b>{device.name}</b>{hasExternalUrl ? <span>This URL is external and cannot be embedded. Use a local preview URL instead.</span> : <span>Enter a running local project URL to preview it in this virtual phone.</span>}</div>}
          </div>
        </div>
      </div>
    </section>
  );
}

function AndroidDevicesSection({ activePort }) {
  const [status, setStatus] = useState(null);
  const [selectedSerial, setSelectedSerial] = useState('');
  const [selectedAvd, setSelectedAvd] = useState('');
  const [packageId, setPackageId] = useState('');
  const [apkPath, setApkPath] = useState('');
  const [recordSeconds, setRecordSeconds] = useState(10);
  const [logcat, setLogcat] = useState('');
  const [message, setMessage] = useState('Loading Android devices...');
  const [busy, setBusy] = useState(false);
  const endpoint = path => `http://localhost:${activePort}${path}`;
  const devices = status?.devices || [];
  const selected = devices.find(device => device.serial === selectedSerial) || devices[0];

  const loadStatus = () => {
    setBusy(true);
    fetch(endpoint('/api/android/status'))
      .then(r => r.json())
      .then(data => {
        setStatus(data);
        setSelectedSerial(current => current || data.devices?.[0]?.serial || '');
        setSelectedAvd(current => current || data.avds?.[0] || '');
        setMessage('Android device list refreshed.');
      })
      .catch(error => setMessage(`Android status failed: ${error.message}`))
      .finally(() => setBusy(false));
  };

  React.useEffect(() => { loadStatus(); }, [activePort]);

  const post = (path, body = {}, after) => {
    setBusy(true);
    fetch(endpoint(path), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(async r => {
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || 'Android command failed');
        setMessage(data.status ? `${data.status}: ${path}` : `Completed: ${path}`);
        if (data.logcat) setLogcat(data.logcat);
        if (after) after(data);
        return data;
      })
      .then(() => loadStatus())
      .catch(error => setMessage(error.message))
      .finally(() => setBusy(false));
  };

  const selectedPayload = () => ({ serial: selectedSerial || selected?.serial || '' });
  const packagePayload = () => ({ ...selectedPayload(), package_id: packageId.trim() });
  const sdkMissing = status && !status.sdk?.found;
  const adbMissing = status && !status.adb?.found;
  const emulatorMissing = status && !status.emulator?.found;
  const scrcpyMissing = status && !status.scrcpy?.found;

  return (
    <section className="fs-android-panel">
      <div className="fs-panel-title">
        <div><span>Android Devices</span><strong>{selected?.serial || 'No device selected'}</strong></div>
        <button type="button" onClick={loadStatus} disabled={busy}>Refresh</button>
      </div>
      <div className="fs-android-grid">
        <div className="fs-android-card">
          <h3>Toolchain</h3>
          <div className="fs-compact-facts vertical">
            <span>SDK: <b>{status?.sdk?.found ? status.sdk.path : 'Not found'}</b></span>
            <span>adb: <b>{status?.adb?.found ? status.adb.path : 'Not found'}</b></span>
            <span>emulator: <b>{status?.emulator?.found ? status.emulator.path : 'Not found'}</b></span>
            <span>scrcpy: <b>{status?.scrcpy?.found ? status.scrcpy.path : 'Unavailable'}</b></span>
          </div>
          {sdkMissing && <p className="fs-empty">Android SDK not found. Expected location includes %LOCALAPPDATA%\Android\Sdk.</p>}
          {adbMissing && <p className="fs-empty">adb unavailable. Install Android platform-tools or set Android SDK location for this session.</p>}
          {emulatorMissing && <p className="fs-empty">Emulator executable unavailable. Existing physical devices can still be used when adb is available.</p>}
          {scrcpyMissing && <p className="fs-empty">scrcpy unavailable. Install scrcpy to open live device mirrors in a separate window.</p>}
        </div>
        <div className="fs-android-card">
          <h3>AVDs</h3>
          <label>Available AVD
            <select value={selectedAvd} onChange={event => setSelectedAvd(event.target.value)}>
              {(status?.avds || []).map(avd => <option key={avd} value={avd}>{avd}</option>)}
            </select>
          </label>
          {status?.avds?.length ? <button type="button" onClick={() => post('/api/android/avd/start', { avd_name: selectedAvd })} disabled={busy || !selectedAvd}>Start AVD / Open Emulator</button> : <p className="fs-empty">No AVD available. Studio will not create or delete AVDs automatically.</p>}
        </div>
        <div className="fs-android-card wide">
          <h3>Connected Devices</h3>
          <label>Device
            <select value={selectedSerial} onChange={event => setSelectedSerial(event.target.value)}>
              {devices.map(device => <option key={device.serial} value={device.serial}>{device.serial} - {pretty(device.status)}</option>)}
            </select>
          </label>
          {selected ? <div className="fs-device-summary">
            <span className={`fs-status ${statusTone(selected.status === 'device' ? 'passed' : selected.status === 'booting' ? 'running' : 'failed')}`}>{pretty(selected.status)}</span>
            <span>Android {selected.android_version || 'unknown'} / API {selected.api_level || 'unknown'}</span>
            <span>{selected.resolution || 'resolution unavailable'}</span>
          </div> : <p className="fs-empty">No device connected. Connect a physical device or start an existing AVD.</p>}
          {selected?.status === 'offline' && <p className="fs-empty">Device offline. Reconnect or restart adb/device.</p>}
          {selected?.status === 'unauthorized' && <p className="fs-empty">Device unauthorized. Approve USB debugging on the device.</p>}
          <div className="fs-android-actions">
            <button type="button" onClick={() => post('/api/android/scrcpy/open', selectedPayload())} disabled={busy || !selected || scrcpyMissing}>Open scrcpy</button>
            <button type="button" onClick={() => post('/api/android/screenshot', selectedPayload())} disabled={busy || !selected}>Screenshot</button>
            <button type="button" onClick={() => post('/api/android/record', { ...selectedPayload(), seconds: recordSeconds })} disabled={busy || !selected}>Record</button>
            <button type="button" onClick={() => post('/api/android/emulator/stop', selectedPayload())} disabled={busy || !selected?.serial?.startsWith('emulator-')}>Stop Emulator</button>
          </div>
          <label>Record seconds
            <input type="number" min="1" max="180" value={recordSeconds} onChange={event => setRecordSeconds(event.target.value)} />
          </label>
        </div>
        <div className="fs-android-card wide">
          <h3>App Controls</h3>
          <label>Package ID
            <input value={packageId} onChange={event => setPackageId(event.target.value)} placeholder="com.example.app" />
          </label>
          <label>APK path
            <input value={apkPath} onChange={event => setApkPath(event.target.value)} placeholder="C:\\path\\to\\app.apk" />
          </label>
          <div className="fs-android-actions">
            <button type="button" onClick={() => post('/api/android/apk/install', { ...selectedPayload(), apk_path: apkPath })} disabled={busy || !selected || !apkPath}>Install APK</button>
            <button type="button" onClick={() => post('/api/android/app/uninstall', packagePayload())} disabled={busy || !selected || !packageId}>Uninstall</button>
            <button type="button" onClick={() => post('/api/android/app/launch', packagePayload())} disabled={busy || !selected || !packageId}>Launch</button>
            <button type="button" onClick={() => post('/api/android/app/stop', packagePayload())} disabled={busy || !selected || !packageId}>Stop App</button>
            <button type="button" onClick={() => post('/api/android/app/clear-data', packagePayload())} disabled={busy || !selected || !packageId}>Clear Data</button>
            <button type="button" onClick={() => post('/api/android/app/package-info', packagePayload(), data => setMessage(`${data.package_id}: ${data.version_name || 'version unknown'}`))} disabled={busy || !selected || !packageId}>Package Info</button>
            <button type="button" onClick={() => post('/api/android/logcat', { ...packagePayload(), lines: 500 })} disabled={busy || !selected}>Open Filtered Logcat</button>
          </div>
        </div>
      </div>
      <div className="fs-android-message">{message}</div>
      {logcat && <details className="fs-android-log" open><summary>Filtered logcat</summary><pre>{logcat}</pre></details>}
    </section>
  );
}
