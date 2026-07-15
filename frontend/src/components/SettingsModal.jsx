import React, { useState, useEffect, useRef } from 'react';
import { tr } from '../i18n.js';
import OpenCodeConnectionSetup from './OpenCodeConnectionSetup.jsx';

const ACCENT_COLORS = [
  { name: 'Sky', color: '#0ea5e9' },
  { name: 'Indigo', color: '#6366f1' },
  { name: 'Emerald', color: '#10b981' },
  { name: 'Amber', color: '#f59e0b' },
  { name: 'Rose', color: '#f43f5e' },
  { name: 'Purple', color: '#a855f7' },
  { name: 'Cyan', color: '#06b6d4' },
  { name: 'White', color: '#f1f5f9' },
];

export default function SettingsModal({ activePort, onClose, addLog, embedded = false }) {
  const [settings, setSettings] = useState(null);
  const [activeTab, setActiveTab] = useState('appearance');
  const [loading, setLoading] = useState(true);
  const dialogRef = useRef(null);
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
    const updated = { ...settings, [key]: value };
    setSettings(updated);
    fetch(`http://localhost:${activePort}/api/config/system`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [key]: value })
    })
      .then(res => res.json())
      .then(data => {
        if (data.status === 'saved') {
          applyTheme(updated);
          addLog(`[Settings]: ${key} set to ${value}.`);
        }
      })
      .catch(err => console.error('[Settings Error]:', err));
  };

  const applyTheme = (s) => {
    const root = document.documentElement;
    if (s.accent_color) root.style.setProperty('--accent', s.accent_color);
    root.classList.toggle('theme-light', s.theme === 'light');
    root.classList.toggle('theme-reduced-motion', s.animation_speed === 'off');
    root.classList.remove('font-small', 'font-medium', 'font-large');
    if (s.font_size) root.classList.add('font-' + s.font_size);
  };

  useEffect(() => {
    if (settings) applyTheme(settings);
  }, [settings]);

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
    { id: 'ai', label: 'AI Provider' },
    { id: 'studio', label: t('studio') },
  ];

  return (
    <div className={embedded ? "settings-inline" : "settings-modal-overlay animate-fade-in"} onMouseDown={(event) => { if (!embedded && event.target === event.currentTarget) onClose?.(); }}>
      <div ref={dialogRef} role={embedded ? undefined : "dialog"} aria-modal={embedded ? undefined : "true"} aria-labelledby="settings-modal-title" className={embedded ? "settings-inline-container bg-[var(--bg-card)] border border-[var(--border)] rounded-xl w-full" : "settings-modal-container bg-[var(--bg-card)] border border-[var(--border)] rounded-xl w-full max-w-2xl shadow-2xl"} style={{ borderColor: 'var(--border)' }}>

        <div className="settings-modal-header p-4 border-b border-[var(--border)] flex justify-between items-center" style={{ backgroundColor: 'var(--bg-secondary)' }}>
          <h3 id="settings-modal-title" className="text-xs font-bold uppercase tracking-widest" style={{ color: 'var(--accent)' }}>{t('systemPreferences')}</h3>
          {!embedded && <button ref={closeButtonRef} onClick={onClose} className="text-[var(--text-muted)] hover:text-[var(--text-primary)] font-mono text-sm" aria-label="Close settings">x</button>}
        </div>

        {/* Tabs */}
        <div className="settings-modal-tabs flex border-b border-[var(--border)]" style={{ backgroundColor: 'var(--bg-secondary)' }}>
          {tabs.map(tab => (
            <button
              key={tab.id}
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

        <div className="settings-modal-body p-5 space-y-5" style={{ backgroundColor: 'var(--bg-card)' }}>
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

              {/* Animation Speed */}
              <Section label={t('animationSpeed')}>
                <div className="flex space-x-2">
                  {[
                    { value: 'off', label: '🚫 Off' },
                    { value: 'reduced', label: '🔹 Reduced' },
                    { value: 'normal', label: '▶ Normal' },
                  ].map(opt => (
                    <button
                      key={opt.value}
                      onClick={() => updateSetting('animation_speed', opt.value)}
                      className="flex-1 py-2 rounded-lg text-[11px] font-medium transition-all border"
                      style={{
                        backgroundColor: s.animation_speed === opt.value ? 'var(--accent-bg)' : 'var(--bg-secondary)',
                        borderColor: s.animation_speed === opt.value ? 'var(--accent)' : 'var(--border)',
                        color: s.animation_speed === opt.value ? 'var(--accent)' : 'var(--text-secondary)',
                      }}
                    >{opt.label}</button>
                  ))}
                </div>
              </Section>

              {/* Font Size */}
              <Section label={t('fontSize')}>
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

              <Section label={t('autoSave')}>
                <Toggle checked={s.auto_save !== false} onChange={v => updateSetting('auto_save', v)} label={t('autoSaveProject')} />
              </Section>
            </>
          )}

          {activeTab === 'ai' && (
            <AIProviderSettings activePort={activePort} addLog={addLog} />
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
              <Section label="PyCharm Path">
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
              color: '#fff',
            }}
          >
            {t('close')}
          </button>}
        </div>
      </div>
    </div>
  );
}

