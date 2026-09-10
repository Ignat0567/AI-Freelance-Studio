import React from 'react';

// Exported so the mid-build checkpoint renders its questions with exactly the same
// controls as the up-front ones -- they are the same ClarificationQuestion shape, and a
// second implementation would drift.
export function QuestionInput({ question, value, onChange }) {
  if (question.type === 'single_select') {
    return <div className="ow-options">{question.options.map(option => <label key={option}><input type="radio" name={question.id} checked={value === option} onChange={() => onChange(option)} />{option}</label>)}</div>;
  }
  if (question.type === 'multi_select') {
    const selected = Array.isArray(value) ? value : [];
    return <div className="ow-options">{question.options.map(option => <label key={option}><input type="checkbox" checked={selected.includes(option)} onChange={event => onChange(event.target.checked ? [...selected, option] : selected.filter(item => item !== option))} />{option}</label>)}</div>;
  }
  if (question.type === 'boolean') {
    return <div className="ow-options"><label><input type="radio" name={question.id} checked={value === true} onChange={() => onChange(true)} />Yes</label><label><input type="radio" name={question.id} checked={value === false} onChange={() => onChange(false)} />No</label></div>;
  }
  if (question.type === 'long_text') return <textarea rows={4} value={value || ''} onChange={event => onChange(event.target.value)} />;
  return <input value={value || ''} onChange={event => onChange(event.target.value)} />;
}

export default function ClarificationPanel({ state, answers, setAnswers, pending, onSubmit, onDefaults, onBack }) {
  const questions = state?.questions || [];
  return (
    <section className="fs-panel ow-card" data-glass aria-labelledby="ow-clarification-title">
      <div className="fs-panel-title"><div><span>Alex Clarification</span><strong id="ow-clarification-title">Answer the questions that affect implementation</strong></div></div>
      <p className="ow-note">Progress: {state?.answers?.length || 0} answered of {questions.length}. You can use recommended defaults to complete the MVP brief quickly.</p>
      <div className="ow-question-list">
        {questions.map(question => (
          <fieldset className="ow-question" key={question.id}>
            <legend>{question.text}</legend>
            <p>{question.reason}</p>
            {question.recommended_answer !== null && question.recommended_answer !== undefined && <small>Recommended: {Array.isArray(question.recommended_answer) ? question.recommended_answer.join(', ') : String(question.recommended_answer)}</small>}
            <QuestionInput question={question} value={answers[question.id]} onChange={value => setAnswers(current => ({ ...current, [question.id]: value }))} />
          </fieldset>
        ))}
      </div>
      {state?.assumptions?.length > 0 && <div className="ow-callout"><strong>Visible assumptions</strong><ul>{state.assumptions.map(item => <li key={item}>{item}</li>)}</ul></div>}
      <div className="ow-actions"><button type="button" className="fs-secondary" onClick={onBack}>Back to order</button><button type="button" className="fs-secondary" onClick={onDefaults} disabled={pending}>Use recommended defaults</button><button type="button" className="fs-primary" onClick={onSubmit} disabled={pending}>{pending ? 'Saving answers...' : 'Submit answers'}</button></div>
    </section>
  );
}
