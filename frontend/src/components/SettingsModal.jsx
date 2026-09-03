import React, { useState, useEffect, useRef } from 'react';
import { tr } from '../i18n.js';
import { applyStudioTheme } from '../theme.js';
import OpenCodeConnectionSetup from './OpenCodeConnectionSetup.jsx';
import ClaudeCodeConnectionSetup from './ClaudeCodeConnectionSetup.jsx';
import GrokConnectionSetup from './GrokConnectionSetup.jsx';

const ACCENT_COLORS = [
  { name: 'Sky', color: '#0ea5e9' },
  { name: 'Indigo', color: '#6366f1' },
  { name: 'Emerald', color: '#10b981' },
  { name: 'Amber', color: '#f59e0b' },
  { name: 'Rose', color: '#f43f5e' },
  { name: 'Purple', color: '#a855f7' },
  { name: 'Cyan', color: '#06b6d4' },
  { name: 'Slate', color: '#475569' },
];

export default function SettingsModal({ activePort, onClose, addLog, embedded = false }) {
  const [settings, setSettings] = useState(null);
  const [activeTab, setActiveTab] = useState('appearance');
  const [loading, setLoading] = useState(true);
  const dialogRef = useRef(null);
  const scrollBodyRef = useRef(null);
  const closeButtonRef = useRef(null);
  const returnFocusRef = useRef(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    fetch(`http://localhost:${activePort}/api/config/system`)
      .then(res => res.json())
      .then(data => {
        setSettings(data);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [activePort]);

  const updateSetting = (key, value) => {
    const previous = settings;
    const updated = { ...settings, [key]: value };
    setSettings(updated);
    fetch(`http://localhost:${activePort}/api/config/system`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [key]: value })
    })
      .then(async res => {
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Settings could not be saved');
        return data;
      })
      .then(data => {
        if (data.status === 'saved') {
          applyStudioTheme(updated, { cache: key === 'theme' });
          addLog(`[Settings]: ${key} set to ${value}.`);
        }
      })
      .catch(err => {
        setSettings(previous);
        applyStudioTheme(previous);
        console.error('[Settings Error]:', err);
      });
  };

  useEffect(() => {
    if (settings) applyStudioTheme(settings);
  }, [settings]);

  useEffect(() => {
    if (scrollBodyRef.current) scrollBodyRef.current.scrollTop = 0;
  }, [activeTab]);

  useEffect(() => {
    if (embedded) return undefined;
    returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    const focusTimer = window.setTimeout(() => closeButtonRef.current?.focus(), 0);
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        if (onCloseRef.current) onCloseRef.current();
        return;
      }
      if (event.key !== 'Tab') return;

      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusable = Array.from(dialog.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'))
        .filter(el => !el.disabled && el.offsetParent !== null);
      if (!focusable.length) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => {
      window.clearTimeout(focusTimer);
      document.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = previousOverflow;
      returnFocusRef.current?.focus?.();
    };
  }, [embedded]);

  if (loading) return <div className={embedded ? "settings-inline-loading" : "settings-modal-overlay"}><div className="text-xs text-slate-500">Loading settings...</div></div>;

  const s = settings || {};
  const t = (key) => tr(s.language || 'en', key);

  const tabs = [
    { id: 'appearance', label: t('appearance') },
    { id: 'general', label: t('general') },
    { id: 'ai', label: 'AI Providers' },
    { id: 'storage', label: 'Storage' },
    { id: 'studio', label: t('studio') },
  ];

  return (
    <div className={embedded ? "settings-inline" : "settings-modal-overlay animate-fade-in"} onMouseDown={(event) => { if (!embedded && event.target === event.currentTarget) onClose?.(); }}>
      <div ref={dialogRef} role={embedded ? undefined : "dialog"} aria-modal={embedded ? undefined : "true"} aria-labelledby="settings-modal-title" className={`settings-modal-container ${embedded ? "settings-inline-container" : "settings-window-wide shadow-2xl"} bg-[var(--bg-card)] border border-[var(--border)] rounded-xl w-full`} style={{ borderColor: 'var(--border)' }}>

        <div className="settings-modal-header p-4 border-b border-[var(--border)] flex justify-between items-center" style={{ backgroundColor: 'var(--bg-secondary)' }}>
          <h3 id="settings-modal-title" className="text-xs font-bold uppercase tracking-widest" style={{ color: 'var(--accent)' }}>{t('systemPreferences')}</h3>
          {!embedded && <button ref={closeButtonRef} onClick={onClose} className="text-[var(--text-muted)] hover:text-[var(--text-primary)] font-mono text-sm" aria-label="Close settings">x</button>}
        </div>

        {/* Tabs */}
        <div className="settings-modal-tabs flex border-b border-[var(--border)]" style={{ backgroundColor: 'var(--bg-secondary)' }}>
          {tabs.map(tab => (
            <button
              key={tab.id}
              data-settings-tab={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className="flex-1 py-2.5 text-[10px] font-bold uppercase tracking-wider transition-colors"
              style={{
                color: activeTab === tab.id ? 'var(--accent)' : 'var(--text-muted)',
                borderBottom: activeTab === tab.id ? '2px solid var(--accent)' : '2px solid transparent',
                backgroundColor: activeTab === tab.id ? 'var(--accent-bg)' : 'transparent',
              }}
            >
              {tab.label}
            </button>
          ))}
        </div>

        <div ref={scrollBodyRef} role="region" tabIndex={0} aria-label="Settings content" data-active-tab={activeTab} className="settings-modal-body p-5 space-y-5" style={{ backgroundColor: 'var(--bg-card)' }}>
          {activeTab === 'appearance' && (
            <>
              {/* Theme */}
              <Section label={t('theme')}>
                <div className="flex space-x-2">
                  {['dark', 'light'].map(t => (
                    <button
                      key={t}
                      onClick={() => updateSetting('theme', t)}
                      className="flex-1 py-2 rounded-lg text-[11px] font-medium transition-all border"
                      style={{
                        backgroundColor: s.theme === t ? 'var(--accent-bg)' : 'var(--bg-secondary)',
                        borderColor: s.theme === t ? 'var(--accent)' : 'var(--border)',
                        color: s.theme === t ? 'var(--accent)' : 'var(--text-secondary)',
                      }}
                    >{t === 'dark' ? '🌙 Dark' : '☀️ Light'}</button>
                  ))}
                </div>
              </Section>

              {/* Accent Color */}
              <Section label={t('accentColor')}>
                <div className="flex flex-wrap gap-2">
                  {ACCENT_COLORS.map(c => (
                    <button
                      key={c.color}
                      onClick={() => updateSetting('accent_color', c.color)}
                      className="w-8 h-8 rounded-full border-2 transition-transform hover:scale-110"
                      style={{
                        backgroundColor: c.color,
                        borderColor: s.accent_color === c.color ? 'var(--text-primary)' : 'transparent',
                        transform: s.accent_color === c.color ? 'scale(1.15)' : 'scale(1)',
                      }}
                      title={c.name}
                    />
                  ))}
                </div>
              </Section>

              {/* Font Size */}
              <Section label={t('fontSize')} testId="settings-last-appearance">
                <div className="flex space-x-2">
                  {['small', 'medium', 'large'].map(size => (
                    <button
                      key={size}
                      onClick={() => updateSetting('font_size', size)}
                      className="flex-1 py-2 rounded-lg text-[11px] font-medium transition-all border"
                      style={{
                        backgroundColor: s.font_size === size ? 'var(--accent-bg)' : 'var(--bg-secondary)',
                        borderColor: s.font_size === size ? 'var(--accent)' : 'var(--border)',
                        color: s.font_size === size ? 'var(--accent)' : 'var(--text-secondary)',
                      }}
                    >{size.charAt(0).toUpperCase() + size.slice(1)}</button>
                  ))}
                </div>
              </Section>
            </>
          )}

          {activeTab === 'general' && (
            <>
              <Section label={t('language')}>
                <select
                  value={s.language || 'en'}
                  onChange={e => {
                    updateSetting('language', e.target.value);
                    window.dispatchEvent(new CustomEvent('studio-language-change', { detail: { language: e.target.value } }));
                  }}
                  className="w-full bg-[var(--bg-secondary)] border border-[var(--border)] rounded-lg px-3 py-2 text-xs focus:outline-none"
                  style={{ color: 'var(--text-primary)' }}
                >
                  <option value="en">English</option>
                  <option value="ru">Русский</option>
                  <option value="de">Deutsch</option>
                </select>
              </Section>

              <Section label={t('notifications')}>
                <Toggle checked={s.notifications_enabled !== false} onChange={v => updateSetting('notifications_enabled', v)} label={t('enableNotifications')} />
              </Section>

              <Section label={t('autoSave')} testId="settings-last-general">
                <Toggle checked={s.auto_save !== false} onChange={v => updateSetting('auto_save', v)} label={t('autoSaveProject')} />
              </Section>

              <Section label="Live coding execution">
                <Toggle checked={s.live_execution_enabled === true} onChange={v => updateSetting('live_execution_enabled', v)} label="Allow live builds that write project files" />
                <p className="provider-message">Turns on the same live pipeline the benchmark uses. You will still confirm each live start on the execution screen, and can change provider, model, and coding worker there.</p>
                <label className="block mt-3 text-[10px] uppercase tracking-wider" style={{ color: 'var(--text-secondary)' }}>Default coding worker
                  <select
                    value={s.coding_backend || ''}
                    onChange={e => updateSetting('coding_backend', e.target.value)}
                    className="w-full bg-[var(--bg-secondary)] border border-[var(--border)] rounded-lg px-3 py-2 text-xs focus:outline-none mt-1"
                    style={{ color: 'var(--text-primary)' }}
                  >
                    <option value="">Auto (first ready worker)</option>
                    <option value="ollama">Local Ollama (Grok spec + Qwen files)</option>
                    <option value="grok">Grok subscription (Grok writes files)</option>
                    <option value="claude_code">Claude Code subscription</option>
                    <option value="opencode_bridge">OpenCode</option>
                    <option value="openrouter">OpenRouter API (OpenRouter writes files)</option>
                  </select>
                </label>
              </Section>

              <Section label="Experimental tools">
                <Toggle checked={s.show_experimental === true} onChange={v => updateSetting('show_experimental', v)} label="Show frozen sidebar tools (Sandbox, Marketplace, Video, Presentations, Knowledge Base, Team Chat)" />
              </Section>
            </>
          )}

          {activeTab === 'ai' && (
            <AIProviderSettings activePort={activePort} addLog={addLog} />
          )}

          {activeTab === 'storage' && (
            <StoragePathsSettings activePort={activePort} />
          )}

          {activeTab === 'studio' && (
            <>
              <Section label={t('defaultBudget')}>
                <input
                  type="text"
                  value={s.default_budget || '500'}
                  onChange={e => updateSetting('default_budget', e.target.value)}
                  className="w-full bg-[var(--bg-secondary)] border border-[var(--border)] rounded-lg px-3 py-2 text-xs font-mono focus:outline-none"
                  style={{ color: 'var(--text-primary)' }}
                />
              </Section>

              <Section label={t('pollingInterval')}>
                <select
                  value={s.polling_interval || 3000}
                  onChange={e => updateSetting('polling_interval', parseInt(e.target.value))}
                  className="w-full bg-[var(--bg-secondary)] border border-[var(--border)] rounded-lg px-3 py-2 text-xs focus:outline-none"
                  style={{ color: 'var(--text-primary)' }}
                >
                  <option value={1000}>1 second</option>
                  <option value={3000}>3 seconds</option>
                  <option value={5000}>5 seconds</option>
                  <option value={10000}>10 seconds</option>
                </select>
              </Section>

              <Section label={t('logDetail')}>
                <div className="flex space-x-2">
                  {['minimal', 'normal', 'verbose'].map(level => (
                    <button
                      key={level}
                      onClick={() => updateSetting('log_detail', level)}
                      className="flex-1 py-2 rounded-lg text-[11px] font-medium transition-all border"
                      style={{
                        backgroundColor: s.log_detail === level ? 'var(--accent-bg)' : 'var(--bg-secondary)',
                        borderColor: s.log_detail === level ? 'var(--accent)' : 'var(--border)',
                        color: s.log_detail === level ? 'var(--accent)' : 'var(--text-secondary)',
                      }}
                    >{level.charAt(0).toUpperCase() + level.slice(1)}</button>
                  ))}
                </div>
              </Section>

              <Section label={t('githubIntegration')}>
                <GitHubConfig activePort={activePort} addLog={addLog} />
              </Section>

              <hr className="border-[var(--border)] my-4" />
              <div className="text-xs font-bold tracking-wider uppercase mb-3" style={{ color: 'var(--text-muted)' }}>🖥️ {t('editorIntegration')}</div>
              <Section label="VS Code Path">
                <input
                  type="text"
                  value={s.vscode_path || 'code'}
                  onChange={e => updateSetting('vscode_path', e.target.value)}
                  className="w-full bg-[var(--bg-secondary)] border border-[var(--border)] rounded-lg px-3 py-2 text-xs font-mono focus:outline-none"
                  style={{ color: 'var(--text-primary)' }}
                  placeholder="code"
                />
                <p className="text-[9px] mt-1" style={{ color: 'var(--text-muted)' }}>Executable path or command (default: <code>code</code>)</p>
              </Section>
              <Section label="PyCharm Path" testId="settings-last-studio">
                <input
                  type="text"
                  value={s.pycharm_path || 'pycharm'}
                  onChange={e => updateSetting('pycharm_path', e.target.value)}
                  className="w-full bg-[var(--bg-secondary)] border border-[var(--border)] rounded-lg px-3 py-2 text-xs font-mono focus:outline-none"
                  style={{ color: 'var(--text-primary)' }}
                  placeholder="pycharm"
                />
                <p className="text-[9px] mt-1" style={{ color: 'var(--text-muted)' }}>Executable path or command (default: <code>pycharm</code>)</p>
              </Section>
            </>
          )}
        </div>

        <div className="settings-modal-footer p-3 border-t border-[var(--border)] flex justify-between items-center" style={{ backgroundColor: 'var(--bg-secondary)' }}>
          <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>{t('savedAutomatically')}</span>
          {!embedded && <button
            onClick={onClose}
            className="px-4 py-1.5 rounded-lg text-xs font-medium transition-colors"
            style={{
              backgroundColor: 'var(--accent)',
              color: 'var(--accent-contrast)',
            }}
          >
            {t('close')}
          </button>}
        </div>
      </div>
    </div>
  );
}

