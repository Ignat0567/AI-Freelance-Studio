import React, { useEffect, useMemo, useState } from 'react';
import { PipelineDetailContent } from './PipelineDetailModal.jsx';

const navItems = [
  { id: 'overview', label: 'Overview', icon: 'OV' },
  { id: 'projects', label: 'Projects', icon: 'PR' },
  { id: 'pipeline', label: 'Pipeline', icon: 'PL' },
  { id: 'mobile', label: 'Mobile Preview', icon: 'MB' },
  { id: 'team', label: 'AI Team', icon: 'AI' },
  { id: 'issues', label: 'Issues', icon: 'IS' },
  { id: 'logs', label: 'Logs', icon: 'LG' },
  { id: 'settings', label: 'Settings', icon: 'ST' },
];

const knownProviderLabels = {
  maya: 'ChatGPT',
  codex: 'Grok',
  elena: 'Gemini',
  lupa: 'Claude',
  alex: 'Alex',
  goldie: 'Goldie',
};

function pretty(value, fallback = 'Unavailable') {
  if (value === null || value === undefined || value === '') return fallback;
  return String(value).replace(/_/g, ' ');
}

function compactText(value, fallback = 'No description available.') {
  const text = String(value || '').trim();
  return text || fallback;
}

function statusTone(status) {
  if (['completed', 'passed', 'done'].includes(status)) return 'success';
  if (['working', 'running', 'in_progress', 'planning', 'building', 'qa'].includes(status)) return 'active';
  if (['failed', 'failed_qa', 'blocked', 'needs_credentials', 'error'].includes(status)) return 'danger';
  if (['awaiting_input', 'needs_user_input', 'created', 'cancelled'].includes(status)) return 'warning';
  return 'idle';
}

function buildPipeline(project, tasks) {
  const rawStage = project?._phase || project?.stage || project?.pipeline_stage || project?.status;
  const stages = Array.isArray(project?.pipeline)
    ? project.pipeline
    : Array.isArray(project?.stages)
      ? project.stages
      : Array.isArray(tasks) && tasks.length
        ? tasks.slice(0, 6).map(task => ({ name: task.title || task.name || task.id, status: task.status }))
        : [];

  if (stages.length) {
    return stages.map((stage, index) => {
      const name = stage.name || stage.label || stage.stage || `Stage ${index + 1}`;
      const state = stage.status || stage.state || (name === rawStage ? 'working' : index === 0 ? project?.status : 'waiting');
      return { name, state, tone: statusTone(state) };
    });
  }

  const defaults = ['Brief', 'Plan', 'Build', 'QA', 'Audit', 'Deliver'];
  const status = project?.status || 'waiting';
  const activeIndex = project ? Math.max(0, defaults.findIndex(stage => rawStage && stage.toLowerCase().includes(String(rawStage).toLowerCase()))) : -1;
  return defaults.map((name, index) => ({
    name,
    state: !project ? 'waiting' : index < activeIndex ? 'completed' : index === activeIndex || (activeIndex === -1 && index === 0) ? status : 'waiting',
    tone: !project ? 'idle' : index < activeIndex ? 'success' : index === activeIndex || (activeIndex === -1 && index === 0) ? statusTone(status) : 'idle',
  }));
}

function getAgentEntries(agents) {
  return Object.entries(agents || {}).map(([id, agent]) => ({ id, ...agent }));
}

function getAgentInitials(agent) {
  if (agent.id === 'opencode') return 'OC';
  const name = agent.name || agent.id || 'AI';
  return name.split(/\s+/).map(part => part[0]).join('').slice(0, 2).toUpperCase();
}

function getPrimaryAction(project, handlers) {
  if (!project) return { label: 'New project', onClick: handlers.onNewProject };
  if (project.status === 'spec_clarification') return { label: 'Open briefing', onClick: handlers.onOpenBriefing };
  if (project.status === 'needs_user_input') return { label: 'Continue', onClick: handlers.onContinueDone };
  if (['failed', 'failed_qa', 'blocked', 'needs_credentials'].includes(project.status)) return { label: 'Resolve issue', onClick: handlers.onRetry };
  if (project._phase && ['created', 'cancelled'].includes(project.status)) return { label: 'Continue', onClick: handlers.onResume };
  if (project.status === 'completed') return { label: 'Open project', onClick: handlers.onFiles };
  return { label: 'View pipeline', onClick: handlers.onPipeline };
}

