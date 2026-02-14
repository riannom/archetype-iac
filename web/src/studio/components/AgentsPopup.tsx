import React, { useEffect, useState } from 'react';
import DetailPopup from './DetailPopup';
import { formatTimestamp, formatUptimeFromBoot } from '../../utils/format';
import { getCpuColor, getMemoryColor, getStorageColor } from '../../utils/status';
import { apiRequest } from '../../api';

interface AgentDetail {
  id: string;
  name: string;
  address: string;
  status: string;
  version: string;
  capabilities: {
    providers?: string[];
    features?: string[];
    max_concurrent_jobs?: number;
  };
  resource_usage: {
    cpu_percent: number;
    memory_percent: number;
    memory_used_gb: number;
    memory_total_gb: number;
    storage_percent: number;
    storage_used_gb: number;
    storage_total_gb: number;
    containers_running: number;
    containers_total: number;
  };
  started_at: string | null;
  last_heartbeat: string | null;
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

interface AgentsPopupProps {
  isOpen: boolean;
  onClose: () => void;
}

const AgentsPopup: React.FC<AgentsPopupProps> = ({ isOpen, onClose }) => {
  const [agents, setAgents] = useState<AgentDetail[]>([]);
  const [loading, setLoading] = useState(true);
  const [, setTick] = useState(0); // Force re-render for live uptime

  useEffect(() => {
    if (isOpen) {
      setLoading(true);
      apiRequest<AgentDetail[]>('/agents/detailed')
        .then(setAgents)
        .catch(console.error)
        .finally(() => setLoading(false));
    }
  }, [isOpen]);

  // Update uptime every second while popup is open
  useEffect(() => {
    if (!isOpen || loading) return;
    const interval = setInterval(() => setTick(t => t + 1), 1000);
    return () => clearInterval(interval);
  }, [isOpen, loading]);

  return (
    <DetailPopup isOpen={isOpen} onClose={onClose} title="Agents" width="max-w-2xl">
      {loading ? (
        <div className="flex items-center justify-center py-8">
          <i className="fa-solid fa-spinner fa-spin text-stone-400" />
          <span className="ml-2 text-sm text-stone-500">Loading...</span>
        </div>
      ) : agents.length > 0 ? (
        <div className="space-y-4">
          {agents.map(agent => (
            <div
              key={agent.id}
              className="border border-stone-200 dark:border-stone-700 rounded-lg p-4"
            >
              {/* Header */}
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-3">
                  <div className={`w-2.5 h-2.5 rounded-full ${
                    agent.status === 'online' ? 'bg-green-500 animate-pulse' : 'bg-stone-400'
                  }`} />
                  <div>
                    <h3 className="font-semibold text-stone-800 dark:text-stone-200">{agent.name}</h3>
                    <p className="text-xs text-stone-500 dark:text-stone-500">{agent.address}</p>
                  </div>
                </div>
                <div className="text-right">
                  <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                    agent.status === 'online'
                      ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                      : 'bg-stone-100 text-stone-500 dark:bg-stone-800 dark:text-stone-400'
                  }`}>
                    {agent.status}
                  </span>
                  <p className="text-xs text-stone-400 mt-1">
                    v{agent.version} · {formatTimestamp(agent.last_heartbeat)}
                  </p>
                  {agent.started_at && (
                    <p className="text-xs text-stone-400">
                      <i className="fa-solid fa-clock text-[10px] mr-1" />
                      Uptime: {formatUptimeFromBoot(agent.started_at)}
                    </p>
                  )}
                </div>
              </div>

              {/* Capabilities */}
              <div className="flex flex-wrap gap-2 mb-3">
                {agent.capabilities.providers?.map(provider => (
                  <span
                    key={provider}
                    className="px-2 py-0.5 bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-400 rounded text-xs font-medium"
                  >
                    {provider}
                  </span>
                ))}
                {agent.capabilities.features?.map(feature => (
                  <span
                    key={feature}
                    className="px-2 py-0.5 bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400 rounded text-xs font-medium"
                  >
                    {feature}
                  </span>
                ))}
              </div>

              {/* Resource Bars */}
              <div className="grid grid-cols-3 gap-4">
                <div>
                  <div className="flex items-center justify-between text-xs mb-1">
                    <span className="text-stone-500 dark:text-stone-400">CPU</span>
                    <span className="font-medium text-stone-700 dark:text-stone-300">
                      {agent.resource_usage.cpu_percent.toFixed(0)}%
                    </span>
                  </div>
                  <div className="h-2 bg-stone-200 dark:bg-stone-700 rounded-full overflow-hidden">
                    <div
                      className={`h-full ${getCpuColor(agent.resource_usage.cpu_percent)} transition-all`}
                      style={{ width: `${Math.min(agent.resource_usage.cpu_percent, 100)}%` }}
                    />
                  </div>
                </div>
                <div>
                  <div className="flex items-center justify-between text-xs mb-1">
                    <span className="text-stone-500 dark:text-stone-400">Memory</span>
                    <span className="font-medium text-stone-700 dark:text-stone-300">
                      {agent.resource_usage.memory_percent.toFixed(0)}%
                    </span>
                  </div>
                  <div className="h-2 bg-stone-200 dark:bg-stone-700 rounded-full overflow-hidden">
                    <div
                      className={`h-full ${getMemoryColor(agent.resource_usage.memory_percent)} transition-all`}
                      style={{ width: `${Math.min(agent.resource_usage.memory_percent, 100)}%` }}
                    />
                  </div>
                  {agent.resource_usage.memory_total_gb > 0 && (
                    <span className="text-[10px] text-stone-400 dark:text-stone-500">
                      {formatMemorySize(agent.resource_usage.memory_used_gb)}/{formatMemorySize(agent.resource_usage.memory_total_gb)}
                    </span>
                  )}
                </div>
                <div>
                  <div className="flex items-center justify-between text-xs mb-1">
                    <span className="text-stone-500 dark:text-stone-400">Storage</span>
                    <span className="font-medium text-stone-700 dark:text-stone-300">
                      {agent.resource_usage.storage_percent.toFixed(0)}%
                    </span>
                  </div>
                  <div className="h-2 bg-stone-200 dark:bg-stone-700 rounded-full overflow-hidden">
                    <div
                      className={`h-full ${getStorageColor(agent.resource_usage.storage_percent)} transition-all`}
                      style={{ width: `${Math.min(agent.resource_usage.storage_percent, 100)}%` }}
                    />
                  </div>
                  {agent.resource_usage.storage_total_gb > 0 && (
                    <span className="text-[10px] text-stone-400 dark:text-stone-500">
                      {formatMemorySize(agent.resource_usage.storage_used_gb)}/{formatMemorySize(agent.resource_usage.storage_total_gb)}
                    </span>
                  )}
                </div>
              </div>

              {/* Container Count */}
              <div className="flex items-center gap-2 mt-3 pt-3 border-t border-stone-100 dark:border-stone-800">
                <i className="fa-solid fa-cube text-stone-400 text-xs" />
                <span className="text-xs text-stone-600 dark:text-stone-400">
                  <span className="font-medium text-stone-800 dark:text-stone-200">
                    {agent.resource_usage.containers_running}
                  </span>
                  <span className="text-stone-400 dark:text-stone-500">
                    /{agent.resource_usage.containers_total}
                  </span>
                  <span className="ml-1">containers</span>
                </span>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-center py-8 text-stone-500 dark:text-stone-400">
          <i className="fa-solid fa-server text-2xl mb-2" />
          <p className="text-sm">No agents registered</p>
        </div>
      )}
    </DetailPopup>
  );
};

export default AgentsPopup;
