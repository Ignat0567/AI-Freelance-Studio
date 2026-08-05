import React, { useEffect, useState } from 'react';
import { marketplaceApi } from './marketplaceApi.js';
import './Marketplace.css';

function cleanError(err) {
  if (!err) return 'Something went wrong.';
  return err.message || 'The local backend rejected the request.';
}

export default function MarketplacePage({ active }) {
  const [sections, setSections] = useState([]);
  const [fontPairings, setFontPairings] = useState([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [fontsInjected, setFontsInjected] = useState(false);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    setPending(true);
    setError('');
    Promise.all([marketplaceApi.getSections(), marketplaceApi.getFontPairings()])
      .then(([sectionsResponse, fontsResponse]) => {
        if (cancelled) return;
        setSections(sectionsResponse.sections || []);
        setFontPairings(fontsResponse.font_pairings || []);
      })
      .catch(err => {
        if (!cancelled) setError(cleanError(err));
      })
      .finally(() => {
        if (!cancelled) setPending(false);
      });
    return () => { cancelled = true; };
  }, [active]);

  useEffect(() => {
    if (fontsInjected || fontPairings.length === 0) return;
    for (const pairing of fontPairings) {
      if (!pairing.google_fonts_import_url) continue;
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = pairing.google_fonts_import_url;
      document.head.appendChild(link);
    }
    setFontsInjected(true);
  }, [fontPairings, fontsInjected]);

  if (!active) return null;

  return (
    <div className="mp-page">
      <section className="mp-hero">
        <div>
          <h2>Marketplace</h2>
          <p>Browse the curated website sections and font pairings the Studio selects from when it builds a cinematic site.</p>
        </div>
      </section>

      {pending && <p className="mp-note">Loading marketplace catalog...</p>}
      {error && <p className="mp-error">{error}</p>}

      <section className="mp-card">
        <h3>Website Sections</h3>
        <div className="mp-section-grid">
          {sections.map(section => (
            <article className="mp-section-card" key={section.slug}>
              <h4>{section.display_name}</h4>
              <p>{section.description}</p>
              <p className="mp-when-to-use">{section.when_to_use}</p>
              <ul className="mp-field-list">
                {section.content_schema.map(field => (
                  <li key={field.field}><strong>{field.field}</strong> - {field.description}</li>
                ))}
              </ul>
              <div className="mp-deps">
                {section.npm_dependencies.map(dep => (
                  <span className="mp-dep-tag" key={dep}>{dep}</span>
                ))}
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="mp-card">
        <h3>Font Pairings</h3>
        <div className="mp-font-grid">
          {fontPairings.map(pairing => (
            <article className="mp-font-card" key={pairing.slug}>
              <p className="mp-font-preview" style={{ fontFamily: pairing.heading_family }}>{pairing.heading_family}</p>
              <p className="mp-font-preview-body" style={{ fontFamily: pairing.body_family }}>{pairing.body_family}</p>
              <p className="mp-font-slug">{pairing.slug}</p>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
