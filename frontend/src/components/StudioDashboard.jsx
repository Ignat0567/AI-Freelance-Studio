import React, { useEffect, useMemo, useRef, useState } from 'react';
import OrderWorkflowPage, { STORAGE_KEY as ORDER_STORAGE_KEY } from '../features/order-workflow/OrderWorkflowPage.jsx';
import ProjectsListPage from '../features/order-workflow/ProjectsListPage.jsx';
import { orderWorkflowApi } from '../features/order-workflow/orderWorkflowApi.js';
import SandboxTestLabPage from '../features/sandbox-test-lab/SandboxTestLabPage.jsx';
import KnowledgeBasePage from '../features/knowledge-base/KnowledgeBasePage.jsx';
import MarketplacePage from '../features/marketplace/MarketplacePage.jsx';
import VideoGenerationPage from '../features/video-generation/VideoGenerationPage.jsx';
import PresentationGeneratorPage from '../features/presentation-generator/PresentationGeneratorPage.jsx';
import TeamChatPage from '../features/collaboration/TeamChatPage.jsx';
import LiquidGlassDefs from './LiquidGlassDefs.jsx';
import ContextRail from './ContextRail.jsx';
import { initLiquidGlass } from '../liquidGlass.js';
import { parseLogLine, LOG_LEVELS } from '../logFormat.js';
import { tr } from '../i18n.js';

