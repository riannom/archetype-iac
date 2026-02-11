
import React, { useEffect, useState } from 'react';
import { ArchetypeIcon } from '../../components/icons';
import { useTheme } from '../../theme/index';
import { getBackgroundById } from '../../theme/backgrounds';
import { getBuiltInTheme } from '../../theme/presets';
import { AnimatedBackground } from '../../components/backgrounds/AnimatedBackground';
import { getLoginDefaults, getVersionInfo, type LoginDefaults } from '../../api';

interface AuthProps {
  onLogin: (username: string, password?: string) => void | Promise<void>;
  error?: string | null;
  loading?: boolean;
}

const Auth: React.FC<AuthProps> = ({ onLogin, error, loading = false }) => {
  const { effectiveMode, toggleMode, setMode } = useTheme();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [displayVersion, setDisplayVersion] = useState(
    typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : '0.0.0'
  );
  const [loginDefaults, setLoginDefaults] = useState<LoginDefaults>({
    dark_theme_id: 'midnight',
    dark_background_id: 'floating-lanterns',
    dark_background_opacity: 50,
    light_theme_id: 'sakura-sumie',
    light_background_id: 'sakura-redux',
    light_background_opacity: 100,
  });
  const isDarkMode = effectiveMode === 'dark';
  const activeThemeId = isDarkMode ? loginDefaults.dark_theme_id : loginDefaults.light_theme_id;
  const activeBackgroundId = isDarkMode
    ? loginDefaults.dark_background_id
    : loginDefaults.light_background_id;
  const activeBackgroundOpacity = isDarkMode
    ? loginDefaults.dark_background_opacity
    : loginDefaults.light_background_opacity;
  const activeTheme = getBuiltInTheme(activeThemeId) || getBuiltInTheme(isDarkMode ? 'midnight' : 'sakura-sumie');
  const modeColors = isDarkMode ? activeTheme?.dark : activeTheme?.light;

  useEffect(() => {
    setMode('system');
  }, [setMode]);

  useEffect(() => {
    let cancelled = false;
    const loadLoginDefaults = async () => {
      try {
        const defaults = await getLoginDefaults();
        if (!cancelled) {
          setLoginDefaults(defaults);
        }
      } catch {
        // Keep built-in defaults
      }
    };

    loadLoginDefaults();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const loadInstalledVersion = async () => {
      try {
        const info = await getVersionInfo();
        if (!cancelled && info?.version) {
          setDisplayVersion(info.version);
        }
      } catch {
        // Keep build-time fallback
      }
    };

    loadInstalledVersion();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (username.trim()) {
      onLogin(username, password);
    }
  };

  return (
    <div
      className={`min-h-screen w-full flex flex-col items-center justify-center p-6 selection:bg-accent-500/30 transition-colors duration-700 ${
        isDarkMode ? 'text-white' : 'text-stone-800'
      }`}
    >
      <style>{`
        @keyframes auth-fade-up {
          from { opacity: 0; transform: translateY(16px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes auth-float {
          0%, 100% { transform: translateY(0px); }
          50% { transform: translateY(-6px); }
        }
      `}</style>

      <div className="fixed inset-0 z-0 overflow-hidden pointer-events-none transition-colors duration-700">
        <div
          className="absolute inset-0 transition-all duration-700"
          style={{
            background: `linear-gradient(to bottom right, ${modeColors?.bgBase || '#111827'}, ${modeColors?.bgSurface || '#1f2937'})`,
          }}
        />
        <div
          className={`absolute inset-0 transition-opacity duration-700 ${isDarkMode ? 'opacity-[0.04]' : 'opacity-[0.08]'}`}
          style={{
            backgroundImage:
              'linear-gradient(rgb(var(--ui-glass-border-rgb) / 0.8) 1px, transparent 1px), linear-gradient(90deg, rgb(var(--ui-glass-border-rgb) / 0.8) 1px, transparent 1px)',
            backgroundSize: '40px 40px',
          }}
        />
        <div
          className={`absolute top-[-24%] left-[-12%] w-[58%] h-[58%] blur-[140px] rounded-full animate-pulse transition-all duration-1000 ${
            isDarkMode ? 'opacity-20' : 'opacity-30'
          }`}
          style={{ backgroundColor: activeTheme?.colors.accent[500] || '#6366F1' }}
        />
        <div
          className={`absolute bottom-[-24%] right-[-12%] w-[58%] h-[58%] blur-[140px] rounded-full animate-pulse transition-all duration-1000 ${
            isDarkMode ? 'opacity-15' : 'opacity-20'
          }`}
          style={{ animationDelay: '3s', backgroundColor: activeTheme?.colors.accent[300] || '#f43f5e' }}
        />
      </div>
      <AnimatedBackground
        pattern={getBackgroundById(activeBackgroundId) ? activeBackgroundId : 'minimal'}
        darkMode={isDarkMode}
        opacity={Math.max(0, Math.min(100, activeBackgroundOpacity))}
      />

      <button
        onClick={toggleMode}
        className={`fixed top-8 right-8 z-20 p-2.5 rounded-full border transition-all duration-300 hover:scale-110 active:scale-95 backdrop-blur-md shadow-sm ${
          isDarkMode
            ? 'border-white/10 bg-white/5 text-stone-200 hover:bg-white/10'
            : 'border-stone-300 bg-white/40 text-stone-700 hover:bg-white/60'
        }`}
        title={`Switch to ${isDarkMode ? 'light' : 'dark'} mode`}
        aria-label={`Switch to ${isDarkMode ? 'light' : 'dark'} mode`}
      >
        <i className={`fa-solid ${isDarkMode ? 'fa-sun' : 'fa-moon'} text-sm`} />
      </button>

      <div
        className="w-full max-w-sm relative z-10"
        style={{ animation: 'auth-fade-up 0.7s cubic-bezier(0.16, 1, 0.3, 1) both' }}
      >
        <div className="flex flex-col items-center mb-11">
          <div style={{ animation: 'auth-float 5s ease-in-out infinite' }}>
            <ArchetypeIcon
              size={80}
              className="mb-5"
              color={activeTheme?.colors.accent[500] || 'currentColor'}
            />
          </div>
          <h1 className={`text-xl font-light tracking-[0.24em] uppercase ${isDarkMode ? 'text-white/90' : 'text-stone-700'}`}>
            ARCHETYPE
          </h1>
        </div>

        <div
          className={`p-1 rounded-2xl transition-all duration-700 ${
            isDarkMode
              ? 'bg-transparent'
              : 'bg-white/24 backdrop-blur-sm shadow-xl shadow-stone-400/10 border border-white/20'
          }`}
        >
          <form onSubmit={handleSubmit} className="space-y-4 p-2">
            <div className="group relative">
              <i className="fa-solid fa-user absolute left-4 top-1/2 -translate-y-1/2 text-stone-400 text-xs" />
              <input
                type="text"
                placeholder="Username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className={`w-full border rounded-xl pl-11 pr-4 py-3.5 transition-all text-sm font-light focus:outline-none focus:border-accent-500/50 focus:ring-4 focus:ring-accent-500/10 ${
                  isDarkMode
                    ? 'bg-white/[0.04] border-white/10 text-white placeholder-stone-500'
                    : 'bg-white/65 border-stone-300 text-stone-900 placeholder-stone-400'
                }`}
                style={{
                  borderColor: isDarkMode ? undefined : modeColors?.border,
                }}
              />
            </div>

            <div className="group relative">
              <i className="fa-solid fa-lock absolute left-4 top-1/2 -translate-y-1/2 text-stone-400 text-xs" />
              <input
                type="password"
                placeholder="••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className={`w-full border rounded-xl pl-11 pr-4 py-3.5 transition-all text-sm font-light focus:outline-none focus:border-accent-500/50 focus:ring-4 focus:ring-accent-500/10 ${
                  isDarkMode
                    ? 'bg-white/[0.04] border-white/10 text-white placeholder-stone-500'
                    : 'bg-white/65 border-stone-300 text-stone-900 placeholder-stone-400'
                }`}
                style={{
                  borderColor: isDarkMode ? undefined : modeColors?.border,
                }}
              />
            </div>

            {error && (
              <p className="text-xs text-red-500 dark:text-red-400 text-center">{error}</p>
            )}

            <button
              type="submit"
              disabled={loading}
              className={`w-full py-4 rounded-xl text-xs font-semibold tracking-[0.18em] uppercase transition-all flex items-center justify-center shadow-lg active:scale-[0.98] ${
                loading
                  ? 'bg-stone-800 text-stone-500 cursor-not-allowed shadow-none'
                  : 'text-white'
              }`}
              style={
                loading
                  ? undefined
                  : {
                      backgroundColor: activeTheme?.colors.accent[600] || '#4f46e5',
                      boxShadow: `0 10px 24px ${(activeTheme?.colors.accent[700] || '#4338ca')}55`,
                    }
              }
            >
              {loading ? 'Signing in...' : 'Sign In to Archetype'}
            </button>
          </form>
        </div>

        <p
          className={`text-center mt-10 text-[10px] font-medium uppercase tracking-[0.2em] transition-opacity ${
            isDarkMode ? 'text-stone-400 opacity-50 hover:opacity-100' : 'text-stone-500 opacity-70 hover:opacity-100'
          }`}
        >
          v{displayVersion}
        </p>
      </div>
    </div>
  );
};

export default Auth;
