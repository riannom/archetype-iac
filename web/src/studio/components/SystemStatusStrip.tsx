import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import AgentsPopup from './AgentsPopup';
import ContainersPopup from './ContainersPopup';
import ResourcesPopup from './ResourcesPopup';
import StoragePopup from './StoragePopup';
import { getCpuColor, getMemoryColor, getStorageColor } from '../../utils/status';

interface PerHostMetrics {
  id: string;
  name: string;
  cpu_percent: number;
  memory_percent: number;
  memory_used_gb: number;
  memory_total_gb: number;
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
  memory?: {
    used_gb: number;
    total_gb: number;
    percent: number;
  };
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

const formatMemorySize = (gb: number): string => {
  if (gb >= 1024) {
    return `${(gb / 1024).toFixed(1)} TB`;
  }
  if (gb >= 1) {
    return `${gb.toFixed(1)} GB`;
  }
  return `${(gb * 1024).toFixed(0)} MB`;
};

interface SystemStatusStripProps {
  metrics: SystemMetrics | null;
}

type PopupType = 'agents' | 'containers' | 'cpu' | 'memory' | 'storage' | null;

const SystemStatusStrip: React.FC<SystemStatusStripProps> = ({ metrics }) => {
  const [activePopup, setActivePopup] = useState<PopupType>(null);
  const [isExpanded, setIsExpanded] = useState(false);
  const [containerHostFilter, setContainerHostFilter] = useState<string | null>(null);
  const navigate = useNavigate();

  const handleCloseContainersPopup = () => {
    setActivePopup(null);
    setContainerHostFilter(null);
  };

  const handleOpenHostContainers = (hostName: string) => {
    setContainerHostFilter(hostName);
    setActivePopup('containers');
  };

  if (!metrics) {
    return (
      <div className="h-12 bg-stone-100/50 dark:bg-stone-800/50 border-b border-stone-200 dark:border-stone-700 flex items-center justify-center">
        <span className="text-xs text-stone-400 dark:text-stone-500">Loading system status...</span>
      </div>
    );
  }

  const clickableClass = "hover:bg-stone-200/70 dark:hover:bg-stone-700/70 rounded-md px-2 py-1 -mx-2 -my-1 cursor-pointer transition-colors";

  return (
    <>
      <div className="flex flex-col border-b border-stone-200 dark:border-stone-700">
        {/* Main aggregate row */}
        <div className="h-12 bg-stone-100/50 dark:bg-stone-800/50 flex items-center px-10 gap-8">
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
          {metrics.memory && (
            <span className="text-[10px] text-stone-400 dark:text-stone-500">
              {formatMemorySize(metrics.memory.used_gb)}/{formatMemorySize(metrics.memory.total_gb)}
            </span>
          )}
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
            <span className="text-[10px] text-stone-400 dark:text-stone-500">
              {formatMemorySize(metrics.storage.used_gb)}/{formatMemorySize(metrics.storage.total_gb)}
            </span>
          </button>
        )}

        {/* Multi-host indicator - clickable to expand/collapse */}
        {metrics.is_multi_host && (
          <button
            onClick={() => setIsExpanded(!isExpanded)}
            aria-expanded={isExpanded}
            className="flex items-center gap-1.5 ml-2 px-2 py-1 bg-blue-100 dark:bg-blue-900/30 rounded-md hover:bg-blue-200 dark:hover:bg-blue-900/50 transition-colors"
          >
            <i className={`fa-solid fa-chevron-down text-blue-500 dark:text-blue-400 text-[8px] transition-transform duration-200 ${
              isExpanded ? '' : '-rotate-90'
            }`}></i>
            <i className="fa-solid fa-network-wired text-blue-500 dark:text-blue-400 text-[10px]"></i>
            <span className="text-[10px] font-bold text-blue-600 dark:text-blue-400 uppercase tracking-wider">
              aggregated
            </span>
            <span className="text-[10px] text-blue-500 dark:text-blue-400">
              ({metrics.per_host?.length || 0})
            </span>
          </button>
        )}
        </div>

