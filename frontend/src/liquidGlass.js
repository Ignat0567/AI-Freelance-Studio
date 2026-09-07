// Liquid Glass interaction layer (design_handoff_liquid_glass/README.md, "Liquid Glass" +
// "Interactions & Behavior"). Deliberately plain DOM/rAF code, not a React hook that drives
// state: seed/scale/tilt/glare must never trigger a re-render (README: "Не через состояние
// (только DOM...): seed и scale SVG-фильтра, наклон и блик карточек"). Call initLiquidGlass()
// once from a top-level useEffect (StudioDashboard.jsx) and call the returned cleanup on
// unmount -- there is only ever one instance of this running for the whole app.

const IDLE_SCALE = 18;
const HOVER_SCALE = 32;
const IDLE_TIMEOUT_MS = 280;

function prefersReducedMotion() {
  return document.documentElement.classList.contains('theme-reduced-motion')
    || (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
}

export function initLiquidGlass() {
  let running = false;
  let rafId = null;
  let seed = 1;
  let scale = IDLE_SCALE;
  let targetScale = IDLE_SCALE;
  let lastMoveAt = 0;
  let activeEl = null;

  const turbulence = () => document.querySelector('#liquid-refraction feTurbulence');
  const displacement = () => document.querySelector('#liquid-refraction feDisplacementMap');

  function tick() {
    if (!running) return;
    if (prefersReducedMotion()) { stopLoop(); return; }
    seed += 0.05;
    const t = turbulence();
    if (t) t.setAttribute('seed', String(Math.floor(seed)));
    if (Date.now() - lastMoveAt > IDLE_TIMEOUT_MS) targetScale = IDLE_SCALE;
    scale += (targetScale - scale) * 0.09;
    const d = displacement();
    if (d) d.setAttribute('scale', scale.toFixed(2));
    rafId = requestAnimationFrame(tick);
  }

  function startLoop() {
    if (running || prefersReducedMotion()) return;
    running = true;
    rafId = requestAnimationFrame(tick);
  }

  function stopLoop() {
    running = false;
    if (rafId) cancelAnimationFrame(rafId);
    rafId = null;
  }

  function clearHover(el) {
    if (!el) return;
    el.style.transform = '';
    el.style.backgroundImage = '';
    el.style.backdropFilter = '';
    el.style.webkitBackdropFilter = '';
    el.style.boxShadow = '';
    el.style.willChange = '';
  }

  function applyHover(el, event) {
    const rect = el.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const w = rect.width;
    const h = rect.height;

    el.style.backdropFilter = 'blur(22px) saturate(170%) url(#liquid-refraction)';
    el.style.webkitBackdropFilter = el.style.backdropFilter;
    el.style.boxShadow = 'inset 0 1px 0 oklch(1 0 0 / 0.24), 0 18px 40px -22px oklch(0.10 0.02 300 / 0.8)';
    el.style.willChange = 'transform';

    if (h >= 44) {
      const amp = w > 520 ? 4.5 : 7;
      const rotX = -((y - h / 2) / h) * amp;
      const rotY = ((x - w / 2) / w) * amp * 1.2;
      el.style.transform = `perspective(1200px) rotateX(${rotX.toFixed(2)}deg) rotateY(${rotY.toFixed(2)}deg)`;
    }

    const glareSize = Math.max(220, w * 0.8);
    el.style.backgroundImage = `radial-gradient(${glareSize}px circle at ${x}px ${y}px, oklch(1 0 0 / 0.16), transparent 62%), var(--fs-glass)`;

    targetScale = HOVER_SCALE;
    lastMoveAt = Date.now();
  }

  function handleMouseMove(event) {
    if (prefersReducedMotion()) return;
    startLoop(); // no-op if already running -- resumes it if animation_speed was just re-enabled
    const el = typeof event.target.closest === 'function' ? event.target.closest('[data-glass]') : null;
    if (el !== activeEl) {
      clearHover(activeEl);
      activeEl = el;
    }
    if (el) applyHover(el, event);
  }

  function handleMouseLeaveDoc() {
    clearHover(activeEl);
    activeEl = null;
  }

  const motionQuery = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
  const handleMotionChange = () => {
    if (prefersReducedMotion()) stopLoop();
    else startLoop();
  };

  document.addEventListener('mousemove', handleMouseMove);
  document.addEventListener('mouseleave', handleMouseLeaveDoc);
  if (motionQuery) {
    if (motionQuery.addEventListener) motionQuery.addEventListener('change', handleMotionChange);
    else if (motionQuery.addListener) motionQuery.addListener(handleMotionChange);
  }
  startLoop();

  return function cleanup() {
    stopLoop();
    document.removeEventListener('mousemove', handleMouseMove);
    document.removeEventListener('mouseleave', handleMouseLeaveDoc);
    if (motionQuery) {
      if (motionQuery.removeEventListener) motionQuery.removeEventListener('change', handleMotionChange);
      else if (motionQuery.removeListener) motionQuery.removeListener(handleMotionChange);
    }
    clearHover(activeEl);
  };
}

// Also exported for the theme toggle: flipping .theme-reduced-motion at runtime (Settings ->
// Appearance -> animation speed) should stop/start the loop immediately, not just gate new
// frames -- but since tick() re-checks prefersReducedMotion()-derived state every frame via
// startLoop()/stopLoop() being called from the class/media-query change paths, no extra wiring
// is needed here; this export exists so other modules can query the same rule if needed.
export function isReducedMotion() {
  return prefersReducedMotion();
}
