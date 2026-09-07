// Presentation-only transform of the app's existing log strings (App.jsx's `logs` state:
// plain "[HH:MM:SS] message" strings, unchanged) into the {time, level, text} shape the new
// Logs table (README "4. Логи") and the context rail's Live Log block both render. Does not
// touch how logs are produced or stored -- addLog() in App.jsx is untouched.

const TIMESTAMP = /^\[(\d{1,2}:\d{2}:\d{2}(?:\s?[AP]M)?)\]\s*/i;

export function classifyLevel(text) {
  if (/\b(error|failed|exception|crash)\b/i.test(text)) return 'err';
  if (/\b(warn|warning|retry|deprecated)\b/i.test(text)) return 'warn';
  if (/\b(saved|success|done|completed|ready|passed)\b/i.test(text)) return 'ok';
  return 'info';
}

export function parseLogLine(raw) {
  const match = TIMESTAMP.exec(raw);
  const time = match ? match[1] : '';
  const text = match ? raw.slice(match[0].length) : raw;
  return { time, text, level: classifyLevel(text), raw };
}

export const LOG_LEVELS = ['info', 'ok', 'warn', 'err'];
