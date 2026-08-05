import React, { useEffect, useMemo, useState } from 'react';
import { PipelineDetailContent } from './PipelineDetailModal.jsx';
import OrderWorkflowPage from '../features/order-workflow/OrderWorkflowPage.jsx';
import SandboxTestLabPage from '../features/sandbox-test-lab/SandboxTestLabPage.jsx';
import KnowledgeBasePage from '../features/knowledge-base/KnowledgeBasePage.jsx';
import MarketplacePage from '../features/marketplace/MarketplacePage.jsx';
import VideoGenerationPage from '../features/video-generation/VideoGenerationPage.jsx';

const navItems = [
  { id: 'overview', label: 'Overview', icon: 'OV' },
  { id: 'create-project', label: 'Create Project', icon: 'CP' },
  { id: 'projects', label: 'Projects', icon: 'PR' },
  { id: 'features', label: 'Features', icon: 'FC' },
  { id: 'pipeline', label: 'Pipeline', icon: 'PL' },
  { id: 'mobile', label: 'Mobile Preview', icon: 'MB' },
  { id: 'team', label: 'AI Team', icon: 'AI' },
  { id: 'issues', label: 'Issues', icon: 'IS' },
  { id: 'logs', label: 'Logs', icon: 'LG' },
  { id: 'sandbox', label: 'Sandbox Test Lab', icon: 'TL' },
  { id: 'knowledge-base', label: 'Knowledge Base', icon: 'KB' },
  { id: 'marketplace', label: 'Marketplace', icon: 'MP' },
  { id: 'video-generation', label: 'AI Video', icon: 'VD' },
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

function normalizeStatus(value) {
  return String(value || '').trim().toLowerCase().replace(/\s+/g, '_');
}

function compactText(value, fallback = 'No description available.') {
  const text = String(value || '').trim();
  return text || fallback;
}

function statusTone(status) {
  status = normalizeStatus(status);
  if (['completed', 'passed', 'done'].includes(status)) return 'success';
  if (['working', 'running', 'in_progress', 'planning', 'building', 'qa'].includes(status)) return 'active';
  if (['failed', 'failed_qa', 'blocked', 'needs_credentials', 'error'].includes(status)) return 'danger';
  if (['awaiting_input', 'needs_user_input', 'created', 'cancelled', 'not_verified', 'incomplete'].includes(status)) return 'warning';
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
  const status = normalizeStatus(project.status);
  if (status === 'spec_clarification') return { label: 'Open briefing', onClick: handlers.onOpenBriefing };
  if (status === 'needs_user_input') return { label: 'Continue', onClick: handlers.onContinueDone };
  if (status === 'blocked') return { label: 'Retry Generation', onClick: handlers.onRetry };
  if (status === 'failed_final_audit') return { label: 'Повторить / исправить', onClick: handlers.onRetry };
  if (['failed', 'failed_qa', 'needs_credentials'].includes(status)) return { label: 'Повторить / исправить', onClick: handlers.onRetry };
  if (project._phase && ['created', 'cancelled'].includes(status)) return { label: 'Continue', onClick: handlers.onResume };
  if (status === 'completed') return { label: 'Open project', onClick: handlers.onFiles };
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
  onRetryProject,
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
  const [sandboxOpened, setSandboxOpened] = useState(false);
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
    if (id === 'sandbox') setSandboxOpened(true);
    if (id === 'projects') onProjects();
    if (id === 'pipeline') onPipeline();
  };

  const openSettings = () => setActiveView('settings');
  const openInfo = () => setActiveView('info');

  const workspaceWide = ['settings', 'info', 'sandbox'].includes(activeView);

    return (
    <div className={`fs-shell ${chatOpen ? '' : 'chat-collapsed'} ${workspaceWide ? 'workspace-wide' : ''}`}>
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
              {(project?.quality_profile || project?.project_mode) && <><i aria-hidden="true" /><span>Quality: {pretty(project.quality_profile || project.project_mode)}</span></>}
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
          <main className={`fs-workspace ${activeView === 'overview' ? 'fs-overview-workspace' : ''} ${activeView === 'settings' ? 'fs-settings-workspace' : ''} ${activeView === 'info' ? 'fs-info-workspace' : ''}`} tabIndex={0} aria-label="Central workspace content">
            {!['settings', 'info', 'sandbox', 'create-project', 'knowledge-base', 'marketplace', 'video-generation'].includes(activeView) && <ProjectOverview project={project} primaryAction={primaryAction} isGenerating={isGenerating} onStopGeneration={onStopGeneration} onNewProject={onNewProject} />}
            {activeView === 'overview' && (
              <>
                <PipelineSummary pipeline={pipeline} project={project} workingAgent={workingAgent} criteria={criteria} passedCriteria={passedCriteria} onPipeline={() => setActiveView('pipeline')} />
                <AgentActivity agents={agentEntries} statuses={statuses} onAgentChat={onAgentChat} />
                <AttentionPanel issues={openIssues} project={project} onRetry={onRetry} onContinueDone={onContinueDone} />
                <ActivityPanel logs={eventRows} onOpenLogs={() => setActiveView('logs')} />
                <DevelopmentTools project={project} onOpenEditor={onOpenEditor} onOpenCode={onOpenCode} onFiles={onFiles} onPush={onPush} onExport={onExport} />
              </>
            )}
            {activeView === 'create-project' && <OrderWorkflowPage active={activeView === 'create-project'} />}
            {activeView === 'knowledge-base' && <KnowledgeBasePage active={activeView === 'knowledge-base'} />}
            {activeView === 'marketplace' && <MarketplacePage active={activeView === 'marketplace'} />}
            {activeView === 'video-generation' && <VideoGenerationPage active={activeView === 'video-generation'} />}
            {activeView === 'team' && <AgentActivity agents={agentEntries} statuses={statuses} onAgentChat={onAgentChat} expanded />}
            {activeView === 'projects' && <ProjectListPanel projects={projects} onRefresh={onProjects} onResume={onResume} onRetry={onRetryProject || onRetry} onDeleteProject={onDeleteProject} onRemoveProjectFromList={onRemoveProjectFromList} />}
            {activeView === 'features' && <FeatureCompletenessPanel project={project} />}
            {activeView === 'pipeline' && <section className="fs-panel fs-pipeline-detail-page"><div className="fs-panel-title"><div><span>Pipeline</span><strong>{project?.title || 'No active project'}</strong></div></div>{project ? <PipelineDetailContent project={project} agentStatuses={statuses} agents={agents} pipelineMetadata={pipelineMetadata} inline /> : <p className="fs-empty">No active project.</p>}</section>}
            {activeView === 'mobile' && <MobilePreviewPanel activePort={activePort} project={project} />}
            {activeView === 'issues' && <AttentionPanel issues={openIssues} project={project} onRetry={onRetry} onContinueDone={onContinueDone} expanded />}
            {activeView === 'logs' && <LogPanel logs={logs} />}
            {sandboxOpened && <section className="fs-test-lab-host" hidden={activeView !== 'sandbox'}><SandboxTestLabPage active={activeView === 'sandbox'} /></section>}
            {activeView === 'settings' && <section className="fs-panel fs-settings-page">{settingsContent}</section>}
            {activeView === 'info' && <section className="fs-panel fs-info-page">{infoContent}</section>}
            {activeView !== 'overview' && !['create-project', 'team', 'projects', 'features', 'pipeline', 'mobile', 'issues', 'logs', 'sandbox', 'knowledge-base', 'marketplace', 'video-generation', 'settings', 'info'].includes(activeView) && <WorkspaceHint activeView={activeView} project={project} />}
            <div className="fs-workspace-bottom-sentinel" data-testid="workspace-bottom-sentinel" aria-hidden="true" />
          </main>

          {chatOpen && !workspaceWide ? (
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
          ) : !workspaceWide ? (
            <button type="button" className="fs-chat-rail" onClick={() => setChatOpen(true)} title="Open AI Chat panel">AI Chat</button>
          ) : null}
        </div>
      </section>
    </div>
  );
}

