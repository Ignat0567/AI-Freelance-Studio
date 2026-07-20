const LIGHT_UNSAFE_ACCENTS = new Set(['#fff', '#ffffff', '#f1f5f9']);

function normalizeHex(color) {
  if (!/^#[0-9a-f]{6}$/i.test(color || '')) return null;
  return color.toLowerCase();
}

function rgb(hex) {
  return [1, 3, 5].map(index => parseInt(hex.slice(index, index + 2), 16));
}

function luminance(channels) {
  const [red, green, blue] = channels.map(value => {
    value /= 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

function lightThemeAccent(color) {
  let channels = rgb(color);
  while (1.05 / (luminance(channels) + 0.05) < 4.5) {
    channels = channels.map(value => Math.round(value * 0.88));
  }
  return `#${channels.map(value => value.toString(16).padStart(2, '0')).join('')}`;
}

function contrastColor(color) {
  const hex = normalizeHex(color);
  if (!hex) return '#ffffff';
  const colorLuminance = luminance(rgb(hex));
  const whiteContrast = 1.05 / (colorLuminance + 0.05);
  const darkContrast = (colorLuminance + 0.05) / 0.05;
  return whiteContrast >= darkContrast ? '#ffffff' : '#0f172a';
}

export function applyStudioTheme(settings, { cache = false } = {}) {
  const root = document.documentElement;
  const light = settings?.theme === 'light';
  const configuredAccent = normalizeHex(settings?.accent_color) || '#0ea5e9';
  const safeAccent = light && LIGHT_UNSAFE_ACCENTS.has(configuredAccent) ? '#475569' : configuredAccent;
  const accent = light ? lightThemeAccent(safeAccent) : safeAccent;

  root.classList.toggle('theme-light', light);
  root.classList.toggle('theme-reduced-motion', settings?.animation_speed === 'off');
  root.classList.remove('font-small', 'font-medium', 'font-large');
  if (settings?.font_size) root.classList.add(`font-${settings.font_size}`);
  root.style.setProperty('--accent', accent);
  root.style.setProperty('--accent-hover', `color-mix(in srgb, ${accent} 84%, var(--text-primary))`);
  root.style.setProperty('--accent-bg', `color-mix(in srgb, ${accent} 13%, transparent)`);
  root.style.setProperty('--accent-contrast', contrastColor(accent));

  if (cache) localStorage.setItem('studio_theme', light ? 'light' : 'dark');
}
