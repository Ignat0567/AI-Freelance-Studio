import React, { useState } from 'react';
import { QuestionInput } from './ClarificationPanel.jsx';

const KEEP_AS_ASSUMED = 'Keep it as assumed';

// The build is parked on this answer, so the panel has to read as a stop, not as an
// optional extra: it names what the studio decided on the client's behalf and offers one
// place to correct the screens before anything is built on top of them.
export default function MidBuildQuestionsPanel({ questions = [], pending, onSubmit }) {
  const [answers, setAnswers] = useState({});

  if (!questions.length) return null;

  // The API rejects blank values and requires at least one answer, so "nothing to change"
  // has to be sent as an explicit confirmation rather than an empty payload.
  const asPayload = source =>
    questions
      .map(question => ({ question_id: question.id, value: source[question.id] }))
      .filter(item => {
        if (typeof item.value === 'boolean') return true;
        return typeof item.value === 'string' ? item.value.trim().length > 0 : Boolean(item.value);
      });

  const submitAnswers = () => {
    const payload = asPayload(answers);
    onSubmit(payload.length ? payload : keepEverythingPayload());
  };

  const keepEverythingPayload = () =>
    questions
      .filter(question => question.recommended_answer)
      .map(question => ({ question_id: question.id, value: question.recommended_answer }));

  return (
    <section className="fs-panel ow-card" aria-labelledby="ow-midbuild-title">
      <div className="fs-panel-title">
        <div>
          <span>Paused for your answer</span>
          <strong id="ow-midbuild-title">Confirm what was assumed, before the feature is built on it</strong>
        </div>
      </div>
      <p className="ow-note">
        The screens are built and passing their checks. Nothing is wired to them yet, so changing
        them now is cheap. The build continues as soon as you answer.
      </p>
      <div className="ow-question-list">
        {questions.map(question => (
          <fieldset className="ow-question" key={question.id}>
            <legend>{question.text}</legend>
            <p>{question.reason}</p>
            <QuestionInput
              question={question}
              value={answers[question.id]}
              onChange={value => setAnswers(current => ({ ...current, [question.id]: value }))}
            />
          </fieldset>
        ))}
      </div>
      <div className="ow-actions">
        <button type="button" className="fs-secondary" onClick={() => onSubmit(keepEverythingPayload())} disabled={pending}>
          {pending ? 'Continuing...' : 'Continue as built'}
        </button>
        <button type="button" className="fs-primary" onClick={submitAnswers} disabled={pending}>
          {pending ? 'Applying...' : 'Apply and continue'}
        </button>
      </div>
    </section>
  );
}

export { KEEP_AS_ASSUMED };