export default function StudioDashboard({
  activePort,
  project,
  logs,
  agents,
  statuses,
  autonomousMode,
  onAutonomousMode,
  onNewProject,
  settingsContent,
  infoContent,
  projects,
  onProjects,
  onDeleteProject,
  onRemoveProjectFromList,
  onFiles,
  onPush,
  onExport,
  onOpenEditor,
  onOpenCode,
  onAgentChat,
  onPipeline,
  pipelineMetadata,
  onOpenBriefing,
  onStopGeneration,
  onRetry,
  onResume,
  onContinueDone,
  onKeyManager,
  isGenerating,
}) {
  const [tasks, setTasks] = useState([]);
  const [activeView, setActiveView] = useState('overview');
  const [chatOpen, setChatOpen] = useState(() => typeof window === 'undefined' || window.innerWidth > 1160);
  const agentEntries = getAgentEntries(agents);
  const chatTabs = agentEntries.length ? agentEntries.filter(agent => agent.enabled !== false).slice(0, 6) : [];
  const [chatTab, setChatTab] = useState('');

  useEffect(() => {
    if (!project?.project_id) { setTasks([]); return; }
    fetch(`http://localhost:${activePort}/api/projects/${project.project_id}/tasks`)
      .then(r => r.json())
      .then(d => setTasks(d.tasks || []))
      .catch(() => setTasks([]));
  }, [activePort, project?.project_id]);

  useEffect(() => {
    if (!chatTab && chatTabs[0]?.id) setChatTab(chatTabs[0].id);
    if (chatTab && chatTabs.length && !chatTabs.some(agent => agent.id === chatTab)) setChatTab(chatTabs[0].id);
  }, [chatTab, chatTabs]);

  const issues = Array.isArray(project?.issues) ? project.issues : [];
  const openIssues = issues.filter(issue => !['closed', 'resolved', 'done'].includes(issue.status));
  const eventRows = (logs || []).slice(0, 7);
  const pipeline = useMemo(() => buildPipeline(project, tasks), [project, tasks]);
  const workingAgent = agentEntries.find(agent => statuses?.[agent.id]?.status === 'working');
  const criteria = Array.isArray(project?.acceptance_criteria) ? project.acceptance_criteria : [];
  const passedCriteria = criteria.filter(item => item.current_verdict === 'passed' || item.verdict === 'passed').length;
  const primaryAction = getPrimaryAction(project, { onNewProject, onOpenBriefing, onContinueDone, onRetry, onResume, onFiles, onPipeline });
  const selectedChatAgent = chatTabs.find(agent => agent.id === chatTab);

  const selectNav = (id) => {
    setActiveView(id);
    if (id === 'projects') onProjects();
    if (id === 'pipeline') onPipeline();
  };

  const openSettings = () => setActiveView('settings');
  const openInfo = () => setActiveView('info');

  return (
    <div className={`fs-shell ${chatOpen ? '' : 'chat-collapsed'}`}>
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
              {item.id === 'issues' && openIssues.length > 0 && <b className="fs-count">{openIssues.length}</b>}
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
            <span className="fs-eyebrow">Active Project</span>
            <strong>{project?.title || 'No active project'}</strong>
            <div>
              <span>{pretty(project?._phase || project?.stage || project?.pipeline_stage || project?.status, 'No stage')}</span>
              <i aria-hidden="true" />
              <span className={`fs-status ${statusTone(project?.status)}`}>{pretty(project?.status, 'Idle')}</span>
              {project?.project_mode && <><i aria-hidden="true" /><span>Mode: {pretty(project.project_mode)}</span></>}
            </div>
          </div>
          <div className="fs-header-actions">
            <label className="fs-compact-toggle" title="When enabled, agents continue without waiting unless blocked.">
              <input type="checkbox" checked={autonomousMode} onChange={event => onAutonomousMode(event.target.checked)} />
              <span>Autonomous</span>
            </label>
            <button type="button" className="fs-pill accent" onClick={onNewProject} title="Create a manual project from your own requirements">Manual Project</button>
            <button type="button" className="fs-pill" onClick={onKeyManager} title="Manage API keys">Keys</button>
            <button type="button" className="fs-pill" onClick={openSettings} title="Open settings">Settings</button>
            <span className="fs-pill status" title="Backend status">Backend {activePort}</span>
            <button type="button" className="fs-pill accent" onClick={() => setChatOpen(value => !value)} aria-expanded={chatOpen} aria-controls="fs-ai-chat-panel">
              {chatOpen ? 'Hide Chat' : 'Show Chat'}
            </button>
          </div>
        </header>

        <div className="fs-body">
          <main className={`fs-workspace ${activeView === 'overview' ? 'fs-overview-workspace' : ''}`} tabIndex={0} aria-label="Central workspace content">
            <ProjectOverview project={project} primaryAction={primaryAction} isGenerating={isGenerating} onStopGeneration={onStopGeneration} onNewProject={onNewProject} />
            {activeView === 'overview' && (
              <>
                <PipelineSummary pipeline={pipeline} project={project} workingAgent={workingAgent} criteria={criteria} passedCriteria={passedCriteria} onPipeline={() => setActiveView('pipeline')} />
                <AgentActivity agents={agentEntries} statuses={statuses} onAgentChat={onAgentChat} />
                <AttentionPanel issues={openIssues} project={project} onRetry={onRetry} onContinueDone={onContinueDone} />
                <ActivityPanel logs={eventRows} onOpenLogs={() => setActiveView('logs')} />
                <DevelopmentTools project={project} onOpenEditor={onOpenEditor} onOpenCode={onOpenCode} onFiles={onFiles} onPush={onPush} onExport={onExport} />
              </>
            )}
            {activeView === 'team' && <AgentActivity agents={agentEntries} statuses={statuses} onAgentChat={onAgentChat} expanded />}
            {activeView === 'projects' && <ProjectListPanel projects={projects} onRefresh={onProjects} onResume={onResume} onDeleteProject={onDeleteProject} onRemoveProjectFromList={onRemoveProjectFromList} />}
            {activeView === 'pipeline' && <section className="fs-panel fs-pipeline-detail-page"><div className="fs-panel-title"><div><span>Pipeline</span><strong>{project?.title || 'No active project'}</strong></div></div>{project ? <PipelineDetailContent project={project} agentStatuses={statuses} agents={agents} pipelineMetadata={pipelineMetadata} inline /> : <p className="fs-empty">No active project.</p>}</section>}
            {activeView === 'mobile' && <MobilePreviewPanel project={project} />}
            {activeView === 'issues' && <AttentionPanel issues={openIssues} project={project} onRetry={onRetry} onContinueDone={onContinueDone} expanded />}
            {activeView === 'logs' && <LogPanel logs={logs} />}
            {activeView === 'settings' && <section className="fs-panel fs-settings-page">{settingsContent}</section>}
            {activeView === 'info' && <section className="fs-panel fs-info-page">{infoContent}</section>}
            {activeView !== 'overview' && !['team', 'projects', 'pipeline', 'mobile', 'issues', 'logs', 'settings', 'info'].includes(activeView) && <WorkspaceHint activeView={activeView} project={project} />}
            <div className="fs-workspace-bottom-sentinel" data-testid="workspace-bottom-sentinel" aria-hidden="true" />
          </main>

          {chatOpen ? (
            <AIChatPanel
              id="fs-ai-chat-panel"
              chatTabs={chatTabs}
              selectedChatAgent={selectedChatAgent}
              chatTab={chatTab}
              setChatTab={setChatTab}
              onAgentChat={onAgentChat}
              project={project}
              onCollapse={() => setChatOpen(false)}
            />
          ) : (
            <button type="button" className="fs-chat-rail" onClick={() => setChatOpen(true)} title="Open AI Chat panel">AI Chat</button>
          )}
        </div>
      </section>
    </div>
  );
}

