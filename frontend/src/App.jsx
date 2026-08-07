import React, { useState, useEffect, useRef } from 'react';
import ModelSelector from './components/ModelSelector.jsx';
import KeyManagerModal from './components/KeyManagerModal.jsx';
import SettingsModal from './components/SettingsModal.jsx';
import InfoModal from './components/InfoModal.jsx';
import StudioDashboard from './components/StudioDashboard.jsx';
import { tr } from './i18n.js';
import { applyStudioTheme } from './theme.js';
import { getAppVersion, versionLabel } from './app-version.js';

function App() {
    const [logs, setLogs] = useState(['[System]: Core systems initialized. Standing by.']);
    const [isModelSelectorOpen, setIsModelSelectorOpen] = useState(false);
    const [isKeyManagerOpen, setIsKeyManagerOpen] = useState(false);
    const [agentStatuses, setAgentStatuses] = useState({});
    const [agentList, setAgentList] = useState({});
    const [language, setLanguage] = useState('en');
    const [appVersion, setAppVersion] = useState('');

    const activePort = Number(window.location.port) || 8080;

    useEffect(() => {
        let active = true;
        getAppVersion().then(version => {
            if (active) setAppVersion(version);
        });
        return () => { active = false; };
    }, []);

    const fetchAgents = () => {
        fetch(`http://localhost:${activePort}/api/agents`)
            .then(r => r.json())
            .then(data => setAgentList(data))
            .catch(err => console.error('[API Error Agents]:', err));
    };

    useEffect(() => {
        fetch(`http://localhost:${activePort}/api/config/system`)
            .then(r => r.json())
            .then(data => {
                applyStudioTheme(data, { cache: true });
                setLanguage(data.language || 'en');
                document.documentElement.lang = data.language || 'en';
            })
            .catch(err => console.error('[API]:', err));

        fetch(`http://localhost:${activePort}/api/config/keys`)
            .then(r => r.json())
            .then(data => {
                if (!data.has_nvidia && !data.has_anthropic && !data.has_openai) {
                    setIsKeyManagerOpen(true);
                }
            })
            .catch(err => console.error('[API]:', err));

        fetchAgents();
    }, [activePort]);

    useEffect(() => {
        const handler = (e) => {
            const lang = e.detail?.language || 'en';
            setLanguage(lang);
            document.documentElement.lang = lang;
        };
        window.addEventListener('studio-language-change', handler);
        return () => window.removeEventListener('studio-language-change', handler);
    }, []);

    const prevConfigOpen = useRef(isModelSelectorOpen);
    useEffect(() => {
        if (prevConfigOpen.current && !isModelSelectorOpen) fetchAgents();
        prevConfigOpen.current = isModelSelectorOpen;
    }, [isModelSelectorOpen]);

    // Persist system logs to localStorage
    useEffect(() => {
        try {
            const saved = localStorage.getItem('studio_session');
            if (saved) {
                const data = JSON.parse(saved);
                if (data.studioLogs) setLogs(data.studioLogs);
            }
        } catch { }
    }, []);

    useEffect(() => {
        try {
            localStorage.setItem('studio_session', JSON.stringify({ studioLogs: logs }));
        } catch { }
    }, [logs]);

    // Poll agent statuses
    useEffect(() => {
        const interval = setInterval(() => {
            fetch(`http://localhost:${activePort}/api/agents/status`)
                .then(r => r.json())
                .then(data => setAgentStatuses(data))
                .catch(() => { });
        }, 2000);
        return () => clearInterval(interval);
    }, [activePort]);

    const addLog = (msg) => {
        const time = new Date().toLocaleTimeString();
        setLogs(prev => [`[${time}] ${msg}`, ...prev].slice(0, 500));
    };

    const t = (key) => tr(language, key);

    return (
        <>
            <StudioDashboard
                activePort={activePort}
                logs={logs}
                agents={agentList}
                statuses={agentStatuses}
                settingsContent={<SettingsModal activePort={activePort} embedded addLog={addLog} />}
                infoContent={<InfoModal activePort={activePort} embedded addLog={addLog} appVersion={appVersion} />}
                onKeyManager={() => setIsKeyManagerOpen(true)}
            />

            {isModelSelectorOpen && <ModelSelector activePort={activePort} onClose={() => setIsModelSelectorOpen(false)} addLog={addLog} />}
            {isKeyManagerOpen && <KeyManagerModal activePort={activePort} onClose={() => setIsKeyManagerOpen(false)} addLog={addLog} />}
        </>
    );
}

export default App;
