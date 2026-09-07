import React from 'react';

// The two SVG refraction filters from design_handoff_liquid_glass/README.md ("Liquid Glass"),
// values copied verbatim. Mounted exactly once at the root of .fs-shell (see
// StudioDashboard.jsx) -- it never unmounts across nav switches, so liquidGlass.js's shared
// rAF loop always finds #liquid-refraction / #liquid-edge in the DOM. Never render this more
// than once: a second copy would just be dead weight, the ids are looked up by liquidGlass.js
// and by the background blob layer's `filter: ... url(#liquid-refraction)`.
export default function LiquidGlassDefs() {
  return (
    <svg aria-hidden="true" style={{ position: 'absolute', width: 0, height: 0 }}>
      <filter id="liquid-refraction" x="-20%" y="-20%" width="140%" height="140%">
        <feTurbulence type="fractalNoise" baseFrequency="0.014" numOctaves="2" seed="1" result="noise" />
        <feDisplacementMap in="SourceGraphic" in2="noise" scale="18" xChannelSelector="R" yChannelSelector="G" />
      </filter>
      <filter id="liquid-edge" x="-15%" y="-15%" width="130%" height="130%">
        <feTurbulence type="fractalNoise" baseFrequency="0.02" numOctaves="1" seed="4" result="n" />
        <feDisplacementMap in="SourceGraphic" in2="n" scale="7" xChannelSelector="R" yChannelSelector="G" />
      </filter>
    </svg>
  );
}