function Section({ label, children, testId }) {
  return (
    <div data-testid={testId}>
      <label className="block text-[10px] font-bold uppercase tracking-wider mb-2" style={{ color: 'var(--text-muted)' }}>{label}</label>
      {children}
    </div>
  );
}

function Toggle({ checked, onChange, label, disabled = false }) {
  return (
    <div className="flex items-center space-x-3 select-none" style={{ minHeight: 32, opacity: disabled ? 0.55 : 1 }}>
      <button
        type="button"
        role="switch"
        aria-label={label}
        aria-checked={checked}
        aria-disabled={disabled}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        onKeyDown={(e) => {
          if (!disabled && (e.key === 'Enter' || e.key === ' ')) {
            e.preventDefault();
            onChange(!checked);
          }
        }}
        className="transition-all"
        style={{
          position: 'relative',
          display: 'inline-flex',
          alignItems: 'center',
          width: 48,
          height: 28,
          minWidth: 48,
          borderRadius: 999,
          padding: 3,
          backgroundColor: checked ? 'var(--accent)' : 'color-mix(in srgb, var(--text-muted) 28%, transparent)',
          boxShadow: checked ? '0 0 18px var(--accent-bg)' : 'inset 0 0 0 1px var(--border)',
          cursor: disabled ? 'not-allowed' : 'pointer',
          border: 'none',
        }}
      >
        <span
          className="transition-transform shadow-md"
          style={{
            display: 'block',
            width: 22,
            height: 22,
            borderRadius: 999,
            backgroundColor: 'var(--control-thumb)',
            transform: checked ? 'translateX(20px)' : 'translateX(0)',
          }}
        />
      </button>
      <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>{label}</span>
    </div>
  );
}

function StoragePathsSettings({ activePort }) {
  const [paths, setPaths] = useState(null);
  const [draft, setDraft] = useState({});
  const [message, setMessage] = useState('');
  const [results, setResults] = useState({});
  const [busy, setBusy] = useState(false);
  const editableKeys = ['studio_root', 'data_dir', 'runtime_dir', 'generated_projects', 'projects_data', 'opencode_config', 'backups'];
  const draftFrom = data => Object.fromEntries(editableKeys.map(key => [key, data.paths?.[key]?.configured_value || data.paths?.[key]?.configured_override || '']));
  const load = () => fetch(`http://localhost:${activePort}/api/system/paths`).then(r => r.json()).then(data => { setPaths(data); setDraft(draftFrom(data)); setMessage(''); }).catch(err => setMessage(`Path audit failed: ${err.message}`));
  useEffect(() => { load(); }, [activePort]);
  const repair = () => {
    setBusy(true);
    fetch(`http://localhost:${activePort}/api/system/portable-migration`, { method: 'POST' })
      .then(r => r.json())
      .then(data => { setPaths(data); setDraft(draftFrom(data)); setMessage(data.portable_ready ? 'Portable folders are ready.' : 'Portable folders were checked; some paths still need attention.'); })
      .catch(err => setMessage(`Portable repair failed: ${err.message}`))
      .finally(() => setBusy(false));
  };
  const save = () => {
    setBusy(true);
    fetch(`http://localhost:${activePort}/api/system/paths`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(draft) })
      .then(async r => { const data = await r.json(); if (!r.ok) throw new Error(data.detail || 'Save failed'); return data; })
      .then(data => { setPaths(data); setDraft(draftFrom(data)); setMessage(data.message || (data.restart_required ? 'Storage paths saved. Restart Studio for runtime paths to take effect.' : 'Storage paths saved.')); })
      .catch(err => setMessage(`Path save failed: ${err.message}`))
      .finally(() => setBusy(false));
  };
  const entries = Object.entries(paths?.paths || {});
  return <section className="provider-setup"><div className="provider-heading"><div><strong>Storage & Paths</strong><p>Choose where Studio stores generated projects, runtime data, OpenCode config, and backups. Runtime path changes may require restart.</p></div><span className="connection-state">{paths?.portable_ready ? 'Portable ready' : 'Action needed'}</span></div>{message && <p className="provider-message" role="status">{message}</p>}<div className="provider-form">{editableKeys.map(key => { const item = paths?.paths?.[key] || {}; return <label key={key}>{key.replaceAll('_', ' ')}<input className="ai-settings-field" value={draft[key] || ''} onChange={e => setDraft(current => ({ ...current, [key]: e.target.value }))} placeholder={item.resolved_path || item.path || ''} /><small>Resolved: <code>{item.resolved_path || item.path || 'not loaded'}</code> · Source: {item.source || 'unknown'}{item.restart_required ? ' · Restart required for runtime to use this path.' : ''}{item.validation_error ? ` · ${item.validation_error}` : ''}</small></label>; })}</div><div className="provider-actions"><button type="button" onClick={load} disabled={busy}>Refresh paths</button><button type="button" className="primary" onClick={save} disabled={busy}>Save paths</button><button type="button" onClick={repair} disabled={busy}>Repair portable folders</button></div><div className="capability-grid">{entries.map(([name, item]) => <span key={name}><b>{name.replaceAll('_', ' ')}</b><code className="block break-all">{item.resolved_path || item.path}</code><small>{item.exists ? 'exists' : item.create_allowed ? 'will be created' : 'missing'} · {item.source || 'unknown source'} · {item.editable ? 'editable' : 'read-only'} · {item.inside_portable_root ? 'inside portable root' : 'outside portable root'} · {item.writable ? 'writable' : 'not writable/unchecked'}{item.configured_value ? ' · custom' : ''}{item.restart_required ? ' · restart required' : ''}{item.validation_error ? ` · ${item.validation_error}` : ''}</small></span>)}</div></section>;
}
function AIProviderSettings({ activePort, addLog }) {
  return (
    <div className="space-y-5" data-testid="settings-last-ai">
      <div className="ai-settings-section-label">Connections</div>
      <GrokConnectionSetup activePort={activePort} addLog={addLog} />
      <OpenCodeConnectionSetup activePort={activePort} addLog={addLog} />
      <ClaudeCodeConnectionSetup activePort={activePort} addLog={addLog} />
      <UniversalProviderConnectionsSettings activePort={activePort} addLog={addLog} />
      <div className="ai-settings-section-label">Agents</div>
      <GlobalAIInheritanceSettings activePort={activePort} cfg={{}} reload={() => {}} addLog={addLog} />
      <AgentModelManagerSettings activePort={activePort} />
      <ProductJudgeSettings activePort={activePort} addLog={addLog} />
    </div>
  );
}

