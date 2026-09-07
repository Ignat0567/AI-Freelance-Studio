import React from 'react';
import { parseLogLine } from '../logFormat.js';
import { tr } from '../i18n.js';

function dotClass(state) {
  const value = String(state || '').toLowerCase();
  if (['working', 'running', 'in_progress', 'completed', 'passed', 'done'].includes(value)) return 'active';
  if (['failed', 'error', 'unavailable', 'not_verified', 'incomplete'].includes(value)) return 'warning';
  return '';
}

// README "Правая панель" (330px context rail): Live Log / Agents / Attention. Rendered by
// StudioDashboard.jsx on every screen except settings/info/sandbox (the workspaceWide views).
// All three blocks are backed by real app state -- no invented per-agent load percentages
// (there is no such field in /api/agents/status), no fabricated log entries.
export default function ContextRail({ logs, agentEntries, statuses, attentionItems, language }) {
  const t = key => tr(language, key);
  const liveLog = (logs || []).slice(0, 14).map(parseLogLine);
  const workingCount = (agentEntries || []).filter(agent => {
    const state = statuses?.[agent.id]?.status;
    return ['working', 'running', 'in_progress'].includes(String(state || '').toLowerCase());
  }).length;

  return (
    <aside className="fs-context-rail" aria-label="Context panel">
      <section>
        <div className="fs-rail-block-title">
          <span>{t('liveLog')}</span>
          <span className="fs-rail-stream"><i aria-hidden="true" />{t('streamLabel')}</span>
        </div>
        <div className="fs-rail-log-list">
          {liveLog.length
            ? liveLog.map((line, index) => (
              <div className={`fs-rail-log-line level-${line.level}`} key={`${line.raw}-${index}`}>
                <time>{line.time}</time>
                <span>{line.text}</span>
              </div>
            ))
            : <p className="fs-empty">{t('noEventsYet')}</p>}
        </div>
      </section>

      <section>
        <div className="fs-rail-block-title">
          <span>{t('agentsLabel')}</span>
          <span className="fs-rail-count">{workingCount} {t('workingLabel')}</span>
        </div>
        <div className="fs-rail-agent-list">
          {(agentEntries || []).length
            ? agentEntries.map(agent => {
              const state = statuses?.[agent.id]?.status || (agent.enabled === false ? 'unavailable' : 'idle');
              return (
                <div className="fs-rail-agent-row" key={agent.id}>
                  <i className={`fs-rail-agent-dot ${dotClass(state)}`} aria-hidden="true" />
                  <div>
                    <strong>{agent.name || agent.id}</strong>
                    <small>{statuses?.[agent.id]?.task || agent.display_role || agent.role || String(state).replace(/_/g, ' ')}</small>
                  </div>
                </div>
              );
            })
            : <p className="fs-empty">{t('noAgentsConfigured')}</p>}
        </div>
      </section>

      {Array.isArray(attentionItems) && attentionItems.length > 0 && (
        <section className="fs-rail-attention">
          {attentionItems.map((item, index) => (
            <div key={item.id || index}>
              <strong>{item.title}</strong>
              <p>{item.description}</p>
              {item.actionLabel && <button type="button" onClick={item.onAction}>{item.actionLabel}</button>}
            </div>
          ))}
        </section>
      )}
    </aside>
  );
}
