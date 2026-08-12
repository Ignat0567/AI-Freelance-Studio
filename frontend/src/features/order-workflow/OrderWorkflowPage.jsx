import React, { useEffect, useRef, useState } from 'react';
import OrderCreatePanel from './OrderCreatePanel.jsx';
import ClarificationPanel from './ClarificationPanel.jsx';
import ProjectBriefPanel from './ProjectBriefPanel.jsx';
import ExecutionDashboard from './ExecutionDashboard.jsx';
import ExecutionResultPanel from './ExecutionResultPanel.jsx';
import UsageSummaryBar from './UsageSummaryBar.jsx';
import { orderWorkflowApi } from './orderWorkflowApi.js';
import { cleanError, isTerminalExecution, nextStepFromState } from './orderWorkflowState.js';
import './OrderWorkflow.css';

export const STORAGE_KEY = 'studio_order_workflow_last_order_id_v1';

const emptyForm = {
  title: '',
  description: '',
  product_type: 'web_app',
  preferred_language: 'en',
  constraints: '',
};

function asOrderPayload(form) {
  return {
    title: form.title.trim(),
    description: form.description.trim(),
    product_type: 'web_app',
    preferred_language: form.preferred_language || 'en',
    constraints: form.constraints.split('\n').map(item => item.trim()).filter(Boolean),
  };
}

function topStatus(state) {
  if (!state?.order) return 'Ready for a new order';
  return `${state.order.title} - ${state.next_action?.message || state.order.status}`;
}