function UniversalProviderConnectionsSettings({ activePort, addLog }) {
  const [state, setState] = useState(null);
  const [testing, setTesting] = useState('');
  const [message, setMessage] = useState('');
  const [results, setResults] = useState({});
  const [apiKeys, setApiKeys] = useState({});
  const [models, setModels] = useState({});
  const [manualModels, setManualModels] = useState({});
  const [endpointDrafts, setEndpointDrafts] = useState({});
  const [agentDrafts, setAgentDrafts] = useState({});
  const [providerTemplate, setProviderTemplate] = useState('grok-subscription');
  const providerTemplates = {
    'grok-subscription': { connection_id: 'grok-subscription', provider_id: 'grok', connection_type: 'grok_subscription', auth_method: 'delegated_cli_login', display_name: 'Grok Subscription (Grok CLI)', model_id: 'grok/grok-4.6', priority: 5, metadata: { preset: 'subscription', user_label: 'grok.com login through official Grok CLI' } },
    'xai-api-key': { connection_id: 'xai-api-key', provider_id: 'xai', connection_type: 'xai_api_key', auth_method: 'api_key', display_name: 'xAI HTTP API Key (not used for Grok subscription)', model_id: 'grok-4.6', priority: 90, metadata: { preset: 'api_key', user_label: 'Paid console.x.ai API. Not needed: Grok in this Studio uses the PowerShell grok.exe login.' } },
    'opencode-openai-subscription': { connection_id: 'opencode-openai-subscription', provider_id: 'opencode_bridge', connection_type: 'opencode_provider', auth_method: 'delegated_cli_login', display_name: 'ChatGPT / OpenAI via OpenCode subscription', model_id: 'openai/gpt-5.5', priority: 10, metadata: { preset: 'subscription', user_label: 'ChatGPT subscription through OpenCode OAuth' } },
    'codex-chatgpt-subscription': { connection_id: 'codex-chatgpt-subscription', provider_id: 'openai', connection_type: 'codex_chatgpt_subscription', auth_method: 'delegated_cli_login', display_name: 'ChatGPT Subscription (Codex CLI)', model_id: 'codex/default', priority: 20, metadata: { preset: 'subscription', user_label: 'ChatGPT subscription through official Codex CLI' } },
    'claude-subscription': { connection_id: 'claude-subscription', provider_id: 'anthropic', connection_type: 'claude_subscription', auth_method: 'delegated_cli_login', display_name: 'Claude Subscription (Claude CLI)', model_id: 'claude/default', priority: 30, metadata: { preset: 'subscription' } },
    'gemini-google-account': { connection_id: 'gemini-google-account', provider_id: 'google', connection_type: 'gemini_google_account', auth_method: 'delegated_cli_login', display_name: 'Google Gemini Account', model_id: 'gemini/default', priority: 40, metadata: { preset: 'subscription' } },
    'openai-api-key': { connection_id: 'openai-api-key', provider_id: 'openai', connection_type: 'openai_api_key', auth_method: 'api_key', display_name: 'OpenAI API Key', model_id: 'gpt-5.5', priority: 100, metadata: { preset: 'api_key' } },
    'anthropic-api-key': { connection_id: 'anthropic-api-key', provider_id: 'anthropic', connection_type: 'anthropic_api_key', auth_method: 'api_key', display_name: 'Anthropic API Key', model_id: 'claude-sonnet-4-20250514', priority: 110, metadata: { preset: 'api_key' } },
    'gemini-api-key': { connection_id: 'gemini-api-key', provider_id: 'google', connection_type: 'gemini_api_key', auth_method: 'api_key', display_name: 'Google Gemini API Key', model_id: 'gemini-2.5-pro', priority: 120, metadata: { preset: 'api_key' } },
    'openrouter-api-key': { connection_id: 'openrouter-api-key', provider_id: 'openrouter', connection_type: 'openrouter_api_key', auth_method: 'api_key', display_name: 'OpenRouter API Key', model_id: 'anthropic/claude-sonnet-4', priority: 130, metadata: { preset: 'api_key', user_label: 'One key, many vendors via OpenRouter' } },
    'ollama-local': { connection_id: 'ollama-local', provider_id: 'ollama', connection_type: 'ollama_local', auth_method: 'local', display_name: 'Ollama Local (coding)', model_id: 'qwen2.5-coder:14b', endpoint: 'http://127.0.0.1:11434', priority: 200, metadata: { preset: 'local', user_label: 'Local coder for project files. Grok writes the spec.' } },
    'lm-studio-local': { connection_id: 'lm-studio-local', provider_id: 'local', connection_type: 'lm_studio_local', auth_method: 'local', display_name: 'LM Studio (local)', model_id: '', endpoint: 'http://127.0.0.1:1234', priority: 210, metadata: { preset: 'local' } },
    'llama-cpp-server': { connection_id: 'llama-cpp-server', provider_id: 'local', connection_type: 'llama_cpp_server', auth_method: 'local', display_name: 'llama.cpp server (local)', model_id: '', endpoint: 'http://127.0.0.1:8080', priority: 220, metadata: { preset: 'local' } },
    'localai-local': { connection_id: 'localai-local', provider_id: 'local', connection_type: 'localai_local', auth_method: 'local', display_name: 'LocalAI (local)', model_id: '', endpoint: 'http://127.0.0.1:8080', priority: 230, metadata: { preset: 'local' } },
    'vllm-local': { connection_id: 'vllm-local', provider_id: 'local', connection_type: 'vllm_local', auth_method: 'local', display_name: 'vLLM (local)', model_id: '', endpoint: 'http://127.0.0.1:8000', priority: 240, metadata: { preset: 'local' } },
    'openai-compatible-local': { connection_id: 'openai-compatible-local', provider_id: 'local', connection_type: 'openai_compatible_local', auth_method: 'local', display_name: 'Custom OpenAI-compatible (local)', model_id: '', endpoint: 'http://127.0.0.1:1234', priority: 250, metadata: { preset: 'local' } },
  };
  const load = () => fetch(`http://localhost:${activePort}/api/provider-layer`)
    .then(r => r.json())
    .then(data => setState(data))
    .catch(err => setMessage(`Provider layer unavailable: ${err.message}`));
  useEffect(() => { load(); }, [activePort]);
  // Mirrors provider_router.LOCAL_TYPES exactly. Substring matching is not safe here:
  // 'llama_cpp_server' contains neither 'local' nor 'ollama', so a substring test would
  // wrongly treat a local runtime as a cloud connection.
  const LOCAL_CONNECTION_TYPES = new Set(['ollama_local', 'lm_studio_local', 'llama_cpp_server', 'localai_local', 'vllm_local', 'openai_compatible_local']);
  const isLocalConnection = (connection) => LOCAL_CONNECTION_TYPES.has(connection?.connection_type || '');
  const kind = (connection) => {
    const type = connection.connection_type || '';
    if (type.includes('subscription') || type.includes('google_account')) return 'subscription / official CLI';
    if (type.includes('api_key')) return 'official API key';
    if (isLocalConnection(connection)) return 'local runtime';
    if (type.includes('opencode')) return 'OpenCode-owned provider';
    return 'custom';
  };
  const testConnection = (connectionId) => {
    setTesting(connectionId); setMessage('Testing connection readiness...');
    fetch(`http://localhost:${activePort}/api/provider-connections/${connectionId}/test`, { method: 'POST' })
      .then(async r => { const data = await r.json(); if (!r.ok) throw new Error(data.detail || 'Connection test failed'); return data; })
      .then(data => { setResults(prev => ({ ...prev, [connectionId]: data })); setMessage(`${connectionId}: ${data.result?.ready ? 'READY' : data.result?.status || 'not ready'} ${data.result?.message || ''}`); addLog?.(`[Provider Layer]: ${connectionId} ${data.result?.status || 'tested'}.`); load(); })
      .catch(err => setMessage(`Test failed: ${err.message}`))
      .finally(() => setTesting(''));
  };
  const patchConnection = (connectionId, patch) => fetch(`http://localhost:${activePort}/api/provider-connections/${connectionId}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch) })
    .then(async r => { const data = await r.json(); if (!r.ok) throw new Error(data.detail?.message || data.detail || 'Connection update failed'); return data; })
    .then(data => { setMessage(`${connectionId}: saved`); addLog?.(`[Provider Layer]: ${connectionId} updated.`); load(); return data; })
    .catch(err => setMessage(`Update failed: ${err.message}`));
  const saveCredential = (connectionId) => {
    const api_key = apiKeys[connectionId] || '';
    if (!api_key.trim()) { setMessage('API key is required.'); return; }
    fetch(`http://localhost:${activePort}/api/provider-connections/${connectionId}/credential`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ api_key }) })
      .then(async r => { const data = await r.json(); if (!r.ok) throw new Error(data.detail?.message || data.detail || 'Credential save failed'); return data; })
      .then(data => { setApiKeys(prev => ({ ...prev, [connectionId]: '' })); setMessage(`${connectionId}: credential saved ${data.masked || ''}`); testConnection(connectionId); })
      .catch(err => setMessage(`Credential save failed: ${err.message}`));
  };
  const removeCredential = (connectionId) => {
    if (!window.confirm('Remove the saved API credential reference secret from the secure backend?')) return;
    fetch(`http://localhost:${activePort}/api/provider-connections/${connectionId}/credential`, { method: 'DELETE' })
      .then(async r => { const data = await r.json(); if (!r.ok) throw new Error(data.detail?.message || data.detail || 'Credential removal failed'); return data; })
      .then(() => { setMessage(`${connectionId}: credential removed`); load(); })
      .catch(err => setMessage(`Credential removal failed: ${err.message}`));
  };
  const loadModels = (connectionId) => fetch(`http://localhost:${activePort}/api/provider-connections/${connectionId}/models`)
    .then(async r => { const data = await r.json(); if (!r.ok) throw new Error(data.detail || 'Model listing failed'); return data; })
    .then(data => { setModels(prev => ({ ...prev, [connectionId]: data })); setMessage(data.supported === false ? data.message : `${connectionId}: models loaded`); })
    .catch(err => setMessage(`Model listing failed: ${err.message}`));
  const login = (connectionId) => {
    const connection = connections.find(item => item.connection_id === connectionId);
    const isOpenCode = ['opencode_provider', 'opencode_oauth_bridge'].includes(connection?.connection_type) || connection?.provider_id === 'opencode_bridge';
    const url = isOpenCode ? '/api/opencode/authenticate' : `/api/provider-connections/${connectionId}/login`;
    return fetch(`http://localhost:${activePort}${url}`, { method: 'POST' }).then(async r => { const data = await r.json(); if (!r.ok) throw new Error(data.detail?.message || data.detail || 'Login unsupported'); setMessage(data.message || data.result?.message || 'Login started'); });
  };
  const logout = (connectionId) => fetch(`http://localhost:${activePort}/api/provider-connections/${connectionId}/logout`, { method: 'POST' }).then(async r => { const data = await r.json(); if (!r.ok) throw new Error(data.detail?.message || data.detail || 'Logout unsupported'); setMessage('Logout completed'); load(); }).catch(err => setMessage(`Logout failed: ${err.message}`));
  const deleteConnection = (connectionId, assigned) => {
    const delete_credential = window.confirm('Also delete the secure credential for this connection?');
    if (assigned.length && !window.confirm(`Connection is assigned to: ${assigned.join(', ')}. Delete and clean assignments?`)) return;
    if (!window.confirm(`Delete provider connection ${connectionId}?`)) return;
    fetch(`http://localhost:${activePort}/api/provider-connections/${connectionId}`, { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirmed: true, delete_credential }) })
      .then(async r => { const data = await r.json(); if (!r.ok) throw new Error(data.detail?.message || data.detail || 'Delete failed'); return data; })
      .then(() => { setMessage(`${connectionId}: deleted`); load(); })
      .catch(err => setMessage(`Delete failed: ${err.message}`));
  };
  const saveAgentAssignment = (agentId) => {
    const draft = agentDrafts[agentId] || state.agent_assignments?.[agentId] || {};
    fetch(`http://localhost:${activePort}/api/agents/${agentId}/config`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(draft) })
      .then(async r => { const data = await r.json(); if (!r.ok) throw new Error(data.detail?.message || data.detail || 'Agent assignment save failed'); return data; })
      .then(() => { setMessage(`${agentId}: assignment saved`); load(); })
      .catch(err => setMessage(`Agent assignment failed: ${err.message}`));
  };
  const addProviderTemplate = () => {
    const template = providerTemplates[providerTemplate];
    if (!template) return;
    fetch(`http://localhost:${activePort}/api/provider-connections`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(template) })
      .then(async r => { const data = await r.json(); if (!r.ok) throw new Error(data.detail?.message || data.detail || 'Provider creation failed'); return data; })
      .then(data => { setMessage(`${data.connection?.display_name || template.display_name} added. Authenticate or test it next.`); addLog?.(`[Provider Layer]: Added ${template.display_name}.`); load(); })
      .catch(err => setMessage(`Add provider failed: ${err.message}`));
  };
  if (!state) return <section className="provider-setup"><div className="text-xs" style={{ color: 'var(--text-muted)' }}>Loading AI provider connection layer...</div></section>;
  const connections = state.connections || [];
  return <section className="provider-setup">
    <div className="provider-heading"><div><strong>AI Providers and Connections</strong><p>Universal connection layer using the existing ExecutionBrief to ProviderAdapter to AgentEvent contract. Subscription login belongs to official CLIs; Studio never reads CLI OAuth tokens. API fallback is disabled by default. Ollama coding: Без отдельной оплаты API. Используются ресурсы вашего компьютера.</p></div><span className="connection-state">{connections.length} connections</span></div>
    {message && <p className="provider-message" role="status">{message}</p>}
    <div className="capability-grid"><span>Adapter contract <b>{state.adapter_contract}</b></span><span>Registered adapters <b>{(state.registered_connection_types || []).length}</b></span><span>API fallback <b>{state.api_fallback_default}</b></span><span>Subscription auth <b>{state.subscription_auth_owner}</b></span></div>
    <div className="saved-connection"><strong>Add provider</strong><span>Choose a ready-made provider template, then authenticate/test it.</span><label>Provider template<select value={providerTemplate} onChange={e => setProviderTemplate(e.target.value)}>{Object.entries(providerTemplates).map(([key, item]) => <option key={key} value={key}>{item.display_name}</option>)}</select></label><code>{providerTemplates[providerTemplate]?.connection_type}</code><button type="button" onClick={addProviderTemplate}>Add Provider</button></div>
    <div className="provider-actions"><button type="button" onClick={load}>Refresh</button></div>
    <div className="provider-message">Credential backend: <b>{state.credential_backend?.mode || 'unknown'}</b> · {state.credential_backend?.remediation || (state.credential_backend?.can_save ? 'secure save available' : 'save unavailable')}</div>
    <div className="provider-card-grid">
      {connections.map(connection => { const detail = results[connection.connection_id] || {}; const caps = detail.capabilities || {}; const status = detail.result?.status || connection.metadata?.last_validation_status || 'not tested'; const isApi = (connection.connection_type || '').includes('api_key'); const isCli = connection.auth_method === 'delegated_cli_login'; const unsupported = status === 'unsupported'; const assigned = Object.entries(state.agent_assignments || {}).filter(([, cfg]) => cfg?.connection_id === connection.connection_id || cfg?.primary_connection === connection.connection_id || (cfg?.fallbacks || []).includes(connection.connection_id)).map(([id]) => id); const modelState = models[connection.connection_id] || {}; const modelList = modelState.models || []; const modelsTried = connection.connection_id in models; const statusTone = ['ready', 'passed'].includes(status) ? 'ready' : ['not_installed', 'unsupported', 'failed', 'error'].includes(status) ? 'error' : 'pending'; return (
        <div className={`provider-card provider-card-${statusTone}`} key={connection.connection_id}>
          <div className="provider-card-header">
            <div className="provider-card-title"><strong>{connection.display_name || connection.connection_id}</strong><span>{kind(connection)}{connection.enabled === false ? ' · disabled' : ''}</span></div>
            <span className={`provider-status-badge provider-status-${statusTone}`}>{status}</span>
          </div>
          <div className="provider-card-model-row">
            {modelList.length > 0
              ? <select value={connection.model_id || ''} onChange={e => patchConnection(connection.connection_id, { model_id: e.target.value })}><option value="">Select a model</option>{modelList.map(m => <option key={m.id || m} value={m.id || m}>{m.display_name || m.id || m}</option>)}</select>
              : <input value={manualModels[connection.connection_id] ?? connection.model_id ?? ''} onChange={e => setManualModels(prev => ({ ...prev, [connection.connection_id]: e.target.value }))} placeholder={modelsTried ? 'Model listing unsupported - enter model ID' : 'No model selected yet'} />}
            {modelList.length === 0 && <button type="button" onClick={() => patchConnection(connection.connection_id, { model_id: manualModels[connection.connection_id] ?? connection.model_id ?? '' })}>Save model</button>}
            <button type="button" onClick={() => loadModels(connection.connection_id)}>{modelsTried ? 'Refresh models' : 'Load models'}</button>
          </div>
          {isApi && <div className="provider-card-credential"><input type="password" autoComplete="off" value={apiKeys[connection.connection_id] || ''} onChange={e => setApiKeys(prev => ({ ...prev, [connection.connection_id]: e.target.value }))} placeholder="Paste API key" /><button type="button" onClick={() => saveCredential(connection.connection_id)} disabled={!state.credential_backend?.can_save}>Save/replace key</button><button type="button" onClick={() => removeCredential(connection.connection_id)}>Remove key</button>{!state.credential_backend?.can_save && <small style={{ color: 'var(--danger)' }}>{state.credential_backend?.remediation || 'Secure credential save is unavailable.'}</small>}</div>}
          {isLocalConnection(connection) && <div className="provider-card-credential"><input value={endpointDrafts[connection.connection_id] ?? connection.endpoint ?? ''} onChange={e => setEndpointDrafts(prev => ({ ...prev, [connection.connection_id]: e.target.value }))} placeholder="http://127.0.0.1:8080" /><button type="button" onClick={() => patchConnection(connection.connection_id, { endpoint: endpointDrafts[connection.connection_id] ?? connection.endpoint ?? '' })}>Save endpoint</button></div>}
          <div className="provider-card-actions">
            <button type="button" disabled={testing === connection.connection_id} onClick={() => testConnection(connection.connection_id)}>{testing === connection.connection_id ? 'Testing…' : 'Test'}</button>
            {isCli && <button type="button" disabled={unsupported} onClick={() => login(connection.connection_id).catch(err => setMessage(`Login failed: ${err.message}`))}>Login</button>}
            {isCli && <button type="button" onClick={() => logout(connection.connection_id)}>Logout</button>}
            <button type="button" onClick={() => patchConnection(connection.connection_id, { enabled: connection.enabled === false })}>{connection.enabled === false ? 'Enable' : 'Disable'}</button>
            <button type="button" className="semantic-danger-action" onClick={() => deleteConnection(connection.connection_id, assigned)}>Delete</button>
          </div>
          <details className="provider-card-details"><summary>Details</summary>
            <div className="capability-grid">
              <span>Provider <b>{connection.provider_id || '-'}</b></span>
              <span>Credential <b>{detail.credential_state || (connection.credential_reference ? 'reference only' : 'none')}</b></span>
              <span>Cost <b>{detail.cost_mode || 'unknown'}</b></span>
              <span>Locality <b>{detail.privacy_locality || 'unknown'}</b></span>
              <span>Last validation <b>{detail.last_validation || connection.metadata?.last_validation || 'never'}</b></span>
              <span>Priority <b>{connection.priority ?? 100}</b></span>
            </div>
            <div className="mt-2"><input style={{ width: 72 }} type="number" value={connection.priority ?? 100} onChange={e => patchConnection(connection.connection_id, { priority: Number(e.target.value) })} title="Priority" /></div>
            {assigned.length > 0 && <p className="provider-message" style={{ color: 'var(--warning)' }}>Assigned to: {assigned.join(', ')}</p>}
            <p className="provider-message">Capabilities: {Object.entries(caps).filter(([, v]) => v === true).map(([k]) => k).join(', ') || 'not validated'}</p>
            {detail.diagnostics && <details><summary>Sanitized diagnostics</summary><pre>{JSON.stringify(detail.diagnostics, null, 2)}</pre></details>}
          </details>
        </div>
      ); })}
      {!connections.length && <p className="provider-message">No connections yet. Add one above.</p>}
    </div>
    <details className="provider-card-details">
      <summary>Advanced: per-agent fallbacks &amp; locality policy</summary>
      <p className="provider-message">Fallback connections, fallback mode, required capabilities, and locality restrictions per agent. Provider and model are set in the Agents section below.</p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-2">
        {Object.keys(state.agent_assignments || {}).map(agentId => { const draft = { ...(state.agent_assignments?.[agentId] || {}), ...(agentDrafts[agentId] || {}) }; const selected = connections.find(c => c.connection_id === (draft.primary_connection || draft.connection_id)); const localOnlyWarning = draft.locality_policy === 'local_only' && selected && !isLocalConnection(selected); const disabledWarning = selected && selected.enabled === false; return <div className="saved-connection" key={`agent-${agentId}`}><strong>{agentId}</strong>{localOnlyWarning && <small style={{ color: 'var(--warning)' }}>LOCAL_ONLY blocks the selected non-local connection.</small>}{disabledWarning && <small style={{ color: 'var(--warning)' }}>Selected connection is disabled.</small>}<label>Fallbacks<input value={(draft.fallbacks || []).join(',')} onChange={e => setAgentDrafts(prev => ({ ...prev, [agentId]: { ...draft, fallbacks: e.target.value.split(',').map(v => v.trim()).filter(Boolean) } }))} placeholder="connection-a,connection-b" /></label><label>Fallback mode<select value={draft.fallback_mode || 'ask_user'} onChange={e => setAgentDrafts(prev => ({ ...prev, [agentId]: { ...draft, fallback_mode: e.target.value } }))}><option value="ask_user">Ask user</option><option value="stop">Stop</option><option value="subscription_only">Subscription only</option><option value="local_only">Local only</option><option value="allow_api">Allow API</option></select></label><label>Locality<select value={draft.locality_policy || 'any'} onChange={e => setAgentDrafts(prev => ({ ...prev, [agentId]: { ...draft, locality_policy: e.target.value } }))}><option value="any">Any</option><option value="local_only">Local only</option><option value="subscription_allowed">Subscription allowed</option><option value="api_allowed">API allowed</option></select></label><label>Required capabilities<input value={(draft.required_capabilities || []).join(',')} onChange={e => setAgentDrafts(prev => ({ ...prev, [agentId]: { ...draft, required_capabilities: e.target.value.split(',').map(v => v.trim()).filter(Boolean) } }))} placeholder="streaming,code_generation" /></label><label><input type="checkbox" checked={!!draft.allow_paid_api} onChange={e => setAgentDrafts(prev => ({ ...prev, [agentId]: { ...draft, allow_paid_api: e.target.checked } }))} /> Allow paid API fallback</label><button type="button" onClick={() => saveAgentAssignment(agentId)}>Save assignment</button></div>; })}
      </div>
    </details>
  </section>;
}

