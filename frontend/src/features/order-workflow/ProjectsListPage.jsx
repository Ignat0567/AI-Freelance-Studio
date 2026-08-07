import React, { useEffect, useState } from 'react';
import { orderWorkflowApi } from './orderWorkflowApi.js';
import { STORAGE_KEY } from './OrderWorkflowPage.jsx';

function statusLabel(status) {
  return String(status || '').replace(/_/g, ' ');
}

function statusTone(status, executionStatus) {
  const value = String(executionStatus || status || '').toLowerCase();
  if (['succeeded', 'approved'].includes(value)) return 'success';
  if (['failed', 'cancelled'].includes(value)) return 'danger';
  if (['running', 'queued'].includes(value)) return 'active';
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

export default function ProjectsListPage({ active, onOpenProject }) {
  const [orders, setOrders] = useState(null);
  const [error, setError] = useState('');

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

  return (
    <section className="fs-panel fs-projects-panel">
      <div className="fs-panel-title">
        <div><span>Projects</span><strong>{orders ? `${orders.length} total` : 'Loading...'}</strong></div>
        <button type="button" onClick={load}>Refresh</button>
      </div>
      {error && <p className="fs-empty">{error}</p>}
      {orders && orders.length === 0 && <p className="fs-empty">No projects yet. Create one from Create Project.</p>}
      {orders && orders.length > 0 && (
        <div className="fs-projects-list">
          {orders.map(order => (
            <button type="button" className="fs-project-row" key={order.id} onClick={() => open(order.id)}>
              <div className="fs-project-row-main">
                <strong>{order.title}</strong>
                <span className={`fs-status-dot ${statusTone(order.status, order.execution_status)}`} aria-hidden="true" />
              </div>
              <div className="fs-project-row-meta">
                <span>{statusLabel(order.execution_status || order.status)}</span>
                {order.execution_status === 'running' && order.execution_progress != null && <span>{order.execution_progress}%</span>}
                <span>Updated {formatDate(order.updated_at)}</span>
              </div>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