const CORE_NAV_IDS = new Set(['overview', 'create-project', 'projects', 'logs', 'settings']);
const navItems = [
  { id: 'overview', label: 'Overview', icon: 'OV' },
  { id: 'create-project', label: 'Create Project', icon: 'CP' },
  { id: 'projects', label: 'Projects', icon: 'PR' },
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

// Real backend pipeline stages (order_workflow.models.ExecutionStage) grouped under README's
// conceptual pipeline names. There is no distinct "security" phase in the real pipeline (that
// belonged to the classic, now-deleted generator) so it is left out rather than fabricated.
const STAGE_GROUPS = [
  { id: 'requirements', labelKey: 'stageBrief', code: 'PM', match: ['requirements'] },
  { id: 'architecture', labelKey: 'stageArchitecture', code: 'ARCH', match: ['design', 'planning', 'backend_decision'] },
  { id: 'ui', labelKey: 'stageUi', code: 'UI', match: ['ui_shell'] },
  { id: 'code', labelKey: 'stageCode', code: 'DEV', match: ['core_feature', 'implementation', 'bot_build', 'static_page_build', 'revision', 'repair'] },
  { id: 'qa', labelKey: 'stageQa', code: 'QA', match: ['verification'] },
  { id: 'deploy', labelKey: 'stageDeploy', code: 'OPS', match: ['packaging', 'completed'] },
];

function stageGroupIndex(stage) {
  return STAGE_GROUPS.findIndex(group => group.match.includes(stage));
}

function pickActiveOrder(orders) {
  if (!Array.isArray(orders) || !orders.length) return null;
  return orders.find(order => ['running', 'in_progress', 'awaiting_user'].includes(String(order.execution_status || '').toLowerCase())) || orders[0];
}

function pretty(value, fallback = 'Unavailable') {
  if (value === null || value === undefined || value === '') return fallback;
  return String(value).replace(/_/g, ' ');
}

function statusTone(status) {
  status = String(status || '').trim().toLowerCase().replace(/\s+/g, '_');
  if (['completed', 'passed', 'done', 'succeeded'].includes(status)) return 'success';
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
  language,
  settingsContent,
  infoContent,
  onKeyManager,
}) {
  const [activeView, setActiveView] = useState('overview');
  const [sandboxOpened, setSandboxOpened] = useState(false);
  const [recentOrders, setRecentOrders] = useState(null);
  const [showExperimental, setShowExperimental] = useState(false);
  const [backendPing, setBackendPing] = useState(null);
  const agentEntries = getAgentEntries(agents);
  const t = key => tr(language, key);

  const eventRows = (logs || []).slice(0, 7);

  // Liquid Glass: one shared rAF loop + one delegated mousemove listener for the whole app,
  // started once and torn down on unmount. Never re-initialized on nav switches -- .fs-shell
  // (this component's root) never unmounts while the app is open.
  useEffect(() => {
    const cleanup = initLiquidGlass();
    return cleanup;
  }, []);

  useEffect(() => {
    fetch(`http://localhost:${activePort}/api/config/system`)
      .then(r => r.json())
      .then(data => setShowExperimental(Boolean(data.show_experimental)))
      .catch(() => setShowExperimental(false));
  }, [activePort]);

  useEffect(() => {
    if (activeView !== 'overview') return;
    orderWorkflowApi.listOrders().then(data => setRecentOrders(data?.orders || [])).catch(() => setRecentOrders([]));
  }, [activeView]);

  // Real backend status + ping for the sidebar footer (README: "backend :8080", "42ms").
  useEffect(() => {
    let cancelled = false;
    const check = () => {
      const startedAt = performance.now();
      fetch(`http://localhost:${activePort}/health`)
        .then(r => { if (r.ok && !cancelled) setBackendPing(Math.round(performance.now() - startedAt)); })
        .catch(() => { if (!cancelled) setBackendPing(null); });
    };
    check();
    const interval = setInterval(check, 8000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [activePort]);

  const visibleNavItems = navItems.filter(item => showExperimental || CORE_NAV_IDS.has(item.id));
  const activeNavItem = navItems.find(item => item.id === activeView);

  useEffect(() => {
    if (showExperimental || CORE_NAV_IDS.has(activeView) || activeView === 'info') return;
    setActiveView('overview');
  }, [showExperimental, activeView]);

  const openProject = (orderId) => {
    localStorage.setItem(ORDER_STORAGE_KEY, orderId);
    setActiveView('create-project');
  };

  const selectNav = (id) => {
    setActiveView(id);
    if (id === 'sandbox') setSandboxOpened(true);
  };

  const openInfo = () => setActiveView('info');

  const workspaceWide = ['settings', 'info', 'sandbox'].includes(activeView);
  const pipelineActive = Array.isArray(recentOrders) && recentOrders.some(order => ['running', 'in_progress'].includes(String(order.execution_status || '').toLowerCase()));

  const attentionItems = useMemo(() => {
    if (!Array.isArray(recentOrders)) return [];
    return recentOrders
      .filter(order => ['failed', 'qa_failed'].includes(String(order.execution_status || '').toLowerCase()))
      .slice(0, 2)
      .map(order => ({
        id: order.id,
        title: `${order.title || order.id} — ${t('needsAttention')}`,
        description: `${t('executionStatusLabel')}: ${pretty(order.execution_status)}.`,
        actionLabel: t('openOrder'),
        onAction: () => openProject(order.id),
      }));
  }, [recentOrders, language]);

  return (
    <div className={`fs-shell ${workspaceWide ? 'workspace-wide' : ''}`}>
      <LiquidGlassDefs />
      <div className="fs-liquid-bg" aria-hidden="true">
        <div className="fs-liquid-blob fs-liquid-blob-1" />
        <div className="fs-liquid-blob fs-liquid-blob-2" />
        <div className="fs-liquid-blob fs-liquid-blob-3" />
        <div className="fs-liquid-blob fs-liquid-blob-4" />
      </div>

      <aside className="fs-sidebar" aria-label="FreelancerStudio navigation">
        <div className="fs-brand">
          <div className="fs-brand-mark" aria-hidden="true">FS</div>
          <div>
            <strong>AI Freelance Studio</strong>
            <span>Autonomous Pipeline</span>
          </div>
        </div>
        <nav className="fs-nav">
          {visibleNavItems.map(item => (
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
        <div className="fs-sidebar-card">
          <span className="fs-device-summary">
            <i className="fs-status-dot success" aria-hidden="true" />
            <b style={{ fontFamily: 'var(--fs-font-mono)', fontSize: 11 }}>backend :{activePort}</b>
            {backendPing !== null && <span style={{ fontFamily: 'var(--fs-font-mono)', fontSize: 11 }}>{backendPing}ms</span>}
          </span>
          <div style={{ display: 'flex', gap: 6, marginTop: 10 }}>
            {/* Theme switching lives only in Settings -> Appearance, per explicit user
                instruction -- no duplicate toggle here. */}
            <button type="button" className="fs-secondary" style={{ flex: 1 }} onClick={onKeyManager} title="Manage API keys">Keys</button>
          </div>
        </div>
      </aside>

      <section className="fs-app">
        <header className="fs-header">
          <div className="fs-project-context">
            <span className="fs-eyebrow" style={{ fontFamily: 'var(--fs-font-mono)' }}>studio / {activeNavItem?.label || activeView}</span>
            <strong>{activeNavItem?.label || 'AI Freelance Studio'}</strong>
          </div>
          <div className="fs-header-actions">
            {pipelineActive && (
              <span className="fs-pill" style={{ borderColor: 'var(--fs-acc-line)', background: 'var(--fs-acc-soft)', color: 'var(--fs-accent)', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <i aria-hidden="true" style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--fs-accent)', animation: 'fs-pulse 1.4s infinite', display: 'inline-block' }} />
                {t('pipelineActive')}
              </span>
            )}
            <button type="button" className="fs-pill" onClick={() => setActiveView('settings')} title="Open settings">Settings</button>
            <button type="button" className="fs-pill accent" onClick={() => setActiveView('create-project')} title="Start a new order">{t('newOrder')}</button>
          </div>
        </header>

        <div className="fs-body">
          <main className={`fs-workspace ${activeView === 'overview' ? 'fs-overview-workspace' : ''} ${activeView === 'settings' ? 'fs-settings-workspace' : ''} ${activeView === 'info' ? 'fs-info-workspace' : ''}`} tabIndex={0} aria-label="Central workspace content">
            {activeView === 'overview' && (
              <>
                <OverviewHero onNewProject={() => setActiveView('create-project')} orders={recentOrders} onOpenProject={openProject} onViewAllProjects={() => setActiveView('projects')} language={language} />
                {pickActiveOrder(recentOrders) && <PipelineStages orderId={pickActiveOrder(recentOrders).id} language={language} />}
                {pickActiveOrder(recentOrders) && <ArtifactsGrid orderId={pickActiveOrder(recentOrders).id} language={language} />}
                <AgentActivity agents={agentEntries} statuses={statuses} />
                <ActivityPanel logs={eventRows} onOpenLogs={() => setActiveView('logs')} />
              </>
            )}
            {activeView === 'create-project' && <OrderWorkflowPage active={activeView === 'create-project'} />}
            {activeView === 'projects' && <ProjectsListPage active={activeView === 'projects'} onOpenProject={() => setActiveView('create-project')} language={language} />}
            {activeView === 'knowledge-base' && <KnowledgeBasePage active={activeView === 'knowledge-base'} />}
            {activeView === 'marketplace' && <MarketplacePage active={activeView === 'marketplace'} />}
            {activeView === 'video-generation' && <VideoGenerationPage active={activeView === 'video-generation'} />}
            {activeView === 'presentation-generator' && <PresentationGeneratorPage active={activeView === 'presentation-generator'} />}
            {activeView === 'team-chat' && <TeamChatPage active={activeView === 'team-chat'} />}
            {activeView === 'team' && <AgentActivity agents={agentEntries} statuses={statuses} expanded />}
            {activeView === 'mobile' && <MobilePreviewPanel activePort={activePort} />}
            {activeView === 'logs' && <LogPanel logs={logs} language={language} />}
            {sandboxOpened && <section className="fs-test-lab-host" hidden={activeView !== 'sandbox'}><SandboxTestLabPage active={activeView === 'sandbox'} /></section>}
            {activeView === 'settings' && <section className="fs-panel fs-settings-page">{settingsContent}</section>}
            {activeView === 'info' && <section className="fs-panel fs-info-page">{infoContent}</section>}
            <div className="fs-workspace-bottom-sentinel" data-testid="workspace-bottom-sentinel" aria-hidden="true" />
          </main>
          {!workspaceWide && (
            <ContextRail logs={logs} agentEntries={agentEntries} statuses={statuses} attentionItems={attentionItems} language={language} />
          )}
        </div>
      </section>
    </div>
  );
}

function OverviewHero({ onNewProject, orders, onOpenProject, onViewAllProjects, language }) {
  const cardRef = useRef(null);
  const t = key => tr(language, key);
  const hasOrders = Array.isArray(orders) && orders.length > 0;
  const recent = hasOrders ? orders.slice(0, 5) : [];
  const activeOrder = pickActiveOrder(orders);
  const [detail, setDetail] = useState(null);

  useEffect(() => {
    if (!activeOrder) { setDetail(null); return; }
    let cancelled = false;
    orderWorkflowApi.getExecution(activeOrder.id).then(data => { if (!cancelled) setDetail(data); }).catch(() => { if (!cancelled) setDetail(null); });
    return () => { cancelled = true; };
  }, [activeOrder?.id]);

  // Hero card gets its own dedicated tilt/glare handling (README: excluded from the shared
  // delegated [data-glass] handler, larger amplitude/glare, opacity fade instead of an
  // instant SVG-filter swap). Ref-based, never touches React state.
  const handleMouseMove = (event) => {
    const el = cardRef.current;
    if (!el || (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches)) return;
    const rect = el.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const w = rect.width;
    const h = rect.height;
    const amp = w > 700 ? 7 : 9;
    const rotX = -((y - h / 2) / h) * amp;
    const rotY = ((x - w / 2) / w) * amp;
    el.style.transform = `perspective(1400px) rotateX(${rotX.toFixed(2)}deg) rotateY(${rotY.toFixed(2)}deg)`;
    el.style.setProperty('--fs-hero-glare-x', `${x}px`);
    el.style.setProperty('--fs-hero-glare-y', `${y}px`);
    el.style.setProperty('--fs-hero-glare-opacity', '1');
  };
  const handleMouseLeave = () => {
    const el = cardRef.current;
    if (!el) return;
    el.style.transform = '';
    el.style.setProperty('--fs-hero-glare-opacity', '0');
  };

  const execution = detail?.execution;
  const groupIndex = execution ? stageGroupIndex(execution.stage) : -1;
  const usage = detail?.execution?.result?.usage;

  if (!hasOrders) {
    return (
      <section className="fs-hero" ref={cardRef} onMouseMove={handleMouseMove} onMouseLeave={handleMouseLeave}>
        <div className="fs-hero-top">
          <div>
            <span className="fs-eyebrow">Workspace Overview</span>
            <h1>Start a project to activate the studio</h1>
            <p>Create an AI order to generate a new project. Frozen tools stay hidden until you enable experimental features in Settings.</p>
          </div>
          <div className="fs-hero-actions">
            <button type="button" className="fs-primary" onClick={onNewProject}>Create Project</button>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="fs-hero" ref={cardRef} onMouseMove={handleMouseMove} onMouseLeave={handleMouseLeave}>
      <div className="fs-hero-top">
        <div>
          <span className="fs-eyebrow" style={{ fontFamily: 'var(--fs-font-mono)' }}>{t('activeOrderEyebrow')} · {activeOrder.id}</span>
          <h1>{activeOrder.title}</h1>
          <p>{execution ? pretty(execution.current_activity, t('queuedLabel')) : `${orders.length} project${orders.length === 1 ? '' : 's'} in this workspace`}</p>
        </div>
        <div className="fs-meta-row" style={{ marginTop: 0 }}>
          <span><b style={{ fontVariantNumeric: 'tabular-nums' }}>{groupIndex >= 0 ? `${groupIndex + 1} / ${STAGE_GROUP_COUNT}` : '—'}</b> {t('stageMetric')}</span>
          <span><b style={{ fontVariantNumeric: 'tabular-nums' }}>{usage ? formatTokens(usage.input_tokens + usage.output_tokens) : '—'}</b> {t('tokensMetric')}</span>
          <span style={{ color: 'var(--fs-warning)' }}><b style={{ fontVariantNumeric: 'tabular-nums', color: 'var(--fs-warning)' }}>{usage ? `$${usage.total_cost_usd.toFixed(2)}` : '—'}</b> {t('costMetric')}</span>
        </div>
      </div>
      {typeof execution?.progress === 'number' && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ flex: 1, height: 5, borderRadius: 20, background: 'var(--fs-line)', overflow: 'hidden' }}>
            <div style={{ width: `${execution.progress}%`, height: '100%', borderRadius: 20, background: 'linear-gradient(90deg, var(--fs-accent), var(--fs-warning))', boxShadow: '0 0 14px -2px var(--fs-accent)' }} />
          </div>
          <span style={{ color: 'var(--fs-accent)', fontWeight: 600, fontSize: 12.5, fontVariantNumeric: 'tabular-nums' }}>{execution.progress}%</span>
        </div>
      )}
      <div className="fs-hero-recent">
        {recent.map(order => (
          <button type="button" className="fs-hero-recent-row" key={order.id} onClick={() => onOpenProject(order.id)}>
            <strong>{order.title}</strong>
            <span>{String(order.execution_status || order.status || '').replace(/_/g, ' ')}</span>
          </button>
        ))}
        {orders.length > recent.length && <button type="button" className="fs-hero-recent-more" onClick={onViewAllProjects}>View all {orders.length} projects</button>}
      </div>
    </section>
  );
}

const STAGE_GROUP_COUNT = STAGE_GROUPS.length;
function formatTokens(total) {
  if (!total) return '0';
  if (total >= 1_000_000) return `${(total / 1_000_000).toFixed(2)}M`;
  if (total >= 1_000) return `${(total / 1_000).toFixed(1)}K`;
  return String(total);
}

function PipelineStages({ orderId, language }) {
  const t = key => tr(language, key);
  const [events, setEvents] = useState([]);
  const [executionStage, setExecutionStage] = useState(null);
  const [openIndex, setOpenIndex] = useState(-1);

  useEffect(() => {
    if (!orderId) { setEvents([]); setExecutionStage(null); return; }
    let cancelled = false;
    Promise.all([orderWorkflowApi.getEvents(orderId), orderWorkflowApi.getExecution(orderId)])
      .then(([eventsData, execData]) => {
        if (cancelled) return;
        setEvents(eventsData?.events || []);
        setExecutionStage(execData?.execution?.stage || null);
      })
      .catch(() => { if (!cancelled) { setEvents([]); setExecutionStage(null); } });
    return () => { cancelled = true; };
  }, [orderId]);

  const currentGroupIndex = executionStage ? stageGroupIndex(executionStage) : -1;
  useEffect(() => { if (currentGroupIndex >= 0) setOpenIndex(currentGroupIndex); }, [currentGroupIndex]);

  return (
    <section className="fs-panel fs-pipeline-panel">
      <div className="fs-panel-title">
        <div>
          <span>{t('pipelineLabel')}</span>
          <strong style={{ fontFamily: 'var(--fs-font-mono)', fontSize: 11, fontWeight: 400, textTransform: 'none', letterSpacing: 0 }}>
            {STAGE_GROUPS.map(group => group.id).join(' → ')}
          </strong>
        </div>
      </div>
      <div className="fs-pipeline">
        {STAGE_GROUPS.map((group, index) => {
          const groupEvents = events.filter(event => group.match.includes(event.stage));
          const isDone = currentGroupIndex > index;
          const isActive = currentGroupIndex === index;
          const isOpen = openIndex === index;
          const lastEvent = groupEvents[groupEvents.length - 1];
          return (
            <div
              key={group.id}
              className={`fs-stage ${isDone ? 'success' : isActive ? 'active' : ''}`}
              data-glass
              style={{ cursor: 'pointer' }}
              onClick={() => setOpenIndex(isOpen ? -1 : index)}
            >
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <i aria-hidden="true" />
                <span style={{ fontFamily: 'var(--fs-font-mono)', fontSize: 11, color: 'var(--fs-dim)' }}>{isOpen ? '▾' : '▸'}</span>
              </div>
              <b>{t(group.labelKey)}</b>
              <span>{isDone ? t('stageDone') : isActive ? t('stageActive') : t('stagePending')}</span>
              {lastEvent?.agent && (
                <div style={{ borderTop: '1px solid var(--fs-border)', marginTop: 8, paddingTop: 6, fontFamily: 'var(--fs-font-mono)', fontSize: 11, color: 'var(--fs-dim)' }}>
                  {lastEvent.agent}
                </div>
              )}
              {isOpen && groupEvents.length > 0 && (
                <div className="fs-stage-events">
                  {groupEvents.slice(-4).map(event => (
                    <div
                      key={event.id}
                      style={{
                        fontFamily: 'var(--fs-font-mono)',
                        fontSize: 11,
                        color: event.level === 'error' ? 'var(--fs-danger)' : event.level === 'warning' ? 'var(--fs-warning)' : 'var(--fs-dim)',
                        overflowWrap: 'anywhere',
                      }}
                    >{event.message}</div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function ArtifactsGrid({ orderId, language }) {
  const t = key => tr(language, key);
  const [artifacts, setArtifacts] = useState([]);
  useEffect(() => {
    if (!orderId) { setArtifacts([]); return; }
    let cancelled = false;
    orderWorkflowApi.getArtifacts(orderId).then(data => { if (!cancelled) setArtifacts(data?.artifacts || []); }).catch(() => { if (!cancelled) setArtifacts([]); });
    return () => { cancelled = true; };
  }, [orderId]);

  return (
    <section className="fs-panel fs-artifacts-panel">
      <div className="fs-panel-title"><div><span>{t('artifactsLabel')}</span><strong>{artifacts.length}</strong></div></div>
      <div className="fs-artifacts-grid">
        {artifacts.length ? artifacts.map(item => (
          <div className="fs-artifact-card" data-glass key={item.id}>
            <div className="fs-artifact-preview"><span>{item.kind.replace(/_/g, ' ')}</span></div>
            <b>{item.name}</b>
            <span>{item.summary}</span>
          </div>
        )) : <p className="fs-empty">{t('noArtifacts')}</p>}
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
            <div className={`fs-agent ${statusTone(state)}`} data-glass key={agent.id} title={agent.name || agent.id}>
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

function LogPanel({ logs, language }) {
  const t = key => tr(language, key);
  const [levelFilter, setLevelFilter] = useState('all');
  const [query, setQuery] = useState('');
  const parsed = (logs || []).map(parseLogLine);
  const filtered = parsed.filter(line => {
    if (levelFilter !== 'all' && line.level !== levelFilter) return false;
    if (query && !line.text.toLowerCase().includes(query.toLowerCase())) return false;
    return true;
  });
  return (
    <section className="fs-panel fs-log-panel">
      <div className="fs-panel-title">
        <div><span>Logs</span><strong>{logs?.length || 0} events</strong></div>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
        {['all', ...LOG_LEVELS].map(level => (
          <button
            key={level}
            type="button"
            className="fs-secondary"
            style={levelFilter === level ? { borderColor: 'var(--fs-acc-line)', background: 'var(--fs-acc-soft)', color: 'var(--fs-accent)' } : undefined}
            onClick={() => setLevelFilter(level)}
          >{level}</button>
        ))}
        <input
          value={query}
          onChange={event => setQuery(event.target.value)}
          placeholder={t('filterByText')}
          style={{ marginLeft: 'auto', width: 200, border: '1px solid var(--fs-border)', borderRadius: 999, padding: '8px 12px', background: 'var(--fs-input-bg)', color: 'var(--fs-text)', font: '11px var(--fs-font-mono)' }}
        />
      </div>
      <div className="fs-full-log">
        {filtered.length ? filtered.map((line, index) => (
          <div key={`${line.raw}-${index}`} className={`fs-rail-log-line level-${line.level}`}>
            <time>{line.time}</time>
            <span>{line.text}</span>
          </div>
        )) : <p className="fs-empty">No log entries are available.</p>}
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
