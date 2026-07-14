import React from 'react';
import { createPortal } from 'react-dom';

const FALLBACK_STAGE_ORDER = ['planning', 'designing', 'testing', 'coding', 'review', 'verifying', 'repairing', 'final_audit', 'product_judge'];

export default function PipelineDetailModal({ project, agentStatuses, agents = {}, pipelineMetadata = {}, onClose }) {
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

  const modal = (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center p-4" style={{ backgroundColor: 'rgba(0,0,0,0.76)', pointerEvents: 'auto' }} onMouseDown={onClose}>
      <div className="rounded-2xl shadow-2xl w-full max-w-3xl max-h-[90vh] overflow-y-auto" style={{ backgroundColor: 'var(--bg-secondary, #0f172a)', border: '1px solid var(--border, #334155)' }} onMouseDown={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-6 py-4" style={{ borderBottom: '1px solid var(--border, #334155)' }}>
          <h2 className="text-base font-bold" style={{ color: 'var(--text-primary, #f1f5f9)' }}>
            📋 Pipeline: {project.title || 'Untitled'}
          </h2>
          <button onClick={onClose} className="text-lg hover:opacity-70" style={{ color: 'var(--text-muted, #64748b)' }}>✕</button>
        </div>

        <div className="p-6 space-y-5">
          {/* Pipeline stage progress */}
          <div className="flex items-center gap-2 flex-wrap">
            {stageOrder.map((s, i) => {
              const isDone = activeStageIdx > i;
              const isCurrent = activeStageIdx === i;
              const isPending = activeStageIdx < i;
              return (
                <React.Fragment key={s}>
                  {i > 0 && <span className="text-xs" style={{ color: 'var(--text-muted, #64748b)' }}>→</span>}
                  <div className="flex items-center gap-1.5 px-2.5 py-1 rounded text-[10px] font-mono"
                    style={{
                      backgroundColor: isDone ? 'color-mix(in srgb, #10b981 15%, transparent)' : isCurrent ? 'color-mix(in srgb, var(--accent, #0ea5e9) 15%, transparent)' : 'color-mix(in srgb, #64748b 10%, transparent)',
                      border: `1px solid ${isDone ? '#10b981' : isCurrent ? 'var(--accent, #0ea5e9)' : 'color-mix(in srgb, #64748b 30%, transparent)'}`,
                      color: isDone ? '#10b981' : isCurrent ? 'var(--accent, #0ea5e9)' : 'var(--text-muted, #64748b)'
                    }}>
                    <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: isDone ? '#10b981' : isCurrent ? 'var(--accent, #0ea5e9)' : 'var(--text-muted, #64748b)' }}></span>
                    {stageNames[s] || s}
                  </div>
                </React.Fragment>
              );
            })}
          </div>

          {/* Agent cards */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {Object.values(agentLogs).map(a => {
              const isActive = a.status === 'working';
              return (
                <div key={a.name} className="rounded-xl p-3 space-y-2" style={{ backgroundColor: 'color-mix(in srgb, var(--bg-card, #1e293b) 80%, transparent)', border: `1px solid ${isActive ? a.color : 'var(--border, #334155)'}` }}>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-lg">{a.emoji}</span>
                      <div>
                        <div className="text-xs font-semibold" style={{ color: 'var(--text-primary, #f1f5f9)' }}>{a.name}</div>
                        <div className="text-[10px]" style={{ color: 'var(--text-muted, #64748b)' }}>{a.role}</div>
                      </div>
                    </div>
                    <div className="flex items-center gap-1">
                      <span className={`w-2 h-2 rounded-full ${isActive ? 'animate-pulse' : ''}`} style={{ backgroundColor: isActive ? a.color : 'var(--text-muted, #64748b)' }}></span>
                      <span className="text-[10px] font-mono" style={{ color: isActive ? a.color : 'var(--text-muted, #64748b)' }}>
                        {isActive ? 'Working' : a.status === 'completed' ? 'Done' : a.status === 'idle' && activeStageIdx >= stageOrder.indexOf(a.stage) ? 'Done' : 'Waiting'}
                      </span>
                    </div>
                  </div>
                  {a.task && <div className="text-[10px] italic" style={{ color: 'var(--text-secondary, #94a3b8)' }}>{a.task}</div>}
                  {a.logs.length > 0 && (
                    <div className="max-h-24 overflow-y-auto space-y-0.5 mt-1 pt-1" style={{ borderTop: '1px solid var(--border, #334155)' }}>
                      {a.logs.map((l, i) => (
                        <div key={i} className="text-[9px] font-mono leading-tight" style={{ color: 'var(--text-muted, #64748b)' }}>{l}</div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Full log */}
          {logs.length > 0 && (
            <div>
              <div className="text-xs font-semibold mb-2" style={{ color: 'var(--text-secondary, #94a3b8)' }}>Full Activity Log</div>
              <div className="max-h-40 overflow-y-auto rounded-lg p-3 space-y-1" style={{ backgroundColor: 'var(--bg-primary, #020617)', border: '1px solid var(--border, #334155)' }}>
                {logs.map((l, i) => (
                  <div key={i} className="text-[10px] font-mono leading-tight" style={{ color: 'var(--text-muted, #64748b)' }}>{l}</div>
                ))}
              </div>
            </div>
          )}
        </div>

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
