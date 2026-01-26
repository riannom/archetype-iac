import React from 'react';

interface SystemMetrics {
  agents: { online: number; total: number };
  containers: { running: number; total: number };
  cpu_percent: number;
  memory_percent: number;
  labs_running: number;
  labs_total: number;
}

interface SystemStatusStripProps {
  metrics: SystemMetrics | null;
}

const SystemStatusStrip: React.FC<SystemStatusStripProps> = ({ metrics }) => {
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

  return (
    <div className="h-12 bg-stone-100/50 dark:bg-stone-800/50 border-b border-stone-200 dark:border-stone-700 flex items-center px-10 gap-8">
      {/* Agents */}
      <div className="flex items-center gap-2">
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
      </div>

      {/* Containers */}
      <div className="flex items-center gap-2">
        <i className="fa-solid fa-cube text-stone-400 dark:text-stone-500 text-xs"></i>
        <span className="text-xs text-stone-600 dark:text-stone-400">
          <span className="font-bold text-stone-800 dark:text-stone-200">{metrics.containers.running}</span>
          <span className="text-stone-400 dark:text-stone-500">/{metrics.containers.total}</span>
          <span className="ml-1 text-stone-500 dark:text-stone-500">containers</span>
        </span>
      </div>

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
      <div className="flex items-center gap-2">
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
      </div>

      {/* Memory */}
      <div className="flex items-center gap-2">
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
      </div>
    </div>
  );
};

export default SystemStatusStrip;
