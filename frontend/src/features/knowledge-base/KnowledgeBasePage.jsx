import React, { useState } from 'react';
import { knowledgeBaseApi } from './knowledgeBaseApi.js';
import './KnowledgeBase.css';

let nextDocumentId = 1;

function emptyDocument() {
  const id = `doc-${nextDocumentId++}`;
  return { id, title: '', text: '' };
}

function cleanError(err) {
  if (!err) return 'Something went wrong.';
  return err.message || 'The local backend rejected the request.';
}

export default function KnowledgeBasePage({ active }) {
  const [documents, setDocuments] = useState([emptyDocument(), emptyDocument()]);
  const [query, setQuery] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);

  const updateDocument = (id, field, value) => {
    setDocuments(prev => prev.map(doc => (doc.id === id ? { ...doc, [field]: value } : doc)));
  };

  const addDocument = () => setDocuments(prev => [...prev, emptyDocument()]);
  const removeDocument = id => setDocuments(prev => prev.filter(doc => doc.id !== id));

  const usableDocuments = documents.filter(doc => doc.title.trim() && doc.text.trim());
  const canAsk = usableDocuments.length > 0 && query.trim().length > 0 && !pending;

  const handleAsk = async () => {
    setPending(true);
    setError('');
    setResult(null);
    try {
      const payload = {
        documents: usableDocuments.map(doc => ({ id: doc.id, title: doc.title.trim(), text: doc.text.trim() })),
        query: query.trim(),
      };
      const response = await knowledgeBaseApi.ask(payload);
      setResult(response);
    } catch (err) {
      setError(cleanError(err));
    } finally {
      setPending(false);
    }
  };

  if (!active) return null;

  return (
    <div className="kb-page">
      <section className="kb-hero">
        <div>
          <h2>Knowledge Base</h2>
          <p>Add a few documents, ask a question, and get an answer grounded only in what you added -- real local embeddings, no cloud upload of your text.</p>
        </div>
      </section>

      <section className="kb-card">
        <h3>Documents</h3>
        <div className="kb-doc-list">
          {documents.map(doc => (
            <div className="kb-doc" key={doc.id}>
              <input
                type="text"
                placeholder="Document title"
                value={doc.title}
                onChange={e => updateDocument(doc.id, 'title', e.target.value)}
              />
              <textarea
                placeholder="Paste document text here"
                rows={4}
                value={doc.text}
                onChange={e => updateDocument(doc.id, 'text', e.target.value)}
              />
              {documents.length > 1 && (
                <button type="button" className="kb-remove" onClick={() => removeDocument(doc.id)}>
                  Remove
                </button>
              )}
            </div>
          ))}
        </div>
        <button type="button" className="kb-add" onClick={addDocument}>
          + Add document
        </button>
      </section>

      <section className="kb-card">
        <h3>Ask a question</h3>
        <div className="kb-ask-row">
          <input
            type="text"
            placeholder="What do you want to know?"
            value={query}
            onChange={e => setQuery(e.target.value)}
          />
          <button type="button" disabled={!canAsk} onClick={handleAsk}>
            {pending ? 'Thinking...' : 'Ask'}
          </button>
        </div>
        {error && <p className="kb-error">{error}</p>}
      </section>

      {result && (
        <section className="kb-card kb-result">
          <h3>Answer</h3>
          <p className="kb-answer">{result.answer}</p>
          <h3>Matching documents</h3>
          <ul className="kb-ranked-list">
            {result.ranked.map(item => (
              <li key={item.id}>
                <strong>{item.title}</strong>
                <span className="kb-score">{(item.score * 100).toFixed(0)}% match</span>
                <p>{item.snippet}</p>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