function ProjectOverview({ project, primaryAction, isGenerating, onStopGeneration, onNewProject }) {
  return (
    <section className="fs-hero">
      <div>
        <span className="fs-eyebrow">Workspace Overview</span>
        <h1>{project?.title || 'Start a project to activate the studio'}</h1>
        <p>{compactText(project?.description || project?.initial_description || project?.summary, project ? 'Project details are loading from the active workspace.' : 'Create or open a project to see pipeline state, agents, logs, and tools.')}</p>
        <div className="fs-meta-row">
          <span>Stage: <b>{pretty(project?._phase || project?.stage || project?.status, 'Idle')}</b></span>
          <span>State: <b>{pretty(project?.status, 'No active project')}</b></span>
          <span>Mode: <b>{pretty(project?.project_mode, 'MVP')}</b></span>
          {project?.project_id && <span>ID: <b>{project.project_id}</b></span>}
        </div>
      </div>
      <div className="fs-hero-actions">
        {isGenerating && <button type="button" className="fs-danger-button" onClick={onStopGeneration}>Stop Generation</button>}
      </div>
    </section>
  );
}

function ProjectListPanel({ projects = [], onRefresh, onResume, onDeleteProject, onRemoveProjectFromList }) {
  useEffect(() => { onRefresh?.(); }, []);
  return (
    <section className="fs-panel fs-project-list-page">
      <div className="fs-panel-title">
        <div><span>Projects</span><strong>{projects.length ? `${projects.length} projects` : 'No projects'}</strong></div>
        <button type="button" onClick={onRefresh}>Refresh</button>
      </div>
      <div className="fs-project-list">
        {projects.length ? projects.map(project => (
          <article className="fs-project-row" key={project.project_id}>
            <div>
              <strong>{project.title || 'Untitled'}</strong>
              <span>{pretty(project.status, 'unknown')} {project.target_path ? `- ${project.target_path}` : ''}</span>
            </div>
            <div className="fs-project-actions">
              {project._phase && ['created', 'failed', 'failed_qa', 'blocked', 'needs_credentials', 'cancelled'].includes(project.status) && <button type="button" onClick={() => onResume(project)}>Resume</button>}
              <button type="button" className="danger" onClick={() => onDeleteProject(project)}>Удалить с компьютера</button>
              <button type="button" onClick={() => onRemoveProjectFromList(project)}>Удалить из списка</button>
            </div>
          </article>
        )) : <p className="fs-empty">No projects are currently registered.</p>}
      </div>
    </section>
  );
}