function GlobalAIInheritanceSettings({ activePort, cfg, reload, addLog }) {
  const [globalAI, setGlobalAI] = useState(cfg.global_ai || {});
  const [connectionId, setConnectionId] = useState(cfg.global_ai?.connection_id || '');
  const [model, setModel] = useState(cfg.global_ai?.model || '');
  const [temperature, setTemperature] = useState(cfg.global_ai?.temperature ?? 0.2);
  const [topP, setTopP] = useState(cfg.global_ai?.top_p ?? '');
  const [topK, setTopK] = useState(cfg.global_ai?.top_k ?? '');
  const [maxTokens, setMaxTokens] = useState(cfg.global_ai?.max_tokens ?? '');
  const [agents, setAgents] = useState({});
  const [connections, setConnections] = useState(cfg.provider_connections || []);
  const [modelOptions, setModelOptions] = useState([]);
  const [loadingConnections, setLoadingConnections] = useState(false);
  const [loadingModels, setLoadingModels] = useState(false);
  const [applyResult, setApplyResult] = useState(null);
  const [agentDrafts, setAgentDrafts] = useState({});
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const selected = connections.find(c => c.connection_id === connectionId) || connections[0];
  const textCapableConnections = connections.filter(c => (c.available_models || []).some(m => m?.capabilities?.text_input !== false) || c.supports_provider_auth || c.auth_method === 'delegated_cli_login');
  const loadedModels = modelOptions.length ? modelOptions : (selected?.available_models || []);
  const models = model && !loadedModels.some(m => m.id === model) ? [{ id: model, capabilities: {}, unavailable: true }, ...loadedModels] : loadedModels;
  const selectedModel = models.find(m => m.id === model);
  const supportsTopK = selected?.provider ? ['nvidia', 'together', 'ollama'].includes(selected.provider) : false;

  const loadAgents = () => fetch(`http://localhost:${activePort}/api/agents`).then(r => r.json()).then(data => { setAgents(data); setAgentDrafts(Object.fromEntries(Object.entries(data).map(([id, agent]) => [id, { temperature: agent.effective_ai?.temperature ?? '', top_p: agent.effective_ai?.top_p ?? '', top_k: agent.effective_ai?.top_k ?? '', max_tokens: agent.effective_ai?.max_tokens ?? '' }]))); }).catch(() => setAgents({}));
  const loadGlobal = () => {
    setLoadingConnections(true);
    return fetch(`http://localhost:${activePort}/api/config/ai/global`).then(r => r.json()).then(data => {
      const nextGlobal = data.global_ai || cfg.global_ai || {};
      const nextConnections = data.connections || cfg.provider_connections || [];
      const mapped = nextConnections.find(c => c.connection_id === nextGlobal.connection_id) || nextConnections.find(c => c.provider === nextGlobal.provider) || nextConnections[0];
      setGlobalAI(nextGlobal);
      setConnections(nextConnections);
      setConnectionId(mapped?.connection_id || nextGlobal.connection_id || '');
      setModel(nextGlobal.model || '');
      setTemperature(nextGlobal.temperature ?? 0.2);
      setTopP(nextGlobal.top_p ?? '');
      setTopK(nextGlobal.top_k ?? '');
      setMaxTokens(nextGlobal.max_tokens ?? '');
      setMessage(mapped ? '' : 'No configured provider connection is available. Save and test a provider below.');
      return mapped?.connection_id || '';
    }).then(id => id ? loadModels(id, true) : null).catch(err => setMessage(`Failed to load global AI configuration: ${err.message}`)).finally(() => setLoadingConnections(false));
  };
  const loadModels = (id, preserve = false) => {
    if (!id) { setModelOptions([]); return Promise.resolve(); }
    setLoadingModels(true);
    return fetch(`http://localhost:${activePort}/api/provider-connections/${encodeURIComponent(id)}/models`).then(r => r.json()).then(data => {
      const nextModels = (data.models || []).filter(m => m?.capabilities?.text_input !== false);
      setModelOptions(nextModels);
      if (!preserve) setModel('');
    }).catch(err => { setModelOptions([]); setMessage(`Failed to load models: ${err.message}`); }).finally(() => setLoadingModels(false));
  };
  useEffect(() => { loadGlobal(); loadAgents(); }, [activePort]);

  const saveGlobal = () => {
    const conn = connections.find(c => c.connection_id === connectionId) || selected;
    setBusy(true);
    fetch(`http://localhost:${activePort}/api/config/ai/global`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ connection_id: connectionId, provider: conn?.provider || globalAI.provider || cfg.provider, model, temperature: parseFloat(temperature), top_p: topP === '' ? null : parseFloat(topP), top_k: topK === '' ? null : parseInt(topK, 10), max_tokens: maxTokens === '' ? null : parseInt(maxTokens, 10), enabled: true }) })
      .then(r => r.json()).then(data => { setGlobalAI(data.global_ai || {}); setMessage(`Global AI configuration saved: ${data.global_ai?.model}.`); addLog?.(`[AI Settings]: Global connection set to ${data.global_ai?.connection_id || connectionId}.`); addLog?.(`[AI Settings]: Global model set to ${data.global_ai?.model}.`); reload(); loadGlobal(); loadAgents(); window.dispatchEvent(new CustomEvent('freelancerstudio:provider-connections-updated')); })
      .catch(err => setMessage(`Save failed: ${err.message}`)).finally(() => setBusy(false));
  };

  const applyAll = () => {
    setBusy(true);
    fetch(`http://localhost:${activePort}/api/agents/apply-global-inheritance`, { method: 'POST' })
      .then(r => r.json()).then(data => { setApplyResult(data); const updated = data.updated || []; const skipped = data.skipped || []; setMessage(updated.length ? `Applied global inheritance to ${updated.length} agent(s).` : (skipped.length ? 'No compatible agents were updated. See skipped reasons.' : 'No agents were updated. Save a global connection and model first.')); addLog?.('[AI Settings]: Applied global inheritance to agents.'); loadAgents(); window.dispatchEvent(new CustomEvent('freelancerstudio:provider-connections-updated')); })
      .catch(err => setMessage(`Apply failed: ${err.message}`)).finally(() => setBusy(false));
  };

  const updateAgent = (agentId, patch) => {
    setBusy(true);
    fetch(`http://localhost:${activePort}/api/agents/${agentId}/config`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch) })
      .then(r => r.json()).then(() => { setMessage(`${agents[agentId]?.name || agentId} settings saved.`); loadAgents(); }).catch(err => setMessage(`Agent save failed: ${err.message}`)).finally(() => setBusy(false));
  };
  const formatList = (items) => (items || []).map(item => typeof item === 'string' ? item : `${item.agent_id} - ${item.reason}`).join('\n');
  return <section className="provider-setup"><div className="provider-heading"><div><strong>Global AI Configuration</strong><p>Use one Studio provider/model for compatible agents while keeping per-agent generation overrides.</p></div><span className="connection-state">{globalAI.provider || cfg.provider}/{globalAI.model || cfg.model}</span></div><div className="provider-form"><label>Provider connection<select value={connectionId} disabled={busy || loadingConnections} onChange={e => { setConnectionId(e.target.value); setModel(''); loadModels(e.target.value); }}><option value="">{loadingConnections ? 'Loading provider connections...' : textCapableConnections.length ? 'Select provider connection' : 'No tested text-capable connection available'}</option>{textCapableConnections.map(c => <option key={c.connection_id} value={c.connection_id}>{c.display_name || c.name || c.provider}{c.tested_status === 'passed' ? ' - tested' : ' - not tested'}</option>)}</select></label><label>Default model{models.length > 0 ? <select value={model} disabled={busy || loadingModels || !connectionId} onChange={e => setModel(e.target.value)}><option value="">{loadingModels ? 'Loading models...' : 'Select model'}</option>{models.map(m => <option key={m.id} value={m.id}>{m.id}{m.unavailable ? ' (unavailable for this connection)' : ''}</option>)}</select> : <input className="ai-settings-field" value={model} disabled={busy || loadingModels || !connectionId} onChange={e => setModel(e.target.value)} placeholder={loadingModels ? 'Loading models...' : connectionId ? 'No tested text-capable models are available for this connection' : 'Select a provider connection first'} />}</label><label>Default temperature<input className="ai-settings-field" type="number" min="0" max="2" step="0.05" value={temperature} onChange={e => setTemperature(e.target.value)} /></label><label>Default top_p<input className="ai-settings-field" type="number" min="0" max="1" step="0.05" value={topP ?? ''} placeholder="Not sent" onChange={e => setTopP(e.target.value)} /></label><label>Default top_k<input className="ai-settings-field" type="number" min="1" step="1" value={topK ?? ''} placeholder={supportsTopK ? 'Not sent' : 'Not supported by this provider'} onChange={e => setTopK(e.target.value)} /></label><label>Default max tokens<input className="ai-settings-field" type="number" min="1" step="1" value={maxTokens ?? ''} placeholder="Not sent" onChange={e => setMaxTokens(e.target.value)} /></label></div>{selectedModel && <div className="capability-grid"><span>Text <b>{selectedModel.capabilities?.text_input === false ? 'No' : 'Yes'}</b></span><span>Images <b>{selectedModel.capabilities?.image_input === true ? 'Yes' : selectedModel.capabilities?.image_input === false ? 'No' : 'Unknown'}</b></span><span>Structured output <b>{selectedModel.capabilities?.structured_output === false ? 'No' : 'Available/Unknown'}</b></span><span>Streaming <b>{selectedModel.capabilities?.streaming === false ? 'No' : 'Available/Unknown'}</b></span></div>}{message && <p className="provider-message" role="status">{message}</p>}<div className="provider-actions"><button type="button" className="primary" onClick={saveGlobal} disabled={busy || !connectionId || !model}>Save global AI</button><button type="button" onClick={applyAll} disabled={busy || !connectionId || !model}>Apply to compatible agents</button><button type="button" onClick={loadGlobal} disabled={busy}>Refresh connections</button></div>{applyResult?.updated?.length > 0 && <pre className="mini-log">Applied successfully:\n{formatList(applyResult.updated)}</pre>}{applyResult?.skipped?.length > 0 && <pre className="mini-log">Skipped:\n{formatList(applyResult.skipped)}</pre>}</section>;
}

