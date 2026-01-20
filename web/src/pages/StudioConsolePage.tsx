import React from 'react';
import { Navigate, useParams } from 'react-router-dom';
import TerminalSession from '../studio/components/TerminalSession';
import '../studio/studio.css';
import 'xterm/css/xterm.css';

const StudioConsolePage: React.FC = () => {
  const { labId, nodeId } = useParams<{ labId: string; nodeId: string }>();
  const token = localStorage.getItem('token');

  if (!token) {
    return <Navigate to="/auth/login" replace />;
  }

  if (!labId || !nodeId) {
    return (
      <div className="min-h-screen bg-[#0b0f16] text-slate-300 flex items-center justify-center text-sm">
        Missing console parameters.
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#0b0f16] text-slate-200 flex flex-col">
      <header className="px-6 py-4 border-b border-slate-800 flex items-center justify-between">
        <div className="text-sm font-bold text-slate-100">
          Console: <span className="text-blue-400">{nodeId}</span>
        </div>
        <div className="text-[10px] text-slate-500 uppercase tracking-widest">Lab {labId}</div>
      </header>
      <div className="flex-1 p-4">
        <div className="h-full border border-slate-800 rounded-xl overflow-hidden">
          <TerminalSession labId={labId} nodeId={nodeId} isActive />
        </div>
      </div>
    </div>
  );
};

export default StudioConsolePage;