function FeatureCompletenessPanel({ project }) {
  const [openId, setOpenId] = useState('');
  const matrix = project?.feature_matrix || project?.final_delivery_report?.feature_matrix || {};
  const features = Array.isArray(matrix.features) ? matrix.features : [];
  const summary = matrix.summary || {};
  const report = project?.final_delivery_report || {};
  const maturity = report.final_maturity_status || {};
  const policy = report.completion_policy || {};
  const targetStatuses = report.target_verification_status || project?.target_verification_status || {};
  const limitations = Array.isArray(report.known_limitations) ? report.known_limitations : [];
  const architecture = report.architecture_report || project?.architecture_review || {};
  const security = report.security_findings || {};
  const evidence = report.achieved_evidence_levels || {};
  const qualityMatrices = matrix.quality_matrices || project?.final_delivery_report?.feature_matrix_report?.quality_matrices || {};
  const qualitySummary = matrix.quality_matrix_summary || project?.final_delivery_report?.feature_matrix_report?.quality_matrix_summary || {};
  const label = value => pretty(value, 'not required');
  const targetRows = Object.entries(targetStatuses).map(([target, record]) => ({ target, ...(record || {}) }));
  const blockerReasons = [
    ...(Array.isArray(policy.blockers) ? policy.blockers : []),
    ...features.flatMap(feature => Array.isArray(feature.blocking_reasons) ? feature.blocking_reasons.map(reason => `${feature.feature_name || feature.feature_id}: ${reason}`) : []),
  ];
  const renderMatrixSection = (title, rows = []) => (
    <section className="fs-panel fs-feature-matrix-page">
      <div className="fs-panel-title"><div><span>{title}</span><strong>{rows.length ? `${rows.length} checks` : 'No checks required'}</strong></div></div>
      <div className="fs-project-list">
        {rows.length ? rows.slice(0, 20).map(row => (
          <article className="fs-project-row" key={row.id || `${title}-${row.kind}`}>
            <div>
              <strong>{row.feature_name || row.kind || 'Quality check'}</strong>
              <span>{row.required ? 'Required' : 'Optional'} - {label(row.status)} - {row.expected_coverage || row.kind}</span>
              {row.achieved_coverage && <span>Achieved: {String(row.achieved_coverage)}</span>}
            </div>
          </article>
        )) : <p className="fs-empty">No {title.toLowerCase()} gaps for this project.</p>}
      </div>
    </section>
  );
  if (!project) return <section className="fs-panel"><p className="fs-empty">No active project.</p></section>;
  return (
    <>
      <section className="fs-panel fs-quality-summary-page">
        <div className="fs-panel-title"><div><span>Maturity Status</span><strong>{pretty(maturity.status || report.final_status || project.status, 'No audit yet')}</strong></div></div>
        <div className="fs-compact-facts">
          <span>Current milestone: <b>{pretty(report.milestone_status || maturity.status || project.status, 'Unavailable').toUpperCase()}</b></span>
          <span>Target acceptance: <b>{pretty(project?.quality_profile || report.quality_profile, 'Strict MVP').toUpperCase()} - {policy.accepted ? 'ACCEPTED' : 'BLOCKED'}</b></span>
          <span>Final label: <b>{maturity.acceptance_label || (policy.accepted ? 'ACCEPTED' : 'MVP ACCEPTANCE INCOMPLETE')}</b></span>
        </div>
        <div className="fs-project-list">
          {blockerReasons.length ? blockerReasons.slice(0, 10).map(reason => <article className="fs-project-row" key={reason}><div><strong>Reason</strong><span>{String(reason).replace(/_/g, ' ')}</span></div></article>) : <p className="fs-empty">No blocking acceptance reasons reported.</p>}
        </div>
      </section>

      <section className="fs-panel fs-feature-matrix-page">
        <div className="fs-panel-title"><div><span>Feature Completeness</span><strong>{features.length ? `${features.length} features` : 'Matrix unavailable'}</strong></div></div>
        <div className="fs-compact-facts">
          <span>Mandatory: <b>{summary.total_mandatory ?? 0}</b></span>
          <span>Complete: <b>{summary.fully_complete ?? 0}</b></span>
          <span>Incomplete: <b>{summary.incomplete ?? 0}</b></span>
          <span>Failed: <b>{summary.failed ?? 0}</b></span>
          <span>Matrix gaps: <b>{qualitySummary.blocking_gaps?.length ?? 0}</b></span>
        </div>
        <div className="fs-quality-table" role="table" aria-label="Feature matrix">
          <div className="fs-quality-row header" role="row"><span>Feature</span><span>Mandatory</span><span>Backend</span><span>Customer UI</span><span>Management UI</span><span>Tests</span><span>E2E</span><span>Platform</span><span>Status</span><span>Details</span></div>
          {features.length ? features.map(feature => {
            const dims = feature.dimensions || {};
            const open = openId === feature.feature_id;
            return (
              <React.Fragment key={feature.feature_id}>
                <div className="fs-quality-row" role="row">
                  <span>{feature.feature_name || 'Unnamed feature'}</span><span>{feature.mandatory ? 'Yes' : 'No'}</span><span>{label(dims.backend)}</span><span>{label(dims.customer_ui)}</span><span>{label(dims.manager_ui)}</span><span>{label(dims.automated_tests)}</span><span>{label(dims.e2e)}</span><span>{label(dims.native_runtime || dims.packaged_artifact)}</span><span>{label(feature.evidence_status)}</span><span><button type="button" onClick={() => setOpenId(open ? '' : feature.feature_id)}>{open ? 'Hide' : 'Drill down'}</button></span>
                </div>
                {open && <div className="fs-quality-drilldown">
                  <div>Requirements: {feature.description || 'No description'}</div>
                  <div>Exact blocking reasons: {feature.blocking_reasons?.length ? feature.blocking_reasons.join(', ') : 'none'}</div>
                  <div>Evidence: {(feature.evidence || []).length ? JSON.stringify(feature.evidence) : 'No durable evidence records linked yet.'}</div>
                  <div>Claims: {(feature.claims || []).length ? JSON.stringify(feature.claims) : 'No implementation claims linked.'}</div>
                </div>}
              </React.Fragment>
            );
          }) : <p className="fs-empty">Feature matrix has not been generated for this project yet.</p>}
        </div>
      </section>

      <section className="fs-panel fs-target-status-page">
        <div className="fs-panel-title"><div><span>Target Verification</span><strong>{targetRows.length ? `${targetRows.length} targets` : 'No target evidence'}</strong></div></div>
        <div className="fs-project-list">
          {targetRows.length ? targetRows.map(row => <article className="fs-project-row" key={row.target}><div><strong>{pretty(row.target_type || row.target)} - {row.verdict === 'passed' ? 'Passed' : 'Not verified'}</strong><span>Required: {row.required_evidence_level || 'none'} | Achieved: {row.achieved_evidence_level || 'none'} | {row.reason || 'No reason reported'}</span></div></article>) : <p className="fs-empty">No target verification records are available.</p>}
        </div>
      </section>

      <section className="fs-panel fs-evidence-page">
        <div className="fs-panel-title"><div><span>Evidence</span><strong>Achieved evidence levels</strong></div></div>
        <div className="fs-pipeline-agent-logs"><div>{JSON.stringify(evidence, null, 2)}</div></div>
      </section>

      {renderMatrixSection('RBAC Matrix', qualityMatrices.rbac || [])}
      {renderMatrixSection('Test Matrix', qualityMatrices.critical_scenarios || [])}
      {renderMatrixSection('Security', qualityMatrices.security || [])}
      {renderMatrixSection('Persistence', qualityMatrices.persistence || [])}
      <section className="fs-panel fs-architecture-page"><div className="fs-panel-title"><div><span>Architecture</span><strong>{pretty(architecture.status, 'not reviewed')}</strong></div></div><div className="fs-pipeline-agent-logs"><div>{JSON.stringify(architecture.blocking_findings || architecture.findings || [], null, 2)}</div></div></section>
      <section className="fs-panel fs-security-page"><div className="fs-panel-title"><div><span>Security</span><strong>{security.secret_scan?.status || 'not reviewed'}</strong></div></div><div className="fs-pipeline-agent-logs"><div>{JSON.stringify(security, null, 2)}</div></div></section>
      <section className="fs-panel fs-limitations-page"><div className="fs-panel-title"><div><span>Limitations</span><strong>{limitations.length ? `${limitations.length} known` : 'None reported'}</strong></div></div><div className="fs-project-list">{limitations.length ? limitations.map((item, index) => <article className="fs-project-row" key={index}><div><strong>{item.source || 'limitation'}</strong><span>{item.limitation || JSON.stringify(item)}</span></div></article>) : <p className="fs-empty">No known limitations reported.</p>}</div></section>
    </>
  );
}

