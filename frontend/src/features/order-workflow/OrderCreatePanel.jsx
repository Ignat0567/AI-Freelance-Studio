import React from 'react';
import { PDF_VOICE_ASSISTANT_EXAMPLE } from './orderWorkflowState.js';

export default function OrderCreatePanel({ form, setForm, pending, onSubmit, onAutoSubmit }) {
  const update = (field, value) => setForm(current => ({ ...current, [field]: value }));
  const fillExample = () => setForm(PDF_VOICE_ASSISTANT_EXAMPLE);
  return (
    <section className="fs-panel ow-card" aria-labelledby="ow-new-order-title">
      <div className="fs-panel-title"><div><span>Create Project</span><strong id="ow-new-order-title">Describe what you want the application to do</strong></div></div>
      <p className="ow-note">MVP profile: <strong>Small browser-based web applications</strong>. Production execution is not enabled in this workflow yet.</p>
      <form className="ow-form" onSubmit={onSubmit}>
        <label>Project title<input value={form.title} onChange={event => update('title', event.target.value)} required /></label>
        <label>Describe the application<textarea value={form.description} onChange={event => update('description', event.target.value)} required rows={7} /></label>
        <div className="ow-grid two">
          <label>Supported product type<select value={form.product_type} onChange={event => update('product_type', event.target.value)}><option value="web_app">Small web application</option></select></label>
          <label>Preferred language<select value={form.preferred_language} onChange={event => update('preferred_language', event.target.value)}><option value="en">English</option><option value="ru">Russian</option><option value="de">German</option></select></label>
        </div>
        <label>Optional constraints<textarea value={form.constraints} onChange={event => update('constraints', event.target.value)} rows={3} placeholder="One constraint per line" /></label>
        <div className="ow-actions">
          <button type="button" className="fs-secondary" onClick={fillExample}>Use PDF Voice Assistant example</button>
          <button type="button" className="fs-secondary" onClick={onAutoSubmit} disabled={pending}>{pending ? 'Running...' : 'Create & run automatically'}</button>
          <button type="submit" className="fs-primary" disabled={pending}>{pending ? 'Creating order...' : 'Create order'}</button>
        </div>
      </form>
    </section>
  );
}