function AgentModelManagerSettings({ activePort }) {
  const [agents, setAgents] = useState({});
  const [connections, setConnections] = useState([]);
  const [drafts, setDrafts] = useState({});
  const [modelOptions, setModelOptions] = useState({});
  const [selectedAgentId, setSelectedAgentId] = useState('');
  const [saving, setSaving] = useState({});
  const [message, setMessage] = useState('');
  const [providerSearch, setProviderSearch] = useState('');
  const [modelSearch, setModelSearch] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [editingPrompt, setEditingPrompt] = useState(false);
  const [promptDrafts, setPromptDrafts] = useState({});

  const normalizeNumber = value => String(value ?? '').trim().replace(',', '.');
  const initialDraft = (agent) => {
    const eff = agent.effective_ai || {};
    return {
      use_global_connection: eff.use_global_connection !== false,
      connection_id: eff.connection_id || '',
      use_global_model: eff.use_global_model !== false,
      model: eff.model || '',
      use_global_generation_parameters: eff.use_global_generation_parameters !== false,
      temperature: eff.temperature ?? '',
      top_p: eff.top_p ?? '',
      top_k: eff.top_k ?? '',
      max_tokens: eff.max_tokens ?? '',
    };
  };
  const textConnections = connections.filter(c => (c.available_models || []).some(m => m?.capabilities?.text_input !== false) || c.supports_provider_auth || c.auth_method === 'delegated_cli_login');
  const setDraft = (id, patch) => setDrafts(prev => ({ ...prev, [id]: { ...prev[id], ...patch } }));
  const loadModels = (agentId, connectionId) => {
    if (!connectionId) { setModelOptions(prev => ({ ...prev, [agentId]: [] })); return Promise.resolve(); }
    return fetch(`http://localhost:${activePort}/api/provider-connections/${encodeURIComponent(connectionId)}/models`)
      .then(r => r.json())
      .then(data => setModelOptions(prev => ({ ...prev, [agentId]: (data.models || []).filter(m => m?.capabilities?.text_input !== false) })))
      .catch(() => setModelOptions(prev => ({ ...prev, [agentId]: [] })));
  };
  const load = () => Promise.all([
    fetch(`http://localhost:${activePort}/api/agents`).then(r => r.json()),
    fetch(`http://localhost:${activePort}/api/provider-connections`).then(r => r.json()),
  ]).then(([agentData, connectionData]) => {
    const nextDrafts = Object.fromEntries(Object.entries(agentData || {}).map(([id, agent]) => [id, initialDraft(agent)]));
    setAgents(agentData || {});
    setConnections(connectionData.connections || []);
    setDrafts(nextDrafts);
    setPromptDrafts(Object.fromEntries(Object.entries(agentData || {}).map(([id, agent]) => [id, agent.custom_prompt || ''])));
    setSelectedAgentId(current => current && agentData?.[current] ? current : Object.keys(agentData || {})[0] || '');
    Object.entries(nextDrafts).forEach(([id, draft]) => loadModels(id, draft.connection_id));
  }).catch(err => setMessage(`Agent model settings failed to load: ${err.message}`));
  useEffect(() => {
    load();
    const refresh = () => load();
    window.addEventListener('freelancerstudio:provider-connections-updated', refresh);
    return () => window.removeEventListener('freelancerstudio:provider-connections-updated', refresh);
  }, [activePort]);

  const chooseConnection = (id, connectionId) => {
    const connection = connections.find(c => c.connection_id === connectionId);
    const firstModel = ((connection?.available_models || []).find(m => m?.capabilities?.text_input !== false) || {}).id || '';
    setDraft(id, { use_global_connection: false, connection_id: connectionId, use_global_model: !firstModel, model: firstModel });
    loadModels(id, connectionId);
  };
  const saveAgent = (id) => {
    const d = drafts[id] || {};
    const body = {
      use_global_connection: !!d.use_global_connection,
      connection_id: d.use_global_connection ? null : d.connection_id,
      use_global_model: !!d.use_global_model,
      model: d.use_global_model ? null : d.model,
      use_global_generation_parameters: !!d.use_global_generation_parameters,
      temperature: d.use_global_generation_parameters ? null : normalizeNumber(d.temperature),
      top_p: d.use_global_generation_parameters ? null : normalizeNumber(d.top_p),
      top_k: d.use_global_generation_parameters ? null : normalizeNumber(d.top_k),
      max_tokens: d.use_global_generation_parameters ? null : normalizeNumber(d.max_tokens),
    };
    setSaving(prev => ({ ...prev, [id]: true }));
    fetch(`http://localhost:${activePort}/api/agents/${id}/config`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(async r => { const data = await r.json(); if (!r.ok) throw new Error(data.detail?.message || data.detail || 'Save failed'); return data; })
      .then(() => { setMessage(`${agents[id]?.name || id} AI choice saved. Prompt was not changed.`); load(); })
      .catch(err => setMessage(`${agents[id]?.name || id} save failed: ${err.message}`))
      .finally(() => setSaving(prev => ({ ...prev, [id]: false })));
  };
  const savePrompt = (id) => {
    setSaving(prev => ({ ...prev, [id]: true }));
    fetch(`http://localhost:${activePort}/api/agents/${id}/config`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ custom_prompt: promptDrafts[id] || '' }) })
      .then(async r => { const data = await r.json(); if (!r.ok) throw new Error(data.detail?.message || data.detail || 'Prompt save failed'); return data; })
      .then(() => { setMessage(`${agents[id]?.name || id} prompt saved.`); setEditingPrompt(false); load(); })
      .catch(err => setMessage(`${agents[id]?.name || id} prompt save failed: ${err.message}`))
      .finally(() => setSaving(prev => ({ ...prev, [id]: false })));
  };
  const resetPrompt = (id) => {
    setPromptDrafts(prev => ({ ...prev, [id]: '' }));
    setSaving(prev => ({ ...prev, [id]: true }));
    fetch(`http://localhost:${activePort}/api/agents/${id}/config`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ custom_prompt: '' }) })
      .then(async r => { const data = await r.json(); if (!r.ok) throw new Error(data.detail?.message || data.detail || 'Prompt reset failed'); return data; })
      .then(() => { setMessage(`${agents[id]?.name || id} prompt reset to default.`); setEditingPrompt(false); load(); })
      .catch(err => setMessage(`${agents[id]?.name || id} prompt reset failed: ${err.message}`))
      .finally(() => setSaving(prev => ({ ...prev, [id]: false })));
  };
  const authenticateConnection = (connection) => {
    if (!connection) return;
    const isOpenCode = ['opencode_oauth_bridge', 'opencode_provider'].includes(connection.connection_type) || connection.provider === 'opencode_bridge';
    const url = isOpenCode ? `/api/opencode/authenticate` : `/api/provider-connections/${encodeURIComponent(connection.connection_id)}/login`;
    setMessage('Opening provider authentication...');
    fetch(`http://localhost:${activePort}${url}`, { method: 'POST' })
      .then(async r => { const data = await r.json(); if (!r.ok) throw new Error(data.detail?.message || data.detail || 'Authentication is not supported for this connection'); return data; })
      .then(data => setMessage(data.message || data.result?.message || 'Complete authentication in the provider-owned flow, then test the connection.'))
      .catch(err => setMessage(`Authentication failed: ${err.message}`));
  };
  const testConnection = (connection) => {
    if (!connection) return;
    setMessage(`Testing ${connection.display_name || connection.name || connection.connection_id}...`);
    fetch(`http://localhost:${activePort}/api/provider-connections/${encodeURIComponent(connection.connection_id)}/test`, { method: 'POST' })
      .then(async r => { const data = await r.json(); if (!r.ok) throw new Error(data.detail?.message || data.detail || 'Test failed'); return data; })
      .then(data => setMessage(data.result?.message || data.message || 'Connection test completed.'))
      .catch(err => setMessage(`Connection test failed: ${err.message}`));
  };

  const entries = Object.entries(agents).filter(([, agent]) => agent?.builtin !== false);
  const selectedEntry = entries.find(([id]) => id === selectedAgentId) || entries[0];
  if (!selectedEntry) return <section className="provider-setup"><div className="provider-heading"><div><strong>Agent Model Manager</strong><p>No agents loaded.</p></div></div></section>;
  const [id, agent] = selectedEntry;
  const eff = agent.effective_ai || {};
  const d = drafts[id] || initialDraft(agent);
  const connectionId = d.use_global_connection ? eff.connection_id : d.connection_id;
  const selectedConnection = connections.find(c => c.connection_id === connectionId);
  const models = modelOptions[id] || (selectedConnection?.available_models || []).filter(m => m?.capabilities?.text_input !== false);
  const providerNeedle = providerSearch.trim().toLowerCase();
  const modelNeedle = modelSearch.trim().toLowerCase();
  const visibleConnections = textConnections.filter(c => !providerNeedle || `${c.display_name || ''} ${c.name || ''} ${c.provider || ''} ${c.connection_id || ''}`.toLowerCase().includes(providerNeedle));
  const visibleModels = models.filter(m => !modelNeedle || `${m.id || ''} ${m.name || ''} ${m.description || ''}`.toLowerCase().includes(modelNeedle));
  const shownModel = d.use_global_model ? eff.model || '' : d.model || '';
  const modelUnavailable = shownModel && models.length > 0 && !models.map(m => m.id).includes(shownModel);
  const disabled = !!saving[id];
  const canAuth = selectedConnection && (selectedConnection.supports_provider_auth || selectedConnection.auth_method === 'delegated_cli_login' || ['opencode_oauth_bridge', 'opencode_provider'].includes(selectedConnection.connection_type));
  const activePrompt = agent.custom_prompt || '';

  return <section className="provider-setup agent-model-manager"><div className="provider-heading"><div><strong>Agent Model Manager</strong><p>Pick an agent, then choose provider and model. Agent descriptions and prompts stay untouched unless you edit the prompt block.</p></div><span className="connection-state">{entries.length} agents</span></div>{message && <p className="provider-message" role="status">{message}</p>}<div className="grid grid-cols-1 xl:grid-cols-[240px_1fr] gap-3 mt-3"><aside className="rounded-lg border p-2" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-primary)' }}><div className="text-[11px] font-semibold mb-2" style={{ color: 'var(--text-secondary)' }}>Agents</div>{entries.map(([agentId, item]) => <button key={agentId} type="button" onClick={() => { setSelectedAgentId(agentId); setEditingPrompt(false); setModelSearch(''); }} className="w-full text-left rounded-md px-3 py-2 mb-1" style={{ backgroundColor: agentId === id ? 'var(--accent-bg)' : 'transparent', color: agentId === id ? 'var(--accent)' : 'var(--text-primary)' }}><div className="flex items-center justify-between gap-2"><strong>{item.name || agentId}</strong><span className="text-[10px] uppercase" style={{ color: 'var(--text-muted)' }}>{item.role}</span></div><div className="text-[10px] truncate" style={{ color: 'var(--text-muted)' }}>{item.effective_ai?.model || 'Default model'}</div></button>)}</aside><div className="space-y-3"><div className="rounded-lg border p-3" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-primary)' }}><div className="flex items-start justify-between gap-3"><div><strong>{agent.name || id}</strong><div className="text-[11px]" style={{ color: 'var(--text-secondary)' }}>{agent.role_contract_summary || agent.role || id}</div></div><span className="text-[10px] uppercase" style={{ color: eff.capability_status === 'compatible' ? 'var(--success)' : 'var(--danger)' }}>{eff.capability_status || 'unknown'}</span></div><div className="grid grid-cols-1 md:grid-cols-2 gap-2 mt-3 text-[11px]" style={{ color: 'var(--text-secondary)' }}><div>Current provider: <span className="font-mono">{eff.connection_display_name || eff.provider || 'none'}</span></div><div>Current model: <span className="font-mono">{eff.model || 'none'}</span></div></div><div className="flex flex-wrap gap-2 mt-3"><button type="button" disabled={disabled} onClick={() => setDraft(id, { use_global_connection: true, use_global_model: true })} className="px-3 py-1 rounded-md border" style={{ borderColor: d.use_global_connection && d.use_global_model ? 'var(--accent)' : 'var(--border)' }}>Use Default</button><button type="button" disabled={disabled || !selectedConnection} onClick={() => testConnection(selectedConnection)} className="px-3 py-1 rounded-md border" style={{ borderColor: 'var(--border)' }}>Test Connection</button>{canAuth && <button type="button" disabled={disabled} onClick={() => authenticateConnection(selectedConnection)} className="px-3 py-1 rounded-md border" style={{ borderColor: 'var(--border)' }}>Authenticate Provider</button>}<button type="button" disabled={disabled} onClick={() => saveAgent(id)} className="px-3 py-1 rounded-md" style={{ backgroundColor: 'var(--accent)', color: 'white' }}>{disabled ? 'Saving...' : 'Save AI Choice'}</button></div></div><div className="grid grid-cols-1 lg:grid-cols-2 gap-3"><div className="rounded-lg border p-3" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-primary)' }}><div className="provider-heading"><div><strong>Provider</strong><p>Connected providers only. Add or authenticate providers in AI Connections.</p></div></div><input value={providerSearch} onChange={e => setProviderSearch(e.target.value)} placeholder="Search providers" className="w-full mb-2" /><div className="space-y-1 max-h-72 overflow-auto">{visibleConnections.map(c => { const active = !d.use_global_connection && d.connection_id === c.connection_id; return <button key={c.connection_id} type="button" onClick={() => chooseConnection(id, c.connection_id)} className="w-full text-left rounded-md px-3 py-2 border" style={{ borderColor: active ? 'var(--accent)' : 'transparent', backgroundColor: active ? 'var(--accent-bg)' : 'transparent' }}><div className="flex items-center justify-between gap-2"><strong>{c.display_name || c.name || c.provider || c.connection_id}</strong>{active && <span>selected</span>}</div><div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>{c.provider || c.connection_type} {c.auth_method === 'delegated_cli_login' ? 'subscription/delegated' : ''}</div></button>; })}{!visibleConnections.length && <p className="text-[11px]" style={{ color: 'var(--text-muted)' }}>No provider connections match this search.</p>}</div></div><div className="rounded-lg border p-3" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-primary)' }}><div className="provider-heading"><div><strong>Model</strong><p>Changing the model only changes routing. Agent identity and prompt are preserved.</p></div></div><input value={modelSearch} onChange={e => setModelSearch(e.target.value)} placeholder="Search models" className="w-full mb-2" /><div className="space-y-1 max-h-72 overflow-auto">{visibleModels.map(m => { const active = !d.use_global_model && d.model === m.id; const caps = m.capabilities || {}; return <button key={m.id} type="button" onClick={() => setDraft(id, { use_global_model: false, model: m.id })} className="w-full text-left rounded-md px-3 py-2 border" style={{ borderColor: active ? 'var(--accent)' : 'transparent', backgroundColor: active ? 'var(--accent-bg)' : 'transparent' }}><div className="flex items-center justify-between gap-2"><strong>{m.name || m.id}</strong>{active && <span>selected</span>}</div><div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>Input {caps.image_input ? 'text, image' : 'text'} / streaming {caps.streaming === false ? 'no' : 'yes'} / tools {caps.tool_use === false ? 'no' : 'yes'}</div></button>; })}{!visibleModels.length && <p className="text-[11px]" style={{ color: 'var(--text-muted)' }}>{selectedConnection ? 'No models match this search. Refresh models in AI Connections if needed.' : 'Select a provider first.'}</p>}</div>{modelUnavailable && <p className="text-[11px] mt-2" style={{ color: 'var(--warning)' }}>Selected model is not listed for this connection; save may be rejected.</p>}</div></div><div className="rounded-lg border p-3" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-primary)' }}><button type="button" onClick={() => setShowAdvanced(value => !value)} className="font-semibold">{showAdvanced ? 'Hide' : 'Show'} Advanced Parameters</button>{showAdvanced && <div className="provider-form mt-3"><label><span><input type="checkbox" checked={!!d.use_global_generation_parameters} disabled={disabled} onChange={e => setDraft(id, { use_global_generation_parameters: e.target.checked })} /> Use global parameters</span><input disabled value={`temp ${eff.temperature ?? '-'} / top_p ${eff.top_p ?? '-'}`} readOnly /></label><label>Temperature<input disabled={disabled || d.use_global_generation_parameters} value={d.temperature} onChange={e => setDraft(id, { temperature: e.target.value })} /></label><label>Top_p<input disabled={disabled || d.use_global_generation_parameters} value={d.top_p} onChange={e => setDraft(id, { top_p: e.target.value })} /></label><label>Top_k<input disabled={disabled || d.use_global_generation_parameters} value={d.top_k} onChange={e => setDraft(id, { top_k: e.target.value })} /></label><label>Max tokens<input disabled={disabled || d.use_global_generation_parameters} value={d.max_tokens} onChange={e => setDraft(id, { max_tokens: e.target.value })} /></label></div>}</div><div className="rounded-lg border p-3" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-primary)' }}><div className="flex items-center justify-between gap-2"><div><strong>Agent Prompt</strong><p className="text-[11px]" style={{ color: 'var(--text-secondary)' }}>Prompt is independent from model/provider selection.</p></div><button type="button" onClick={() => { setPromptDrafts(prev => ({ ...prev, [id]: activePrompt })); setEditingPrompt(value => !value); }}>{editingPrompt ? 'Close' : 'Edit Prompt'}</button></div>{editingPrompt ? <div className="mt-2"><textarea rows={5} value={promptDrafts[id] || ''} onChange={e => setPromptDrafts(prev => ({ ...prev, [id]: e.target.value }))} placeholder="Leave empty to use the built-in default prompt." className="w-full font-mono" /><div className="provider-actions"><button type="button" disabled={disabled} onClick={() => savePrompt(id)}>Save Prompt</button><button type="button" disabled={disabled} onClick={() => resetPrompt(id)}>Reset to Default Prompt</button></div></div> : <div className="mt-2 rounded-md border px-3 py-2 text-[11px] font-mono" style={{ borderColor: 'var(--border)', color: activePrompt ? 'var(--text-secondary)' : 'var(--text-muted)' }}>{activePrompt || 'Using built-in default prompt. Model/provider changes will not rewrite it.'}</div>}</div></div></div></section>;
}