function ProjectOverview({ project, primaryAction, isGenerating, onStopGeneration, onNewProject }) {
  const badges = project?.final_delivery_report?.maturity_badges || project?.final_delivery_report?.final_maturity_status?.badges || {};
  const badgeOrder = ['build', 'runtime', 'core_e2e', 'platform_verification', 'strict_mvp', 'production_readiness'];
  return (
    <section className="fs-hero">
      <div>
        <span className="fs-eyebrow">Workspace Overview</span>
        <h1>{project?.title || 'Start a project to activate the studio'}</h1>
        <p>{compactText(project?.description || project?.initial_description || project?.summary, project ? 'Project details are loading from the active workspace.' : 'Create or open a project to see pipeline state, agents, logs, and tools.')}</p>
        <div className="fs-meta-row">
          <span>Stage: <b>{pretty(project?._phase || project?.stage || project?.status, 'Idle')}</b></span>
          <span>State: <b>{pretty(project?.status, 'No active project')}</b></span>
          <span>Quality: <b>{pretty(project?.quality_profile || project?.project_mode, 'Strict MVP')}</b></span>
          {project?.project_id && <span>ID: <b>{project.project_id}</b></span>}
        </div>
        <div className="fs-meta-row" aria-label="Evidence maturity badges">
          {badgeOrder.map(key => {
            const badge = badges[key] || {};
            return <span key={key} className={`fs-status ${statusTone(badge.status)}`}>{badge.label || pretty(key)}: <b>{pretty(badge.status, 'not verified')}</b></span>;
          })}
        </div>
      </div>
      <div className="fs-hero-actions">
        {isGenerating && <button type="button" className="fs-danger-button" onClick={onStopGeneration}>Stop Generation</button>}
      </div>
    </section>
  );
}

