import React, { useEffect, useState } from 'react';
import DetailPopup from './DetailPopup';

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
    containers_running: number;
    containers_total: number;
  };
  last_heartbeat: string | null;
}

interface AgentsPopupProps {
  isOpen: boolean;
  onClose: () => void;
}

const AgentsPopup: React.FC<AgentsPopupProps> = ({ isOpen, onClose }) => {
  const [agents, setAgents] = useState<AgentDetail[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (isOpen) {
      setLoading(true);
      fetch('/api/agents/detailed')
        .then(res => res.json())
        .then(setAgents)
        .catch(console.error)
        .finally(() => setLoading(false));
    }
  }, [isOpen]);

  const formatRelativeTime = (isoString: string | null): string => {
    if (!isoString) return 'Never';
    const date = new Date(isoString);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffSec = Math.floor(diffMs / 1000);

    if (diffSec < 60) return `${diffSec}s ago`;
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    return `${Math.floor(diffHr / 24)}d ago`;
  };

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
                    v{agent.version} · {formatRelativeTime(agent.last_heartbeat)}
                  </p>
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
              <div className="grid grid-cols-2 gap-4">
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
