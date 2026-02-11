
import React, { useState } from 'react';
import { ArchetypeIcon } from '../../components/icons';

interface AuthProps {
  onLogin: (username: string, password?: string) => void | Promise<void>;
  error?: string | null;
  loading?: boolean;
}

// Abstract network topology coordinates - positioned around the screen
// periphery to frame the centered login card, evoking a topology graph
const NODES: [number, number][] = [
  // Top edge
  [100, 55], [310, 85], [530, 30], [710, 75], [910, 40], [1100, 70],
  // Left edge
  [45, 225], [75, 425], [50, 635],
  // Right edge
  [1155, 245], [1125, 435], [1150, 645],
  // Bottom edge
  [125, 725], [345, 755], [590, 775], [835, 740], [1075, 715],
];

const EDGES: [number, number][] = [
  // Top chain
  [0, 1], [1, 2], [2, 3], [3, 4], [4, 5],
  // Left chain
  [0, 6], [6, 7], [7, 8],
  // Right chain
  [5, 9], [9, 10], [10, 11],
  // Bottom chain
  [8, 12], [12, 13], [13, 14], [14, 15], [15, 16], [16, 11],
  // Cross-connections (diagonal bridges)
  [1, 7], [4, 10], [7, 13], [10, 15],
];

const Auth: React.FC<AuthProps> = ({ onLogin, error, loading = false }) => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (username.trim()) {
      onLogin(username, password);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-6 relative overflow-hidden">
      {/* Entrance animation */}
      <style>{`
        @keyframes auth-fade-up {
          from { opacity: 0; transform: translateY(16px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>

      {/* Abstract network topology — decorative SVG mesh */}
      <div className="absolute inset-0 pointer-events-none" aria-hidden="true">
        <svg
          className="w-full h-full"
          viewBox="0 0 1200 800"
          preserveAspectRatio="xMidYMid slice"
          fill="none"
        >
          {/* Edge lines */}
          <g stroke="var(--color-accent-500, #65A30D)" strokeWidth="0.5" opacity="0.07">
            {EDGES.map(([from, to], i) => (
              <line
                key={`e${i}`}
                x1={NODES[from][0]}
                y1={NODES[from][1]}
                x2={NODES[to][0]}
                y2={NODES[to][1]}
              />
            ))}
          </g>

          {/* Topology nodes with gentle pulse */}
          <g fill="var(--color-accent-500, #65A30D)">
            {NODES.map(([x, y], i) => (
              <circle key={`n${i}`} cx={x} cy={y} r={2.5} opacity="0.1">
                <animate
                  attributeName="opacity"
                  values="0.06;0.22;0.06"
                  dur={`${3.5 + (i % 5) * 0.7}s`}
                  repeatCount="indefinite"
                  begin={`${i * 0.25}s`}
                />
              </circle>
            ))}
          </g>

          {/* Small diamond accents at select intersections — echoes the logo */}
          {[[2, 30], [14, 775], [6, 225], [9, 245]].map(([nodeIdx, _], i) => {
            const [cx, cy] = NODES[nodeIdx as number];
            return (
              <g key={`d${i}`} opacity="0.06" fill="none" stroke="var(--color-accent-500, #65A30D)" strokeWidth="0.6">
                <polygon points={`${cx},${cy - 8} ${cx + 6},${cy} ${cx},${cy + 8} ${cx - 6},${cy}`} />
              </g>
            );
          })}
        </svg>
      </div>

      {/* Login content */}
      <div
        className="w-full max-w-sm relative z-10"
        style={{ animation: 'auth-fade-up 0.7s cubic-bezier(0.16, 1, 0.3, 1) both' }}
      >
        {/* Logo + branding */}
        <div className="flex flex-col items-center mb-10">
          <ArchetypeIcon size={56} className="text-sage-600 dark:text-sage-400 mb-4" />
          <h1 className="text-4xl font-black text-stone-900 dark:text-white tracking-tighter uppercase">
            ARCHETYPE
          </h1>
          <p className="text-sage-600 dark:text-sage-500 text-[10px] font-bold tracking-[0.3em] uppercase mt-1">
            Network Studio
          </p>
        </div>

        {/* Glass form card */}
        <div className="glass-surface-elevated p-8 rounded-2xl">
          <form onSubmit={handleSubmit} className="space-y-5">
            <div className="space-y-1.5">
              <label className="text-[10px] font-bold text-stone-400 dark:text-stone-500 uppercase tracking-widest ml-1">
                Username
              </label>
              <div className="relative">
                <i className="fa-solid fa-user absolute left-4 top-1/2 -translate-y-1/2 text-stone-400 text-xs" />
                <input
                  type="text"
                  placeholder="Username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  className="w-full glass-control border rounded-xl py-3 pl-11 pr-4 text-sm text-stone-900 dark:text-white focus:outline-none focus:border-sage-500 transition-all"
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="text-[10px] font-bold text-stone-400 dark:text-stone-500 uppercase tracking-widest ml-1">
                Password
              </label>
              <div className="relative">
                <i className="fa-solid fa-lock absolute left-4 top-1/2 -translate-y-1/2 text-stone-400 text-xs" />
                <input
                  type="password"
                  placeholder="••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full glass-control border rounded-xl py-3 pl-11 pr-4 text-sm text-stone-900 dark:text-white focus:outline-none focus:border-sage-500 transition-all"
                />
              </div>
            </div>

            {error && (
              <p className="text-xs text-red-500 dark:text-red-400 text-center">{error}</p>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full py-3 bg-sage-600 hover:bg-sage-500 disabled:opacity-60 text-white font-bold rounded-xl shadow-lg shadow-sage-900/20 transition-all active:scale-[0.98]"
            >
              {loading ? 'Signing in...' : 'Sign In to Archetype'}
            </button>
          </form>
        </div>

        <p className="text-center mt-8 text-stone-400 dark:text-stone-500 text-[11px] font-bold uppercase tracking-widest">
          v0.4.0
        </p>
      </div>
    </div>
  );
};

export default Auth;
