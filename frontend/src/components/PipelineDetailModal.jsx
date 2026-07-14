import React from 'react';
import { createPortal } from 'react-dom';

const FALLBACK_STAGE_ORDER = ['planning', 'designing', 'testing', 'coding', 'review', 'verifying', 'repairing', 'final_audit', 'product_judge'];

export function PipelineDetailContent({ project, agentStatuses, agents = {}, pipelineMetadata = {}, inline = false }) {
  if (!project) return null;

  const logs = project.logs || [];
  const status = project.status || 'unknown';
  const normalizedStatus = status.startsWith('review_iteration_') ? 'review' : status;
  const stageOrder = pipelineMetadata.stage_order?.length ? pipelineMetadata.stage_order : FALLBACK_STAGE_ORDER;
  const stageNames = Object.fromEntries(
    Object.entries(pipelineMetadata.stages || {}).map(([id, meta]) => [id, meta.label || id])
  );

  const activeStageIdx = stageOrder.indexOf(normalizedStatus);

  const agentLogs = {};
  for (const [key, info] of Object.entries(agents || {})) {
    const stageLogs = logs.filter(l => l.toLowerCase().startsWith(info.name.toLowerCase()));
    const agentStatus = agentStatuses?.[key] || {};
    agentLogs[key] = { ...info, role: info.display_role || info.role, logs: stageLogs, status: agentStatus.status || 'idle', task: agentStatus.task || '' };
  }

  return (
    <div className={inline ? "fs-pipeline-detail" : "p-6 space-y-5"}>
      <div className="fs-pipeline-steps">
        {stageOrder.map((s, i) => {
          const isDone = activeStageIdx > i;
          const isCurrent = activeStageIdx === i;
          return (
            <React.Fragment key={s}>
              {i > 0 && <span className="fs-pipeline-arrow">{'->'}</span>}
              <div className={`fs-pipeline-chip ${isDone ? 'done' : isCurrent ? 'current' : ''}`}>
                <i aria-hidden="true" />
                {stageNames[s] || s}
              </div>
            </React.Fragment>
          );
        })}
      </div>

      <div className="fs-pipeline-agent-grid">
        {Object.values(agentLogs).map(a => {
          const isActive = a.status === 'working';
          return (
            <article key={a.name} className={`fs-pipeline-agent-card ${isActive ? 'active' : ''}`} style={{ '--agent-color': a.color || 'var(--fs-accent)' }}>
              <div className="fs-pipeline-agent-head">
                <div>
                  <strong>{a.name}</strong>
                  <span>{a.role}</span>
                </div>
                <b>{isActive ? 'Working' : a.status === 'completed' ? 'Done' : a.status === 'idle' && activeStageIdx >= stageOrder.indexOf(a.stage) ? 'Done' : 'Waiting'}</b>
              </div>
              {a.task && <p>{a.task}</p>}
              {a.logs.length > 0 && <div className="fs-pipeline-agent-logs">{a.logs.map((l, i) => <div key={i}>{l}</div>)}</div>}
            </article>
          );
        })}
      </div>

      {logs.length > 0 && (
        <section className="fs-pipeline-full-log">
          <strong>Full Activity Log</strong>
          <div>{logs.map((l, i) => <p key={i}>{l}</p>)}</div>
        </section>
      )}
    </div>
  );
}

export default function PipelineDetailModal({ project, agentStatuses, agents = {}, pipelineMetadata = {}, onClose }) {
  if (!project) return null;

  const modal = (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center p-4" style={{ backgroundColor: 'rgba(0,0,0,0.76)', pointerEvents: 'auto' }} onMouseDown={onClose}>
      <div className="rounded-2xl shadow-2xl w-full max-w-3xl max-h-[90vh] overflow-y-auto" style={{ backgroundColor: 'var(--bg-secondary, #0f172a)', border: '1px solid var(--border, #334155)' }} onMouseDown={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-6 py-4" style={{ borderBottom: '1px solid var(--border, #334155)' }}>
          <h2 className="text-base font-bold" style={{ color: 'var(--text-primary, #f1f5f9)' }}>
            📋 Pipeline: {project.title || 'Untitled'}
          </h2>
          <button onClick={onClose} className="text-lg hover:opacity-70" style={{ color: 'var(--text-muted, #64748b)' }}>✕</button>
        </div>

        <PipelineDetailContent project={project} agentStatuses={agentStatuses} agents={agents} pipelineMetadata={pipelineMetadata} />

        <div className="flex justify-end px-6 py-4" style={{ borderTop: '1px solid var(--border, #334155)' }}>
          <button onClick={onClose}
            className="px-4 py-1.5 rounded-lg text-xs font-medium transition-colors"
            style={{ backgroundColor: 'var(--bg-card, #1e293b)', border: '1px solid var(--border, #334155)', color: 'var(--text-primary, #f1f5f9)' }}>
            Close
          </button>
        </div>
      </div>
    </div>
  );

  return createPortal(modal, document.body);
}