function ProjectListPanel({ projects = [], onRefresh, onResume, onRetry, onDeleteProject, onRemoveProjectFromList }) {
  useEffect(() => { onRefresh?.(); }, []);
  return (
    <section className="fs-panel fs-project-list-page">
      <div className="fs-panel-title">
        <div><span>Projects</span><strong>{projects.length ? `${projects.length} projects` : 'No projects'}</strong></div>
        <button type="button" onClick={onRefresh}>Refresh</button>
      </div>
      <div className="fs-project-list">
        {projects.length ? projects.map(project => {
          const status = normalizeStatus(project.status);
          const retryable = ['failed_final_audit', 'failed_qa', 'failed', 'blocked', 'needs_credentials', 'needs_user_input'].includes(status);
          return (
          <article className="fs-project-row" key={project.project_id}>
            <div>
              <strong>{project.title || 'Untitled'}</strong>
              <span>{pretty(project.status, 'unknown')} {project.target_path ? `- ${project.target_path}` : ''}</span>
            </div>
            <div className="fs-project-actions">
              {retryable && <button type="button" className="primary" onClick={() => onRetry(project)}>Повторить / исправить</button>}
              {project._phase && ['created', 'cancelled'].includes(status) && <button type="button" onClick={() => onResume(project)}>Resume</button>}
              <button type="button" className="danger" onClick={() => onDeleteProject(project)}>Удалить с компьютера</button>
              <button type="button" onClick={() => onRemoveProjectFromList(project)}>Удалить из списка</button>
            </div>
          </article>
        ); }) : <p className="fs-empty">No projects are currently registered.</p>}
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
      {issues.length ? issues.slice(0, expanded ? issues.length : 3).map((issue, index) => <div className="fs-alert" key={`${issue.title || issue.message || index}`}><b>{issue.title || issue.type || `Issue ${index + 1}`}</b><span>{issue.message || issue.detail || pretty(issue.status)}</span><button type="button" onClick={onRetry}>{normalizeStatus(project?.status) === 'blocked' ? 'Retry Generation' : normalizeStatus(project?.status) === 'failed_final_audit' ? 'Повторить / исправить' : 'Resolve'}</button></div>) : !needsInput && <p className="fs-empty">No open issues are currently reported.</p>}
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
    { id: 'claude', name: 'Claude Code', mark: 'CC', action: () => onOpenEditor('claude'), available: true },
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
        return ['http:', 'https:'].includes(url.protocol)
          && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
          && !url.username
          && !url.password;
  } catch {
    return false;
  }
}

function MobilePreviewPanel({ activePort, project }) {
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
      <AndroidDevicesSection activePort={activePort} project={project} />
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

  useEffect(() => { loadStatus(); }, [activePort]);

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
