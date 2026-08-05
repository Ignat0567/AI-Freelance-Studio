import React, { useState } from 'react';
import { presentationApi } from './presentationApi.js';
import './PresentationGenerator.css';

function cleanError(err) {
  if (!err) return 'Something went wrong.';
  return err.message || 'The local backend rejected the request.';
}

export default function PresentationGeneratorPage({ active }) {
  const [topic, setTopic] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  const canGenerate = topic.trim().length > 0 && !pending;

  const handleGenerate = async () => {
    setPending(true);
    setError('');
    setSuccess('');
    try {
      const blob = await presentationApi.generate(topic.trim());
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = 'presentation.pptx';
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      setSuccess('Presentation generated and downloaded.');
    } catch (err) {
      setError(cleanError(err));
    } finally {
      setPending(false);
    }
  };

  if (!active) return null;

  return (
    <div className="pg-page">
      <section className="pg-hero">
        <div>
          <h2>AI Presentations</h2>
          <p>Generate a real PowerPoint deck from a topic -- a curated slide layout library filled in by AI, downloaded straight to your computer.</p>
        </div>
      </section>

      <section className="pg-card">
        <h3>Topic</h3>
        <input
          type="text"
          placeholder="What is this presentation about?"
          value={topic}
          onChange={e => setTopic(e.target.value)}
        />
        <button type="button" disabled={!canGenerate} onClick={handleGenerate}>
          {pending ? 'Generating...' : 'Generate presentation'}
        </button>
        {error && <p className="pg-error">{error}</p>}
        {success && <p className="pg-success">{success}</p>}
      </section>
    </div>
  );
}
