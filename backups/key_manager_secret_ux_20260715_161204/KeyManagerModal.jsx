import React, { useEffect, useState } from 'react';
import { tr } from '../i18n.js';

const PLATFORM_FIELDS = [
  {
    id: 'nvidia_key',
    label: 'NVIDIA API Key (Recommended)',
    placeholder: 'nvapi-........................',
    url: null,
    urlLabel: null,
  },
  {
    id: 'openai_key',
    label: 'OpenAI API Key (Optional)',
    placeholder: 'sk-proj-........................',
    url: null,
    urlLabel: null,
  },
  {
    id: 'anthropic_key',
    label: 'Anthropic Claude API Key (Optional)',
    placeholder: 'sk-ant-........................',
    url: null,
    urlLabel: null,
  },
  {
    id: 'freelancer_client_id',
    label: 'Freelancer Client ID (Optional — job search)',
    placeholder: 'Freelancer Client ID',
    url: 'https://developers.freelancer.com/',
    urlLabel: 'Register app',
  },
  {
    id: 'freelancer_client_secret',
    label: 'Freelancer Client Secret',
    placeholder: 'Freelancer Client Secret',
    url: null,
    urlLabel: null,
  },
  {
    id: 'upwork_client_id',
    label: 'Upwork Client ID (Optional — job search)',
    placeholder: 'Upwork Client ID',
    url: 'https://www.upwork.com/developer/keys/apply',
    urlLabel: 'Request key',
  },
  {
    id: 'upwork_client_secret',
    label: 'Upwork Client Secret',
    placeholder: 'Upwork Client Secret',
    url: null,
    urlLabel: null,
  },
];

