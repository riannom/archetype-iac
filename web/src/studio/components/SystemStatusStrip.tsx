import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import AgentsPopup from './AgentsPopup';
import ContainersPopup from './ContainersPopup';
import ResourcesPopup from './ResourcesPopup';
import StoragePopup from './StoragePopup';

interface PerHostMetrics {
  id: string;
  name: string;
  cpu_percent: number;
  memory_percent: number;
  storage_percent: number;
  storage_used_gb: number;
  storage_total_gb: number;
  containers_running: number;
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
  per_host?: PerHostMetrics[];
  is_multi_host?: boolean;
}

interface SystemStatusStripProps {
  metrics: SystemMetrics | null;
}

type PopupType = 'agents' | 'containers' | 'cpu' | 'memory' | 'storage' | null;

const SystemStatusStrip: React.FC<SystemStatusStripProps> = ({ metrics }) => {
  const [activePopup, setActivePopup] = useState<PopupType>(null);
  const navigate = useNavigate();

  if (!metrics) {
    return (
      <div className="h-12 bg-stone-100/50 dark:bg-stone-800/50 border-b border-stone-200 dark:border-stone-700 flex items-center justify-center">
        <span className="text-xs text-stone-400 dark:text-stone-500">Loading system status...</span>
      </div>
    );
  }

  const getCpuColor = (percent: number) => {
    if (percent >= 80) return 'bg-red-500';
    if (percent >= 60) return 'bg-amber-500';
    return 'bg-sage-500';
  };

  const getMemoryColor = (percent: number) => {
    if (percent >= 85) return 'bg-red-500';
    if (percent >= 70) return 'bg-amber-500';
    return 'bg-blue-500';
  };

  const getStorageColor = (percent: number) => {
    if (percent >= 90) return 'bg-red-500';
    if (percent >= 75) return 'bg-amber-500';
    return 'bg-violet-500';
  };

  const clickableClass = "hover:bg-stone-200/70 dark:hover:bg-stone-700/70 rounded-md px-2 py-1 -mx-2 -my-1 cursor-pointer transition-colors";

  return (
    <>
      <div className="h-12 bg-stone-100/50 dark:bg-stone-800/50 border-b border-stone-200 dark:border-stone-700 flex items-center px-10 gap-8">
        {/* Agents - Click navigates to hosts page */}
        <button
          onClick={() => navigate('/hosts')}
          className={`flex items-center gap-2 ${clickableClass}`}
          title="View all hosts"
        >
          <i className="fa-solid fa-server text-stone-400 dark:text-stone-500 text-xs"></i>
          <span className="text-xs text-stone-600 dark:text-stone-400">
            <span className="font-bold text-stone-800 dark:text-stone-200">{metrics.agents.online}</span>
            <span className="text-stone-400 dark:text-stone-500">/{metrics.agents.total}</span>
            <span className="ml-1 text-stone-500 dark:text-stone-500">agents</span>
          </span>
          {metrics.agents.online === metrics.agents.total && metrics.agents.total > 0 && (
            <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse"></div>
          )}
          {metrics.agents.online < metrics.agents.total && (
            <div className="w-2 h-2 rounded-full bg-amber-500"></div>
          )}
        </button>

        {/* Containers */}
        <button
          onClick={() => setActivePopup('containers')}
          className={`flex items-center gap-2 ${clickableClass}`}
        >
          <i className="fa-solid fa-cube text-stone-400 dark:text-stone-500 text-xs"></i>
          <span className="text-xs text-stone-600 dark:text-stone-400">
            <span className="font-bold text-stone-800 dark:text-stone-200">{metrics.containers.running}</span>
            <span className="text-stone-400 dark:text-stone-500">/{metrics.containers.total}</span>
            <span className="ml-1 text-stone-500 dark:text-stone-500">containers</span>
          </span>
        </button>

        {/* Labs */}
        <div className="flex items-center gap-2">
          <i className="fa-solid fa-diagram-project text-stone-400 dark:text-stone-500 text-xs"></i>
          <span className="text-xs text-stone-600 dark:text-stone-400">
            <span className="font-bold text-stone-800 dark:text-stone-200">{metrics.labs_running}</span>
            <span className="text-stone-400 dark:text-stone-500">/{metrics.labs_total}</span>
            <span className="ml-1 text-stone-500 dark:text-stone-500">labs running</span>
          </span>
        </div>

        <div className="h-6 w-px bg-stone-300 dark:bg-stone-600"></div>

        {/* CPU */}
        <button
          onClick={() => setActivePopup('cpu')}
          className={`flex items-center gap-2 ${clickableClass}`}
        >
          <i className="fa-solid fa-microchip text-stone-400 dark:text-stone-500 text-xs"></i>
          <span className="text-xs text-stone-500 dark:text-stone-500 w-8">CPU</span>
          <div className="w-24 h-2 bg-stone-200 dark:bg-stone-700 rounded-full overflow-hidden">
            <div
              className={`h-full ${getCpuColor(metrics.cpu_percent)} transition-all duration-500`}
              style={{ width: `${Math.min(metrics.cpu_percent, 100)}%` }}
            ></div>
          </div>
          <span className="text-xs font-bold text-stone-700 dark:text-stone-300 w-10 text-right">
            {metrics.cpu_percent.toFixed(0)}%
          </span>
        </button>

        {/* Memory */}
        <button
          onClick={() => setActivePopup('memory')}
          className={`flex items-center gap-2 ${clickableClass}`}
        >
          <i className="fa-solid fa-memory text-stone-400 dark:text-stone-500 text-xs"></i>
          <span className="text-xs text-stone-500 dark:text-stone-500 w-8">MEM</span>
          <div className="w-24 h-2 bg-stone-200 dark:bg-stone-700 rounded-full overflow-hidden">
            <div
              className={`h-full ${getMemoryColor(metrics.memory_percent)} transition-all duration-500`}
              style={{ width: `${Math.min(metrics.memory_percent, 100)}%` }}
            ></div>
          </div>
          <span className="text-xs font-bold text-stone-700 dark:text-stone-300 w-10 text-right">
            {metrics.memory_percent.toFixed(0)}%
          </span>
        </button>

        {/* Storage */}
        {metrics.storage && (
          <button
            onClick={() => setActivePopup('storage')}
            className={`flex items-center gap-2 ${clickableClass}`}
          >
            <i className="fa-solid fa-hard-drive text-stone-400 dark:text-stone-500 text-xs"></i>
            <span className="text-xs text-stone-500 dark:text-stone-500 w-8">DISK</span>
            <div className="w-24 h-2 bg-stone-200 dark:bg-stone-700 rounded-full overflow-hidden">
              <div
                className={`h-full ${getStorageColor(metrics.storage.percent)} transition-all duration-500`}
                style={{ width: `${Math.min(metrics.storage.percent, 100)}%` }}
              ></div>
            </div>
            <span className="text-xs font-bold text-stone-700 dark:text-stone-300 w-10 text-right">
              {metrics.storage.percent.toFixed(0)}%
            </span>
          </button>
        )}

        {/* Multi-host indicator */}
        {metrics.is_multi_host && (
          <div className="flex items-center gap-1.5 ml-2 px-2 py-1 bg-blue-100 dark:bg-blue-900/30 rounded-md">
            <i className="fa-solid fa-network-wired text-blue-500 dark:text-blue-400 text-[10px]"></i>
            <span className="text-[10px] font-bold text-blue-600 dark:text-blue-400 uppercase tracking-wider">
              aggregated
            </span>
          </div>
        )}
      </div>

      {/* Popups */}
      <AgentsPopup
        isOpen={activePopup === 'agents'}
        onClose={() => setActivePopup(null)}
      />
      <ContainersPopup
        isOpen={activePopup === 'containers'}
        onClose={() => setActivePopup(null)}
      />
      <ResourcesPopup
        isOpen={activePopup === 'cpu' || activePopup === 'memory'}
        onClose={() => setActivePopup(null)}
        type={activePopup === 'memory' ? 'memory' : 'cpu'}
      />
      <StoragePopup
        isOpen={activePopup === 'storage'}
        onClose={() => setActivePopup(null)}
        perHost={metrics?.per_host || []}
        totals={metrics?.storage || { used_gb: 0, total_gb: 0, percent: 0 }}
      />
    </>
  );
};

export default SystemStatusStrip;
