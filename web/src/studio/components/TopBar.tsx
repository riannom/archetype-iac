import React, { useState } from 'react';
import { useTheme, ThemeSelector } from '../../theme/index';

interface TopBarProps {
  labName: string;
  onExport: () => void;
  onDeploy: () => void;
  onExit: () => void;
}

const TopBar: React.FC<TopBarProps> = ({ labName, onExport, onDeploy, onExit }) => {
  const { effectiveMode, toggleMode } = useTheme();
  const [showThemeSelector, setShowThemeSelector] = useState(false);

  return (
    <>
      <div className="h-14 bg-white/80 dark:bg-stone-900/80 backdrop-blur-xl border-b border-stone-200 dark:border-stone-800 flex items-center justify-between px-6 z-20 shadow-sm shrink-0">
        <div className="flex items-center gap-4">
          <button
            onClick={onExit}
            className="w-8 h-8 flex items-center justify-center text-stone-400 dark:text-stone-500 hover:text-stone-900 dark:hover:text-white hover:bg-stone-100 dark:hover:bg-stone-800 rounded-lg transition-all"
            title="Back to Dashboard"
          >
            <i className="fa-solid fa-chevron-left"></i>
          </button>

          <div className="flex items-center gap-2 group cursor-default">
            <div className="relative">
               <div className="absolute inset-0 bg-sage-500 blur-md opacity-20 group-hover:opacity-40 transition-opacity"></div>
               <div className="relative w-9 h-9 bg-white dark:bg-stone-800 border border-sage-500/50 rounded-xl flex items-center justify-center shadow-sm group-hover:border-sage-400 transition-colors">
                  <i className="fa-solid fa-bolt-lightning text-sage-600 dark:text-sage-400 text-lg group-hover:rotate-12 transition-transform"></i>
               </div>
            </div>
            <div className="flex flex-col leading-none ml-1">
              <span className="text-stone-900 dark:text-white font-black text-lg tracking-tighter">
                AURA
              </span>
              <span className="text-[9px] text-sage-600 dark:text-sage-500 font-bold tracking-[0.2em] uppercase">
                Visual Studio
              </span>
            </div>
          </div>

          <div className="h-8 w-px bg-stone-200 dark:bg-stone-800 mx-2"></div>

          <div className="flex items-center gap-2 px-3 py-1 bg-stone-100 dark:bg-stone-800/50 rounded-full border border-stone-200/50 dark:border-stone-700/50">
            <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse"></div>
            <span className="text-[11px] text-stone-500 dark:text-stone-400 font-medium uppercase tracking-tight">Lab:</span>
            <span className="text-xs font-semibold text-stone-700 dark:text-stone-200">{labName}</span>
          </div>
        </div>

        <div className="flex items-center gap-4">
          <button
            onClick={() => setShowThemeSelector(true)}
            className="w-9 h-9 flex items-center justify-center bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-400 hover:text-sage-600 dark:hover:text-sage-400 rounded-xl transition-all border border-stone-200 dark:border-stone-700"
            title="Theme Settings"
          >
            <i className="fa-solid fa-palette"></i>
          </button>

          <button
            onClick={toggleMode}
            className="w-9 h-9 flex items-center justify-center bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-400 hover:text-sage-600 dark:hover:text-sage-400 rounded-xl transition-all border border-stone-200 dark:border-stone-700"
            title={`Switch to ${effectiveMode === 'dark' ? 'light' : 'dark'} mode`}
          >
            <i className={`fa-solid ${effectiveMode === 'dark' ? 'fa-sun' : 'fa-moon'}`}></i>
          </button>

          <button
            onClick={onExport}
            className="flex items-center gap-2 px-3 py-1.5 bg-white dark:bg-stone-800 hover:bg-stone-50 dark:hover:bg-stone-700 text-stone-700 dark:text-stone-200 text-xs font-semibold border border-stone-200 dark:border-stone-700 rounded-lg transition-all active:scale-95 shadow-sm"
          >
            <i className="fa-solid fa-file-code text-sage-600 dark:text-sage-400"></i>
            IAC EXPORT
          </button>
          <button
            onClick={onDeploy}
            className="flex items-center gap-2 px-4 py-1.5 bg-sage-600 hover:bg-sage-500 text-white text-xs font-bold rounded-lg transition-all shadow-lg shadow-sage-900/20 active:scale-95 border border-sage-400/20"
          >
            <i className="fa-solid fa-rocket"></i>
            DEPLOY
          </button>

          <div className="h-8 w-px bg-stone-200 dark:bg-stone-800 mx-1"></div>

          <button
            onClick={onExit}
            className="flex items-center gap-2 px-3 py-1.5 text-stone-500 hover:text-red-500 dark:text-stone-400 dark:hover:text-red-400 text-xs font-bold transition-all"
            title="Logout"
          >
            <i className="fa-solid fa-right-from-bracket"></i>
            LOGOUT
          </button>
        </div>
      </div>

      <ThemeSelector
        isOpen={showThemeSelector}
        onClose={() => setShowThemeSelector(false)}
      />
    </>
  );
};

export default TopBar;
