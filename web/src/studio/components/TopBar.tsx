
import React from 'react';
import { useTheme } from '../StudioPage';

interface TopBarProps {
  labName: string;
  onExport: () => void;
  onDeploy: () => void;
  onExit: () => void;
}

const TopBar: React.FC<TopBarProps> = ({ labName, onExport, onDeploy, onExit }) => {
  const { theme, toggleTheme } = useTheme();

  return (
    <div className="h-14 bg-white/80 dark:bg-slate-900/80 backdrop-blur-xl border-b border-slate-200 dark:border-slate-800 flex items-center justify-between px-6 z-20 shadow-sm shrink-0">
      <div className="flex items-center gap-4">
        <button 
          onClick={onExit}
          className="w-8 h-8 flex items-center justify-center text-slate-400 dark:text-slate-500 hover:text-slate-900 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg transition-all"
          title="Back to Dashboard"
        >
          <i className="fa-solid fa-chevron-left"></i>
        </button>

        <div className="flex items-center gap-2 group cursor-default">
          <div className="relative">
             <div className="absolute inset-0 bg-blue-500 blur-md opacity-20 group-hover:opacity-40 transition-opacity"></div>
             <div className="relative w-9 h-9 bg-white dark:bg-slate-800 border border-blue-500/50 rounded-xl flex items-center justify-center shadow-sm group-hover:border-blue-400 transition-colors">
                <i className="fa-solid fa-bolt-lightning text-blue-600 dark:text-blue-400 text-lg group-hover:rotate-12 transition-transform"></i>
             </div>
          </div>
          <div className="flex flex-col leading-none ml-1">
            <span className="text-slate-900 dark:text-white font-black text-lg tracking-tighter">
              AURA
            </span>
            <span className="text-[9px] text-blue-600 dark:text-blue-500 font-bold tracking-[0.2em] uppercase">
              Visual Studio
            </span>
          </div>
        </div>
        
        <div className="h-8 w-px bg-slate-200 dark:bg-slate-800 mx-2"></div>
        
        <div className="flex items-center gap-2 px-3 py-1 bg-slate-100 dark:bg-slate-800/50 rounded-full border border-slate-200/50 dark:border-slate-700/50">
          <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse"></div>
          <span className="text-[11px] text-slate-500 dark:text-slate-400 font-medium uppercase tracking-tight">Lab:</span>
          <span className="text-xs font-semibold text-slate-700 dark:text-slate-200">{labName}</span>
        </div>
      </div>

      <div className="flex items-center gap-4">
        <button 
          onClick={toggleTheme}
          className="w-9 h-9 flex items-center justify-center bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 hover:text-blue-600 dark:hover:text-blue-400 rounded-xl transition-all border border-slate-200 dark:border-slate-700"
          title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
        >
          <i className={`fa-solid ${theme === 'dark' ? 'fa-sun' : 'fa-moon'}`}></i>
        </button>

        <button 
          onClick={onExport}
          className="flex items-center gap-2 px-3 py-1.5 bg-white dark:bg-slate-800 hover:bg-slate-50 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-200 text-xs font-semibold border border-slate-200 dark:border-slate-700 rounded-lg transition-all active:scale-95 shadow-sm"
        >
          <i className="fa-solid fa-file-code text-blue-600 dark:text-blue-400"></i>
          IAC EXPORT
        </button>
        <button 
          onClick={onDeploy}
          className="flex items-center gap-2 px-4 py-1.5 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white text-xs font-bold rounded-lg transition-all shadow-lg shadow-blue-900/20 active:scale-95 border border-blue-400/20"
        >
          <i className="fa-solid fa-rocket"></i>
          DEPLOY
        </button>

        <div className="h-8 w-px bg-slate-200 dark:bg-slate-800 mx-1"></div>

        <button 
          onClick={onExit}
          className="flex items-center gap-2 px-3 py-1.5 text-slate-500 hover:text-red-500 dark:text-slate-400 dark:hover:text-red-400 text-xs font-bold transition-all"
          title="Logout"
        >
          <i className="fa-solid fa-right-from-bracket"></i>
          LOGOUT
        </button>
      </div>
    </div>
  );
};

export default TopBar;
