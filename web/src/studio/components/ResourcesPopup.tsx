import React, { useEffect, useState } from 'react';
import DetailPopup from './DetailPopup';

interface AgentResource {
  id: string;
  name: string;
  cpu_percent: number;
  memory_percent: number;
  containers: number;
}

interface LabResource {
  id: string;
  name: string;
  container_count: number;
  estimated_percent: number;
}

interface ResourcesData {
  by_agent: AgentResource[];
  by_lab: LabResource[];
}

interface ResourcesPopupProps {
  isOpen: boolean;
  onClose: () => void;
  type: 'cpu' | 'memory';
}

const ResourcesPopup: React.FC<ResourcesPopupProps> = ({ isOpen, onClose, type }) => {
  const [data, setData] = useState<ResourcesData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (isOpen) {
      setLoading(true);
      fetch('/api/dashboard/metrics/resources')
        .then(res => res.json())
        .then(setData)
        .catch(console.error)
        .finally(() => setLoading(false));
    }
  }, [isOpen]);

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

  const getBarColor = type === 'cpu' ? getCpuColor : getMemoryColor;
  const title = type === 'cpu' ? 'CPU Usage Distribution' : 'Memory Usage Distribution';
  const metricKey = type === 'cpu' ? 'cpu_percent' : 'memory_percent';

  return (
    <DetailPopup isOpen={isOpen} onClose={onClose} title={title} width="max-w-xl">
      {loading ? (
        <div className="flex items-center justify-center py-8">
          <i className="fa-solid fa-spinner fa-spin text-stone-400" />
          <span className="ml-2 text-sm text-stone-500">Loading...</span>
        </div>
      ) : data ? (
        <div className="space-y-6">
          {/* By Agent */}
          <div>
            <h3 className="text-sm font-semibold text-stone-700 dark:text-stone-300 mb-3 flex items-center gap-2">
              <i className="fa-solid fa-server text-stone-400" />
              By Agent
            </h3>
            {data.by_agent.length > 0 ? (
              <div className="space-y-3">
                {data.by_agent.map(agent => {
                  const percent = agent[metricKey];
                  return (
                    <div key={agent.id}>
                      <div className="flex items-center justify-between text-xs mb-1">
                        <span className="font-medium text-stone-700 dark:text-stone-300">{agent.name}</span>
                        <span className="text-stone-500 dark:text-stone-400">
                          {percent.toFixed(1)}% · {agent.containers} containers
                        </span>
                      </div>
                      <div className="h-4 bg-stone-200 dark:bg-stone-700 rounded overflow-hidden">
                        <div
                          className={`h-full ${getBarColor(percent)} transition-all`}
                          style={{ width: `${Math.min(percent, 100)}%` }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="text-sm text-stone-500 dark:text-stone-400 text-center py-4">
                No agents online
              </p>
            )}
          </div>

          {/* By Lab */}
          {data.by_lab.length > 0 && (
            <div>
              <h3 className="text-sm font-semibold text-stone-700 dark:text-stone-300 mb-3 flex items-center gap-2">
                <i className="fa-solid fa-diagram-project text-stone-400" />
                By Lab (Container Distribution)
              </h3>
              <div className="space-y-3">
                {data.by_lab.map(lab => (
                  <div key={lab.id}>
                    <div className="flex items-center justify-between text-xs mb-1">
                      <span className="font-medium text-stone-700 dark:text-stone-300">{lab.name}</span>
                      <span className="text-stone-500 dark:text-stone-400">
                        {lab.container_count} containers · {lab.estimated_percent}%
                      </span>
                    </div>
                    <div className="h-4 bg-stone-200 dark:bg-stone-700 rounded overflow-hidden">
                      <div
                        className="h-full bg-sage-500 transition-all"
                        style={{ width: `${Math.min(lab.estimated_percent, 100)}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Legend */}
          <div className="pt-4 border-t border-stone-200 dark:border-stone-700">
            <h4 className="text-xs font-medium text-stone-500 dark:text-stone-400 mb-2">Thresholds</h4>
            <div className="flex items-center gap-4 text-xs">
              <div className="flex items-center gap-1.5">
                <div className={`w-3 h-3 rounded ${type === 'cpu' ? 'bg-sage-500' : 'bg-blue-500'}`} />
                <span className="text-stone-600 dark:text-stone-400">Normal</span>
              </div>
              <div className="flex items-center gap-1.5">
                <div className="w-3 h-3 rounded bg-amber-500" />
                <span className="text-stone-600 dark:text-stone-400">
                  {type === 'cpu' ? '60%+' : '70%+'}
                </span>
              </div>
              <div className="flex items-center gap-1.5">
                <div className="w-3 h-3 rounded bg-red-500" />
                <span className="text-stone-600 dark:text-stone-400">
                  {type === 'cpu' ? '80%+' : '85%+'}
                </span>
              </div>
            </div>
          </div>
        </div>
      ) : (
        <div className="text-center py-8 text-red-500">Failed to load data</div>
      )}
    </DetailPopup>
  );
};

export default ResourcesPopup;