export default function OrderWorkflowPage({ active }) {
  const [form, setForm] = useState(emptyForm);
  const [state, setState] = useState(null);
  const [step, setStep] = useState('new-order');
  const [answers, setAnswers] = useState({});
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [readiness, setReadiness] = useState(null);
  const [liveConfirm, setLiveConfirm] = useState(false);
  const [recovering, setRecovering] = useState(false);
  const [polling, setPolling] = useState(false);
  const [proposal, setProposal] = useState(null);
  const [proposalPending, setProposalPending] = useState(false);
  const [proposalError, setProposalError] = useState('');
  const [usageSummary, setUsageSummary] = useState(null);
  const mounted = useRef(true);
  const startInFlight = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const applyState = next => {
    if (!mounted.current) return;
    setState(next);
    setStep(nextStepFromState(next));
    if (next?.order?.id) localStorage.setItem(STORAGE_KEY, next.order.id);
  };

  const loadOrder = async orderId => {
    const next = await orderWorkflowApi.getOrder(orderId);
    applyState(next);
    return next;
  };

  const loadReadiness = async orderId => {
    const next = await orderWorkflowApi.getReadiness(orderId, 'production');
    if (mounted.current) setReadiness(next);
    return next;
  };

  const loadUsageSummary = async () => {
    const next = await orderWorkflowApi.getUsageSummary();
    if (mounted.current) setUsageSummary(next);
    return next;
  };

  useEffect(() => {
    if (!active) return;
    const orderId = localStorage.getItem(STORAGE_KEY);
    if (!orderId || state?.order?.id) return;
    setRecovering(true);
    loadOrder(orderId)
      .catch(() => {
        setError('The backend restarted and this in-memory order is no longer available. Create a new order to continue.');
        localStorage.removeItem(STORAGE_KEY);
      })
      .finally(() => mounted.current && setRecovering(false));
  }, [active]);

  useEffect(() => {
    if (!active || !state?.order?.id || !state.execution || isTerminalExecution(state.execution)) return;
    let cancelled = false;
    setPolling(true);
    const timer = setInterval(async () => {
      try {
        const next = await orderWorkflowApi.getOrder(state.order.id);
        if (!cancelled) applyState(next);
      } catch (err) {
        if (!cancelled) setError(cleanError(err, 'Execution status could not be refreshed.'));
      }
    }, 700);
    return () => {
      cancelled = true;
      clearInterval(timer);
      setPolling(false);
    };
  }, [active, state?.order?.id, state?.execution?.id, state?.execution?.status]);

  useEffect(() => {
    if (!active || !state?.order?.id || step !== 'execution') return;
    loadReadiness(state.order.id).catch(err => setError(cleanError(err, 'Execution readiness could not be refreshed.')));
  }, [active, state?.order?.id, state?.approval?.approved, state?.handoff_ready, state?.design_preview?.approved, step]);

  useEffect(() => {
    if (!active) return;
    loadUsageSummary().catch(() => {});
  }, [active, state?.execution?.status]);

  const run = async action => {
    setPending(true);
    setError('');
    try {
      const next = await action();
      if (next) applyState(next);
    } catch (err) {
      setError(cleanError(err));
    } finally {
      if (mounted.current) setPending(false);
    }
  };

  const submitOrder = event => {
    event.preventDefault();
    run(() => orderWorkflowApi.createOrder(asOrderPayload(form)));
  };

  const submitOrderAutomatically = event => {
    event.preventDefault();
    run(async () => {
      const created = await orderWorkflowApi.createOrder(asOrderPayload(form));
      return orderWorkflowApi.runAutopilot(created.order.id);
    });
  };

  const generateProposal = async () => {
    // Deliberately not routed through run()/applyState() -- the proposal
    // response ({proposal_markdown, estimate}) is not an order-snapshot shape,
    // and feeding it to applyState would corrupt the current step state.
    setProposalPending(true);
    setProposalError('');
    try {
      const next = await orderWorkflowApi.generateProposal(state.order.id);
      if (mounted.current) setProposal(next);
    } catch (err) {
      if (mounted.current) setProposalError(cleanError(err));
    } finally {
      if (mounted.current) setProposalPending(false);
    }
  };

  const submitAnswers = () => {
    const payload = Object.entries(answers).map(([question_id, value]) => ({ question_id, value }));
    if (!payload.length) { setError('Choose at least one answer or use recommended defaults.'); return; }
    run(() => orderWorkflowApi.answerQuestions(state.order.id, payload));
  };

  const approveBrief = brief => run(() => orderWorkflowApi.approveBrief(state.order.id, brief.revision, brief.approval_fingerprint));
  const reviseBrief = value => run(() => orderWorkflowApi.reviseBrief(state.order.id, [{ kind: 'add_requirement', value }]));
  const generateDesignPreview = () => run(() => orderWorkflowApi.generateDesignPreview(state.order.id));
  const approveDesignPreview = preview => run(() => orderWorkflowApi.approveDesignPreview(state.order.id, preview.preview_id, preview.brief_version));
  const reviseDesignPreview = note => run(() => orderWorkflowApi.reviseDesignPreview(state.order.id, note));
  const canStartExecution = Boolean(state?.approval?.approved && state?.handoff_ready && !state?.execution && (!state?.design_preview_required || state?.design_preview?.approved));
  const startExecution = (mode = 'fake') => {
    if (!canStartExecution) { setError('Approve the current brief and Elena design preview before starting simulated execution.'); return; }
    if (startInFlight.current) return;
    startInFlight.current = true;
    run(() => orderWorkflowApi.startExecution(state.order.id, mode, false)).finally(() => { startInFlight.current = false; });
    setTimeout(() => { startInFlight.current = false; }, 1500);
  };
  const startDryRun = () => {
    if (!readiness?.can_prepare_dry_run) { setError('Resolve production dry-run blockers before preparing a package.'); return; }
    startExecution('production');
  };
  const startLive = () => {
    if (!readiness?.can_run_live) { setError('Live execution is locked. Set FREELANCERSTUDIO_ENABLE_LIVE_OPENCODE_EXECUTION=1 and restart Studio to enable it.'); return; }
    if (!liveConfirm) { setError('Confirm live OpenCode execution before starting.'); return; }
    run(() => orderWorkflowApi.startExecution(state.order.id, 'production', true));
  };
  const retryExecution = () => run(() => orderWorkflowApi.retryExecution(state.order.id));
  const reviseExecution = revisionNote => run(() => orderWorkflowApi.reviseExecution(state.order.id, revisionNote));
  const reset = () => {
    localStorage.removeItem(STORAGE_KEY);
    setState(null);
    setStep('new-order');
    setAnswers({});
    setForm(emptyForm);
    setError('');
  };

  return (
    <section className="ow-page" aria-labelledby="ow-page-title">
      <div className="ow-hero">
        <div><span className="fs-eyebrow">MVP Core Workflow</span><h2 id="ow-page-title">Create Project</h2><p>{topStatus(state)}</p></div>
        <div className="ow-mode"><strong>Simulation mode</strong><span>Fake executor only</span>{polling && <small>Polling execution...</small>}</div>
      </div>
      <UsageSummaryBar usage={usageSummary} />
      {recovering && <div className="ow-callout" role="status">Reloading the last order from the local backend...</div>}
      {error && <div className="ow-callout warning" role="alert">{error}</div>}
      <nav className="ow-steps" aria-label="Order workflow steps">
        {['new-order', 'clarification', 'brief', 'execution', 'result'].map(item => <button type="button" key={item} className={step === item ? 'active' : ''} onClick={() => setStep(item)}>{item.replace('-', ' ')}</button>)}
      </nav>
      {step === 'new-order' && <OrderCreatePanel form={form} setForm={setForm} pending={pending} onSubmit={submitOrder} onAutoSubmit={submitOrderAutomatically} />}
      {step === 'clarification' && <ClarificationPanel state={state} answers={answers} setAnswers={setAnswers} pending={pending} onSubmit={submitAnswers} onDefaults={() => run(() => orderWorkflowApi.applyDefaults(state.order.id))} onBack={() => setStep('new-order')} />}
      {step === 'brief' && <ProjectBriefPanel state={state} pending={pending} onGenerate={() => run(() => orderWorkflowApi.generateBrief(state.order.id))} onApprove={approveBrief} onRevise={reviseBrief} onGeneratePreview={generateDesignPreview} onApprovePreview={approveDesignPreview} onRevisePreview={reviseDesignPreview} onBack={() => setStep('clarification')} proposal={proposal} proposalPending={proposalPending} proposalError={proposalError} onGenerateProposal={generateProposal} />}
      {step === 'execution' && <ExecutionDashboard state={state} readiness={readiness} pending={pending} canStart={canStartExecution} canDryRun={Boolean(readiness?.can_prepare_dry_run && canStartExecution)} canLive={Boolean(readiness?.can_run_live && canStartExecution)} liveConfirm={liveConfirm} setLiveConfirm={setLiveConfirm} onStart={() => startExecution('fake')} onDryRun={startDryRun} onLive={startLive} onCancel={() => run(() => orderWorkflowApi.cancelExecution(state.order.id))} onRefresh={() => loadOrder(state.order.id).catch(err => setError(cleanError(err)))} onRefreshReadiness={() => loadReadiness(state.order.id).catch(err => setError(cleanError(err)))} />}
      {step === 'result' && <ExecutionResultPanel state={state} pending={pending} onRetry={retryExecution} onRevise={reviseExecution} onNewOrder={reset} onBackToBrief={() => setStep('brief')} />}
      {!state?.order && step !== 'new-order' && <div className="ow-callout warning">No current order is loaded. Use the new order screen to begin.</div>}
    </section>
  );
}
