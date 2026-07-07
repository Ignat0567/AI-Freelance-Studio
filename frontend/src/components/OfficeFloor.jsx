import React, { useEffect, useState, useRef, useCallback } from 'react';

const AGENTS = [
  { id: 'maya', name: 'Maya', color: '#a855f7', role: 'Analyst' },
  { id: 'alex', name: 'Alex', color: '#6366f1', role: 'PM' },
  { id: 'codex', name: 'Codex', color: '#0ea5e9', role: 'Dev' },
  { id: 'elena', name: 'Elena', color: '#ec4899', role: 'Design' },
  { id: 'bugcatcher', name: 'BugCatcher', color: '#10b981', role: 'QA' },
  { id: 'goldie', name: 'Goldie', color: '#f59e0b', role: 'Finance' },
  { id: 'sentinel', name: 'Sentinel', color: '#ef4444', role: 'Security' },
  { id: 'lupa', name: 'Lupa', color: '#8b5cf6', role: 'Review' },
];

const ROOMS = [
  { id: 'ceo', name: 'CEO Cabinet', icon: '🏢', desc: 'Command hub for kick-off syncs.', agents: ['maya', 'alex'], status: 'meeting', col: 0, row: 0, color: 'indigo' },
  { id: 'dev', name: 'Open Space Devs', icon: '🖥️', desc: 'Code generation workspace.', agents: ['codex', 'elena'], status: 'coding', col: 1, row: 0, color: 'sky' },
  { id: 'security', name: 'Security Lab', icon: '🛡️', desc: 'Security audit & vulnerability scanning.', agents: ['sentinel'], status: 'security_audit', col: 2, row: 0, color: 'red' },
  { id: 'qa', name: 'QA Laboratory', icon: '🧪', desc: 'Test containers & verification.', agents: ['bugcatcher'], status: 'testing', col: 0, row: 1, color: 'emerald' },
  { id: 'review', name: 'Review Room', icon: '🔍', desc: 'Code review & quality analysis.', agents: ['lupa'], status: 'code_review', col: 1, row: 1, color: 'violet' },
  { id: 'finance', name: 'Finance Department', icon: '📊', desc: 'Budget & milestone tracking.', agents: ['goldie'], status: null, col: 2, row: 1, color: 'amber' },
];

const HOME_POSITIONS = {};
AGENTS.forEach((agent, i) => {
  const room = ROOMS.find(r => r.agents.includes(agent.id));
  if (room) {
    const idx = room.agents.indexOf(agent.id);
    const cx = 12.5 + room.col * 50;
    const cy = 12.5 + room.row * 50;
    HOME_POSITIONS[agent.id] = {
      x: cx + (idx === 0 ? -6 : 6),
      y: cy + (idx === 0 ? -3 : 3),
    };
  } else {
    HOME_POSITIONS[agent.id] = { x: 50, y: 50 };
  }
});

const AGENT_FURNITURE = {
  maya: '📋', alex: '💻', codex: '🖥️', elena: '🖥️', bugcatcher: '🔬', goldie: '💹', sentinel: '🛡️', lupa: '🔍',
};

const AGENT_WORK_EMOJI = {
  maya: '📋', alex: '⌨️', codex: '💻', elena: '🎨', bugcatcher: '🔍', goldie: '📊', sentinel: '🛡️', lupa: '🔎',
};

function HumanoidSVG({ color, isActive }) {
  return (
    <svg width="24" height="38" viewBox="0 0 24 38">
      <ellipse cx="12" cy="36" rx="8" ry="2" fill="rgba(0,0,0,0.15)" />
      {isActive ? (
        <>
          <rect x="5" y="22" width="4.5" height="13" rx="2.25" fill={color} className="leg-left" />
          <rect x="14.5" y="22" width="4.5" height="13" rx="2.25" fill={color} className="leg-right" />
        </>
      ) : (
        <>
          <rect x="5.5" y="22" width="4.5" height="12" rx="2.25" fill={color} opacity="0.6" />
          <rect x="14" y="22" width="4.5" height="12" rx="2.25" fill={color} opacity="0.6" />
        </>
      )}
      <rect x="3" y="10" width="18" height="14" rx="5" fill={color} opacity="0.85" />
      <rect x="3.5" y="10.5" width="17" height="13" rx="4.5" fill="rgba(255,255,255,0.08)" />
      <circle cx="12" cy="5.5" r="6.5" fill={color} />
      <circle cx="12" cy="5.5" r="5.5" fill="rgba(255,255,255,0.12)" />
      <circle cx="9.5" cy="4.5" r="1.2" fill="white" />
      <circle cx="14.5" cy="4.5" r="1.2" fill="white" />
      <circle cx="10" cy="5" r="0.5" fill="#1e293b" />
      <circle cx="15" cy="5" r="0.5" fill="#1e293b" />
    </svg>
  );
}

