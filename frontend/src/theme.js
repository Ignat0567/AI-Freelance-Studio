// Accent colors are stored/selected as oklch() strings (see SettingsModal's ACCENT_COLORS and
// the Liquid Glass redesign's oklch token system in index.css). This module still accepts a
// legacy hex value transparently (older saved settings, or a hand-edited config file) by
// converting it to oklch on the fly, so nobody's saved accent silently resets to the default.

const LIGHT_UNSAFE_L = 0.92; // near-white/near-gray accents read as invisible on a light panel

function srgbToLinear(value) {
  return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
}

function hexToOklch(hex) {
  const clean = hex.replace('#', '');
  const [r, g, b] = [0, 2, 4].map(index => srgbToLinear(parseInt(clean.slice(index, index + 2), 16) / 255));
  const l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b;
  const m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b;
  const s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b;
  const [l_, m_, s_] = [l, m, s].map(value => Math.cbrt(value));
  const L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_;
  const A = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_;
  const B = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_;
  const C = Math.sqrt(A * A + B * B);
  let H = (Math.atan2(B, A) * 180) / Math.PI;
  if (H < 0) H += 360;
  return { l: L, c: C, h: H, a: null };
}

function parseOklch(value) {
  const match = /^oklch\(\s*([\d.]+)%?\s+([\d.]+)\s+([\d.]+)(?:\s*\/\s*([\d.]+%?))?\s*\)$/i.exec(String(value || '').trim());
  if (!match) return null;
  return { l: parseFloat(match[1]), c: parseFloat(match[2]), h: parseFloat(match[3]), a: match[4] ?? null };
}

function formatOklch({ l, c, h, a }) {
  const alpha = a === null || a === undefined ? '' : ` / ${a}`;
  return `oklch(${l.toFixed(3)} ${c.toFixed(3)} ${h.toFixed(1)}${alpha})`;
}

function normalizeAccent(value) {
  const oklch = parseOklch(value);
  if (oklch) return oklch;
  if (/^#[0-9a-f]{6}$/i.test(String(value || ''))) return hexToOklch(value);
  return null;
}

// Darken an accent (in perceptual OKLCH lightness) until it reads safely on a light panel.
// This replaces the old sRGB-luminance loop with a direct L-channel reduction: OKLCH's L is
// already the perceptually uniform lightness axis, so no relative-luminance round-trip is
// needed to get a "dark enough for light-theme use" result.
function lightThemeAccent(oklch) {
  let { l, c, h, a } = oklch;
  while (l > 0.6) l *= 0.88;
  return { l, c, h, a };
}

// Deliberate change from the old WCAG relative-luminance contrast check: OKLCH's L channel is
// already a perceptual lightness, so picking white vs. dark text by an L threshold is both
// simpler and at least as correct as the sRGB-luminance formula it replaces.
function contrastColor(oklch) {
  return oklch.l < 0.6 ? '#ffffff' : '#0f172a';
}

export function applyStudioTheme(settings, { cache = false } = {}) {
  const root = document.documentElement;
  const light = settings?.theme === 'light';
  const configured = normalizeAccent(settings?.accent_color) || { l: 0.685, c: 0.148, h: 237.3, a: null };
  const safe = light && configured.l > LIGHT_UNSAFE_L && configured.c < 0.03
    ? { l: 0.45, c: 0.02, h: 257.3, a: null } // Slate fallback, unreadable near-white accents only
    : configured;
  const accent = light ? lightThemeAccent(safe) : safe;
  const accentStr = formatOklch(accent);

  root.classList.toggle('theme-light', light);
  root.classList.toggle('theme-reduced-motion', settings?.animation_speed === 'off');
  root.classList.remove('font-small', 'font-medium', 'font-large');
  if (settings?.font_size) root.classList.add(`font-${settings.font_size}`);

  root.style.setProperty('--accent', accentStr);
  root.style.setProperty('--accent-hover', `color-mix(in oklch, ${accentStr} 84%, ${light ? 'black' : 'white'})`);
  root.style.setProperty('--accent-bg', `color-mix(in oklch, ${accentStr} 13%, transparent)`);
  root.style.setProperty('--accent-contrast', contrastColor(accent));
  root.style.setProperty('--fs-acc-soft', `color-mix(in oklch, ${accentStr} 14%, transparent)`);
  root.style.setProperty('--fs-acc-line', `color-mix(in oklch, ${accentStr} 42%, transparent)`);
  root.style.setProperty('--fs-glow', `0 0 0 1px color-mix(in oklch, ${accentStr} 45%, transparent), 0 0 22px -4px color-mix(in oklch, ${accentStr} 55%, transparent)`);

  if (cache) localStorage.setItem('studio_theme', light ? 'light' : 'dark');
}