function ProductJudgeSettings({ activePort, addLog }) {
  const [config, setConfig] = useState(null);
  const [status, setStatus] = useState(null);
  const [connections, setConnections] = useState([]);
  const [connectionId, setConnectionId] = useState('');
  const [model, setModel] = useState('');
  const [enabled, setEnabled] = useState(true);
  const [useGlobal, setUseGlobal] = useState(false);
  const [temperature, setTemperature] = useState(0.3);
  const [topP, setTopP] = useState(0.9);
  const [topK, setTopK] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  const load = () => fetch(`http://localhost:${activePort}/api/product-judge/config`)
    .then(r => r.json())
    .then(data => {
      const cfg = data.config || {};
      const options = data.connections || [];
      const selectedId = cfg.connection_id || options[0]?.connection_id || '';
      const selected = options.find(c => c.connection_id === selectedId) || options[0];
      const models = (selected?.available_models || []).filter(m => m?.capabilities?.image_input === true);
      setConfig(cfg);
      setStatus(data.status || null);
      setConnections(options);
      setConnectionId(selected?.connection_id || selectedId);
      setModel(models.some(m => m.id === cfg.model) ? cfg.model : models[0]?.id || '');
      setEnabled(cfg.enabled !== false);
      setUseGlobal(!!cfg.use_global);
      setTemperature(cfg.temperature ?? 0.3);
      setTopP(cfg.top_p ?? 0.9);
      setTopK(cfg.top_k ?? '');
    })
    .catch(() => setMessage('Product Judge settings are unavailable.'));

  useEffect(() => {
    load();
    const refresh = () => load();
    window.addEventListener('freelancerstudio:provider-connections-updated', refresh);
    return () => window.removeEventListener('freelancerstudio:provider-connections-updated', refresh);
  }, [activePort]);

  const selected = connections.find(c => c.connection_id === connectionId);
  const models = (selected?.available_models || []).filter(m => m?.capabilities?.image_input === true);
  const hasTestedVisionConnection = connections.some(c => c.tested_status === 'passed' && (c.available_models || []).some(m => m?.capabilities?.image_input === true));
  const readableStatus = status?.available ? status.reason : `Unavailable - ${status?.reason || 'no tested vision connection'}`;
  const save = async () => {
    if (!selected || !model) { setMessage('No tested image-capable provider connection is available.'); return; }
    setBusy(true);
    const response = await fetch(`http://localhost:${activePort}/api/product-judge/config`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled, use_global: useGlobal, connection_id: connectionId, model, temperature: parseFloat(temperature), top_p: topP === '' ? null : parseFloat(topP), top_k: topK === '' ? null : parseInt(topK, 10) }) });
    const data = await response.json();
    if (data.status === 'success') { setMessage(data.readiness?.available ? 'Product Judge saved and available.' : `Saved, but unavailable: ${data.readiness?.reason || 'not ready'}`); addLog?.(`[Product Judge]: ${data.readiness?.status || 'saved'} ${model}.`); load(); } else setMessage(data.detail || 'Save failed.');
    setBusy(false);
  };
  const test = async () => {
    setBusy(true);
    const response = await fetch(`http://localhost:${activePort}/api/product-judge/test`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled, use_global: useGlobal, connection_id: connectionId, model, temperature: parseFloat(temperature), top_p: topP === '' ? null : parseFloat(topP), top_k: topK === '' ? null : parseInt(topK, 10) }) });
    const data = await response.json();
    setStatus(data);
    setMessage(data.available ? `Ready: ${data.reason}` : `Not ready: ${data.reason || data.message}`);
    setBusy(false);
  };
  return <section className="provider-setup product-judge-settings"><div className="provider-heading"><div><strong>Product Judge</strong><p>Independent Product Reviewer. Read-only, evidence-bound vision review.</p></div><span className="connection-state">{status?.available ? 'Available' : 'Unavailable'}</span></div><p className="provider-message" role="status">{readableStatus}</p>{!hasTestedVisionConnection && <div className="judge-details"><span>No tested image-capable provider connection is available.</span><button type="button" onClick={load}>Refresh connections</button><span>{connections.length ? 'Select a connection, then test provider below if needed.' : 'Configure provider and Test provider below.'}</span></div>}<div className="provider-form"><label>Provider connection<select value={connectionId} disabled={busy} onChange={e => { const next = connections.find(c => c.connection_id === e.target.value); const imageModels = (next?.available_models || []).filter(m => m?.capabilities?.image_input === true); setConnectionId(e.target.value); setModel(imageModels[0]?.id || ''); }}><option value="">{connections.length ? 'Select provider connection' : 'No provider connection saved'}</option>{connections.map(c => <option key={c.connection_id} value={c.connection_id}>{c.display_name || c.name || c.provider}{c.tested_status === 'passed' ? ' - tested' : ' - not tested'}</option>)}</select></label><label>Model<select value={model} disabled={busy} onChange={e => setModel(e.target.value)}><option value="">{models.length ? 'Select image-capable model' : 'No image-capable model available'}</option>{models.map(m => <option key={m.id} value={m.id}>{m.id}</option>)}</select></label><label>Enabled<select value={enabled ? 'yes' : 'no'} onChange={e => setEnabled(e.target.value === 'yes')}><option value="yes">Enabled</option><option value="no">Disabled</option></select></label><label>Use global provider<select value={useGlobal ? 'yes' : 'no'} onChange={e => setUseGlobal(e.target.value === 'yes')}><option value="no">No</option><option value="yes">Yes, if image-capable</option></select></label><label>Temperature<input type="number" min="0" max="2" step="0.1" value={temperature} onChange={e => setTemperature(e.target.value)} /></label><label>Top-p<input type="number" min="0" max="1" step="0.05" value={topP ?? ''} onChange={e => setTopP(e.target.value)} /></label><label>Top-k<input type="number" min="1" step="1" value={topK ?? ''} onChange={e => setTopK(e.target.value)} /></label></div><div className="judge-details"><span>Vision: <b>{selected && model ? 'Image-capable model selected' : 'Unavailable'}</b></span><span>Independence: <b>{status?.independence_level || config?.independence_level || 'unavailable'}</b></span></div><div className="provider-actions"><button type="button" className="primary" onClick={save} disabled={!selected || !model || busy}>Save Product Judge</button><button type="button" onClick={test} disabled={!selected || !model || busy}>Test Product Judge</button><button type="button" onClick={load} disabled={busy}>Refresh connections</button></div>{message && <p className="provider-message" role="status">{message}</p>}</section>;
}