export default function OfficeFloor({ activeProject, onRoomClick, activePort }) {
  const [agentStatuses, setAgentStatuses] = useState({});
  const [positions, setPositions] = useState(() => {
    const init = {};
    AGENTS.forEach((a) => { init[a.id] = { ...HOME_POSITIONS[a.id] }; });
    return init;
  });
  const prevStatusesRef = useRef({});
  const animFrameRef = useRef(null);

  const fetchStatuses = useCallback(async () => {
    if (!activePort) return;
    try {
      const r = await fetch(`http://localhost:${activePort}/api/agents/status`);
      const data = await r.json();
      setAgentStatuses(data);
    } catch (e) { /* silent */ }
  }, [activePort]);

  useEffect(() => {
    fetchStatuses();
    const interval = setInterval(fetchStatuses, 2000);
    return () => clearInterval(interval);
  }, [fetchStatuses]);

  useEffect(() => {
    const prev = prevStatusesRef.current;
    const updates = {};
    let changed = false;

    AGENTS.forEach((agent) => {
      const st = agentStatuses[agent.id];
      const was = prev[agent.id];
      const isWorking = st?.status === 'working';

      if (isWorking) {
        const targetRoom = ROOMS.find(r => r.status === activeProject?.status) ||
                           ROOMS.find(r => r.agents.includes(agent.id));
        if (targetRoom) {
          const cx = 12.5 + targetRoom.col * 50;
          const cy = 12.5 + targetRoom.row * 50;
          const idx = targetRoom.agents.indexOf(agent.id);
          const offset = idx >= 0 ? (idx === 0 ? -5 : 5) : 0;
          updates[agent.id] = { x: cx + offset, y: cy };
          changed = true;
        }
      } else {
        const home = HOME_POSITIONS[agent.id];
        if (home) {
          updates[agent.id] = { x: home.x, y: home.y };
          changed = true;
        }
      }
    });

    if (changed) {
      setPositions(prev => ({ ...prev, ...updates }));
    }
    prevStatusesRef.current = agentStatuses;
  }, [agentStatuses, activeProject?.status]);

  const colorStyle = {
    indigo: { border: 'rgba(99,102,241,0.5)', bg: 'rgba(99,102,241,0.08)', glow: 'rgba(99,102,241,0.2)', text: '#818cf8' },
    sky: { border: 'rgba(14,165,233,0.5)', bg: 'rgba(14,165,233,0.08)', glow: 'rgba(14,165,233,0.2)', text: '#38bdf8' },
    emerald: { border: 'rgba(16,185,129,0.5)', bg: 'rgba(16,185,129,0.08)', glow: 'rgba(16,185,129,0.2)', text: '#34d399' },
    amber: { border: 'rgba(245,158,11,0.5)', bg: 'rgba(245,158,11,0.08)', glow: 'rgba(245,158,11,0.2)', text: '#fbbf24' },
    red: { border: 'rgba(239,68,68,0.5)', bg: 'rgba(239,68,68,0.08)', glow: 'rgba(239,68,68,0.2)', text: '#f87171' },
    violet: { border: 'rgba(139,92,246,0.5)', bg: 'rgba(139,92,246,0.08)', glow: 'rgba(139,92,246,0.2)', text: '#a78bfa' },
  };

  const isRoomActive = (room) => {
    if (!activeProject) return false;
    return activeProject.status === room.status;
  };

  return (
    <div className="flex-1 p-4 bg-slate-950/60 relative overflow-hidden" style={{ minHeight: 400 }}>
      <style>{`
        @keyframes walkLeft {
          0%, 100% { transform: rotate(-8deg) translateY(0); }
          50% { transform: rotate(10deg) translateY(-2px); }
        }
        @keyframes walkRight {
          0%, 100% { transform: rotate(8deg) translateY(0); }
          50% { transform: rotate(-10deg) translateY(-2px); }
        }
        .agent-walking .leg-left { animation: walkLeft 0.4s ease-in-out infinite; transform-origin: 7px 22px; }
        .agent-walking .leg-right { animation: walkRight 0.4s ease-in-out infinite; transform-origin: 17px 22px; }
        @keyframes statusPulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.6; }
        }
        .status-pulse { animation: statusPulse 1.5s ease-in-out infinite; }
      `}</style>

      {/* Floor grid */}
      <div className="absolute inset-0 opacity-[0.03]" style={{
        backgroundImage: 'linear-gradient(rgba(148,163,184,1) 1px, transparent 1px), linear-gradient(90deg, rgba(148,163,184,1) 1px, transparent 1px)',
        backgroundSize: '40px 40px',
      }} />

      {/* Room grid */}
      <div className="grid grid-cols-3 grid-rows-2 gap-3 h-full w-full relative z-0">
        {ROOMS.map((room) => {
          const active = isRoomActive(room);
          const cs = colorStyle[room.color] || colorStyle.indigo;
          const roomAgents = room.agents.map(id => AGENTS.find(a => a.id === id)).filter(Boolean);
          const busyAgents = roomAgents.filter(a => agentStatuses[a.id]?.status === 'working');

          return (
            <button
              key={room.id}
              onClick={() => onRoomClick(room)}
              className="relative rounded-xl border text-left transition-all duration-300 group overflow-hidden flex flex-col"
              style={{
                borderColor: active ? cs.border : 'rgba(51,65,85,0.6)',
                backgroundColor: active ? cs.bg : 'rgba(15,23,42,0.5)',
                boxShadow: active ? `0 0 20px ${cs.glow}, inset 0 0 40px ${cs.bg}` : 'none',
              }}
            >
              <div className="px-3 py-2 flex items-center gap-2"
                style={{
                  borderBottom: active ? `1px solid ${cs.border}40` : '1px solid rgba(51,65,85,0.4)',
                  backgroundColor: active ? `${cs.border}15` : 'rgba(15,23,42,0.3)',
                }}>
                <span className="text-base">{room.icon}</span>
                <span className="text-xs font-bold tracking-wide" style={{ color: active ? cs.text : '#94a3b8' }}>{room.name}</span>
                {busyAgents.length > 0 && (
                  <span className="ml-auto text-[8px] font-bold uppercase px-1.5 py-0.5 rounded status-pulse" style={{ backgroundColor: `${cs.text}30`, color: cs.text }}>
                    {busyAgents.length} active
                  </span>
                )}
              </div>

              <div className="flex-1 px-3 py-2 flex flex-col justify-between">
                <div>
                  <div className="flex items-center gap-1.5 mb-1.5 opacity-60">
                    {room.agents.map(id => {
                      const isAgentWorking = agentStatuses[id]?.status === 'working';
                      const emoji = isAgentWorking ? (AGENT_WORK_EMOJI[id] || '💻') : (AGENT_FURNITURE[id] || '🪑');
                      return (
                        <span key={id} className={`text-sm ${isAgentWorking ? 'status-pulse' : ''}`} title={AGENTS.find(a => a.id === id)?.name}>{emoji}</span>
                      );
                    })}
                    <span className="text-xs ml-auto" style={{ color: '#475569' }}>
                      {room.agents.length} desks
                    </span>
                  </div>
                  <p className="text-[10px] leading-relaxed" style={{ color: '#64748b' }}>{room.desc}</p>
                </div>

                {/* Agent status list */}
                <div className="mt-2 pt-1.5 space-y-1" style={{ borderTop: '1px solid rgba(51,65,85,0.3)' }}>
                  {roomAgents.map(a => {
                    const st = agentStatuses[a.id];
                    const isWorking = st?.status === 'working';
                    return (
                      <div key={a.id} className="flex items-center gap-1.5 text-[9px]">
                        <div className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: isWorking ? a.color : '#475569' }} />
                        <span style={{ color: isWorking ? a.color : '#64748b', fontWeight: isWorking ? 700 : 400 }}>{a.name}</span>
                        <span style={{ color: '#475569' }}>-</span>
                        <span className="truncate" style={{ color: isWorking ? '#94a3b8' : '#475569' }}>
                          {isWorking ? st.task : 'idle'}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            </button>
          );
        })}
      </div>

      {/* Character sprites */}
      <div className="absolute inset-0 pointer-events-none z-10">
        {AGENTS.map((agent) => {
          const pos = positions[agent.id] || HOME_POSITIONS[agent.id];
          const st = agentStatuses[agent.id];
          const isWorking = st?.status === 'working';

          return (
            <div
              key={agent.id}
              className="absolute flex flex-col items-center transition-all duration-[1200ms] ease-in-out"
              style={{
                left: `${pos.x}%`,
                top: `${pos.y}%`,
                transform: 'translate(-50%, -50%)',
              }}
            >
              {isWorking && (
                <span className="text-sm mb-0.5 status-pulse" style={{ filter: `drop-shadow(0 0 4px ${agent.color})` }}>
                  {AGENT_WORK_EMOJI[agent.id] || '💻'}
                </span>
              )}
              <div
                className={`transition-all duration-300 ${isWorking ? 'agent-walking' : ''}`}
                style={{
                  filter: isWorking ? `drop-shadow(0 0 8px ${agent.color}80)` : 'none',
                }}
              >
                <HumanoidSVG color={agent.color} isActive={isWorking} />
              </div>
              <span
                className="text-[8px] font-bold mt-0.5 whitespace-nowrap px-1.5 py-0.5 rounded transition-all duration-500"
                style={{
                  color: isWorking ? agent.color : '#64748b',
                  backgroundColor: isWorking ? agent.color + '20' : 'rgba(15,23,42,0.6)',
                }}
              >
                {agent.name}
              </span>
              {isWorking && st.task && (
                <span
                  className="text-[7px] whitespace-nowrap px-1 py-0 rounded mt-0.5 max-w-[80px] truncate"
                  style={{ color: '#94a3b8', backgroundColor: 'rgba(15,23,42,0.8)' }}
                >
                  {st.task}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
