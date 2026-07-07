import React, { useEffect, useState } from 'react';

const MODEL_URL = '/models/isometric_office.glb';

const AGENTS = [
  { id: 'alex', name: 'Alex', role: 'PM', color: '#6366f1' },
  { id: 'maya', name: 'Maya', role: 'Analyst', color: '#a855f7' },
  { id: 'elena', name: 'Elena', role: 'Design', color: '#ec4899' },
  { id: 'codex', name: 'Codex', role: 'Dev', color: '#0ea5e9' },
  { id: 'bugcatcher', name: 'BugCatcher', role: 'QA', color: '#10b981' },
  { id: 'sentinel', name: 'Sentinel', role: 'Security', color: '#ef4444' },
  { id: 'lupa', name: 'Lupa', role: 'Review', color: '#8b5cf6' },
  { id: 'goldie', name: 'Goldie', role: 'Finance', color: '#f59e0b' },
];

export default function IsoOffice({ agentStatuses = {} }) {
  const [viewerReady, setViewerReady] = useState(false);
  const [autoRotate, setAutoRotate] = useState(() => localStorage.getItem('studio_office_auto_rotate') !== 'false');
  const workingAgents = AGENTS.filter(agent => agentStatuses?.[agent.id]?.status === 'working');

  useEffect(() => {
    let mounted = true;
    import('@google/model-viewer').then(() => {
      if (mounted) setViewerReady(true);
    });
    return () => { mounted = false; };
  }, []);

  const toggleAutoRotate = () => {
    setAutoRotate(prev => {
      const next = !prev;
      localStorage.setItem('studio_office_auto_rotate', String(next));
      return next;
    });
  };

  return (
    <div className="studio-3d-office flex-1 p-2 bg-slate-950/60 relative overflow-hidden rounded-xl border border-slate-800" style={{ minHeight: 400 }}>
      <div className="absolute top-3 left-4 z-10">
        <div className="text-[10px] font-bold uppercase tracking-widest text-slate-400">3D Office View</div>
        <div className="text-[9px] text-slate-600 mt-0.5">Drag to rotate · wheel to zoom</div>
      </div>

      <div
        className="flex flex-col items-end gap-2"
        style={{
          position: 'absolute',
          top: 12,
          right: 16,
          left: 'auto',
          zIndex: 20,
          alignItems: 'flex-end',
        }}
      >
        <button
          type="button"
          onClick={toggleAutoRotate}
          className="px-3 py-1.5 rounded-full border text-[10px] font-mono uppercase tracking-wider transition-all"
          style={{
            backgroundColor: autoRotate ? 'color-mix(in srgb, var(--accent) 20%, rgba(15,23,42,0.72))' : 'rgba(15,23,42,0.72)',
            borderColor: autoRotate ? 'color-mix(in srgb, var(--accent) 45%, transparent)' : 'rgba(148,163,184,0.24)',
            color: autoRotate ? 'var(--accent)' : 'var(--text-secondary)'
          }}
        >
          Auto Rotate: {autoRotate ? 'On' : 'Off'}
        </button>
        {workingAgents.length > 0 && (
          <div className="flex flex-wrap justify-end gap-1.5 max-w-[280px]">
            {workingAgents.map(agent => (
              <div key={agent.id} className="px-2.5 py-1 rounded-full border text-[10px] font-semibold backdrop-blur-md"
                style={{ backgroundColor: `${agent.color}24`, borderColor: `${agent.color}66`, color: agent.color }}>
                {agent.name} working
              </div>
            ))}
          </div>
        )}
      </div>

      {viewerReady ? (
        <model-viewer
          src={MODEL_URL}
          camera-controls
          touch-action="pan-y"
          auto-rotate={autoRotate || undefined}
          auto-rotate-delay="2500"
          rotation-per-second="6deg"
          interaction-prompt="auto"
          shadow-intensity="0.85"
          exposure="1.05"
          camera-orbit="45deg 62deg 7m"
          min-camera-orbit="auto 18deg 3m"
          max-camera-orbit="auto 82deg 14m"
          field-of-view="32deg"
          ar="false"
          loading="eager"
          reveal="auto"
          class="w-full h-full min-h-[400px]"
          style={{ '--poster-color': 'transparent', background: 'transparent' }}
        >
          <div slot="poster" className="w-full h-full min-h-[400px] flex items-center justify-center text-xs text-slate-500">
            Loading 3D office...
          </div>
          <div slot="progress-bar" className="absolute left-1/2 top-1/2 h-1 w-40 -translate-x-1/2 rounded-full overflow-hidden bg-slate-800">
            <div className="h-full w-1/2 rounded-full bg-sky-400 animate-pulse" />
          </div>
        </model-viewer>
      ) : (
        <div className="w-full h-full min-h-[400px] flex items-center justify-center text-xs text-slate-500">
          Preparing 3D office viewer...
        </div>
      )}

      <div className="pointer-events-none absolute inset-x-6 bottom-4 z-10 grid grid-cols-2 sm:grid-cols-4 gap-2">
        {AGENTS.slice(0, 8).map(agent => {
          const status = agentStatuses?.[agent.id]?.status || 'idle';
          const isWorking = status === 'working';
          return (
            <div key={agent.id} className="rounded-xl border px-2.5 py-2 backdrop-blur-md"
              style={{ backgroundColor: 'rgba(2,6,23,0.52)', borderColor: isWorking ? `${agent.color}80` : 'rgba(148,163,184,0.16)' }}>
              <div className="flex items-center gap-2">
                <span className="h-2 w-2 rounded-full" style={{ backgroundColor: isWorking ? agent.color : '#475569', boxShadow: isWorking ? `0 0 16px ${agent.color}` : 'none' }} />
                <div className="min-w-0">
                  <div className="truncate text-[10px] font-bold" style={{ color: isWorking ? agent.color : 'var(--text-secondary)' }}>{agent.name}</div>
                  <div className="truncate text-[9px] text-slate-600">{isWorking ? (agentStatuses[agent.id]?.task || 'working') : agent.role}</div>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
