import React, { useState, useEffect, useRef } from 'react';
import IsoOffice from './components/IsoOffice.jsx';
import SearchHub from './components/SearchHub.jsx';
import ModelSelector from './components/ModelSelector.jsx';
import NewProjectModal from './components/NewProjectModal.jsx';
import ProjectChat from './components/ProjectChat.jsx';
import KeyManagerModal from './components/KeyManagerModal.jsx';
import AgentChat from './components/AgentChat.jsx';
import SettingsModal from './components/SettingsModal.jsx';
import FileBrowserModal from './components/FileBrowserModal.jsx';
import InfoModal from './components/InfoModal.jsx';
import JobSearchModal from './components/JobSearchModal.jsx';
import AccountsModal from './components/AccountsModal.jsx';
import PipelineDetailModal from './components/PipelineDetailModal.jsx';
import GoldieChat from './components/GoldieChat.jsx';
import ErrorBoundary from './components/ErrorBoundary.jsx';
import QuestionAnswerModal from './components/QuestionAnswerModal.jsx';
import StudioDashboard from './components/StudioDashboard.jsx';
import { tr } from './i18n.js';

const fallbackAgents = {};

function App() {
    const [searchQuery, setSearchQuery] = useState('');
    const [suggestions, setSuggestions] = useState([]);
    const [searchResults, setSearchResults] = useState([]);
    const [logs, setLogs] = useState(['[System]: Core systems initialized. Standing by.']);
    const [activeProject, setActiveProject] = useState(null);
    const [chatHistory, setChatHistory] = useState([]);
    const [isModelSelectorOpen, setIsModelSelectorOpen] = useState(false);
    const [isNewProjectOpen, setIsNewProjectOpen] = useState(false);
    const [isChatOpen, setIsChatOpen] = useState(false);
    const [activeRoom, setActiveRoom] = useState(null);
    const [isKeyManagerOpen, setIsKeyManagerOpen] = useState(false);
    const [activeAgentChat, setActiveAgentChat] = useState(null);
    const [isSettingsOpen, setIsSettingsOpen] = useState(false);
    const [isFileBrowserOpen, setIsFileBrowserOpen] = useState(false);
    const [isInfoOpen, setIsInfoOpen] = useState(false);
    const [isUpworkOpen, setIsUpworkOpen] = useState(false);
    const [isAccountsOpen, setIsAccountsOpen] = useState(false);
    const [isDarkTheme, setIsDarkTheme] = useState(true);
    const [showConfigMenu, setShowConfigMenu] = useState(false);
    const [isPipelineDetailOpen, setIsPipelineDetailOpen] = useState(false);
    const [agentStatuses, setAgentStatuses] = useState({});
    const [isCompletedOpen, setIsCompletedOpen] = useState(false);
    const [completedProjects, setCompletedProjects] = useState([]);
    const [isProjectsListOpen, setIsProjectsListOpen] = useState(false);
    const [allProjects, setAllProjects] = useState([]);
    const [agentList, setAgentList] = useState({});
    const [pipelineMetadata, setPipelineMetadata] = useState({ stage_order: [], stages: {}, agent_stages: {} });
    const [isQuestionOpen, setIsQuestionOpen] = useState(false);
    const [isLogPanelOpen, setIsLogPanelOpen] = useState(true);
    const [language, setLanguage] = useState('en');
    const [autonomousMode, setAutonomousMode] = useState(true);

    const activePort = window.BACKEND_PORT || 8080;

    const fetchAgents = () => {
        fetch(`http://localhost:${activePort}/api/agents`)
            .then(r => r.json())
            .then(data => setAgentList(data))
            .catch(err => console.error('[API Error Agents]:', err));
    };

    const fetchPipelineMetadata = () => {
        fetch(`http://localhost:${activePort}/api/pipeline/metadata`)
            .then(r => r.json())
            .then(data => setPipelineMetadata(data))
            .catch(err => console.error('[API Error Pipeline Metadata]:', err));
    };

    useEffect(() => {
        fetch(`http://localhost:${activePort}/api/config/system`)
            .then(r => r.json())
            .then(data => {
                const root = document.documentElement;
                if (data.accent_color) root.style.setProperty('--accent', data.accent_color);
                const isDark = data.theme !== 'light';
                root.classList.toggle('theme-light', !isDark);
                root.classList.toggle('theme-reduced-motion', data.animation_speed === 'off');
                root.classList.remove('font-small', 'font-medium', 'font-large');
                if (data.font_size) root.classList.add('font-' + data.font_size);
                setIsDarkTheme(isDark);
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

        fetch(`http://localhost:${activePort}/api/projects/active/current`)
            .then(r => r.json())
            .then(data => {
                if (data.project) {
                    setActiveProject(data.project);
                    setChatHistory(data.project.chat_history || []);
                }
            })
            .catch(err => console.error('[API]:', err));

        fetchAgents();
        fetchPipelineMetadata();

        const handleClickOutside = (e) => {
            if (!e.target.closest('[data-config-trigger]')) setShowConfigMenu(false);
        };
        document.addEventListener('click', handleClickOutside);
        return () => document.removeEventListener('click', handleClickOutside);
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

    // Persist session to localStorage
    useEffect(() => {
        try {
            const saved = localStorage.getItem('studio_session');
            if (saved) {
                const data = JSON.parse(saved);
                if (data.activeProject) setActiveProject(data.activeProject);
                if (data.chatHistory) setChatHistory(data.chatHistory);
                if (data.studioLogs) setLogs(data.studioLogs);
            }
        } catch { }
    }, []);

    useEffect(() => {
        if (activeProject) {
            try {
                localStorage.setItem('studio_session', JSON.stringify({
                    activeProject, chatHistory, studioLogs: logs
                }));
            } catch { }
        }
    }, [activeProject, chatHistory, logs]);

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

    // Poll active project status
    const pollRef = useRef(null);
    useEffect(() => {
        if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
        if (!activeProject || ['completed', 'failed', 'failed_qa', 'blocked', 'needs_credentials', 'cancelled'].includes(activeProject.status)) return;

        pollRef.current = setInterval(() => {
            fetch(`http://localhost:${activePort}/api/projects/active/current`)
                .then(r => r.json())
                .then(data => {
                    if (data.project) {
                        setActiveProject(data.project);
                        if (data.project.status === 'awaiting_input') {
                            setIsQuestionOpen(true);
                        }
                        if (data.project.logs && data.project.logs.length > 0) {
                            setLogs([...data.project.logs].reverse().slice(0, 500));
                        }
                    }
                })
                .catch(err => console.error('[Polling Error]:', err));
        }, 3000);

        return () => { if (pollRef.current) clearInterval(pollRef.current); };
    }, [activeProject?.status, activePort]);

    // Stream project logs over WebSocket; polling above remains the fallback.
    useEffect(() => {
        if (!activeProject?.project_id) return;
        const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
        const ws = new WebSocket(`${protocol}://localhost:${activePort}/ws/projects/${activeProject.project_id}/logs`);
        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                if (data.type === 'snapshot' && Array.isArray(data.logs)) {
                    setLogs([...data.logs].reverse().slice(0, 500));
                    if (data.status) setActiveProject(p => p ? { ...p, status: data.status } : p);
                }
                if (data.type === 'log' && data.message) {
                    setLogs(prev => prev[0] === data.message ? prev : [data.message, ...prev].slice(0, 500));
                    if (data.status) setActiveProject(p => p ? { ...p, status: data.status } : p);
                }
            } catch { }
        };
        ws.onerror = () => { };
        return () => ws.close();
    }, [activeProject?.project_id, activePort]);

    // Listen for project-created custom event
    useEffect(() => {
        const handler = (e) => {
            const project = e.detail;
            if (project) {
                setActiveProject(project);
                addLog(`[System]: Project "${project.title}" started. Pipeline running...`);
            }
        };
        window.addEventListener('project-created', handler);
        return () => window.removeEventListener('project-created', handler);
    }, [activePort]);

    // Listen for create-from-spec custom event
    useEffect(() => {
        const handler = (e) => {
            const { spec } = e.detail || {};
            if (!spec) return;
            const title = spec.title || spec.project_title || spec.job_title || 'Project from Proposal';
            const description = spec.refined_spec || spec.project_description || spec.proposal || JSON.stringify(spec);
            fetch(`http://localhost:${activePort}/api/projects/manual`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title, description, platform: 'proposal' })
            })
                .then(r => r.json())
                .then(data => {
                    setActiveProject(data);
                    setChatHistory([]);
                    addLog(`[System]: Project "${title}" created from proposal. Ready to start.`);
                })
                .catch(err => addLog(`[System]: Failed to create project from spec - ${err.message}`));
        };
        window.addEventListener('create-from-spec', handler);
        return () => window.removeEventListener('create-from-spec', handler);
    }, [activePort]);

    // Search suggestions
    useEffect(() => {
        if (searchQuery.trim().length > 0) {
            fetch(`http://localhost:${activePort}/api/platforms/suggest?q=${encodeURIComponent(searchQuery)}`)
                .then(r => r.json()).then(data => setSuggestions(data))
                .catch(err => console.error('[API Error Suggestions]:', err));
        } else {
            setSuggestions([]);
        }
    }, [searchQuery, activePort]);

    const handleSearchTrigger = () => {
        if (searchQuery.trim()) {
            fetch(`http://localhost:${activePort}/api/jobs/search?q=${encodeURIComponent(searchQuery)}`)
                .then(r => r.json())
                .then(data => {
                    setSearchResults(data.results || []);
                    addLog(`[Search]: Query complete. Found ${(data.results || []).length} tasks.`);
                })
                .catch(err => console.error('[API Error Search]:', err));
        }
    };

    const handleClaimJob = (job) => {
        fetch(`http://localhost:${activePort}/api/projects/claim`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title: job.title, platform: job.platform, description: job.description, budget: job.budget, url: job.url })
        })
            .then(r => r.json())
            .then(data => {
                setActiveProject(data);
                setChatHistory([]);
                addLog(`[System]: Job CLAIMED! "${job.title}" moved to CEO office.`);
            })
            .catch(err => console.error('[API Error Claim]:', err));
    };

    const handleCreateManualProject = (title, description, projectMode = 'mvp') => {
        setIsNewProjectOpen(false);
        fetch(`http://localhost:${activePort}/api/projects/manual`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, initial_description: description, project_mode: projectMode })
        })
            .then(r => r.json())
            .then(data => {
                setActiveProject({ project_id: data.project_id, status: data.status, title, project_mode: data.project_mode });
                setChatHistory(data.chat_history);
                setIsChatOpen(true);
                addLog(`[Manual]: Created project "${title}". Chatting with Maya.`);
            })
            .catch(err => console.error('[Error Manual Project Setup]:', err));
    };

    const handleApproveSpec = () => {
        if (activeProject) {
            fetch(`http://localhost:${activePort}/api/projects/${activeProject.project_id}/approve`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ approved: true, autonomous_mode: autonomousMode })
            })
                .then(r => r.json())
                .then(data => {
                    if (data.status === 'success') {
                        setActiveProject(s => ({ ...s, status: data.next_status }));
                        setIsChatOpen(false);
                        addLog('[System]: Specification approved. Archon generating architecture paths.');
                    }
                })
                .catch(err => console.error('[Error Approving Spec]:', err));
        }
    };

    const toggleTheme = () => {
        const newDark = !isDarkTheme;
        setIsDarkTheme(newDark);
        document.documentElement.classList.toggle('theme-light', !newDark);
        fetch(`http://localhost:${activePort}/api/config/system`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ theme: newDark ? 'dark' : 'light' })
        }).catch(() => { });
        addLog(`[System]: Switched to ${newDark ? 'dark' : 'light'} theme.`);
    };

    const scrollRef = useRef(null);

    const addLog = (msg) => {
        const time = new Date().toLocaleTimeString();
        setLogs(prev => [`[${time}] ${msg}`, ...prev].slice(0, 500));
    };

    useEffect(() => {
        if (scrollRef.current) scrollRef.current.scrollTop = 0;
    }, [logs]);

    const handlePushToGitHub = (port, project) => {
        if (!project || !project.project_id) { addLog('[GitHub]: No active project to push.'); return; }
        fetch(`http://localhost:${port}/api/projects/${project.project_id}/github/push`, { method: 'POST' })
            .then(r => r.json())
            .then(data => addLog(`[GitHub]: ${data.status === 'pushed' ? 'Pushed! ' + (data.url || '') : 'Failed: ' + JSON.stringify(data)}`))
            .catch(err => addLog(`[GitHub]: Error - ${err.message}`));
    };

    const handleExport = (port, project) => {
        if (!project || !project.project_id) { addLog('[Export]: No active project.'); return; }
        const url = `http://localhost:${port}/api/projects/${project.project_id}/export?fmt=markdown`;
        window.open(url, '_blank');
        addLog(`[Export]: Download started for ${project.title}.`);
    };

    const handleQARetry = (port, project) => {
        if (!project || !project.project_id) { addLog('[QA]: No active project.'); return; }
        fetch(`http://localhost:${port}/api/projects/${project.project_id}/retry`, { method: 'POST' })
            .then(r => r.json())
            .then(data => addLog(`[QA]: ${data.message || data.status}`))
            .catch(err => addLog(`[QA]: Error - ${err.message}`));
    };

    const getSystemPath = async (port, editor) => {
        try {
            const data = await (await fetch(`http://localhost:${port}/api/config/system`)).json();
            return data[`${editor}_path`] || editor;
        } catch { return editor; }
    };

    const handleOpenEditor = async (port, project, editor) => {
        if (!project || !project.project_id) { addLog('[Editor]: No active project.'); return; }
        try {
            const dirData = await (await fetch(`http://localhost:${port}/api/projects/${project.project_id}/dir`)).json();
            const editorPath = await getSystemPath(port, editor);
            const openRes = await fetch(`http://localhost:${port}/api/system/open-editor`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ editor, path: dirData.path })
            });
            if (openRes.ok) {
                const data = await openRes.json().catch(() => ({}));
                addLog(`[Editor]: Opened in ${editor} (${data.executable || editorPath}).`);
                return;
            }
            if (window.env && window.env.openInEditor) {
                const errData = await openRes.json().catch(() => ({}));
                addLog(`[Editor]: Backend fallback failed - ${errData.detail || openRes.status}. Trying Electron...`);
                await window.env.openInEditor(editorPath, dirData.path);
                addLog(`[Editor]: Opened in ${editor}.`);
                return;
            }
            const errData = await openRes.json().catch(() => ({}));
            throw new Error(errData.detail || `Run "${editorPath}" "${dirData.path}" in terminal.`);
        } catch (err) {
            addLog(`[Editor]: Error - ${err.message}`);
            if (window.env && window.env.openExternal) window.env.openExternal('https://code.visualstudio.com/download');
        }
    };

    const handleShowInExplorer = async (port, project) => {
        if (!project || !project.project_id) { addLog('[Explorer]: No active project.'); return; }
        try {
            let dirData = { path: project.path || project.target_path || '' };
            if (!dirData.path) {
                const dirRes = await fetch(`http://localhost:${port}/api/projects/${project.project_id}/dir`);
                if (!dirRes.ok) {
                    const errData = await dirRes.json().catch(() => ({}));
                    throw new Error(errData.detail || 'Project directory not found');
                }
                dirData = await dirRes.json();
            }
            const openRes = await fetch(`http://localhost:${port}/api/system/open-path`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: dirData.path })
            });
            if (!openRes.ok) {
                if (openRes.status === 405) {
                    const fallbackRes = await fetch(`http://localhost:${port}/api/system/open-path?path=${encodeURIComponent(dirData.path)}`);
                    if (fallbackRes.ok) {
                        addLog('[Explorer]: Opened folder.');
                        return;
                    }
                }
                if (window.env && window.env.revealInExplorer) {
                    const electronError = await window.env.revealInExplorer(dirData.path);
                    if (!electronError) {
                        addLog('[Explorer]: Opened folder.');
                        return;
                    }
                    throw new Error(electronError);
                }
                const errData = await openRes.json().catch(() => ({}));
                throw new Error(errData.detail || 'Backend could not open folder');
            }
            const openData = await openRes.json().catch(() => ({}));
            if (openData.status !== 'opened') {
                throw new Error(openData.detail || 'Backend did not confirm folder opening');
            }
            addLog('[Explorer]: Opened folder.');
        } catch (err) { addLog(`[Explorer]: Error - ${err.message}`); }
    };

    const getActiveStageInfo = () => {
        if (!activeProject || ['completed', 'failed', 'failed_qa', 'blocked', 'needs_credentials'].includes(activeProject.status)) return null;
        const agents = Object.values(agentList).length ? Object.values(agentList) : Object.values(fallbackAgents);

        const workingEntry = Object.entries(agentStatuses).find(([, status]) => (
            status?.status === 'working' && (!status.project_id || status.project_id === activeProject.project_id)
        ));
        if (workingEntry) {
            const [agentId, status] = workingEntry;
            const info = agents.find(a => a.id === agentId) || { name: agentId, emoji: '⚙️', color: 'var(--accent)' };
            return { ...info, task: status.task || 'working' };
        }

        const status = activeProject.status || '';
        const attentionInfo = {
            awaiting_input: { name: 'User', emoji: '❓', color: '#f59e0b', task: 'awaiting input' },
            needs_user_input: { name: 'User', emoji: '⚠️', color: '#f59e0b', task: 'manual action needed' },
        };
        return attentionInfo[status] || null;
    };

    const handleRestart = () => {
        if (!activeProject) return;
        setActiveProject(p => ({ ...p, status: 'planning' }));
        fetch(`http://localhost:${activePort}/api/projects/${activeProject.project_id}/restart`, { method: 'POST' })
            .then(r => r.json())
            .then(data => addLog(`[System]: ${data.message}`))
            .catch(err => addLog(`[System]: Restart failed - ${err.message}`));
    };

    const handleStopGeneration = () => {
        if (!activeProject) return;
        fetch(`http://localhost:${activePort}/api/projects/${activeProject.project_id}/cancel`, { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                setActiveProject(p => p ? { ...p, status: 'cancelled' } : p);
                addLog(`[System]: Stop Generation requested (${data.status}).`);
            })
            .catch(err => addLog(`[System]: Stop Generation failed - ${err.message}`));
    };

    const handleResume = (project) => {
        if (!project) return;
        fetch(`http://localhost:${activePort}/api/projects/${project.project_id}/resume`, { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                setActiveProject(project);
                setIsProjectsListOpen(false);
                addLog(`[System]: ${data.message}`);
            })
            .catch(err => addLog(`[System]: Resume failed - ${err.message}`));
    };

    const activeStageInfo = getActiveStageInfo();
    const displayedAgents = Object.values(agentList).length ? Object.values(agentList) : Object.values(fallbackAgents);
    const isGenerating = activeProject && !['created', 'completed', 'failed', 'failed_qa', 'blocked', 'needs_credentials', 'cancelled', 'awaiting_input', 'needs_user_input'].includes(activeProject.status);
    const t = (key) => tr(language, key);

    // The dashboard is the primary workspace; existing dialogs below remain mounted by state.
    if (true) return (
        <>
            <StudioDashboard
                activePort={activePort}
                project={activeProject}
                logs={logs}
                agents={agentList}
                statuses={agentStatuses}
                autonomousMode={autonomousMode}
                onAutonomousMode={setAutonomousMode}
                onNewProject={() => setIsNewProjectOpen(true)}
                onSettings={() => setIsSettingsOpen(true)}
                onProjects={() => {
                    fetch(`http://localhost:${activePort}/api/projects/all`).then(r => r.json()).then(data => { setAllProjects(data.projects || []); setIsProjectsListOpen(true); }).catch(() => addLog('[Projects]: Unable to load projects.'));
                }}
                onFiles={() => activeProject ? setIsFileBrowserOpen(true) : addLog('[Files]: No active project.')}
                onPush={() => handlePushToGitHub(activePort, activeProject)}
                onExport={() => handleExport(activePort, activeProject)}
                onOpenEditor={(editor) => handleOpenEditor(activePort, activeProject, editor)}
                onOpenCode={() => fetch(`http://localhost:${activePort}/api/opencode/web`, { method: 'POST' }).then(r => r.json()).then(data => { if (data.url) window.open(data.url, '_blank', 'noopener,noreferrer'); addLog(`[OpenCode]: ${data.message || data.status}`); }).catch(error => addLog(`[OpenCode]: ${error.message}`))}
                onAgentChat={setActiveAgentChat}
                onPipeline={() => activeProject ? setIsPipelineDetailOpen(true) : addLog('[Pipeline]: No active project.')}
                onOpenBriefing={() => activeProject ? setIsChatOpen(true) : addLog('[Chat]: No active project.')}
                onStopGeneration={handleStopGeneration}
                onRetry={() => activeProject ? handleRestart() : addLog('[System]: No active project.')}
                onResume={() => activeProject ? handleResume(activeProject) : addLog('[System]: No active project.')}
                onContinueDone={() => handleQARetry(activePort, activeProject)}
                onKeyManager={() => setIsKeyManagerOpen(true)}
                onInfo={() => setIsInfoOpen(true)}
                isGenerating={Boolean(isGenerating)}
            />

            {isModelSelectorOpen && <ModelSelector activePort={activePort} onClose={() => setIsModelSelectorOpen(false)} addLog={addLog} />}
            {isNewProjectOpen && <NewProjectModal onCreate={handleCreateManualProject} onClose={() => setIsNewProjectOpen(false)} />}
            {isChatOpen && activeProject && <ProjectChat activePort={activePort} projectId={activeProject.project_id} chatHistory={chatHistory} onUpdateHistory={setChatHistory} onApprove={handleApproveSpec} onClose={() => setIsChatOpen(false)} />}
            {isKeyManagerOpen && <KeyManagerModal activePort={activePort} onClose={() => setIsKeyManagerOpen(false)} addLog={addLog} />}
            {activeAgentChat && activeAgentChat === 'goldie' ? <GoldieChat activePort={activePort} onClose={() => setActiveAgentChat(null)} addLog={addLog} project={activeProject} /> : activeAgentChat && <AgentChat agentId={activeAgentChat} activePort={activePort} onClose={() => setActiveAgentChat(null)} addLog={addLog} project={activeProject} />}
            {isSettingsOpen && <SettingsModal activePort={activePort} onClose={() => setIsSettingsOpen(false)} addLog={addLog} />}
            {isFileBrowserOpen && activeProject && <FileBrowserModal activePort={activePort} projectId={activeProject.project_id} projectTitle={activeProject.title} onClose={() => setIsFileBrowserOpen(false)} addLog={addLog} />}
            {isInfoOpen && <InfoModal activePort={activePort} onClose={() => setIsInfoOpen(false)} addLog={addLog} />}
            {isPipelineDetailOpen && activeProject && <PipelineDetailModal project={activeProject} agentStatuses={agentStatuses} agents={agentList} pipelineMetadata={pipelineMetadata} onClose={() => setIsPipelineDetailOpen(false)} />}
            {isQuestionOpen && activeProject && <QuestionAnswerModal activePort={activePort} projectId={activeProject.project_id} onClose={() => setIsQuestionOpen(false)} />}
        </>
    );

    return (
        <div className="studio-shell flex flex-col h-screen w-screen font-sans antialiased" style={{ backgroundColor: 'var(--bg-primary)', color: 'var(--text-primary)' }}>
            <div className="studio-orb studio-orb-a" />
            <div className="studio-orb studio-orb-b" />
            <div className="studio-grid" />

            {/* HEADER CONTROLS BAR */}
            <header className="studio-topbar flex items-center justify-between px-6 py-4 shadow-md" style={{ backgroundColor: 'var(--bg-secondary)', borderBottom: '1px solid var(--border)' }}>
                <div className="studio-brand flex items-center space-x-3">
                    <div className="studio-status-dot w-3 h-3 rounded-full bg-emerald-500 animate-pulse" />
                    <h1 className="text-lg font-bold tracking-wider" style={{ color: 'var(--text-primary)' }}>{t('appTitle')}</h1>
                    <button
                        onClick={() => setIsUpworkOpen(true)}
                        className="ml-0.5 px-2.5 py-1 rounded text-[10px] font-medium transition-colors"
                        style={{ backgroundColor: 'color-mix(in srgb, var(--success) 15%, transparent)', border: '1px solid color-mix(in srgb, var(--success) 30%, transparent)', color: 'var(--success)' }}
                        title="Find work on freelance platforms"
                    >
                        ⚡ {t('jobs')}
                    </button>
                    <button
                        onClick={() => setIsAccountsOpen(true)}
                        className="ml-0.5 px-2.5 py-1 rounded text-[10px] font-medium transition-colors"
                        style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
                        title="Connected accounts"
                    >
                        🔗 {t('accounts')}
                    </button>
                    <button
                        onClick={() => {
                            fetch(`http://localhost:${activePort}/api/projects/all`)
                                .then(r => r.json())
                                .then(data => { setAllProjects(data.projects || []); setIsProjectsListOpen(true); })
                                .catch(() => {});
                        }}
                        className="ml-0.5 px-2.5 py-1 rounded text-[10px] font-medium transition-colors"
                        style={{ backgroundColor: 'color-mix(in srgb, var(--warning) 15%, transparent)', border: '1px solid color-mix(in srgb, var(--warning) 30%, transparent)', color: 'var(--warning)' }}
                        title="View all projects"
                    >
                        📋 {t('projects')}
                    </button>
                </div>

                <button
                    onClick={() => setIsModelSelectorOpen(true)}
                    className="font-medium px-4 py-1.5 rounded-lg text-xs transition-colors"
                    style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}
                >
                    ⚙️ {t('configureStaff')}
                </button>

                    <div className="flex items-center gap-1 ml-2" style={{ borderLeft: '1px solid var(--border)', paddingLeft: '8px' }}>
                        {displayedAgents.filter(a => a.enabled).map(agent => (
                          <button
                            key={agent.id}
                            onClick={() => setActiveAgentChat(agent.id)}
                            className="px-2 py-1.5 rounded-lg text-[10px] font-medium transition-colors"
                            style={{
                              backgroundColor: `color-mix(in srgb, ${agent.color || '#6366f1'} 10%, transparent)`,
                              border: `1px solid color-mix(in srgb, ${agent.color || '#6366f1'} 30%, transparent)`,
                              color: agent.color || '#6366f1'
                            }}
                            title={`${agent.name} — ${agent.display_role || agent.role}`}
                          >
                            {agent.emoji} {agent.name}
                          </button>
                        ))}
                    </div>

                <button
                    onClick={toggleTheme}
                    className="p-1.5 rounded-lg text-xs transition-colors ml-2"
                    style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text-muted)' }}
                    title={isDarkTheme ? 'Switch to light theme' : 'Switch to dark theme'}
                >
                    {isDarkTheme ? '☀️' : '🌙'}
                </button>

                <div className="relative ml-1">
                    <button
                        onClick={() => setShowConfigMenu(prev => !prev)}
                        data-config-trigger="true"
                        className="p-1.5 rounded-lg text-xs transition-colors"
                        style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text-muted)' }}
                        title="More settings"
                    >
                        🛠️
                    </button>
                    {showConfigMenu && (
                        <div className="absolute top-full right-0 mt-1 shadow-2xl z-50 overflow-hidden min-w-[200px]" style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: '0.5rem' }}>
                            <button
                                onClick={() => { setIsKeyManagerOpen(true); setShowConfigMenu(false); }}
                                className="w-full text-left px-4 py-2.5 text-xs transition-colors"
                                style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}
                            >
                                🔑 {t('manageKeys')}
                            </button>
                            <button
                                onClick={() => { setIsModelSelectorOpen(true); setShowConfigMenu(false); }}
                                className="w-full text-left px-4 py-2.5 text-xs transition-colors"
                                style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}
                            >
                                🤖 {t('configureStaff')}
                            </button>
                            <button
                                onClick={() => { setIsSettingsOpen(true); setShowConfigMenu(false); }}
                                className="w-full text-left px-4 py-2.5 text-xs transition-colors"
                                style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}
                            >
                                🔧 {t('systemPreferences')}
                            </button>
                            <button
                                onClick={() => { setIsInfoOpen(true); setShowConfigMenu(false); }}
                                className="w-full text-left px-4 py-2.5 text-xs"
                                style={{ color: 'var(--text-secondary)' }}
                            >
                                ℹ️ {t('about')}
                            </button>
                        </div>
                    )}
                </div>

                {activeProject && (
                    <div className="flex items-center space-x-3 animate-fade-in">
                        <label
                            className="flex items-center gap-2 px-3 py-1.5 rounded text-[10px] font-bold uppercase tracking-wider"
                            style={{ backgroundColor: autonomousMode ? 'color-mix(in srgb, var(--success) 14%, transparent)' : 'var(--bg-card)', border: '1px solid var(--border)', color: autonomousMode ? 'var(--success)' : 'var(--text-muted)' }}
                            title="When enabled, Codex/OpenCode makes implementation decisions without waiting for confirmation unless blocked."
                        >
                            <input
                                type="checkbox"
                                checked={autonomousMode}
                                onChange={(e) => setAutonomousMode(e.target.checked)}
                                className="accent-emerald-500"
                            />
                            Autonomous Mode
                        </label>
                        {isGenerating && (
                            <button
                                onClick={handleStopGeneration}
                                className="px-3 py-1.5 rounded text-xs font-bold transition-all animate-pulse"
                                style={{ backgroundColor: 'color-mix(in srgb, var(--danger, #ef4444) 18%, transparent)', border: '1px solid color-mix(in srgb, var(--danger, #ef4444) 45%, transparent)', color: 'var(--danger, #ef4444)' }}
                                title="Stop the current OpenCode generation task"
                            >
                                Stop Generation
                            </button>
                        )}
                        {activeProject.status === 'spec_clarification' && (
                            <button
                                onClick={() => setIsChatOpen(true)}
                                className="text-white font-bold px-4 py-1.5 rounded-lg text-xs animate-pulse transition-all active:scale-95"
                                style={{ backgroundColor: 'var(--accent)', border: '1px solid var(--accent-hover)', boxShadow: '0 0 20px var(--accent-bg)' }}
                            >
                                💬 Open Active Briefing Chat
                            </button>
                        )}
                        {activeProject._phase && ['created', 'failed', 'failed_qa', 'blocked', 'needs_credentials'].includes(activeProject.status) && (
                            <button
                                onClick={() => handleResume(activeProject)}
                                className="px-3 py-1.5 rounded text-xs font-bold transition-all animate-pulse"
                                style={{ backgroundColor: '#fbbf24', color: '#000' }}
                                title={`Resume from phase: ${activeProject._phase}`}
                            >
                                ▶ Resume
                            </button>
                        )}
                        {['failed', 'failed_qa', 'blocked', 'needs_credentials'].includes(activeProject.status) && (
                            <button
                                onClick={handleRestart}
                                className="px-3 py-1.5 rounded text-xs font-bold transition-all animate-pulse"
                                style={{ backgroundColor: 'var(--accent)', color: '#fff' }}
                            >
                                🔄 Retry
                            </button>
                        )}
                        {activeProject.status === 'needs_user_input' && activeProject.manual_steps && (
                            <div className="flex items-center space-x-2">
                                <div className="px-3 py-1.5 rounded text-[10px] max-w-[300px] overflow-hidden" style={{ backgroundColor: 'color-mix(in srgb, var(--warning) 15%, transparent)', border: '1px solid color-mix(in srgb, var(--warning) 30%, transparent)', color: 'var(--warning)' }}>
                                    ⚠️ {activeProject.manual_steps[0]}
                                    {activeProject.manual_steps.length > 1 && <span className="ml-1">+{activeProject.manual_steps.length - 1} more</span>}
                                </div>
                                <button
                                    onClick={() => handleQARetry(activePort, activeProject)}
                                    className="px-3 py-1.5 rounded text-xs font-bold transition-all animate-pulse"
                                    style={{ backgroundColor: 'var(--success)', color: '#fff' }}
                                >
                                    ✅ Continue (done)
                                </button>
                            </div>
                        )}
                        <div className="flex items-center gap-2">
                            {activeStageInfo ? (
                                <div
                                    onClick={() => setIsPipelineDetailOpen(true)}
                                    className="cursor-pointer flex items-center gap-2 text-[10px] font-mono px-3 py-1.5 rounded hover:opacity-80 transition-opacity animate-pulse"
                                    style={{ backgroundColor: `color-mix(in srgb, ${activeStageInfo.color} 15%, transparent)`, border: `1px solid color-mix(in srgb, ${activeStageInfo.color} 30%, transparent)`, color: activeStageInfo.color }}
                                >
                                    <span>{activeStageInfo.emoji}</span>
                                    <span className="font-bold">{activeStageInfo.name}</span>
                                    <span className="max-w-[160px] truncate" style={{ color: `color-mix(in srgb, ${activeStageInfo.color} 60%, transparent)` }}>{activeStageInfo.task || 'working'}</span>
                                    <span className="w-1.5 h-1.5 rounded-full animate-ping" style={{ backgroundColor: activeStageInfo.color }} />
                                </div>
                            ) : (
                                <div className="px-4 py-1.5 rounded text-xs font-semibold uppercase font-mono tracking-wider" style={{ backgroundColor: 'color-mix(in srgb, var(--accent) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--accent) 30%, transparent)', color: 'var(--accent)' }}>
                                    {t('stage')}: <span style={{ color: 'var(--text-primary)' }}>{activeProject.status}</span>
                                </div>
                            )}
                        </div>
                        <button
                            onClick={() => handlePushToGitHub(activePort, activeProject)}
                            className="px-2.5 py-1.5 rounded text-[10px] font-medium transition-colors"
                            style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
                            title="Push project to GitHub"
                        >
                            🐙 {t('push')}
                        </button>
                        <button
                            onClick={() => handleExport(activePort, activeProject)}
                            className="px-2.5 py-1.5 rounded text-[10px] font-medium transition-colors"
                            style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
                            title="Export project as Markdown"
                        >
                            📄 {t('export')}
                        </button>
                        <button
                            onClick={() => setIsFileBrowserOpen(true)}
                            className="px-2.5 py-1.5 rounded text-[10px] font-medium transition-colors"
                            style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
                            title="Browse project files"
                        >
                            📁 {t('files')}
                        </button>
                        {activeProject.status === 'completed' && (
                            <>
                                <button
                                    onClick={() => handleOpenEditor(activePort, activeProject, 'vscode')}
                                    className="px-2.5 py-1.5 rounded text-[10px] font-medium transition-colors"
                                    style={{ backgroundColor: 'color-mix(in srgb, var(--accent) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--accent) 30%, transparent)', color: 'var(--accent)' }}
                                    title="Open project in VS Code"
                                >
                                    VS Code
                                </button>
                                <button
                                    onClick={() => handleOpenEditor(activePort, activeProject, 'pycharm')}
                                    className="px-2.5 py-1.5 rounded text-[10px] font-medium transition-colors"
                                    style={{ backgroundColor: 'color-mix(in srgb, var(--accent) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--accent) 30%, transparent)', color: 'var(--accent)' }}
                                    title="Open project in PyCharm"
                                >
                                    PyCharm
                                </button>
                                <button
                                    onClick={() => handleShowInExplorer(activePort, activeProject)}
                                    className="px-2.5 py-1.5 rounded text-[10px] font-medium transition-colors"
                                    style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
                                    title="Show in File Explorer"
                                >
                                    📂 {t('showFolder')}
                                </button>
                            </>
                        )}
                    </div>
                )}
            </header>

            {/* CORE WORKSPACE */}
            <main className="studio-workspace flex flex-1 overflow-hidden p-6 gap-6">
                <ErrorBoundary>

                <section className="studio-panel flex-1 flex flex-col rounded-xl relative overflow-hidden" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
                    <div className="studio-panel-header p-4 flex justify-between items-center" style={{ backgroundColor: 'var(--bg-card)', borderBottom: '1px solid var(--border)' }}>
                        <span className="text-xs font-bold tracking-widest uppercase" style={{ color: 'var(--text-muted)' }}>🏢 {t('liveStudio')}</span>
                        <div className="flex items-center gap-2">
                            <button
                                onClick={() => setIsLogPanelOpen(prev => !prev)}
                                className="px-3 py-1 rounded text-xs font-medium transition-all"
                                style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
                            >
                                {isLogPanelOpen ? t('hideLog') : `${t('showLog')} (${logs.length})`}
                            </button>
                            <button
                                onClick={() => setIsNewProjectOpen(true)}
                                className="px-3 py-1 rounded text-xs font-medium transition-all"
                                style={{ backgroundColor: 'var(--accent)', color: '#fff' }}
                            >
                                ➕ {t('newProject')}
                            </button>
                        </div>
                    </div>

                    <IsoOffice activeProject={activeProject} activePort={activePort} agentStatuses={agentStatuses} />
                </section>

                {isLogPanelOpen ? (
                    <aside className="studio-panel studio-log-panel w-[360px] min-w-[320px] max-w-[420px] flex flex-col rounded-xl overflow-hidden" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
                        <div className="studio-panel-header p-4 flex items-center justify-between" style={{ backgroundColor: 'var(--bg-card)', borderBottom: '1px solid var(--border)' }}>
                            <div>
                                <div className="text-xs font-bold tracking-widest uppercase" style={{ color: 'var(--text-muted)' }}>{t('activityLog')}</div>
                                <div className="text-[10px] mt-0.5" style={{ color: 'var(--text-dim)' }}>{logs.length} events</div>
                            </div>
                            <div className="flex items-center gap-2">
                                {logs.length > 0 && (
                                    <button onClick={() => setLogs([])} className="px-2 py-1 rounded text-[10px] transition-colors" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-muted)' }}>{t('clear')}</button>
                                )}
                                <button onClick={() => setIsLogPanelOpen(false)} className="px-2 py-1 rounded text-[10px] transition-colors" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-muted)' }}>{t('close')}</button>
                            </div>
                        </div>
                        <div className="studio-log flex-1 min-h-0 p-4 font-mono text-xs shadow-inner" style={{ backgroundColor: 'var(--bg-primary)' }}>
                            <div ref={scrollRef} className="studio-log-scroll h-full min-h-0 overflow-y-auto pr-1 space-y-1">
                                {logs.map((log, i) => (
                                    <div key={i} className="studio-log-entry py-1.5 hover:bg-slate-800/20 px-2 rounded-lg" style={{ color: 'var(--success)' }} title={log}>
                                        {log}
                                    </div>
                                ))}
                            </div>
                        </div>
                    </aside>
                ) : (
                    <button
                        onClick={() => setIsLogPanelOpen(true)}
                        className="studio-log-rail rounded-xl px-3 py-4 text-[10px] font-bold uppercase tracking-widest transition-all"
                        style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)', color: 'var(--accent)' }}
                        title="Open Activity Log"
                    >
                        {t('activityLog')} ({logs.length})
                    </button>
                )}

                </ErrorBoundary>
            </main>

            {/* MODALS */}
            {isModelSelectorOpen && (
                <ModelSelector activePort={activePort} onClose={() => setIsModelSelectorOpen(false)} addLog={addLog} />
            )}

            {isNewProjectOpen && (
                <NewProjectModal onCreate={handleCreateManualProject} onClose={() => setIsNewProjectOpen(false)} />
            )}

            {isChatOpen && activeProject && (
                <ProjectChat
                    activePort={activePort}
                    projectId={activeProject.project_id}
                    chatHistory={chatHistory}
                    onUpdateHistory={setChatHistory}
                    onApprove={handleApproveSpec}
                    onClose={() => setIsChatOpen(false)}
                />
            )}

            {activeRoom && (
                <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center p-4 z-50" style={{ backgroundColor: 'color-mix(in srgb, var(--bg-primary) 80%, transparent)' }}>
                    <div className="rounded-xl w-full max-w-md overflow-hidden shadow-2xl" style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}>
                        <div className="p-4 flex justify-between items-center" style={{ backgroundColor: 'var(--bg-secondary)', borderBottom: '1px solid var(--border)' }}>
                            <h3 className="text-xs font-bold uppercase tracking-widest" style={{ color: 'var(--accent)' }}>🏠 Room Monitor: {activeRoom.name}</h3>
                            <button onClick={() => setActiveRoom(null)} className="font-mono text-sm" style={{ color: 'var(--text-muted)' }}>✕</button>
                        </div>
                        <div className="p-5 space-y-4">
                            <p className="text-xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>{activeRoom.desc}</p>
                            <div className="p-3 rounded-lg text-xs font-mono" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                                <span style={{ color: 'var(--text-muted)' }}>Active Staff:</span> {(activeRoom.agents || []).map(a => {
                                    const meta = agentList[a];
                                    return meta ? `${meta.name} (${meta.display_role || meta.role})` : a;
                                }).join(', ') || '-'}
                                <br />
                                <span style={{ color: 'var(--text-muted)' }}>Status:</span> <span style={{ color: 'var(--success)' }}>Idle / Online</span>
                            </div>
                        </div>
                    </div>
                </div>
            )}

            {isKeyManagerOpen && (
                <KeyManagerModal activePort={activePort} onClose={() => setIsKeyManagerOpen(false)} addLog={addLog} />
            )}

            {activeAgentChat && activeAgentChat === 'goldie' ? (
                <GoldieChat activePort={activePort} onClose={() => setActiveAgentChat(null)} addLog={addLog} project={activeProject} />
            ) : activeAgentChat && (
                <AgentChat agentId={activeAgentChat} activePort={activePort} onClose={() => setActiveAgentChat(null)} addLog={addLog} project={activeProject} />
            )}

            {isSettingsOpen && (
                <SettingsModal activePort={activePort} onClose={() => setIsSettingsOpen(false)} addLog={addLog} />
            )}

            {isFileBrowserOpen && activeProject && (
                <FileBrowserModal activePort={activePort} projectId={activeProject.project_id} projectTitle={activeProject.title} onClose={() => setIsFileBrowserOpen(false)} addLog={addLog} />
            )}

            {isInfoOpen && (
                <InfoModal activePort={activePort} onClose={() => setIsInfoOpen(false)} addLog={addLog} />
            )}

            {isUpworkOpen && (
                <JobSearchModal activePort={activePort} onClose={() => setIsUpworkOpen(false)} addLog={addLog} />
            )}

            {isAccountsOpen && (
                <AccountsModal activePort={activePort} onClose={() => setIsAccountsOpen(false)} addLog={addLog} />
            )}

            {isPipelineDetailOpen && activeProject && (
                <PipelineDetailModal project={activeProject} agentStatuses={agentStatuses} agents={agentList} pipelineMetadata={pipelineMetadata} onClose={() => setIsPipelineDetailOpen(false)} />
            )}

            {isQuestionOpen && activeProject && (
                <QuestionAnswerModal activePort={activePort} projectId={activeProject.project_id} onClose={() => setIsQuestionOpen(false)} />
            )}

            {isProjectsListOpen && (
                <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center p-4 z-50" style={{ backgroundColor: 'rgba(0,0,0,0.6)' }}>
                    <div className="rounded-xl w-full max-w-4xl overflow-hidden shadow-2xl" style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}>
                        <div className="p-4 flex justify-between items-center" style={{ backgroundColor: 'var(--bg-secondary)', borderBottom: '1px solid var(--border)' }}>
                            <h3 className="text-xs font-bold uppercase tracking-widest" style={{ color: 'var(--accent)' }}>📋 {t('allProjects')}</h3>
                            <button onClick={() => setIsProjectsListOpen(false)} className="font-mono text-sm" style={{ color: 'var(--text-muted)' }}>✕</button>
                        </div>
                        <div className="p-4 max-h-[500px] overflow-y-auto space-y-2">
                            {allProjects.length === 0 ? (
                                <p className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('noProjects')}</p>
                            ) : allProjects.map(p => (
                                <div key={p.project_id} className="flex items-center justify-between p-3 rounded-lg" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
                                    <div className="flex-1 min-w-0 mr-3">
                                        <div className="flex items-center gap-2">
                                            <p className="text-xs font-medium truncate" style={{ color: 'var(--text-primary)' }}>{p.title}</p>
                                            <span className={`text-[9px] px-1.5 py-0.5 rounded font-medium ${p.status === 'completed' ? 'text-green-400' : ['failed', 'failed_qa', 'blocked', 'needs_credentials'].includes(p.status) ? 'text-red-400' : p.status === 'planning' ? 'text-yellow-400' : 'text-slate-400'}`}
                                                style={{ backgroundColor: p.status === 'completed' ? 'rgba(34,197,94,0.15)' : ['failed', 'failed_qa', 'blocked', 'needs_credentials'].includes(p.status) ? 'rgba(239,68,68,0.15)' : p.status === 'planning' ? 'rgba(234,179,8,0.15)' : 'rgba(100,116,139,0.15)' }}
                                            >
                                                {p.status}
                                            </span>
                                        </div>
                                        <p className="text-[10px] truncate mt-0.5" style={{ color: 'var(--text-muted)' }}>
                                            {p.platform} {p.budget ? `💰 ${p.budget}` : ''} {p.logs_count > 0 ? `📝 ${p.logs_count} logs` : ''} {p.target_path ? ` · ${p.target_path}` : ''}
                                        </p>
                                    </div>
                                    <div className="flex items-center gap-1.5 flex-wrap justify-end">
                                        {p._phase && ['created', 'failed', 'failed_qa', 'blocked', 'needs_credentials'].includes(p.status) && (
                                            <button
                                                onClick={() => handleResume(p)}
                                                className="px-2 py-1 rounded text-[10px] font-medium whitespace-nowrap transition-colors hover:opacity-80"
                                                style={{ backgroundColor: 'rgba(251,191,36,0.15)', border: '1px solid rgba(251,191,36,0.3)', color: '#fbbf24' }}
                                                title={`Resume from phase: ${p._phase}`}
                                            >
                                                ▶ {t('resume')}
                                            </button>
                                        )}
                                        {p.status === 'completed' && p.target_path && (
                                            <button
                                                onClick={() => handleShowInExplorer(activePort, { ...p, path: p.target_path })}
                                                className="px-2 py-1 rounded text-[10px] font-medium whitespace-nowrap transition-colors hover:opacity-80"
                                                style={{ backgroundColor: 'color-mix(in srgb, var(--accent) 15%, transparent)', border: '1px solid color-mix(in srgb, var(--accent) 30%, transparent)', color: 'var(--accent)' }}
                                            >
                                                📂 {t('openFolder')}
                                            </button>
                                        )}
                                        <button
                                            onClick={() => {
                                                if (confirm(`Delete "${p.title}" from computer? This removes project files and the project record.`)) {
                                                    fetch(`http://localhost:${activePort}/api/projects/${p.project_id}`, { method: 'DELETE' })
                                                        .then(r => r.json())
                                                        .then(() => {
                                                            setAllProjects(s => s.filter(x => x.project_id !== p.project_id));
                                                            addLog(`[Projects]: Deleted from computer: "${p.title}"`);
                                                        })
                                                        .catch(err => addLog(`[Projects]: Delete failed - ${err.message}`));
                                                }
                                            }}
                                            className="px-2 py-1 rounded text-[10px] font-medium whitespace-nowrap transition-colors hover:opacity-80"
                                            style={{ backgroundColor: 'rgba(239,68,68,0.15)', border: '1px solid rgba(239,68,68,0.3)', color: '#ef4444' }}
                                        >
                                            🗑 {t('deleteComputer')}
                                        </button>
                                        {p.status === 'completed' && (
                                            <button
                                                onClick={() => {
                                                    if (confirm(`Remove "${p.title}" from completed projects list? Files will stay on computer.`)) {
                                                        fetch(`http://localhost:${activePort}/api/projects/${p.project_id}/list`, { method: 'DELETE' })
                                                            .then(r => r.json())
                                                            .then(() => {
                                                                setAllProjects(s => s.filter(x => x.project_id !== p.project_id));
                                                                addLog(`[Projects]: Removed from completed list: "${p.title}"`);
                                                            })
                                                            .catch(err => addLog(`[Projects]: Remove failed - ${err.message}`));
                                                    }
                                                }}
                                                className="px-2 py-1 rounded text-[10px] font-medium whitespace-nowrap transition-colors hover:opacity-80"
                                                style={{ backgroundColor: 'rgba(100,116,139,0.15)', border: '1px solid rgba(100,116,139,0.3)', color: '#94a3b8' }}
                                            >
                                                {t('removeCompleted')}
                                            </button>
                                        )}
                                    </div>
                                </div>
                            ))}
                        </div>
                        <div className="p-3 flex justify-end" style={{ backgroundColor: 'var(--bg-secondary)', borderTop: '1px solid var(--border)' }}>
                            <button onClick={() => setIsProjectsListOpen(false)} className="px-3 py-1 rounded text-xs" style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>{t('close')}</button>
                        </div>
                    </div>
                </div>
            )}

            {isCompletedOpen && (
                <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center p-4 z-50" style={{ backgroundColor: 'rgba(0,0,0,0.6)' }}>
                    <div className="rounded-xl w-full max-w-lg overflow-hidden shadow-2xl" style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}>
                        <div className="p-4 flex justify-between items-center" style={{ backgroundColor: 'var(--bg-secondary)', borderBottom: '1px solid var(--border)' }}>
                            <h3 className="text-xs font-bold uppercase tracking-widest" style={{ color: 'var(--accent)' }}>✅ Completed Projects</h3>
                            <button onClick={() => setIsCompletedOpen(false)} className="font-mono text-sm" style={{ color: 'var(--text-muted)' }}>✕</button>
                        </div>
                        <div className="p-4 max-h-[400px] overflow-y-auto">
                            {completedProjects.length === 0 ? (
                                <p className="text-xs" style={{ color: 'var(--text-muted)' }}>No completed projects yet.</p>
                            ) : completedProjects.map(p => (
                                <div key={p.project_id} className="flex items-center justify-between p-3 rounded-lg mb-2" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
                                    <div className="flex-1 min-w-0">
                                        <p className="text-xs font-medium truncate" style={{ color: 'var(--text-primary)' }}>{p.title}</p>
                                        <p className="text-[10px] truncate" style={{ color: 'var(--text-muted)' }}>{p.path}</p>
                                    </div>
                                    <button onClick={() => handleShowInExplorer(activePort, p)} className="ml-3 px-2.5 py-1 rounded text-[10px] font-medium whitespace-nowrap transition-colors"
                                        style={{ backgroundColor: 'color-mix(in srgb, var(--accent) 15%, transparent)', border: '1px solid color-mix(in srgb, var(--accent) 30%, transparent)', color: 'var(--accent)' }}>
                                        📂 Open Folder
                                    </button>
                                </div>
                            ))}
                        </div>
                        <div className="p-3 flex justify-end" style={{ backgroundColor: 'var(--bg-secondary)', borderTop: '1px solid var(--border)' }}>
                            <button onClick={() => setIsCompletedOpen(false)} className="px-3 py-1 rounded text-xs" style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>Close</button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

export default App;