function Section({ label, children }) {
  return (
    <div>
      <label className="block text-[10px] font-bold uppercase tracking-wider mb-2" style={{ color: 'var(--text-muted)' }}>{label}</label>
      {children}
    </div>
  );
}

function Toggle({ checked, onChange, label, disabled = false }) {
  return (
    <label className="flex items-center space-x-3 cursor-pointer select-none" style={{ minHeight: 32, opacity: disabled ? 0.55 : 1 }}>
      <span
        role="switch"
        aria-checked={checked}
        aria-disabled={disabled}
        tabIndex={disabled ? -1 : 0}
        onClick={(e) => { e.preventDefault(); if (!disabled) onChange(!checked); }}
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
        }}
      >
        <span
          className="transition-transform shadow-md"
          style={{
            display: 'block',
            width: 22,
            height: 22,
            borderRadius: 999,
            backgroundColor: '#fff',
            transform: checked ? 'translateX(20px)' : 'translateX(0)',
          }}
        />
      </span>
      <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>{label}</span>
    </label>
  );
}

function AIProviderSettings({ activePort, addLog }) {
  const [cfg, setCfg] = useState(null);
  const [provider, setProvider] = useState('nvidia');
  const [model, setModel] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [openCodeStatus, setOpenCodeStatus] = useState(null);

  const load = () => {
    fetch(`http://localhost:${activePort}/api/config/ai`)
      .then(r => r.json())
      .then(data => {
        setCfg(data);
        setProvider(data.provider || 'nvidia');
        setModel(data.model || data.providers?.[data.provider]?.[0] || '');
      })
      .catch(() => setMessage('Failed to load AI settings.'));
    fetch(`http://localhost:${activePort}/api/opencode/status`)
      .then(r => r.json())
      .then(data => setOpenCodeStatus(data))
      .catch(() => setOpenCodeStatus({ installed: false, error: 'OpenCode status unavailable' }));
  };

  useEffect(() => { load(); }, [activePort]);

  if (!cfg) return <div className="text-xs" style={{ color: 'var(--text-muted)' }}>Loading AI Provider Settings...</div>;

  const providers = cfg.providers || {};
  const models = providers[provider] || [];
  const saved = cfg.saved_keys?.[provider];

  const changeProvider = (next) => {
    setProvider(next);
    setModel((providers[next] || [''])[0]);
    setApiKey('');
    setMessage('');
  };

  const save = () => {
    setBusy(true);
    setMessage('');
    fetch(`http://localhost:${activePort}/api/config/ai`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider, model, api_key: apiKey }),
    })
      .then(r => r.json())
      .then(data => {
        setCfg(data);
        setApiKey('');
        setMessage('Saved. FreelancerStudio agents will use this provider/model unless an agent override is enabled.');
        addLog(`[AI Settings]: Saved ${provider}/${model}.`);
        window.dispatchEvent(new CustomEvent('freelancerstudio:provider-connections-updated'));
      })
      .catch(err => setMessage(`Save failed: ${err.message}`))
      .finally(() => setBusy(false));
  };

  const test = () => {
    setBusy(true);
    setMessage('Testing provider connection...');
    fetch(`http://localhost:${activePort}/api/config/ai/test`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider, api_key: apiKey }),
    })
      .then(r => r.json())
      .then(data => {
        setMessage(`${data.status === 'ok' ? 'Connection OK' : 'Connection failed'}: ${data.message}`);
        addLog(`[AI Settings]: ${provider} test ${data.status}.`);
        window.dispatchEvent(new CustomEvent('freelancerstudio:provider-connections-updated'));
      })
      .catch(err => setMessage(`Test failed: ${err.message}`))
      .finally(() => setBusy(false));
  };

  const applyOpenCode = () => {
    setBusy(true);
    setMessage('Applying settings to OpenCode...');
    fetch(`http://localhost:${activePort}/api/config/ai/apply-opencode`, { method: 'POST' })
      .then(r => r.json())
      .then(data => {
        setMessage(data.message || 'OpenCode config regenerated. Restart OpenCode to apply it.');
        addLog('[AI Settings]: OpenCode config regenerated from saved settings.');
      })
      .catch(err => setMessage(`Apply failed: ${err.message}`))
      .finally(() => setBusy(false));
  };

  const openCodeWebLogin = () => {
    setBusy(true);
    setMessage('Starting OpenCode web login...');
    fetch(`http://localhost:${activePort}/api/opencode/web`, { method: 'POST' })
      .then(r => r.json())
      .then(data => {
        if (data.url) window.open(data.url, '_blank', 'noopener,noreferrer');
        setMessage(data.message || 'OpenCode web opened. Log in in the browser, then retry generation.');
        addLog('[OpenCode]: Web login opened.');
        load();
      })
      .catch(err => setMessage(`OpenCode web failed: ${err.message}`))
      .finally(() => setBusy(false));
  };

  const openCodeProviderLogin = () => {
    setBusy(true);
    setMessage(`Starting OpenCode ${provider} OAuth/login flow...`);
    fetch(`http://localhost:${activePort}/api/opencode/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider, method: 'oauth' }),
    })
      .then(r => r.json())
      .then(data => {
        setMessage(data.message || 'OpenCode login flow started. Complete browser login if prompted.');
        addLog(`[OpenCode]: ${provider} login flow started.`);
        setTimeout(load, 2000);
      })
      .catch(err => setMessage(`OpenCode login failed: ${err.message}`))
      .finally(() => setBusy(false));
  };

  const deleteKey = () => {
    if (!window.confirm(`Delete saved ${provider} API key?`)) return;
    setBusy(true);
    fetch(`http://localhost:${activePort}/api/config/ai/key/${encodeURIComponent(provider)}`, { method: 'DELETE' })
      .then(r => r.json())
      .then(data => {
        setCfg(data);
        setApiKey('');
        setMessage(`${provider} key deleted.`);
        addLog(`[AI Settings]: Deleted ${provider} API key.`);
      })
      .catch(err => setMessage(`Delete failed: ${err.message}`))
      .finally(() => setBusy(false));
  };

  return (
    <div className="space-y-5">
      <OpenCodeConnectionSetup activePort={activePort} addLog={addLog} />
      <GlobalAIInheritanceSettings activePort={activePort} cfg={cfg} reload={load} addLog={addLog} />
      <AgentRoleContractsSettings activePort={activePort} />
      <ProductJudgeSettings activePort={activePort} addLog={addLog} />
      <div className="rounded-xl p-4 border" style={{ backgroundColor: 'var(--bg-secondary)', borderColor: 'var(--border)' }}>
        <div className="flex items-center justify-between gap-3 mb-3">
          <div>
            <div className="text-xs font-bold uppercase tracking-widest" style={{ color: 'var(--accent)' }}>Direct API Connections</div>
            <div className="text-[10px] mt-1" style={{ color: 'var(--text-muted)' }}>Uses provider API keys. OpenAI API usage and billing are separate from a ChatGPT subscription.</div>
          </div>
          <div className="text-right text-[10px] font-mono" style={{ color: 'var(--text-secondary)' }}>
            Current: <span style={{ color: 'var(--accent)' }}>{cfg.provider}/{cfg.model}</span>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <Section label="Provider">
            <select value={provider} onChange={e => changeProvider(e.target.value)} className="w-full bg-[var(--bg-primary)] border border-[var(--border)] rounded-lg px-3 py-2 text-xs focus:outline-none" style={{ color: 'var(--text-primary)', backgroundColor: 'var(--bg-primary)' }}>
              {Object.keys(providers).map(p => <option key={p} value={p}>{p.toUpperCase()}</option>)}
            </select>
          </Section>
          <Section label="Model">
            <select value={model} onChange={e => setModel(e.target.value)} className="w-full bg-[var(--bg-primary)] border border-[var(--border)] rounded-lg px-3 py-2 text-xs font-mono focus:outline-none" style={{ color: 'var(--text-primary)', backgroundColor: 'var(--bg-primary)' }}>
              {models.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          </Section>
        </div>

        <Section label="API Key">
          <input
            type="password"
            value={apiKey}
            onChange={e => setApiKey(e.target.value)}
            placeholder={provider === 'ollama' ? 'Ollama does not require an API key' : saved?.saved ? `${saved.masked} saved - enter a new key to replace` : `Paste ${provider} API key`}
            disabled={provider === 'ollama'}
            className="ai-settings-field w-full bg-[var(--bg-primary)] border border-[var(--border)] rounded-lg px-3 py-2 text-xs font-mono focus:outline-none disabled:opacity-50"
            style={{ color: 'var(--text-primary)', backgroundColor: 'var(--bg-primary)' }}
          />
          <div className="mt-2 flex items-center justify-between gap-2">
            <span className="text-[10px]" style={{ color: saved?.saved || provider === 'ollama' ? 'var(--success)' : 'var(--warning)' }}>
              {provider === 'ollama' ? 'Local provider - no key required' : saved?.saved ? `Saved: ${saved.masked}` : 'No saved key for this provider'}
            </span>
            {saved?.saved && provider !== 'ollama' && <button type="button" onClick={deleteKey} disabled={busy} className="text-[10px] text-red-400 hover:text-red-300">Delete saved key</button>}
          </div>
        </Section>

        <div className="flex flex-wrap gap-2 pt-2">
          <button onClick={save} disabled={busy} className="px-3 py-2 rounded-lg text-xs font-bold disabled:opacity-50" style={{ backgroundColor: 'var(--accent)', color: '#fff' }}>Save</button>
          <button onClick={test} disabled={busy || (provider !== 'ollama' && !apiKey && !saved?.saved)} className="px-3 py-2 rounded-lg text-xs font-bold disabled:opacity-50" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>Test Connection</button>
          <button onClick={applyOpenCode} disabled={busy} className="px-3 py-2 rounded-lg text-xs font-bold disabled:opacity-50" style={{ backgroundColor: 'color-mix(in srgb, var(--success) 18%, transparent)', border: '1px solid color-mix(in srgb, var(--success) 35%, transparent)', color: 'var(--success)' }}>Apply to OpenCode</button>
        </div>

        <div className="mt-3 rounded-lg px-3 py-2 text-[10px]" style={{ backgroundColor: 'color-mix(in srgb, var(--warning) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--warning) 25%, transparent)', color: 'var(--warning)' }}>
          Direct API connections are separate from OpenCode browser authentication. Raw API keys are never returned to the frontend.
        </div>
        {message && <div className="mt-3 text-[11px]" style={{ color: message.toLowerCase().includes('failed') ? 'var(--warning)' : 'var(--text-secondary)' }}>{message}</div>}
      </div>

      <div className="rounded-xl p-4 border" style={{ backgroundColor: 'var(--bg-secondary)', borderColor: 'var(--border)' }}>
        <div className="flex items-start justify-between gap-3 mb-3">
          <div>
            <div className="text-xs font-bold uppercase tracking-widest" style={{ color: 'var(--accent)' }}>OpenCode Browser Bridge</div>
            <div className="text-[10px] mt-1" style={{ color: 'var(--text-muted)' }}>Uses your authenticated OpenCode session. FreelancerStudio does not store your browser password, OAuth token, cookies, or browser storage.</div>
          </div>
          <button onClick={load} disabled={busy} className="px-2.5 py-1 rounded text-[10px] font-bold disabled:opacity-50" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>Refresh</button>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-[10px] font-mono mb-3" style={{ color: 'var(--text-secondary)' }}>
          <div>Installed: <span style={{ color: openCodeStatus?.installed ? 'var(--success)' : 'var(--warning)' }}>{openCodeStatus?.installed ? 'yes' : 'no'}</span></div>
          <div>Version: <span style={{ color: 'var(--text-primary)' }}>{openCodeStatus?.version || '-'}</span></div>
          <div>Credentials: <span style={{ color: openCodeStatus?.credentials_count ? 'var(--success)' : 'var(--warning)' }}>{openCodeStatus?.credentials_count ?? 0}</span></div>
          <div>Web: <span style={{ color: 'var(--text-primary)' }}>{openCodeStatus?.web_url || '-'}</span></div>
          <div className="md:col-span-2">Effective code model: <span style={{ color: 'var(--accent)' }}>{openCodeStatus?.effective_provider && openCodeStatus?.effective_model ? `${openCodeStatus.effective_provider}/${openCodeStatus.effective_model}` : '-'}</span></div>
        </div>
        {openCodeStatus?.effective_note && (
          <div className="rounded-lg px-3 py-2 text-[10px] mb-3" style={{ backgroundColor: 'color-mix(in srgb, var(--warning) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--warning) 25%, transparent)', color: 'var(--warning)' }}>
            {openCodeStatus.effective_note}
          </div>
        )}
        {openCodeStatus?.credentials?.length > 0 && (
          <div className="flex flex-wrap gap-1 mb-3">
            {openCodeStatus.credentials.map((c, i) => (
              <span key={`${c.provider}-${i}`} className="px-2 py-1 rounded-full text-[10px]" style={{ backgroundColor: 'color-mix(in srgb, var(--success) 12%, transparent)', border: '1px solid color-mix(in srgb, var(--success) 30%, transparent)', color: 'var(--success)' }}>{c.provider} · {c.method}</span>
            ))}
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          <button onClick={openCodeWebLogin} disabled={busy} className="px-3 py-2 rounded-lg text-xs font-bold disabled:opacity-50" style={{ backgroundColor: 'var(--accent)', color: '#fff' }}>Open OpenCode Login/Web</button>
          <button onClick={openCodeProviderLogin} disabled={busy || provider === 'ollama'} className="px-3 py-2 rounded-lg text-xs font-bold disabled:opacity-50" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>Start {provider} OAuth Login</button>
        </div>
        <div className="mt-3 text-[10px]" style={{ color: 'var(--text-muted)' }}>OpenCode owns the browser login session. FreelancerStudio stores only bridge metadata, status, and model/capability records.</div>
      </div>
    </div>
  );
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
  const textCapableConnections = connections.filter(c => (c.available_models || []).some(m => m?.capabilities?.text_input !== false));
  const models = modelOptions.length ? modelOptions : (selected?.available_models || []);
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
      setModel(nextGlobal.model || mapped?.available_models?.[0]?.id || '');
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
      if (!preserve || !nextModels.some(m => m.id === model)) setModel(prev => (preserve && nextModels.some(m => m.id === prev)) ? prev : nextModels[0]?.id || '');
    }).catch(err => { setModelOptions([]); setMessage(`Failed to load models: ${err.message}`); }).finally(() => setLoadingModels(false));
  };
  useEffect(() => { loadGlobal(); loadAgents(); }, [activePort]);

  const saveGlobal = () => {
    const conn = connections.find(c => c.connection_id === connectionId) || selected;
    setBusy(true);
    fetch(`http://localhost:${activePort}/api/config/ai/global`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ connection_id: connectionId, provider: conn?.provider || globalAI.provider || cfg.provider, model, temperature: parseFloat(temperature), top_p: topP === '' ? null : parseFloat(topP), top_k: topK === '' ? null : parseInt(topK, 10), max_tokens: maxTokens === '' ? null : parseInt(maxTokens, 10), enabled: true }) })
      .then(r => r.json()).then(data => { setGlobalAI(data.global_ai || {}); setMessage(`Global AI configuration saved: ${data.global_ai?.provider}/${data.global_ai?.model}.`); addLog?.(`[AI Settings]: Global model set to ${data.global_ai?.provider}/${data.global_ai?.model}.`); reload(); loadGlobal(); loadAgents(); })
      .catch(err => setMessage(`Save failed: ${err.message}`)).finally(() => setBusy(false));
  };

  const applyAll = () => {
    setBusy(true);
    fetch(`http://localhost:${activePort}/api/agents/apply-global-inheritance`, { method: 'POST' })
      .then(r => r.json()).then(data => { setApplyResult(data); const updated = data.updated || []; const skipped = data.skipped || []; setMessage(updated.length ? `Applied global inheritance to ${updated.length} agent(s).` : (skipped.length ? 'No compatible agents were updated. See skipped reasons.' : 'No agents were updated. Save a global connection and model first.')); addLog?.('[AI Settings]: Applied global inheritance to agents.'); loadAgents(); })
      .catch(err => setMessage(`Apply failed: ${err.message}`)).finally(() => setBusy(false));
  };

  const updateAgent = (agentId, patch) => {
    setBusy(true);
    fetch(`http://localhost:${activePort}/api/agents/${agentId}/config`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch) })
      .then(r => r.json()).then(() => { setMessage(`${agents[agentId]?.name || agentId} settings saved.`); loadAgents(); }).catch(err => setMessage(`Agent save failed: ${err.message}`)).finally(() => setBusy(false));
  };
  const formatList = (items) => (items || []).map(item => typeof item === 'string' ? item : `${item.agent_id} - ${item.reason}`).join('\n');
  return <section className="provider-setup"><div className="provider-heading"><div><strong>Global AI Configuration</strong><p>Use one Studio provider/model for compatible agents while keeping per-agent generation overrides.</p></div><span className="connection-state">{globalAI.provider || cfg.provider}/{globalAI.model || cfg.model}</span></div><div className="provider-form"><label>Provider connection<select value={connectionId} disabled={busy || loadingConnections} onChange={e => { const next = connections.find(c => c.connection_id === e.target.value); setConnectionId(e.target.value); setModel(next?.available_models?.[0]?.id || ''); loadModels(e.target.value); }}><option value="">{loadingConnections ? 'Loading provider connections...' : textCapableConnections.length ? 'Select provider connection' : 'No tested text-capable connection available'}</option>{textCapableConnections.map(c => <option key={c.connection_id} value={c.connection_id}>{c.display_name || c.name || c.provider}{c.tested_status === 'passed' ? ' - tested' : ' - not tested'}</option>)}</select></label><label>Default model<select value={model} disabled={busy || loadingModels || !connectionId} onChange={e => setModel(e.target.value)}><option value="">{loadingModels ? 'Loading models...' : models.length ? 'Select model' : 'No tested text-capable models are available for this connection'}</option>{models.map(m => <option key={m.id} value={m.id}>{m.id}</option>)}</select></label><label>Default temperature<input className="ai-settings-field" type="number" min="0" max="2" step="0.05" value={temperature} onChange={e => setTemperature(e.target.value)} /></label><label>Default top_p<input className="ai-settings-field" type="number" min="0" max="1" step="0.05" value={topP ?? ''} placeholder="Not sent" onChange={e => setTopP(e.target.value)} /></label><label>Default top_k<input className="ai-settings-field" type="number" min="1" step="1" value={topK ?? ''} placeholder={supportsTopK ? 'Not sent' : 'Not supported by this provider'} disabled={!supportsTopK} onChange={e => setTopK(e.target.value)} /></label><label>Default max tokens<input className="ai-settings-field" type="number" min="1" step="1" value={maxTokens ?? ''} placeholder="Provider default" onChange={e => setMaxTokens(e.target.value)} /></label></div>{selectedModel?.capabilities?.image_input !== true && <p className="provider-message">Product Judge note: this global model is not eligible for Product Judge because image input is not proven.</p>}<div className="provider-actions"><button type="button" className="primary" onClick={saveGlobal} disabled={busy || !connectionId || !model}>Save global configuration</button><button type="button" onClick={applyAll} disabled={busy || !connectionId || !model}>Apply inheritance to all agents</button><button type="button" onClick={() => { loadGlobal(); loadAgents(); }} disabled={busy}>Refresh preview</button></div>{message && <p className="provider-message" role="status">{message}</p>}{applyResult && <pre className="provider-message whitespace-pre-wrap">{`Applied successfully:\n${formatList(applyResult.updated).split('\n').filter(Boolean).map(x => `- ${agents[x]?.name || x}`).join('\n') || '- none'}\n\nSkipped:\n${(applyResult.skipped || []).map(x => `- ${agents[x.agent_id]?.name || x.agent_id} - ${x.reason}`).join('\n') || '- none'}`}</pre>}<div className="grid grid-cols-1 xl:grid-cols-2 gap-3 mt-3">{Object.entries(agents).map(([agentId, agent]) => { const eff = agent.effective_ai || {}; const draft = agentDrafts[agentId] || {}; const inheritedParams = !!eff.use_global_generation_parameters; const isJudge = agentId === 'product_judge'; return <article key={agentId} className="rounded-xl border p-3 space-y-3" style={{ backgroundColor: 'var(--bg-primary)', borderColor: 'var(--border)' }}><div className="flex items-start justify-between gap-3"><div><strong className="text-xs" style={{ color: 'var(--text-primary)' }}>{agent.name || agentId}</strong><p className="text-[10px] mt-0.5" style={{ color: 'var(--text-muted)' }}>{agent.role || agent.display_role || agentId}</p></div><span className="text-[10px] font-bold" style={{ color: eff.capability_status === 'compatible' ? 'var(--success)' : 'var(--warning)' }}>{eff.capability_status || 'unresolved'}</span></div><div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-[10px]" style={{ color: 'var(--text-secondary)' }}><span>Provider: <b>{eff.provider || 'Unresolved'}</b></span><span>Model: <b>{eff.model || 'No model selected'}</b></span><span>Temperature: <b>{eff.temperature ?? 'Provider default'}</b></span><span>Source: <b>{eff.configuration_source?.model === 'global' ? 'Global model' : 'Agent model override'} / {eff.configuration_source?.generation_parameters === 'global' ? 'Global parameters' : 'Agent parameters'}</b></span>{isJudge && <span className="md:col-span-2">Product Judge: <b>{eff.capability_validation?.valid ? 'global model eligible' : (eff.capability_validation?.reason || 'Global model is not eligible for Product Judge because image input is not proven.')}</b></span>}</div><div className="grid grid-cols-1 sm:grid-cols-3 gap-2"><Toggle checked={!!eff.use_global_connection} onChange={value => updateAgent(agentId, { use_global_connection: value, use_global: value && eff.use_global_model })} label="Use global connection" disabled={busy} /><Toggle checked={!!eff.use_global_model} onChange={value => updateAgent(agentId, { use_global_model: value, use_global: value && eff.use_global_connection })} label="Use global model" disabled={busy} /><Toggle checked={inheritedParams} onChange={value => updateAgent(agentId, { use_global_generation_parameters: value })} label="Use global parameters" disabled={busy} /></div><div className="provider-form"><label>Temperature<input className="ai-settings-field" type="number" min="0" max="2" step="0.05" value={draft.temperature ?? ''} disabled={inheritedParams} placeholder={inheritedParams ? 'Inherited from Global' : 'Agent override'} onChange={e => setAgentDrafts(prev => ({ ...prev, [agentId]: { ...(prev[agentId] || {}), temperature: e.target.value } }))} /></label><label>Top_p<input className="ai-settings-field" type="number" min="0" max="1" step="0.05" value={draft.top_p ?? ''} disabled={inheritedParams} placeholder={inheritedParams ? 'Inherited from Global' : 'Not sent'} onChange={e => setAgentDrafts(prev => ({ ...prev, [agentId]: { ...(prev[agentId] || {}), top_p: e.target.value } }))} /></label><label>Top_k<input className="ai-settings-field" type="number" min="1" step="1" value={draft.top_k ?? ''} disabled={inheritedParams || !!eff.unsupported_generation_parameters?.top_k} placeholder={eff.unsupported_generation_parameters?.top_k || (inheritedParams ? 'Inherited from Global' : 'Not sent')} onChange={e => setAgentDrafts(prev => ({ ...prev, [agentId]: { ...(prev[agentId] || {}), top_k: e.target.value } }))} /></label><label>Max tokens<input className="ai-settings-field" type="number" min="1" step="1" value={draft.max_tokens ?? ''} disabled={inheritedParams} placeholder={inheritedParams ? 'Inherited from Global' : 'Provider default'} onChange={e => setAgentDrafts(prev => ({ ...prev, [agentId]: { ...(prev[agentId] || {}), max_tokens: e.target.value } }))} /></label></div><div className="provider-actions"><button type="button" className="primary" disabled={busy} onClick={() => updateAgent(agentId, { use_global_generation_parameters: inheritedParams, temperature: draft.temperature === '' ? null : parseFloat(draft.temperature), top_p: draft.top_p === '' ? null : parseFloat(draft.top_p), top_k: draft.top_k === '' ? null : parseInt(draft.top_k, 10), max_tokens: draft.max_tokens === '' ? null : parseInt(draft.max_tokens, 10) })}>Save agent settings</button><button type="button" disabled={busy} onClick={() => updateAgent(agentId, { use_global_connection: true, use_global_model: true, use_global_generation_parameters: false, use_global: true })}>Reset to defaults</button></div></article>; })}</div></section>;
}

function AgentRoleContractsSettings({ activePort }) {
  const [agents, setAgents] = useState({});
  const [message, setMessage] = useState('');
  const load = () => fetch(`http://localhost:${activePort}/api/agents`).then(r => r.json()).then(setAgents).catch(() => setAgents({}));
  useEffect(() => { load(); }, [activePort]);
  const saveAgent = (id, patch) => {
    fetch(`http://localhost:${activePort}/api/agents/${id}/config`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch) })
      .then(r => r.json())
      .then(() => { setMessage(`${agents[id]?.name || id} settings persisted.`); load(); })
      .catch(err => setMessage(`Agent settings failed: ${err.message}`));
  };
  const entries = Object.entries(agents).filter(([, agent]) => agent?.builtin !== false);
  return <section className="provider-setup"><div className="provider-heading"><div><strong>Agent Role Contracts</strong><p>Effective model, inheritance source, generation parameters, role contract summary, and reset targets. API keys are not shown here.</p></div><span className="connection-state">{entries.length} contracts</span></div>{message && <p className="provider-message">{message}</p>}<div className="grid grid-cols-1 xl:grid-cols-2 gap-3">{entries.map(([id, agent]) => { const eff = agent.effective_ai || {}; const defaults = agent.recommended_defaults || {}; return <div key={id} className="rounded-lg border p-3" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-primary)' }}><div className="flex items-center justify-between gap-2"><strong>{agent.name || id}</strong><span className="text-[10px] uppercase" style={{ color: 'var(--text-muted)' }}>{agent.role}</span></div><div className="text-[11px] mt-2" style={{ color: 'var(--text-secondary)' }}>Effective model: <span className="font-mono">{eff.provider}/{eff.model}</span></div><div className="text-[11px]" style={{ color: 'var(--text-secondary)' }}>Inheritance source: connection {eff.configuration_source?.connection || 'agent'}, model {eff.configuration_source?.model || 'agent'}, params {eff.configuration_source?.generation_parameters || 'agent'}</div><div className="text-[11px]" style={{ color: 'var(--text-secondary)' }}>temperature/top_p/top_k: {eff.temperature ?? '-'} / {eff.top_p ?? '-'} / {eff.top_k ?? '-'}</div><div className="text-[11px] mt-2" style={{ color: 'var(--text-primary)' }}>Role contract summary: {agent.role_contract_summary || 'No structured contract'}</div><div className="text-[10px] mt-2" style={{ color: 'var(--text-muted)' }}>Reset to recommended defaults: temperature {defaults.temperature ?? '-'}, top_p {defaults.top_p ?? '-'}, top_k {defaults.top_k ?? 'not sent'}</div><div className="provider-actions"><button type="button" onClick={() => saveAgent(id, { temperature: defaults.temperature, top_p: defaults.top_p ?? null, top_k: defaults.top_k ?? null, use_global_generation_parameters: false })}>Reset to recommended role defaults</button><button type="button" onClick={() => saveAgent(id, { use_global_connection: true, use_global_model: true, use_global: true })}>Inherit global model</button><button type="button" onClick={() => saveAgent(id, { use_global_generation_parameters: false })}>Override generation parameters</button></div></div>; })}</div></section>;
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
          style={{ backgroundColor: 'var(--accent)', color: '#fff' }}
        >Save GitHub Config</button>
      </div>
    </div>
  );
}