export default function KeyManagerModal({ activePort, onClose, addLog }) {
    const [customKeys, setCustomKeys] = useState({});
    const [savedKeys, setSavedKeys] = useState([]);
    const [language, setLanguage] = useState(document.documentElement.lang || 'en');
    const t = (key) => tr(language, key);

    const refreshSavedKeys = () => {
        fetch(`http://localhost:${activePort}/api/config/keys`)
            .then(res => res.json())
            .then(data => setSavedKeys(data.saved_keys || []))
            .catch(err => {
                console.error('[Keys Load Error]:', err);
                addLog('[Error]: Failed to load saved keys.');
            });
    };

    useEffect(() => {
        if (activePort) refreshSavedKeys();
    }, [activePort]);

    useEffect(() => {
        const handler = (e) => setLanguage(e.detail?.language || 'en');
        window.addEventListener('studio-language-change', handler);
        return () => window.removeEventListener('studio-language-change', handler);
    }, []);

    const handleSaveKeys = (e) => {
        e.preventDefault();
        if (!activePort) {
            addLog('[Error]: Backend port not detected.');
            return;
        }

        const keysToSave = Object.fromEntries(Object.entries(customKeys).filter(([, value]) => value && value.trim()));
        const payload = { keys: keysToSave };

        fetch(`http://localhost:${activePort}/api/config/keys`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        })
            .then(res => res.json())
            .then(data => {
                if (data.status === 'saved' || data.status === 'success') {
                    addLog('[System]: API credentials saved. Agents ready.');
                    setCustomKeys({});
                    refreshSavedKeys();
                } else {
                    addLog(`[Warning]: ${JSON.stringify(data)}`);
                }
            })
            .catch(err => {
                console.error('[API Error]:', err);
                addLog(`[Error]: Failed to save keys. Backend on port ${activePort}?`);
            });
    };

    const [newProvider, setNewProvider] = useState('');
    const [newKey, setNewKey] = useState('');

    const addCustomKey = () => {
        if (!newProvider.trim() || !newKey.trim()) return;
        const keyName = newProvider.trim().toLowerCase().replace(/\s+/g, '_') + '_key';
        if (customKeys[keyName]) return;
        setCustomKeys(prev => ({ ...prev, [keyName]: newKey.trim() }));
        setNewProvider('');
        setNewKey('');
    };

    const removeCustomKey = (key) => {
        setCustomKeys(prev => {
            const next = { ...prev };
            delete next[key];
            return next;
        });
    };

    const deleteSavedKey = (key) => {
        if (!window.confirm(`Delete stored key "${key}"?`)) return;
        fetch(`http://localhost:${activePort}/api/config/keys/${encodeURIComponent(key)}`, {
            method: 'DELETE'
        })
            .then(res => res.json())
            .then(data => {
                if (data.status === 'deleted') {
                    setSavedKeys(prev => prev.filter(k => k !== key));
                    setCustomKeys(prev => {
                        const next = { ...prev };
                        delete next[key];
                        return next;
                    });
                    addLog(`[System]: API key deleted: ${key}`);
                }
            })
            .catch(err => {
                console.error('[Delete Key Error]:', err);
                addLog(`[Error]: Failed to delete key ${key}.`);
            });
    };

    const handleReset = () => {
        if (!window.confirm('Permanently delete ALL stored API keys?')) return;
        fetch(`http://localhost:${activePort}/api/config/keys`, {
            method: 'DELETE'
        })
            .then(res => res.json())
            .then(data => {
                if (data.status === 'reset') {
                    setCustomKeys({});
                    setSavedKeys([]);
                    addLog('[System]: All credentials cleared.');
                    onClose();
                }
            })
            .catch(err => {
                console.error('[Reset Error]:', err);
                addLog('[Error]: Reset failed.');
            });
    };

    const knownIds = PLATFORM_FIELDS.map(f => f.id);

    return (
        <div className="fixed inset-0 bg-slate-950/90 backdrop-blur-md flex items-center justify-center p-4 z-[60]">
            <div className="bg-slate-900 border border-slate-800 rounded-xl w-full max-w-lg shadow-2xl p-6 space-y-4 max-h-[90vh] overflow-y-auto">
                <div className="text-center">
                    <div className="w-10 h-10 rounded-lg bg-indigo-600/20 border border-indigo-500/40 flex items-center justify-center mx-auto mb-3">
                        <span className="text-lg">🔑</span>
                    </div>
                    <h3 className="text-sm font-bold uppercase tracking-wider text-slate-200">{t('activateStudio')}</h3>
                    <p className="text-[11px] text-slate-500 mt-1">{t('configureTokens')}</p>
                </div>

                <form onSubmit={handleSaveKeys} className="space-y-3">
                    {PLATFORM_FIELDS.map(field => (
                        <div key={field.id}>
                            <div className="flex items-center justify-between mb-1">
                                <label className="block text-[10px] text-slate-400 uppercase font-semibold">
                                    {field.label}
                                    {field.url && (
                                        <a
                                            href={field.url}
                                            target="_blank"
                                            rel="noopener noreferrer"
                                            className="ml-2 text-indigo-400 hover:text-indigo-300 underline"
                                            onClick={e => e.stopPropagation()}
                                        >
                                            {field.urlLabel}
                                        </a>
                                    )}
                                </label>
                                {savedKeys.includes(field.id) && (
                                    <button
                                        type="button"
                                        onClick={() => deleteSavedKey(field.id)}
                                        className="text-[9px] text-red-400 hover:text-red-300 transition-colors"
                                    >
                                        {t('deleteKey')}
                                    </button>
                                )}
                            </div>
                            <input
                                type="password"
                                value={customKeys[field.id] || ''}
                                onChange={(e) => setCustomKeys(prev => ({ ...prev, [field.id]: e.target.value }))}
                                placeholder={savedKeys.includes(field.id) ? 'Saved permanently - enter new value to replace' : field.placeholder}
                                className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs focus:outline-none focus:border-indigo-500 text-slate-300 placeholder-slate-700 font-mono"
                            />
                        </div>
                    ))}

                    <div className="border-t border-slate-800 pt-3">
                        <div className="flex items-center justify-between mb-2">
                            <label className="text-[10px] text-slate-400 uppercase font-semibold">{t('customProviders')}</label>
                        </div>

                        <div className="flex gap-2 mb-3">
                            <input
                                type="text"
                                value={newProvider}
                                onChange={(e) => setNewProvider(e.target.value)}
                                placeholder="provider name (e.g. deepseek)"
                                className="flex-1 bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs focus:outline-none focus:border-indigo-500 text-slate-300 placeholder-slate-700 font-mono"
                            />
                            <input
                                type="password"
                                value={newKey}
                                onChange={(e) => setNewKey(e.target.value)}
                                placeholder="API key"
                                className="flex-[2] bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs focus:outline-none focus:border-indigo-500 text-slate-300 placeholder-slate-700 font-mono"
                            />
                            <button
                                type="button"
                                onClick={addCustomKey}
                                className="bg-indigo-600 hover:bg-indigo-500 text-white px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors"
                            >
                                + Add
                            </button>
                        </div>

                        {Object.entries(customKeys).map(([key, value]) => (
                            !knownIds.includes(key) && (
                                <div key={key} className="flex items-center gap-2 mb-2">
                                    <input
                                        type="password"
                                        value={value}
                                        onChange={(e) => setCustomKeys(prev => ({ ...prev, [key]: e.target.value }))}
                                        placeholder={`${key.replace('_key', '')}: ...`}
                                        className="flex-1 bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs focus:outline-none focus:border-indigo-500 text-slate-300 font-mono"
                                    />
                                    {savedKeys.includes(key) && (
                                        <button
                                            type="button"
                                            onClick={() => deleteSavedKey(key)}
                                            className="text-red-400 hover:text-red-300 text-[10px] px-2 whitespace-nowrap"
                                        >
                                            {t('deleteKey')}
                                        </button>
                                    )}
                                    <button
                                        type="button"
                                        onClick={() => removeCustomKey(key)}
                                        className="text-red-400 hover:text-red-300 text-xs px-2"
                                    >
                                        ✕
                                    </button>
                                </div>
                            )
                        ))}

                        {savedKeys.filter(key => !knownIds.includes(key) && customKeys[key] === undefined).map(key => (
                            <div key={key} className="flex items-center gap-2 mb-2 rounded-lg border border-slate-800 bg-slate-950 px-3 py-2">
                                <div className="flex-1 min-w-0">
                                    <div className="text-[10px] text-slate-300 font-mono truncate">{key}</div>
                                    <div className="text-[9px] text-slate-600">{t('savedPermanently')}</div>
                                </div>
                                <button
                                    type="button"
                                    onClick={() => deleteSavedKey(key)}
                                    className="text-red-400 hover:text-red-300 text-[10px] px-2 whitespace-nowrap"
                                >
                                    {t('deleteKey')}
                                </button>
                            </div>
                        ))}
                    </div>

                    <button
                        type="submit"
                        className="w-full bg-indigo-600 hover:bg-indigo-500 text-white font-bold py-2 rounded-lg text-xs transition-all shadow-md active:scale-98"
                    >
                        {t('activateSave')}
                    </button>
                </form>

                <div className="border-t border-slate-800 pt-3 space-y-2">
                    <button
                        type="button"
                        onClick={onClose}
                        className="w-full bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 font-semibold py-1.5 rounded-lg text-[10px] transition-all"
                    >
                        {t('closeWindow')}
                    </button>
                    <button
                        type="button"
                        onClick={handleReset}
                        className="w-full bg-red-900/40 hover:bg-red-800/60 text-red-300 border border-red-800/40 font-semibold py-1.5 rounded-lg text-[10px] transition-all"
                    >
                        ✕ {t('resetConfiguration')}
                    </button>
                </div>
            </div>
        </div>
    );
}
