import React, { useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import AgentsPopup from './AgentsPopup';
import ContainersPopup from './ContainersPopup';
import ResourcesPopup from './ResourcesPopup';
import StoragePopup from './StoragePopup';
import { getCpuColor, getMemoryColor, getStorageColor } from '../../utils/status';
import { formatUptimeFromBoot } from '../../utils/format';
import { useNotifications } from '../../contexts/NotificationContext';
import { useTheme } from '../../theme';

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
  vms_running?: number;
  started_at: string | null;
}

interface SystemMetrics {
  agents: { online: number; total: number };
  containers: { running: number; total: number };
  vms?: { running: number; total: number };
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
  const { preferences, updateCanvasSettings } = useNotifications();
  const { effectiveMode } = useTheme();
  const [activePopup, setActivePopup] = useState<PopupType>(null);
  const [containerHostFilter, setContainerHostFilter] = useState<string | null>(null);
  const navigate = useNavigate();

  // Use preferences for expanded state with fallback to false
  const isExpanded = preferences?.canvas_settings?.metricsBarExpanded ?? false;

  const handleToggleExpanded = useCallback(() => {
    updateCanvasSettings({ metricsBarExpanded: !isExpanded });
  }, [isExpanded, updateCanvasSettings]);

  const handleCloseContainersPopup = () => {
    setActivePopup(null);
    setContainerHostFilter(null);
  };

  const handleOpenHostContainers = (hostName: string) => {
    setContainerHostFilter(hostName);
    setActivePopup('containers');
  };

  const stripSurfaceStyle: React.CSSProperties = {
    backgroundColor:
      effectiveMode === 'light'
        ? 'var(--color-accent-700)'
        : 'color-mix(in srgb, var(--color-bg-surface) 88%, transparent)',
  };

  const stripSubRowStyle: React.CSSProperties = {
    backgroundColor:
      effectiveMode === 'light'
        ? 'var(--color-accent-800)'
        : 'color-mix(in srgb, var(--color-bg-base) 84%, var(--color-bg-surface) 16%)',
  };

  if (!metrics) {
    return (
      <div
        className="h-12 border-b border-stone-200 dark:border-stone-700 flex items-center justify-center"
        style={stripSurfaceStyle}
      >
        <span className="text-xs text-stone-400 dark:text-stone-500">Loading system status...</span>
      </div>
    );
  }

  const isLightStrip = effectiveMode === 'light';
  const clickableClass = isLightStrip
    ? 'hover:bg-white/20 rounded-md px-2 py-1 -mx-2 -my-1 cursor-pointer transition-colors'
    : 'hover:bg-stone-200/70 dark:hover:bg-stone-700/70 rounded-md px-2 py-1 -mx-2 -my-1 cursor-pointer transition-colors';
  const iconClass = isLightStrip ? 'text-white/85' : 'text-stone-400 dark:text-stone-500';
  const textClass = isLightStrip ? 'text-white/90' : 'text-stone-600 dark:text-stone-400';
  const strongTextClass = isLightStrip ? 'text-white' : 'text-stone-800 dark:text-stone-200';
  const mutedTextClass = isLightStrip ? 'text-white/75' : 'text-stone-400 dark:text-stone-500';
  const labelTextClass = isLightStrip ? 'text-white/80' : 'text-stone-500 dark:text-stone-500';
  const progressTrackClass = isLightStrip ? 'bg-black/20' : 'bg-stone-200 dark:bg-stone-700';
  const subProgressTrackClass = isLightStrip ? 'bg-black/20' : 'bg-stone-200 dark:bg-stone-600';
  const dividerClass = isLightStrip ? 'bg-white/30' : 'bg-stone-300 dark:bg-stone-600';

