import React, { useState } from 'react';
import { videoGenerationApi } from './videoGenerationApi.js';
import './VideoGeneration.css';

function cleanError(err) {
  if (!err) return 'Something went wrong.';
  return err.message || 'The local backend rejected the request.';
}

export default function VideoGenerationPage({ active }) {
  const [prompt, setPrompt] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);

  const canGenerate = prompt.trim().length > 0 && !pending;

  const handleGenerate = async () => {
    setPending(true);
    setError('');
    setResult(null);
    try {
      const response = await videoGenerationApi.generate({ prompt: prompt.trim() });
      setResult(response);
    } catch (err) {
      setError(cleanError(err));
    } finally {
      setPending(false);
    }
  };

  if (!active) return null;

  return (
    <div className="vg-page">
      <section className="vg-hero">
        <div>
          <h2>AI Video</h2>
          <p>Generate a short video from a text prompt via Replicate. Real generation can take a few minutes and requires a REPLICATE_API_TOKEN to be configured.</p>
        </div>
      </section>

      <section className="vg-card">
        <h3>Prompt</h3>
        <textarea
          placeholder="Describe the video you want to generate"
          rows={4}
          value={prompt}
          onChange={e => setPrompt(e.target.value)}
        />
        <button type="button" disabled={!canGenerate} onClick={handleGenerate}>
          {pending ? 'Generating (this can take a few minutes)...' : 'Generate video'}
        </button>
        {error && <p className="vg-error">{error}</p>}
      </section>

      {result && (
        <section className="vg-card vg-result">
          <h3>Result</h3>
          <video src={result.video_url} controls className="vg-video" />
          <p className="vg-meta">Model: {result.model_slug}</p>
        </section>
      )}
    </div>
  );
}
