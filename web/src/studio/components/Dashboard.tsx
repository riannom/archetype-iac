
import React, { useEffect, useRef, useState } from 'react';
import { useTheme, ThemeSelector } from '../../theme/index';
import { useUser } from '../../contexts/UserContext';
import { canViewInfrastructure } from '../../utils/permissions';
import SystemStatusStrip from './SystemStatusStrip';
import SystemLogsModal from './SystemLogsModal';
import { ArchetypeIcon } from '../../components/icons';
import { VersionBadge } from '../../components/VersionBadge';
import AdminMenuButton from '../../components/AdminMenuButton';
import type { SystemMetrics } from '../types';

interface LabSummary {
  id: string;
  name: string;
  created_at?: string;
}

interface LabStatus {
  running: number;
  total: number;
}

interface DashboardProps {
  labs: LabSummary[];
  labStatuses?: Record<string, LabStatus>;
  systemMetrics?: SystemMetrics | null;
  onSelect: (lab: LabSummary) => void;
  onDownload?: (lab: LabSummary) => void;
  onCreate: () => void;
  onDelete: (labId: string) => void;
  onRename?: (labId: string, newName: string) => void;
  onLogout: () => void;
}

const Dashboard: React.FC<DashboardProps> = ({
  labs,
  labStatuses,
  systemMetrics,
  onSelect,
  onDownload,
  onCreate,
  onDelete,
  onRename,
  onLogout,
}) => {
  const { effectiveMode, toggleMode } = useTheme();
  const { user } = useUser();
  const [showThemeSelector, setShowThemeSelector] = useState(false);
  const [showSystemLogs, setShowSystemLogs] = useState(false);
  const [editingLabId, setEditingLabId] = useState<string | null>(null);
  const [editName, setEditName] = useState('');
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const deleteTimeoutRef = useRef<number | null>(null);
  const showInfra = canViewInfrastructure(user ?? null);

  useEffect(() => {
    return () => {
      if (deleteTimeoutRef.current) {
        window.clearTimeout(deleteTimeoutRef.current);
      }
    };
  }, []);

  const handleStartEdit = (lab: LabSummary, e: React.MouseEvent) => {
    e.stopPropagation();
    if (onRename) {
      setEditingLabId(lab.id);
      setEditName(lab.name);
    }
  };

  const handleSaveEdit = (labId: string) => {
    const trimmed = editName.trim();
    const lab = labs.find(l => l.id === labId);
    if (trimmed && lab && trimmed !== lab.name && onRename) {
      onRename(labId, trimmed);
    }
    setEditingLabId(null);
    setEditName('');
  };

  const handleKeyDown = (e: React.KeyboardEvent, labId: string) => {
    if (e.key === 'Enter') {
      handleSaveEdit(labId);
    } else if (e.key === 'Escape') {
      setEditingLabId(null);
      setEditName('');
    }
  };

  const handleDeleteRequest = (labId: string) => {
    if (pendingDeleteId === labId) {
      setPendingDeleteId(null);
      if (deleteTimeoutRef.current) {
        window.clearTimeout(deleteTimeoutRef.current);
      }
      onDelete(labId);
      return;
    }

    setPendingDeleteId(labId);
    if (deleteTimeoutRef.current) {
      window.clearTimeout(deleteTimeoutRef.current);
    }
    deleteTimeoutRef.current = window.setTimeout(() => {
      setPendingDeleteId(null);
    }, 3000);
  };

  const handleDeleteCancel = () => {
    setPendingDeleteId(null);
    if (deleteTimeoutRef.current) {
      window.clearTimeout(deleteTimeoutRef.current);
    }
  };

  return (
    <>
    <div className="min-h-screen bg-stone-50/72 dark:bg-stone-900/72 backdrop-blur-[1px] flex flex-col overflow-hidden">
      <header className="h-20 border-b border-stone-200 dark:border-stone-800 bg-white/30 dark:bg-stone-900/30 flex items-center justify-between px-10">
        <div className="flex items-center gap-4">
          <ArchetypeIcon size={40} className="text-sage-600 dark:text-sage-400" />
          <div>
            <h1 className="text-xl font-black text-stone-900 dark:text-white tracking-tight">ARCHETYPE</h1>
            <p className="text-[10px] text-sage-600 dark:text-sage-500 font-bold uppercase tracking-widest">Network Studio</p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {showInfra && <AdminMenuButton />}

          {showInfra && (
            <>
              <button
                onClick={() => setShowSystemLogs(true)}
                className="flex items-center gap-2 px-3 py-2 glass-control text-stone-600 dark:text-stone-300 rounded-lg transition-all"
                title="View System Logs"
              >
                <i className="fa-solid fa-file-lines text-xs"></i>
                <span className="text-[10px] font-bold uppercase">Logs</span>
              </button>
            </>
          )}

          <button
            onClick={() => setShowThemeSelector(true)}
            className="w-9 h-9 flex items-center justify-center glass-control text-stone-600 dark:text-stone-400 hover:text-sage-600 dark:hover:text-sage-400 rounded-lg transition-all"
            title="Theme Settings"
          >
            <i className="fa-solid fa-palette text-sm"></i>
          </button>

          <button
            onClick={toggleMode}
            className="w-9 h-9 flex items-center justify-center glass-control text-stone-600 dark:text-stone-400 hover:text-sage-600 dark:hover:text-sage-400 rounded-lg transition-all"
            title={`Switch to ${effectiveMode === 'dark' ? 'light' : 'dark'} mode`}
          >
            <i className={`fa-solid ${effectiveMode === 'dark' ? 'fa-sun' : 'fa-moon'} text-sm`}></i>
          </button>

          <button
            onClick={onLogout}
            className="flex items-center gap-2 px-3 py-2 text-stone-500 hover:text-red-500 dark:text-stone-400 dark:hover:text-red-400 text-xs font-bold transition-all"
            title="Logout"
          >
            <i className="fa-solid fa-right-from-bracket text-xs"></i>
            <span className="text-[10px] font-bold uppercase">Logout</span>
          </button>

        </div>
      </header>

      {showInfra && <SystemStatusStrip metrics={systemMetrics || null} />}

      <main className="flex-1 overflow-y-auto p-10 custom-scrollbar">
        <div className="max-w-6xl mx-auto">
          <div className="flex justify-between items-center mb-8">
            <div>
              <h2 className="text-2xl font-bold text-stone-900 dark:text-white">Your Workspace</h2>
              <p className="text-stone-500 text-sm mt-1">Manage, design and deploy your virtual network environments.</p>
            </div>
            <button
              onClick={onCreate}
              className="bg-sage-600 hover:bg-sage-500 text-white px-6 py-2.5 rounded-xl font-bold text-sm shadow-lg shadow-sage-900/20 transition-all flex items-center gap-2 active:scale-95"
            >
              <i className="fa-solid fa-plus"></i>
              Create New Lab
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {labs.length > 0 ? labs.map((lab) => {
              const status = labStatuses?.[lab.id];
              const isRunning = status && status.running > 0;
              const isAllRunning = status && status.running === status.total && status.total > 0;
              const statusDotColor = isAllRunning ? 'bg-green-500' : isRunning ? 'bg-amber-500' : 'bg-stone-400 dark:bg-stone-600';

              return (
              <div
                key={lab.id}
                className="group relative glass-surface border rounded-2xl p-6 hover:border-sage-500/50 hover:shadow-2xl hover:shadow-sage-900/10 transition-all cursor-default overflow-hidden"
              >
                <div className="absolute top-0 right-0 p-4 opacity-0 group-hover:opacity-100 transition-opacity">
                  {pendingDeleteId === lab.id ? (
                    <div className="flex items-center gap-1">
                      <button
                        onClick={(e) => { e.stopPropagation(); handleDeleteRequest(lab.id); }}
                        className="w-8 h-8 rounded-lg bg-red-500 text-white hover:bg-red-600 transition-all border border-red-500/20"
                        title="Confirm delete"
                      >
                        <i className="fa-solid fa-check text-xs"></i>
                      </button>
                      <button
                        onClick={(e) => { e.stopPropagation(); handleDeleteCancel(); }}
                        className="w-8 h-8 rounded-lg glass-control text-stone-500 transition-all"
                        title="Cancel"
                      >
                        <i className="fa-solid fa-xmark text-xs"></i>
                      </button>
                    </div>
                  ) : (
                    <button
                      onClick={(e) => { e.stopPropagation(); handleDeleteRequest(lab.id); }}
                      className="w-8 h-8 rounded-lg bg-red-500/10 text-red-500 hover:bg-red-500 hover:text-white transition-all border border-red-500/20"
                      title="Delete lab"
                    >
                      <i className="fa-solid fa-trash-can text-xs"></i>
                    </button>
                  )}
                </div>

                <div className="w-12 h-12 bg-stone-100 dark:bg-stone-800 rounded-xl flex items-center justify-center mb-4 text-stone-500 dark:text-stone-400 group-hover:bg-sage-600 group-hover:text-white transition-all border border-stone-200 dark:border-stone-700">
                  <i className="fa-solid fa-diagram-project"></i>
                </div>

                {editingLabId === lab.id ? (
                  <input
                    type="text"
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    onBlur={() => handleSaveEdit(lab.id)}
                    onKeyDown={(e) => handleKeyDown(e, lab.id)}
                    onClick={(e) => e.stopPropagation()}
                    autoFocus
                    className="text-lg font-bold text-stone-900 dark:text-white mb-1 bg-transparent border-b-2 border-sage-500 outline-none w-full"
                  />
                ) : (
                  <h3
                    onClick={(e) => handleStartEdit(lab, e)}
                    className={`text-lg font-bold text-stone-900 dark:text-white mb-1 group-hover:text-sage-600 dark:group-hover:text-sage-400 transition-colors ${onRename ? 'cursor-text hover:bg-stone-100 dark:hover:bg-stone-800 -mx-1 px-1 rounded' : ''}`}
                    title={onRename ? "Click to rename" : undefined}
                  >
                    {lab.name}
                  </h3>
                )}
                <div className="flex items-center gap-4 text-[10px] font-bold text-stone-500 uppercase tracking-wider mb-3">
                   <span className="flex items-center gap-1.5"><i className="fa-solid fa-server"></i> Lab</span>
                   <span className="flex items-center gap-1.5"><i className="fa-solid fa-calendar"></i> {lab.created_at ? new Date(lab.created_at).toLocaleDateString() : 'New'}</span>
                </div>

                {status && status.total > 0 && (
                  <div className="flex items-center gap-2 mb-4">
                    <div className={`w-2 h-2 rounded-full ${statusDotColor} ${isAllRunning ? 'animate-pulse' : ''}`}></div>
                    <span className="text-xs text-stone-600 dark:text-stone-400">
                      <span className="font-bold">{status.running}</span>
                      <span className="text-stone-400 dark:text-stone-500">/{status.total}</span>
                      <span className="ml-1 text-stone-500 dark:text-stone-500">nodes running</span>
                    </span>
                  </div>
                )}

                <div className="flex gap-2">
                  <button
                    onClick={() => onSelect(lab)}
                    className="flex-1 py-2 glass-control text-stone-700 dark:text-stone-200 text-xs font-bold rounded-lg border transition-all"
                  >
                    Open Designer
                  </button>
                  <button
                    onClick={() => onDownload?.(lab)}
                    className="w-10 py-2 glass-control text-stone-700 dark:text-stone-200 text-xs font-bold rounded-lg border transition-all flex items-center justify-center"
                    title="Download lab bundle"
                  >
                    <i className="fa-solid fa-download"></i>
                  </button>
                </div>
              </div>
              );
            }) : (
              <div className="col-span-full py-20 bg-stone-100/50 dark:bg-stone-900/30 border-2 border-dashed border-stone-300 dark:border-stone-800 rounded-3xl flex flex-col items-center justify-center text-stone-500 dark:text-stone-600">
                 <i className="fa-solid fa-folder-open text-5xl mb-4 opacity-10"></i>
                 <h3 className="text-lg font-bold text-stone-500 dark:text-stone-400">Empty Workspace</h3>
                 <p className="text-sm max-w-xs text-center mt-1">Start your first journey by clicking 'Create New Lab' above.</p>
              </div>
            )}
          </div>
        </div>
      </main>

      <footer className="h-10 border-t border-stone-200 dark:border-stone-900 glass-surface flex items-center px-10 justify-between text-[10px] text-stone-500 dark:text-stone-600 font-medium">
        <span>© 2026 Archetype Network Studio</span>
        <VersionBadge />
      </footer>
    </div>

    <ThemeSelector
      isOpen={showThemeSelector}
      onClose={() => setShowThemeSelector(false)}
    />

    <SystemLogsModal
      isOpen={showSystemLogs}
      onClose={() => setShowSystemLogs(false)}
    />
    </>
  );
};

export default Dashboard;