  return (
    <>
      <div className="flex flex-col border-b border-stone-200 dark:border-stone-700">
        {/* Main aggregate row */}
        <div className={`h-12 flex items-center px-10 gap-8 ${isLightStrip ? 'text-white' : ''}`} style={stripSurfaceStyle}>
        {/* Agents - Click navigates to hosts page */}
        <button
          onClick={() => navigate('/hosts')}
          className={`flex items-center gap-2 ${clickableClass}`}
          title="View all hosts"
        >
          <i className={`fa-solid fa-server ${iconClass} text-xs`}></i>
          <span className={`text-xs ${textClass}`}>
            <span className={`font-bold ${strongTextClass}`}>{metrics.agents.online}</span>
            <span className={mutedTextClass}>/{metrics.agents.total}</span>
            <span className={`ml-1 ${labelTextClass}`}>agents</span>
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
          <i className={`fa-solid fa-cube ${iconClass} text-xs`}></i>
          <span className={`text-xs ${textClass}`}>
            <span className={`font-bold ${strongTextClass}`}>{metrics.containers.running}</span>
            <span className={mutedTextClass}>/{metrics.containers.total}</span>
            <span className={`ml-1 ${labelTextClass}`}>containers</span>
          </span>
        </button>

        {/* VMs - only show if there are VMs */}
        {metrics.vms && metrics.vms.total > 0 && (
          <button
            onClick={() => setActivePopup('containers')}
            className={`flex items-center gap-2 ${clickableClass}`}
          >
            <i className={`fa-solid fa-desktop ${iconClass} text-xs`}></i>
            <span className={`text-xs ${textClass}`}>
              <span className={`font-bold ${strongTextClass}`}>{metrics.vms.running}</span>
              <span className={mutedTextClass}>/{metrics.vms.total}</span>
              <span className={`ml-1 ${labelTextClass}`}>VMs</span>
            </span>
          </button>
        )}

        {/* Labs */}
        <div className="flex items-center gap-2">
          <i className={`fa-solid fa-diagram-project ${iconClass} text-xs`}></i>
          <span className={`text-xs ${textClass}`}>
            <span className={`font-bold ${strongTextClass}`}>{metrics.labs_running}</span>
            <span className={mutedTextClass}>/{metrics.labs_total}</span>
            <span className={`ml-1 ${labelTextClass}`}>labs running</span>
          </span>
        </div>

        <div className={`h-6 w-px ${dividerClass}`}></div>

        {/* CPU */}
        <button
          onClick={() => setActivePopup('cpu')}
          className={`flex items-center gap-2 ${clickableClass}`}
        >
          <i className={`fa-solid fa-microchip ${iconClass} text-xs`}></i>
          <span className={`text-xs ${labelTextClass} w-8`}>CPU</span>
          <div className={`w-24 h-2 ${progressTrackClass} rounded-full overflow-hidden`}>
            <div
              className={`h-full ${getCpuColor(metrics.cpu_percent)} transition-all duration-500`}
              style={{ width: `${Math.min(metrics.cpu_percent, 100)}%` }}
            ></div>
          </div>
          <span className={`text-xs font-bold ${strongTextClass} w-10 text-right`}>
            {metrics.cpu_percent.toFixed(0)}%
          </span>
        </button>

        {/* Memory */}
        <button
          onClick={() => setActivePopup('memory')}
          className={`flex items-center gap-2 ${clickableClass}`}
        >
          <i className={`fa-solid fa-memory ${iconClass} text-xs`}></i>
          <span className={`text-xs ${labelTextClass} w-8`}>MEM</span>
          <div className={`w-24 h-2 ${progressTrackClass} rounded-full overflow-hidden`}>
            <div
              className={`h-full ${getMemoryColor(metrics.memory_percent)} transition-all duration-500`}
              style={{ width: `${Math.min(metrics.memory_percent, 100)}%` }}
            ></div>
          </div>
          <span className={`text-xs font-bold ${strongTextClass} w-10 text-right`}>
            {metrics.memory_percent.toFixed(0)}%
          </span>
          {metrics.memory && (
            <span className={`text-[10px] ${mutedTextClass}`}>
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
            <i className={`fa-solid fa-hard-drive ${iconClass} text-xs`}></i>
            <span className={`text-xs ${labelTextClass} w-8`}>DISK</span>
            <div className={`w-24 h-2 ${progressTrackClass} rounded-full overflow-hidden`}>
              <div
                className={`h-full ${getStorageColor(metrics.storage.percent)} transition-all duration-500`}
                style={{ width: `${Math.min(metrics.storage.percent, 100)}%` }}
              ></div>
            </div>
            <span className={`text-xs font-bold ${strongTextClass} w-10 text-right`}>
              {metrics.storage.percent.toFixed(0)}%
            </span>
            <span className={`text-[10px] ${mutedTextClass}`}>
              {formatMemorySize(metrics.storage.used_gb)}/{formatMemorySize(metrics.storage.total_gb)}
            </span>
          </button>
        )}

        {/* Multi-host indicator - clickable to expand/collapse */}
        {metrics.is_multi_host && (
          <button
            onClick={handleToggleExpanded}
            aria-expanded={isExpanded}
            className={`flex items-center gap-1.5 ml-2 px-2 py-1 rounded-md transition-colors ${
              isLightStrip
                ? 'bg-white/20 hover:bg-white/30'
                : 'bg-blue-100 dark:bg-blue-900/30 hover:bg-blue-200 dark:hover:bg-blue-900/50'
            }`}
          >
            <i className={`fa-solid fa-chevron-down ${isLightStrip ? 'text-white/90' : 'text-blue-500 dark:text-blue-400'} text-[8px] transition-transform duration-200 ${
              isExpanded ? '' : '-rotate-90'
            }`}></i>
            <i className={`fa-solid fa-network-wired ${isLightStrip ? 'text-white/90' : 'text-blue-500 dark:text-blue-400'} text-[10px]`}></i>
            <span className={`text-[10px] font-bold ${isLightStrip ? 'text-white' : 'text-blue-600 dark:text-blue-400'} uppercase tracking-wider`}>
              aggregated
            </span>
            <span className={`text-[10px] ${isLightStrip ? 'text-white/85' : 'text-blue-500 dark:text-blue-400'}`}>
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
                className="h-10 flex items-center px-10 gap-6 border-t border-stone-200/50 dark:border-stone-600/30"
                style={stripSubRowStyle}
              >
                {/* Indent spacer to align with aggregate row content */}
                <div className="flex items-center gap-2 min-w-[120px]">
                  <div className={`w-1 h-4 ${dividerClass} rounded-full`}></div>
                  <i className={`fa-solid fa-server ${iconClass} text-[10px]`}></i>
                  <span className={`text-[11px] font-medium ${textClass} truncate`}>
                    {host.name}
                  </span>
                </div>

                {/* Containers for this host */}
                <button
                  onClick={() => handleOpenHostContainers(host.name)}
                  className={`flex items-center gap-1.5 rounded px-1 -mx-1 transition-colors ${
                    isLightStrip ? 'hover:bg-white/20' : 'hover:bg-stone-200/70 dark:hover:bg-stone-600/50'
                  }`}
                  title={`View containers on ${host.name}`}
                >
                  <i className={`fa-solid fa-cube ${iconClass} text-[10px]`}></i>
                  <span className={`text-[11px] ${textClass}`}>
                    <span className={`font-bold ${strongTextClass}`}>{host.containers_running}</span>
                    <span className={`${mutedTextClass} ml-0.5`}>containers</span>
                  </span>
                </button>

                {/* VMs for this host - only show if there are VMs */}
                {host.vms_running !== undefined && host.vms_running > 0 && (
                  <button
                    onClick={() => handleOpenHostContainers(host.name)}
                    className={`flex items-center gap-1.5 rounded px-1 -mx-1 transition-colors ${
                      isLightStrip ? 'hover:bg-white/20' : 'hover:bg-stone-200/70 dark:hover:bg-stone-600/50'
                    }`}
                    title={`View VMs on ${host.name}`}
                  >
                    <i className={`fa-solid fa-desktop ${iconClass} text-[10px]`}></i>
                    <span className={`text-[11px] ${textClass}`}>
                      <span className={`font-bold ${strongTextClass}`}>{host.vms_running}</span>
                      <span className={`${mutedTextClass} ml-0.5`}>VMs</span>
                    </span>
                  </button>
                )}

                <div className={`h-4 w-px ${isLightStrip ? 'bg-white/25' : 'bg-stone-300/50 dark:bg-stone-600/50'}`}></div>

                {/* CPU */}
                <div className="flex items-center gap-1.5">
                  <i className={`fa-solid fa-microchip ${iconClass} text-[10px]`}></i>
                  <span className={`text-[10px] ${labelTextClass} w-6`}>CPU</span>
                  <div className={`w-16 h-1.5 ${subProgressTrackClass} rounded-full overflow-hidden`}>
                    <div
                      className={`h-full ${getCpuColor(host.cpu_percent)} transition-all duration-500`}
                      style={{ width: `${Math.min(host.cpu_percent, 100)}%` }}
                    ></div>
                  </div>
                  <span className={`text-[10px] font-medium ${textClass} w-8 text-right`}>
                    {host.cpu_percent.toFixed(0)}%
                  </span>
                </div>

                {/* Memory */}
                <div className="flex items-center gap-1.5">
                  <i className={`fa-solid fa-memory ${iconClass} text-[10px]`}></i>
                  <span className={`text-[10px] ${labelTextClass} w-6`}>MEM</span>
                  <div className={`w-16 h-1.5 ${subProgressTrackClass} rounded-full overflow-hidden`}>
                    <div
                      className={`h-full ${getMemoryColor(host.memory_percent)} transition-all duration-500`}
                      style={{ width: `${Math.min(host.memory_percent, 100)}%` }}
                    ></div>
                  </div>
                  <span className={`text-[10px] font-medium ${textClass} w-8 text-right`}>
                    {host.memory_percent.toFixed(0)}%
                  </span>
                  {host.memory_used_gb > 0 && (
                    <span className={`text-[9px] ${mutedTextClass}`}>
                      {formatMemorySize(host.memory_used_gb)}/{formatMemorySize(host.memory_total_gb)}
                    </span>
                  )}
                </div>

                {/* Storage */}
                <div className="flex items-center gap-1.5">
                  <i className={`fa-solid fa-hard-drive ${iconClass} text-[10px]`}></i>
                  <span className={`text-[10px] ${labelTextClass} w-6`}>DISK</span>
                  <div className={`w-16 h-1.5 ${subProgressTrackClass} rounded-full overflow-hidden`}>
                    <div
                      className={`h-full ${getStorageColor(host.storage_percent)} transition-all duration-500`}
                      style={{ width: `${Math.min(host.storage_percent, 100)}%` }}
                    ></div>
                  </div>
                  <span className={`text-[10px] font-medium ${textClass} w-8 text-right`}>
                    {host.storage_percent.toFixed(0)}%
                  </span>
                  {host.storage_total_gb > 0 && (
                    <span className={`text-[9px] ${mutedTextClass}`}>
                      {formatMemorySize(host.storage_used_gb)}/{formatMemorySize(host.storage_total_gb)}
                    </span>
                  )}
                </div>

                {/* Spacer to push uptime to far right */}
                <div className="flex-1"></div>

                {/* Uptime */}
                {host.started_at && (
                  <div className="flex items-center gap-1.5">
                    <i className={`fa-solid fa-clock ${iconClass} text-[10px]`}></i>
                    <span className={`text-[10px] ${labelTextClass} font-mono`}>
                      {formatUptimeFromBoot(host.started_at)}
                    </span>
                  </div>
                )}
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
