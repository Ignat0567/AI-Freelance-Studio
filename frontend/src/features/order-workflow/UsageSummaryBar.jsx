import React from 'react';

function formatCost(value) {
  if (!value) return '$0.00';
  return `$${value.toFixed(2)}`;
}

function formatTokens(value) {
  if (!value) return '0';
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(value);
}

export default function UsageSummaryBar({ usage }) {
  if (!usage) return null;
  const totalTokens = (usage.total_input_tokens || 0) + (usage.total_output_tokens || 0);
  return (
    <div className="ow-usage-bar" role="status" aria-label="Coding-agent usage this Studio instance has spent">
      <div className="ow-usage-bar-stats">
        <span><b>{formatCost(usage.total_cost_usd)}</b> spent</span>
        <span><b>{formatTokens(totalTokens)}</b> tokens ({usage.executions_with_usage} execution{usage.executions_with_usage === 1 ? '' : 's'})</span>
      </div>
      {usage.last_rate_limit && !usage.last_rate_limit.stale && (
        <div className="ow-usage-bar-limit">
          <strong>Rate limited:</strong> {usage.last_rate_limit.message}
        </div>
      )}
    </div>
  );
}
