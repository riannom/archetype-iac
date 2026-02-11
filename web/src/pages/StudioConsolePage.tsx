import React from 'react';
import { Navigate, useParams } from 'react-router-dom';
import TerminalSession from '../studio/components/TerminalSession';
import '../studio/studio.css';
import 'xterm/css/xterm.css';

const StudioConsolePage: React.FC = () => {
  const { labId, nodeId } = useParams<{ labId: string; nodeId: string }>();
  const token = localStorage.getItem('token');

  if (!token) {
    return <Navigate to="/" replace />;
  }

  if (!labId || !nodeId) {
    return (
      <div className="min-h-screen bg-stone-50/72 dark:bg-stone-900/72 backdrop-blur-[1px] text-stone-600 dark:text-stone-300 flex items-center justify-center text-sm">
        Missing console parameters.
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-stone-50/72 dark:bg-stone-900/72 backdrop-blur-[1px] text-stone-700 dark:text-stone-200 flex flex-col">
      <header className="px-6 py-4 border-b border-stone-200 dark:border-stone-800 bg-white/30 dark:bg-stone-900/30 flex items-center justify-between">
        <div className="text-sm font-bold text-stone-900 dark:text-stone-100">
          Console: <span className="text-sage-400">{nodeId}</span>
        </div>
        <div className="text-[10px] text-stone-500 uppercase tracking-widest">Lab {labId}</div>
      </header>
      <div className="flex-1 p-4">
        <div className="h-full glass-surface border border-stone-200 dark:border-stone-800 rounded-xl overflow-hidden">
          <TerminalSession labId={labId} nodeId={nodeId} isActive />
        </div>
      </div>
    </div>
  );
};

export default StudioConsolePage;