function PipelineSummary({ pipeline, project, workingAgent, criteria, passedCriteria, onPipeline }) {
  return (
    <section className="fs-panel fs-pipeline-panel">
      <div className="fs-panel-title">
        <div><span>Pipeline Status</span><strong>{pretty(project?._phase || project?.status, 'Waiting')}</strong></div>
        <button type="button" onClick={onPipeline}>Details</button>
      </div>
      <div className="fs-pipeline">
        {pipeline.map((stage, index) => (
          <div className={`fs-stage ${stage.tone}`} key={`${stage.name}-${index}`}>
            <i aria-hidden="true" />
            <b>{stage.name}</b>
            <span>{pretty(stage.state, 'waiting')}</span>
          </div>
        ))}
      </div>
      <div className="fs-compact-facts">
        <span>Active agent: <b>{workingAgent?.name || workingAgent?.id || 'None reported'}</b></span>
        <span>QA criteria: <b>{criteria.length ? `${passedCriteria}/${criteria.length}` : 'Unavailable'}</b></span>
      </div>
    </section>
  );
}

function AgentActivity({ agents, statuses, onAgentChat, expanded = false }) {
  return (
    <section className="fs-panel fs-team-panel">
      <div className="fs-panel-title"><div><span>AI Team</span><strong>{agents.length || 0} configured agents</strong></div></div>
      <div className={`fs-agent-grid ${expanded ? 'expanded' : ''}`}>
        {agents.length ? agents.map(agent => {
          const state = statuses?.[agent.id]?.status || (agent.enabled === false ? 'unavailable' : 'idle');
          return (
            <button type="button" className={`fs-agent ${statusTone(state)}`} key={agent.id} onClick={() => onAgentChat(agent.id)} disabled={agent.enabled === false} title={`Open ${agent.name || agent.id} chat`}>
              <span className="fs-agent-mark" style={{ '--agent-color': agent.color || 'var(--fs-accent)' }}>{getAgentInitials(agent)}</span>
              <span><b>{agent.name || agent.id}</b><small>{agent.current_task || statuses?.[agent.id]?.task || agent.display_role || agent.role || pretty(state)}</small></span>
              <i aria-label={state} />
            </button>
          );
        }) : <p className="fs-empty">No configured agents were returned by the backend.</p>}
      </div>
    </section>
  );
}

