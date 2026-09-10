import React, { useEffect, useMemo, useState } from 'react';
import { orderWorkflowApi } from './orderWorkflowApi.js';
import { STORAGE_KEY } from './OrderWorkflowPage.jsx';
import { tr } from '../../i18n.js';

const FILTERS = [
  { id: 'all', labelKey: 'filterAll', match: () => true },
  { id: 'running', labelKey: 'filterRunning', match: status => ['running', 'queued', 'in_progress', 'awaiting_user'].includes(status) },
  { id: 'done', labelKey: 'filterDone', match: status => ['succeeded', 'approved'].includes(status) },
  { id: 'paused', labelKey: 'filterPaused', match: status => ['paused', 'cancelled'].includes(status) },
];

const PRODUCT_TYPE_LABELS = { static_page: 'Website', web_app: 'Web App', bot: 'Bot' };

function statusLabel(status) {
  return String(status || '').replace(/_/g, ' ');
}

function statusTone(status, executionStatus) {
  const value = String(executionStatus || status || '').toLowerCase();
  if (['succeeded', 'approved'].includes(value)) return 'success';
  if (['failed', 'cancelled', 'qa_failed'].includes(value)) return 'danger';
  if (['running', 'queued', 'in_progress'].includes(value)) return 'active';
  return 'idle';
}

function formatDate(iso) {
  if (!iso) return '-';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export default function ProjectsListPage({ active, onOpenProject, language }) {
  const t = key => tr(language, key);
  const [orders, setOrders] = useState(null);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState('all');

  const load = () => {
    orderWorkflowApi.listOrders()
      .then(data => setOrders(data?.orders || []))
      .catch(err => setError(err.message || 'Could not load projects.'));
  };

  useEffect(() => {
    if (!active) return;
    load();
  }, [active]);

  const open = (orderId) => {
    localStorage.setItem(STORAGE_KEY, orderId);
    onOpenProject?.();
  };

  const activeFilter = FILTERS.find(item => item.id === filter) || FILTERS[0];
  const filtered = useMemo(() => {
    if (!orders) return [];
    return orders.filter(order => activeFilter.match(String(order.execution_status || order.status || '').toLowerCase()));
  }, [orders, activeFilter]);

  return (
    <section className="fs-panel fs-projects-panel">
      <div className="fs-panel-title">
        <div><span>Projects</span><strong>{orders ? `${orders.length} total` : 'Loading...'}</strong></div>
        <button type="button" onClick={load}>Refresh</button>
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
        {FILTERS.map(item => (
          <button
            key={item.id}
            type="button"
            className="fs-secondary"
            style={filter === item.id ? { borderColor: 'var(--fs-acc-line)', background: 'var(--fs-acc-soft)', color: 'var(--fs-accent)' } : undefined}
            onClick={() => setFilter(item.id)}
          >{t(item.labelKey)}</button>
        ))}
      </div>

      {error && <p className="fs-empty">{error}</p>}
      {orders && orders.length === 0 && <p className="fs-empty">No projects yet. Create one from Create Project.</p>}
      {orders && filtered.length === 0 && orders.length > 0 && <p className="fs-empty">No projects match this filter.</p>}
      {filtered.length > 0 && (
        <div className="fs-projects-grid">
          {filtered.map(order => {
            const status = String(order.execution_status || order.status || '').toLowerCase();
            const tone = statusTone(order.status, order.execution_status);
            return (
              <button type="button" className="fs-project-card" data-glass key={order.id} onClick={() => open(order.id)}>
                <div className="fs-project-card-preview"><span>{(order.product_type || 'project').replace(/_/g, ' ')}</span></div>
                <div className="fs-project-card-body">
                  <strong>{order.title}</strong>
                  <span className="fs-project-card-id">{order.id}</span>
                  <div className="fs-project-card-meta">
                    <span className={`fs-status-dot ${tone}`} aria-hidden="true" />
                    <span>{statusLabel(status)}</span>
                    {order.product_type && <span className="fs-tag">{PRODUCT_TYPE_LABELS[order.product_type] || order.product_type}</span>}
                  </div>
                  {order.execution_progress != null && (
                    <div className="fs-project-card-progress">
                      <div className={`fs-project-card-progress-fill ${tone}`} style={{ width: `${order.execution_progress}%` }} />
                    </div>
                  )}
                  <span className="fs-project-card-updated">Updated {formatDate(order.updated_at)}</span>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}