        {/* Collapsible per-host rows */}
        {metrics.is_multi_host && metrics.per_host && metrics.per_host.length > 0 && (
          <div
            className={`overflow-hidden transition-all duration-200 ease-in-out ${
              isExpanded ? 'max-h-[500px] opacity-100' : 'max-h-0 opacity-0'
            }`}
          >
            {metrics.per_host.map((host) => (
              <div
                key={host.id}
                className="h-10 bg-stone-50/50 dark:bg-stone-700/30 flex items-center px-10 gap-6 border-t border-stone-200/50 dark:border-stone-600/30"
              >
                {/* Indent spacer to align with aggregate row content */}
                <div className="flex items-center gap-2 min-w-[120px]">
                  <div className="w-1 h-4 bg-stone-300 dark:bg-stone-600 rounded-full"></div>
                  <i className="fa-solid fa-server text-stone-400 dark:text-stone-500 text-[10px]"></i>
                  <span className="text-[11px] font-medium text-stone-600 dark:text-stone-400 truncate">
                    {host.name}
                  </span>
                </div>

                {/* Containers for this host */}
                <button
                  onClick={() => handleOpenHostContainers(host.name)}
                  className="flex items-center gap-1.5 hover:bg-stone-200/70 dark:hover:bg-stone-600/50 rounded px-1 -mx-1 transition-colors"
                  title={`View containers on ${host.name}`}
                >
                  <i className="fa-solid fa-cube text-stone-400 dark:text-stone-500 text-[10px]"></i>
                  <span className="text-[11px] text-stone-600 dark:text-stone-400">
                    <span className="font-bold text-stone-700 dark:text-stone-300">{host.containers_running}</span>
                    <span className="text-stone-400 dark:text-stone-500 ml-0.5">containers</span>
                  </span>
                </button>

                <div className="h-4 w-px bg-stone-300/50 dark:bg-stone-600/50"></div>

                {/* CPU */}
                <div className="flex items-center gap-1.5">
                  <i className="fa-solid fa-microchip text-stone-400 dark:text-stone-500 text-[10px]"></i>
                  <span className="text-[10px] text-stone-400 dark:text-stone-500 w-6">CPU</span>
                  <div className="w-16 h-1.5 bg-stone-200 dark:bg-stone-600 rounded-full overflow-hidden">
                    <div
                      className={`h-full ${getCpuColor(host.cpu_percent)} transition-all duration-500`}
                      style={{ width: `${Math.min(host.cpu_percent, 100)}%` }}
                    ></div>
                  </div>
                  <span className="text-[10px] font-medium text-stone-600 dark:text-stone-400 w-8 text-right">
                    {host.cpu_percent.toFixed(0)}%
                  </span>
                </div>

                {/* Memory */}
                <div className="flex items-center gap-1.5">
                  <i className="fa-solid fa-memory text-stone-400 dark:text-stone-500 text-[10px]"></i>
                  <span className="text-[10px] text-stone-400 dark:text-stone-500 w-6">MEM</span>
                  <div className="w-16 h-1.5 bg-stone-200 dark:bg-stone-600 rounded-full overflow-hidden">
                    <div
                      className={`h-full ${getMemoryColor(host.memory_percent)} transition-all duration-500`}
                      style={{ width: `${Math.min(host.memory_percent, 100)}%` }}
                    ></div>
                  </div>
                  <span className="text-[10px] font-medium text-stone-600 dark:text-stone-400 w-8 text-right">
                    {host.memory_percent.toFixed(0)}%
                  </span>
                  {host.memory_used_gb > 0 && (
                    <span className="text-[9px] text-stone-400 dark:text-stone-500">
                      {formatMemorySize(host.memory_used_gb)}/{formatMemorySize(host.memory_total_gb)}
                    </span>
                  )}
                </div>

                {/* Storage */}
                <div className="flex items-center gap-1.5">
                  <i className="fa-solid fa-hard-drive text-stone-400 dark:text-stone-500 text-[10px]"></i>
                  <span className="text-[10px] text-stone-400 dark:text-stone-500 w-6">DISK</span>
                  <div className="w-16 h-1.5 bg-stone-200 dark:bg-stone-600 rounded-full overflow-hidden">
                    <div
                      className={`h-full ${getStorageColor(host.storage_percent)} transition-all duration-500`}
                      style={{ width: `${Math.min(host.storage_percent, 100)}%` }}
                    ></div>
                  </div>
                  <span className="text-[10px] font-medium text-stone-600 dark:text-stone-400 w-8 text-right">
                    {host.storage_percent.toFixed(0)}%
                  </span>
                  {host.storage_total_gb > 0 && (
                    <span className="text-[9px] text-stone-400 dark:text-stone-500">
                      {formatMemorySize(host.storage_used_gb)}/{formatMemorySize(host.storage_total_gb)}
                    </span>
                  )}
                </div>
              </div>
            ))}
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
        onClose={handleCloseContainersPopup}
        filterHostName={containerHostFilter || undefined}
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