function AttentionPanel({ issues, project, onRetry, onContinueDone, expanded = false }) {
  const manualSteps = Array.isArray(project?.manual_steps) ? project.manual_steps : [];
  const needsInput = ['needs_user_input', 'awaiting_input'].includes(project?.status);
  return (
    <section className="fs-panel fs-attention-panel">
      <div className="fs-panel-title"><div><span>Attention Required</span><strong>{issues.length || manualSteps.length || (needsInput ? 1 : 0) || 'Clear'}</strong></div></div>
      {needsInput && <div className="fs-alert warning"><b>User input required</b><span>{manualSteps[0] || 'The active project is waiting for guidance.'}</span><button type="button" onClick={onContinueDone}>Continue</button></div>}
      {issues.length ? issues.slice(0, expanded ? issues.length : 3).map((issue, index) => <div className="fs-alert" key={`${issue.title || issue.message || index}`}><b>{issue.title || issue.type || `Issue ${index + 1}`}</b><span>{issue.message || issue.detail || pretty(issue.status)}</span><button type="button" onClick={onRetry}>Resolve</button></div>) : !needsInput && <p className="fs-empty">No open issues are currently reported.</p>}
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

function DevelopmentTools({ project, onOpenEditor, onOpenCode, onFiles, onPush, onExport }) {
  const tools = [
    { id: 'vscode', name: 'VS Code', mark: '<>', action: () => onOpenEditor('vscode'), available: true },
    { id: 'pycharm', name: 'PyCharm', mark: 'PC', action: () => onOpenEditor('pycharm'), available: true },
    { id: 'cursor', name: 'Cursor', mark: 'CU', action: () => onOpenEditor('cursor'), available: true },
    { id: 'opencode', name: 'OpenCode', mark: 'OC', action: onOpenCode, available: true },
    { id: 'claude', name: 'Claude Code', mark: 'CC', action: undefined, available: false },
  ];
  return (
    <section className="fs-panel fs-dev-tools-panel">
      <div className="fs-panel-title"><div><span>Development Tools</span><strong>{project ? 'Project actions' : 'No active project'}</strong></div></div>
      <div className="fs-tool-grid">
        {tools.map(tool => {
          const projectRequired = tool.id !== 'opencode';
          const disabled = !tool.available || (projectRequired && !project);
          const title = !tool.available ? `${tool.name} is not configured in Studio.` : disabled ? 'No active project.' : `Open ${tool.name}`;
          return <button type="button" className={`fs-tool ${tool.id}`} key={tool.id} onClick={tool.action} disabled={disabled} title={title}><b>{tool.mark}</b><span>{tool.name}<small>{tool.available ? 'Open workspace' : 'Unavailable'}</small></span></button>;
        })}
        <button type="button" className="fs-tool folder" onClick={onFiles} disabled={!project} title="Browse project files"><b>FD</b><span>Files<small>Browse project</small></span></button>
        <button type="button" className="fs-tool git" onClick={onPush} disabled={!project} title="Push project to GitHub"><b>GH</b><span>GitHub<small>Push</small></span></button>
        <button type="button" className="fs-tool export" onClick={onExport} disabled={!project} title="Export project"><b>EX</b><span>Export<small>Markdown</small></span></button>
      </div>
    </section>
  );
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
    return ['localhost', '127.0.0.1', '0.0.0.0'].includes(url.hostname) || url.hostname.endsWith('.local');
  } catch {
    return false;
  }
}

function MobilePreviewPanel({ project }) {
  const [deviceId, setDeviceId] = useState('iphone16pro');
  const initialUrl = project?.preview_url || project?.local_preview_url || '';
  const [previewUrl, setPreviewUrl] = useState(initialUrl);
  const device = MOBILE_DEVICES.find(item => item.id === deviceId) || MOBILE_DEVICES[0];
  const scale = device.width > 500 ? 0.48 : device.width > 430 ? 0.62 : 0.74;
  const canEmbed = isEmbeddablePreviewUrl(previewUrl);
  const hasExternalUrl = previewUrl && !canEmbed;
  return (
    <section className="fs-panel fs-mobile-preview-page">
      <div className="fs-panel-title">
        <div><span>Mobile Preview</span><strong>{project?.title || 'No active project'}</strong></div>
      </div>
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
          {project?.url && !previewUrl && <button type="button" onClick={() => setPreviewUrl(project.url)}>Use project URL</button>}
          <p>Use a local running preview URL, for example Vite, Expo Web, React Native Web, Ionic, or Capacitor. External sites can block embedding inside the phone frame.</p>
        </div>
        <div className="fs-phone-stage">
          <div className="fs-phone-frame" style={{ width: device.width * scale, height: device.height * scale, borderRadius: device.radius * scale }}>
            <div className="fs-phone-speaker" />
            {canEmbed ? <iframe title={`${device.name} preview`} src={previewUrl} style={{ borderRadius: Math.max(18, device.radius * scale - 10) }} /> : <div className="fs-phone-empty"><b>{device.name}</b>{hasExternalUrl ? <><span>This URL is external and may block embedded preview. Use a local preview URL to render it here.</span><button type="button" onClick={() => window.open(previewUrl, '_blank', 'noopener,noreferrer')}>Open externally</button></> : <span>Enter a running local project URL to preview it in this virtual phone.</span>}</div>}
          </div>
        </div>
      </div>
    </section>
  );
}

function AIChatPanel({ id, chatTabs, selectedChatAgent, chatTab, setChatTab, onAgentChat, project, onCollapse }) {
  return (
    <aside className="fs-chat-panel" id={id} aria-label="AI Chat panel">
      <div className="fs-chat-header"><div><span>AI Chat</span><strong>{selectedChatAgent?.name || 'No provider selected'}</strong></div><button type="button" onClick={onCollapse} aria-label="Collapse AI Chat panel">Collapse</button></div>
      <div className="fs-chat-tabs" role="tablist" aria-label="Configured AI chat providers">
        {chatTabs.length ? chatTabs.map(agent => <button type="button" role="tab" aria-selected={chatTab === agent.id} className={chatTab === agent.id ? 'selected' : ''} key={agent.id} onClick={() => setChatTab(agent.id)}>{knownProviderLabels[agent.id] || agent.provider || agent.name || agent.id}</button>) : <span>No configured providers</span>}
      </div>
      <div className="fs-chat-body">
        {selectedChatAgent ? <><span className={`fs-status ${selectedChatAgent.enabled === false ? 'danger' : 'success'}`}>{selectedChatAgent.enabled === false ? 'Configuration required' : 'Configured'}</span><p>Current model: <b>{selectedChatAgent.model || selectedChatAgent.provider_model || 'Model not reported'}</b></p><p>{project ? 'Project context is available for the connected agent chat.' : 'Open or create a project to attach workspace context.'}</p><button type="button" className="fs-primary" onClick={() => onAgentChat(selectedChatAgent.id)} disabled={selectedChatAgent.enabled === false}>Open {selectedChatAgent.name || selectedChatAgent.id}</button></> : <p className="fs-empty">No configured AI providers were returned by the backend.</p>}
      </div>
      <div className="fs-chat-input"><span>{project ? 'Context attached' : 'No project context'}</span><input aria-label="AI chat message" placeholder="Open agent chat to send messages" disabled /><button type="button" onClick={() => selectedChatAgent && onAgentChat(selectedChatAgent.id)} disabled={!selectedChatAgent}>Send</button></div>
    </aside>
  );
}

function WorkspaceHint({ activeView, project }) {
  return <section className="fs-panel"><div className="fs-panel-title"><div><span>{pretty(activeView)}</span><strong>{project ? 'Opened via existing handler' : 'No active project'}</strong></div></div><p className="fs-empty">This section uses the existing modal or action flow. Use Overview to return to the workspace summary.</p></section>;
}