function GitHubConfig({ activePort, addLog }) {
  const [cfg, setCfg] = useState({ token: '', username: '', repo: '', connected: false });
  const [dirty, setDirty] = useState(false);
  const [hasToken, setHasToken] = useState(false);

  useEffect(() => {
    fetch(`http://localhost:${activePort}/api/config/github`)
      .then(r => r.json())
      .then(d => {
        setHasToken(!!d.token);
        // Never put the masked placeholder into the real token field — keep it empty
        setCfg(prev => ({ ...prev, username: d.username || '', repo: d.repo || '', connected: d.connected || false, token: '' }));
      })
      .catch(() => {});
  }, [activePort]);

  const save = () => {
    const payload = { ...cfg };
    // If token field is empty and we already have a token on disk, don't overwrite it
    if (!payload.token && hasToken) {
      delete payload.token;
    }
    fetch(`http://localhost:${activePort}/api/config/github`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
      .then(r => r.json())
      .then(d => {
        if (cfg.token) setHasToken(true);
        setCfg(prev => ({ ...prev, connected: d.connected, token: '' }));
        setDirty(false);
        addLog(`[GitHub]: ${d.connected ? 'Connected to ' + cfg.username + '/' + cfg.repo : 'Disconnected'}.`);
      })
      .catch(() => addLog('[GitHub]: Save failed.'));
  };

  return (
    <div className="space-y-2">
      <input type="text" placeholder="GitHub username" value={cfg.username} onChange={e => { setCfg(prev => ({...prev, username: e.target.value})); setDirty(true); }}
        className="w-full bg-[var(--bg-secondary)] border border-[var(--border)] rounded-lg px-3 py-2 text-xs font-mono focus:outline-none" style={{ color: 'var(--text-primary)' }} />
      <input type="text" placeholder="Repository name" value={cfg.repo} onChange={e => { setCfg(prev => ({...prev, repo: e.target.value})); setDirty(true); }}
        className="w-full bg-[var(--bg-secondary)] border border-[var(--border)] rounded-lg px-3 py-2 text-xs font-mono focus:outline-none" style={{ color: 'var(--text-primary)' }} />
      <input type="password" placeholder={hasToken ? "Token saved — enter new to replace" : "GitHub token (classic with repo scope)"} value={cfg.token} onChange={e => { setCfg(prev => ({...prev, token: e.target.value})); setDirty(true); }}
        className="w-full bg-[var(--bg-secondary)] border border-[var(--border)] rounded-lg px-3 py-2 text-xs font-mono focus:outline-none" style={{ color: 'var(--text-primary)' }} />
      <div className="flex items-center justify-between pt-1">
        <span className="text-[10px]" style={{ color: cfg.connected ? 'var(--success)' : 'var(--text-muted)' }}>
          {cfg.connected ? '✓ Connected' : '○ Not connected'}
        </span>
        <button onClick={save} disabled={!dirty}
          className="px-3 py-1 rounded text-[10px] font-medium transition-opacity disabled:opacity-40"
          style={{ backgroundColor: 'var(--accent)', color: 'var(--accent-contrast)' }}
        >Save GitHub Config</button>
      </div>
    </div>
  );
}
