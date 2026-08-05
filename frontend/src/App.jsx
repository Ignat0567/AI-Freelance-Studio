import React, { useState, useEffect, useRef } from 'react';
import ModelSelector from './components/ModelSelector.jsx';
import NewProjectModal from './components/NewProjectModal.jsx';
import ProjectChat from './components/ProjectChat.jsx';
import KeyManagerModal from './components/KeyManagerModal.jsx';
import AgentChat from './components/AgentChat.jsx';
import SettingsModal from './components/SettingsModal.jsx';
import FileBrowserModal from './components/FileBrowserModal.jsx';
import InfoModal from './components/InfoModal.jsx';
import GoldieChat from './components/GoldieChat.jsx';
import QuestionAnswerModal from './components/QuestionAnswerModal.jsx';
import StudioDashboard from './components/StudioDashboard.jsx';
import { tr } from './i18n.js';
import { applyStudioTheme } from './theme.js';
import { getAppVersion, versionLabel } from './app-version.js';

function App() {
    const [logs, setLogs] = useState(['[System]: Core systems initialized. Standing by.']);
    const [activeProject, setActiveProject] = useState(null);
    const [chatHistory, setChatHistory] = useState([]);
    const [isModelSelectorOpen, setIsModelSelectorOpen] = useState(false);
    const [isNewProjectOpen, setIsNewProjectOpen] = useState(false);
    const [isChatOpen, setIsChatOpen] = useState(false);
    const [isKeyManagerOpen, setIsKeyManagerOpen] = useState(false);
    const [activeAgentChat, setActiveAgentChat] = useState(null);
    const [isFileBrowserOpen, setIsFileBrowserOpen] = useState(false);
    const [agentStatuses, setAgentStatuses] = useState({});
    const [allProjects, setAllProjects] = useState([]);
    const [agentList, setAgentList] = useState({});
    const [pipelineMetadata, setPipelineMetadata] = useState({ stage_order: [], stages: {}, agent_stages: {} });
    const [isQuestionOpen, setIsQuestionOpen] = useState(false);
    const [language, setLanguage] = useState('en');
    const [appVersion, setAppVersion] = useState('');

    const clearActiveProjectState = () => {
        setActiveProject(null);
        setChatHistory([]);
        setIsChatOpen(false);
        setIsFileBrowserOpen(false);
        setActiveAgentChat(null);
        try {
            const saved = JSON.parse(localStorage.getItem('studio_session') || '{}');
            delete saved.activeProject;
            delete saved.chatHistory;
            localStorage.setItem('studio_session', JSON.stringify(saved));
        } catch { }
    };
    const [autonomousMode, setAutonomousMode] = useState(true);

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

        Promise.all([
            fetch(`http://localhost:${activePort}/api/projects/active/current`).then(r => r.json()).catch(() => ({})),
            fetch(`http://localhost:${activePort}/api/projects/all`).then(r => r.json()).catch(() => ({ projects: [] })),
        ])
            .then(([current, all]) => {
                const projects = all.projects || [];
                setAllProjects(projects);
                const currentProject = current.project;
                if (!currentProject) return;
                if (!projects.some(project => project.project_id === currentProject.project_id)) {
                    clearActiveProjectState();
                    addLog(`[Projects]: Cleared stale active project "${currentProject.title || currentProject.project_id}".`);
                    return;
                }
                setActiveProject(currentProject);
                setChatHistory(currentProject.chat_history || []);
            })
            .catch(err => console.error('[API]:', err));

        fetchAgents();
        fetchPipelineMetadata();
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
                if (data.activeProject) {
                    fetch(`http://localhost:${activePort}/api/projects/all`)
                        .then(r => r.json())
                        .then(projectData => {
                            const projects = projectData.projects || [];
                            setAllProjects(projects);
                            if (projects.some(project => project.project_id === data.activeProject.project_id)) {
                                setActiveProject(data.activeProject);
                            } else {
                                clearActiveProjectState();
                            }
                        })
                        .catch(() => clearActiveProjectState());
                }
                if (data.chatHistory) setChatHistory(data.chatHistory);
                if (data.studioLogs) setLogs(data.studioLogs);
            }
        } catch { }
    }, [activePort]);

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

    const handleCreateManualProject = (title, description, qualityProfile = 'strict_mvp', targets = {}) => {
        setIsNewProjectOpen(false);
        fetch(`http://localhost:${activePort}/api/projects/manual`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, initial_description: description, project_mode: qualityProfile, quality_profile: qualityProfile, required_targets: targets.requiredTargets || [], optional_targets: targets.optionalTargets || [], strict_completion_toggles: targets.strictToggles || {} })
        })
            .then(r => r.json())
            .then(data => {
                setActiveProject({ project_id: data.project_id, status: data.status, title, project_mode: data.project_mode, quality_profile: data.quality_profile });
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

    const addLog = (msg) => {
        const time = new Date().toLocaleTimeString();
        setLogs(prev => [`[${time}] ${msg}`, ...prev].slice(0, 500));
    };

    const handlePushToGitHub = (port, project) => {
        if (!project || !project.project_id) { addLog('[GitHub]: No active project to push.'); return; }
        fetch(`http://localhost:${port}/api/projects/${project.project_id}/github/push`, { method: 'POST' })
            .then(r => r.json())
            .then(data => addLog(`[GitHub]: ${data.status === 'pushed' ? 'Pushed! ' + (data.url || '') : 'Failed: ' + JSON.stringify(data)}`))
            .catch(err => addLog(`[GitHub]: Error - ${err.message}`));
    };

    const handleExport = (port, project) => {
        if (!project || !project.project_id) { addLog('[Export]: No active project.'); return; }
        const url = `${window.location.origin}/api/projects/${project.project_id}/export?fmt=markdown`;
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

    const normalizeStatus = (value) => String(value || '').trim().toLowerCase().replace(/\s+/g, '_');

    const handleProjectRetry = (project) => {
        if (!project) return;
        if (activeProject?.project_id === project.project_id) setActiveProject(p => p ? { ...p, status: 'verifying' } : p);
        handleQARetry(activePort, project);
    };

    const handleOpenEditor = async (port, project, editor) => {
        if (!project || !project.project_id) { addLog('[Editor]: No active project.'); return; }
        try {
            const dirData = await (await fetch(`http://localhost:${port}/api/projects/${project.project_id}/dir`)).json();
            const openRes = await fetch(`http://localhost:${port}/api/system/open-editor`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ editor, path: dirData.path })
            });
            if (openRes.ok) {
                const data = await openRes.json().catch(() => ({}));
                addLog(`[Editor]: Opened in ${editor} (${data.executable || editor}).`);
                return;
            }
            const errData = await openRes.json().catch(() => ({}));
            throw new Error(errData.detail || `Run "${editor}" "${dirData.path}" in terminal.`);
        } catch (err) {
            addLog(`[Editor]: Error - ${err.message}`);
            addLog('[Editor]: Download an editor manually from its official website.');
        }
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
                addLog(`[System]: ${data.message}`);
            })
            .catch(err => addLog(`[System]: Resume failed - ${err.message}`));
    };

    const loadAllProjects = () => {
        return fetch(`http://localhost:${activePort}/api/projects/all`)
            .then(r => r.json())
            .then(data => {
                const projects = data.projects || [];
                setAllProjects(projects);
                if (activeProject?.project_id && !projects.some(project => project.project_id === activeProject.project_id)) {
                    clearActiveProjectState();
                    addLog(`[Projects]: Cleared stale active project "${activeProject.title || activeProject.project_id}".`);
                }
                return projects;
            })
            .catch(() => {
                addLog('[Projects]: Unable to load projects.');
                return [];
            });
    };

    const handleDeleteProjectFromComputer = (project) => {
        if (!project) return;
        if (!confirm(`Delete "${project.title}" from computer? This removes project files and the project record.`)) return;
        fetch(`http://localhost:${activePort}/api/projects/${project.project_id}`, { method: 'DELETE' })
            .then(r => r.json())
            .then(() => {
                setAllProjects(s => s.filter(x => x.project_id !== project.project_id));
                if (activeProject?.project_id === project.project_id) setActiveProject(null);
                addLog(`[Projects]: Deleted from computer: "${project.title}"`);
            })
            .catch(err => addLog(`[Projects]: Delete failed - ${err.message}`));
    };

    const handleRemoveProjectFromList = (project) => {
        if (!project) return;
        if (!confirm(`Remove "${project.title}" from project list? Files will stay on computer.`)) return;
        fetch(`http://localhost:${activePort}/api/projects/${project.project_id}/list`, { method: 'DELETE' })
            .then(r => r.json())
            .then(() => {
                setAllProjects(s => s.filter(x => x.project_id !== project.project_id));
                if (activeProject?.project_id === project.project_id) setActiveProject(null);
                addLog(`[Projects]: Removed from list: "${project.title}"`);
            })
            .catch(err => addLog(`[Projects]: Remove failed - ${err.message}`));
    };

    const activeStatus = normalizeStatus(activeProject?.status);
    const isGenerating = activeProject && !['created', 'completed', 'failed', 'failed_qa', 'failed_final_audit', 'blocked', 'needs_credentials', 'cancelled', 'awaiting_input', 'needs_user_input'].includes(activeStatus);
    const t = (key) => tr(language, key);

    return (
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
                settingsContent={<SettingsModal activePort={activePort} embedded addLog={addLog} />}
                infoContent={<InfoModal activePort={activePort} embedded addLog={addLog} appVersion={appVersion} />}
                projects={allProjects}
                onProjects={loadAllProjects}
                onRetryProject={handleProjectRetry}
                onDeleteProject={handleDeleteProjectFromComputer}
                onRemoveProjectFromList={handleRemoveProjectFromList}
                onFiles={() => activeProject ? setIsFileBrowserOpen(true) : addLog('[Files]: No active project.')}
                onPush={() => handlePushToGitHub(activePort, activeProject)}
                onExport={() => handleExport(activePort, activeProject)}
                onOpenEditor={(editor) => handleOpenEditor(activePort, activeProject, editor)}
                onOpenCode={() => fetch(`http://localhost:${activePort}/api/opencode/web`, { method: 'POST' }).then(async r => { const data = await r.json(); if (!r.ok) throw new Error(data.detail || 'OpenCode Web failed'); return data; }).then(data => { const browserWindow = data.url ? window.open(data.url, '_blank', 'noopener,noreferrer') : null; addLog(`[OpenCode]: ${browserWindow ? data.message || data.status : `Web is ready, but the browser could not be opened. Open ${data.url} manually.`}`); }).catch(error => addLog(`[OpenCode]: ${error.message}`))}
                onAgentChat={setActiveAgentChat}
                pipelineMetadata={pipelineMetadata}
                onPipeline={() => activeProject ? null : addLog('[Pipeline]: No active project.')}
                onOpenBriefing={() => activeProject ? setIsChatOpen(true) : addLog('[Chat]: No active project.')}
                onStopGeneration={handleStopGeneration}
                onRetry={() => activeProject ? (['blocked', 'failed_qa', 'failed_final_audit', 'needs_credentials', 'needs_user_input'].includes(normalizeStatus(activeProject.status)) ? handleQARetry(activePort, activeProject) : handleRestart()) : addLog('[System]: No active project.')}
                onResume={() => activeProject ? handleResume(activeProject) : addLog('[System]: No active project.')}
                onContinueDone={() => handleQARetry(activePort, activeProject)}
                onKeyManager={() => setIsKeyManagerOpen(true)}
                isGenerating={Boolean(isGenerating)}
            />

            {isModelSelectorOpen && <ModelSelector activePort={activePort} onClose={() => setIsModelSelectorOpen(false)} addLog={addLog} />}
            {isNewProjectOpen && <NewProjectModal onCreate={handleCreateManualProject} onClose={() => setIsNewProjectOpen(false)} />}
            {isChatOpen && activeProject && <ProjectChat activePort={activePort} projectId={activeProject.project_id} chatHistory={chatHistory} onUpdateHistory={setChatHistory} onApprove={handleApproveSpec} onClose={() => setIsChatOpen(false)} />}
            {isKeyManagerOpen && <KeyManagerModal activePort={activePort} onClose={() => setIsKeyManagerOpen(false)} addLog={addLog} />}
            {activeAgentChat && activeAgentChat === 'goldie' ? <GoldieChat activePort={activePort} onClose={() => setActiveAgentChat(null)} addLog={addLog} project={activeProject} /> : activeAgentChat && <AgentChat agentId={activeAgentChat} activePort={activePort} onClose={() => setActiveAgentChat(null)} addLog={addLog} project={activeProject} />}
            {isFileBrowserOpen && activeProject && <FileBrowserModal activePort={activePort} projectId={activeProject.project_id} projectTitle={activeProject.title} onClose={() => setIsFileBrowserOpen(false)} addLog={addLog} />}
            {isQuestionOpen && activeProject && <QuestionAnswerModal activePort={activePort} projectId={activeProject.project_id} onClose={() => setIsQuestionOpen(false)} />}
        </>
    );
}

export default App;
