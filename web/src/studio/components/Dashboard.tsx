
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTheme, ThemeSelector } from '../../theme/index';
import { useUser } from '../../contexts/UserContext';
import SystemStatusStrip from './SystemStatusStrip';

interface LabSummary {
  id: string;
  name: string;
  created_at?: string;
}

interface LabStatus {
  running: number;
  total: number;
}

interface SystemMetrics {
  agents: { online: number; total: number };
  containers: { running: number; total: number };
  cpu_percent: number;
  memory_percent: number;
  storage?: {
    used_gb: number;
    total_gb: number;
    percent: number;
  };
  labs_running: number;
  labs_total: number;
  per_host?: {
    id: string;
    name: string;
    cpu_percent: number;
    memory_percent: number;
    storage_percent: number;
    storage_used_gb: number;
    storage_total_gb: number;
    containers_running: number;
  }[];
  is_multi_host?: boolean;
}

interface DashboardProps {
  labs: LabSummary[];
  labStatuses?: Record<string, LabStatus>;
  systemMetrics?: SystemMetrics | null;
  onSelect: (lab: LabSummary) => void;
  onCreate: () => void;
  onDelete: (labId: string) => void;
  onRefresh: () => void;
}

const Dashboard: React.FC<DashboardProps> = ({ labs, labStatuses, systemMetrics, onSelect, onCreate, onDelete, onRefresh }) => {
  const { effectiveMode, toggleMode } = useTheme();
  const { user } = useUser();
  const navigate = useNavigate();
  const [showThemeSelector, setShowThemeSelector] = useState(false);
  const isAdmin = user?.is_admin ?? false;

  return (
    <>
    <div className="min-h-screen bg-stone-50 dark:bg-stone-900 flex flex-col overflow-hidden">
      <header className="h-20 border-b border-stone-200 dark:border-stone-800 bg-white/30 dark:bg-stone-900/30 flex items-center justify-between px-10">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-sage-600 rounded-xl flex items-center justify-center shadow-lg shadow-sage-900/20 border border-sage-400/30">
            <i className="fa-solid fa-bolt-lightning text-white"></i>
          </div>
          <div>
            <h1 className="text-xl font-black text-stone-900 dark:text-white tracking-tight">AURA</h1>
            <p className="text-[10px] text-sage-600 dark:text-sage-500 font-bold uppercase tracking-widest">Visual Studio</p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {isAdmin && (
            <button
              onClick={() => navigate('/hosts')}
              className="flex items-center gap-2 px-3 py-2 bg-stone-100 dark:bg-stone-800 hover:bg-stone-200 dark:hover:bg-stone-700 text-stone-600 dark:text-stone-300 border border-stone-300 dark:border-stone-700 rounded-lg transition-all"
              title="Manage Hosts"
            >
              <i className="fa-solid fa-server text-xs"></i>
              <span className="text-[10px] font-bold uppercase">Hosts</span>
            </button>
          )}

          <button
            onClick={() => navigate('/images')}
            className="flex items-center gap-2 px-3 py-2 bg-stone-100 dark:bg-stone-800 hover:bg-stone-200 dark:hover:bg-stone-700 text-stone-600 dark:text-stone-300 border border-stone-300 dark:border-stone-700 rounded-lg transition-all"
            title="Manage Images"
          >
            <i className="fa-solid fa-hard-drive text-xs"></i>
            <span className="text-[10px] font-bold uppercase">Images</span>
          </button>

          <button
            onClick={() => setShowThemeSelector(true)}
            className="w-9 h-9 flex items-center justify-center bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-400 hover:text-sage-600 dark:hover:text-sage-400 rounded-lg transition-all border border-stone-300 dark:border-stone-700"
            title="Theme Settings"
          >
            <i className="fa-solid fa-palette text-sm"></i>
          </button>

          <button
            onClick={toggleMode}
            className="w-9 h-9 flex items-center justify-center bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-400 hover:text-sage-600 dark:hover:text-sage-400 rounded-lg transition-all border border-stone-300 dark:border-stone-700"
            title={`Switch to ${effectiveMode === 'dark' ? 'light' : 'dark'} mode`}
          >
            <i className={`fa-solid ${effectiveMode === 'dark' ? 'fa-sun' : 'fa-moon'} text-sm`}></i>
          </button>

          <button
            onClick={onRefresh}
            className="flex items-center gap-2 px-3 py-2 bg-stone-100 dark:bg-stone-800 hover:bg-stone-200 dark:hover:bg-stone-700 text-stone-600 dark:text-stone-300 border border-stone-300 dark:border-stone-700 rounded-lg transition-all"
          >
            <i className="fa-solid fa-rotate text-xs"></i>
            <span className="text-[10px] font-bold uppercase">Refresh</span>
          </button>
        </div>
      </header>

      {isAdmin && <SystemStatusStrip metrics={systemMetrics || null} />}

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
                className="group relative bg-white dark:bg-stone-900 border border-stone-200 dark:border-stone-800 rounded-2xl p-6 hover:border-sage-500/50 hover:shadow-2xl hover:shadow-sage-900/10 transition-all cursor-default overflow-hidden"
              >
                <div className="absolute top-0 right-0 p-4 opacity-0 group-hover:opacity-100 transition-opacity">
                   <button
                    onClick={(e) => { e.stopPropagation(); onDelete(lab.id); }}
                    className="w-8 h-8 rounded-lg bg-red-500/10 text-red-500 hover:bg-red-500 hover:text-white transition-all border border-red-500/20"
                   >
                     <i className="fa-solid fa-trash-can text-xs"></i>
                   </button>
                </div>

                <div className="w-12 h-12 bg-stone-100 dark:bg-stone-800 rounded-xl flex items-center justify-center mb-4 text-stone-500 dark:text-stone-400 group-hover:bg-sage-600 group-hover:text-white transition-all border border-stone-200 dark:border-stone-700">
                  <i className="fa-solid fa-diagram-project"></i>
                </div>

                <h3 className="text-lg font-bold text-stone-900 dark:text-white mb-1 group-hover:text-sage-600 dark:group-hover:text-sage-400 transition-colors">{lab.name}</h3>
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
                    className="flex-1 py-2 bg-stone-100 dark:bg-stone-800 hover:bg-stone-200 dark:hover:bg-stone-700 text-stone-700 dark:text-stone-200 text-xs font-bold rounded-lg border border-stone-200 dark:border-stone-700 transition-all"
                  >
                    Open Designer
                  </button>
                  <button className="w-10 py-2 bg-stone-100 dark:bg-stone-800 hover:bg-stone-200 dark:hover:bg-stone-700 text-stone-700 dark:text-stone-200 text-xs font-bold rounded-lg border border-stone-200 dark:border-stone-700 transition-all flex items-center justify-center">
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

      <footer className="h-10 border-t border-stone-200 dark:border-stone-900 bg-stone-100 dark:bg-stone-950 flex items-center px-10 justify-between text-[10px] text-stone-500 dark:text-stone-600 font-medium">
        <span>© 2024 Aura Visual Studio | Professional Edition</span>
        <div className="flex gap-4">
          <a href="#" className="hover:text-stone-700 dark:hover:text-stone-400">Documentation</a>
          <a href="#" className="hover:text-stone-700 dark:hover:text-stone-400">API Status</a>
          <a href="#" className="hover:text-stone-700 dark:hover:text-stone-400">Support</a>
        </div>
      </footer>
    </div>

    <ThemeSelector
      isOpen={showThemeSelector}
      onClose={() => setShowThemeSelector(false)}
    />
    </>
  );
};

export default Dashboard;
