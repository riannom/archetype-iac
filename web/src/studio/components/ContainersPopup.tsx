import React, { useEffect, useMemo, useState } from 'react';
import DetailPopup from './DetailPopup';

interface ContainerInfo {
  name: string;
  status: string;
  lab_id: string | null;
  lab_name: string | null;
  node_name: string | null;
  node_kind: string | null;
  image: string;
  agent_name: string;
}

interface LabContainers {
  name: string;
  containers: ContainerInfo[];
}

interface ContainersData {
  by_lab: Record<string, LabContainers>;
  system_containers: ContainerInfo[];
  total_running: number;
  total_stopped: number;
}

interface ContainersPopupProps {
  isOpen: boolean;
  onClose: () => void;
  filterHostName?: string;
}

const ContainersPopup: React.FC<ContainersPopupProps> = ({ isOpen, onClose, filterHostName }) => {
  const [data, setData] = useState<ContainersData | null>(null);
  const [loading, setLoading] = useState(true);
  const [expandedLabs, setExpandedLabs] = useState<Set<string>>(new Set());

  // Filter data by host if filterHostName is provided
  const filteredData = useMemo(() => {
    if (!data || !filterHostName) return data;

    // Filter lab containers
    const filteredByLab: Record<string, LabContainers> = {};
    for (const [labId, labData] of Object.entries(data.by_lab)) {
      const filteredContainers = labData.containers.filter(c => c.agent_name === filterHostName);
      if (filteredContainers.length > 0) {
        filteredByLab[labId] = {
          name: labData.name,
          containers: filteredContainers,
        };
      }
    }

    // Filter system containers
    const filteredSystemContainers = data.system_containers.filter(c => c.agent_name === filterHostName);

    // Recompute totals for filtered data
    const allFilteredContainers = [
      ...Object.values(filteredByLab).flatMap(l => l.containers),
      ...filteredSystemContainers,
    ];
    const totalRunning = allFilteredContainers.filter(c => c.status === 'running').length;
    const totalStopped = allFilteredContainers.filter(c => c.status !== 'running').length;

    return {
      by_lab: filteredByLab,
      system_containers: filteredSystemContainers,
      total_running: totalRunning,
      total_stopped: totalStopped,
    };
  }, [data, filterHostName]);

  const popupTitle = filterHostName ? `Containers on ${filterHostName}` : 'Containers';

  useEffect(() => {
    if (isOpen) {
      setLoading(true);
      fetch('/api/dashboard/metrics/containers')
        .then(res => res.json())
        .then(setData)
        .catch(console.error)
        .finally(() => setLoading(false));
    }
  }, [isOpen]);

  const toggleLab = (labId: string) => {
    setExpandedLabs(prev => {
      const next = new Set(prev);
      if (next.has(labId)) {
        next.delete(labId);
      } else {
        next.add(labId);
      }
      return next;
    });
  };

  const StatusBadge: React.FC<{ status: string }> = ({ status }) => (
    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
      status === 'running'
        ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
        : 'bg-stone-100 text-stone-500 dark:bg-stone-800 dark:text-stone-400'
    }`}>
      {status}
    </span>
  );

  return (
    <DetailPopup isOpen={isOpen} onClose={onClose} title={popupTitle} width="max-w-2xl">
      {loading ? (
        <div className="flex items-center justify-center py-8">
          <i className="fa-solid fa-spinner fa-spin text-stone-400" />
          <span className="ml-2 text-sm text-stone-500">Loading...</span>
        </div>
      ) : filteredData ? (
        <div className="space-y-4">
          {/* Summary */}
          <div className="flex items-center gap-4 p-3 bg-stone-100 dark:bg-stone-800 rounded-lg">
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 rounded-full bg-green-500"></div>
              <span className="text-sm font-medium text-stone-700 dark:text-stone-300">
                {filteredData.total_running} running
              </span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 rounded-full bg-stone-400"></div>
              <span className="text-sm font-medium text-stone-700 dark:text-stone-300">
                {filteredData.total_stopped} stopped
              </span>
            </div>
          </div>

          {/* Lab Containers */}
          {Object.entries(filteredData.by_lab).map(([labId, labData]) => (
            <div key={labId} className="border border-stone-200 dark:border-stone-700 rounded-lg overflow-hidden">
              <button
                onClick={() => toggleLab(labId)}
                className="w-full flex items-center justify-between px-4 py-3 bg-stone-50 dark:bg-stone-800/50 hover:bg-stone-100 dark:hover:bg-stone-800 transition-colors"
              >
                <div className="flex items-center gap-3">
                  <i className={`fa-solid fa-chevron-${expandedLabs.has(labId) ? 'down' : 'right'} text-xs text-stone-400`} />
                  <i className="fa-solid fa-diagram-project text-sage-500" />
                  <span className="font-medium text-stone-800 dark:text-stone-200">{labData.name}</span>
                  <span className="text-xs text-stone-500 dark:text-stone-400">
                    {labData.containers.length} container{labData.containers.length !== 1 ? 's' : ''}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-green-600 dark:text-green-400">
                    {labData.containers.filter(c => c.status === 'running').length} running
                  </span>
                </div>
              </button>
              {expandedLabs.has(labId) && (
                <div className="divide-y divide-stone-100 dark:divide-stone-700">
                  {labData.containers.map((container, idx) => (
                    <div key={idx} className="px-4 py-2 flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <i className="fa-solid fa-cube text-stone-400 text-xs" />
                        <div>
                          <div className="text-sm font-medium text-stone-700 dark:text-stone-300">
                            {container.node_name || container.name}
                          </div>
                          <div className="text-xs text-stone-500 dark:text-stone-500">
                            {container.node_kind && <span className="mr-2">{container.node_kind}</span>}
                            <span className="text-stone-400">{container.image}</span>
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-stone-400 dark:text-stone-500">{container.agent_name}</span>
                        <StatusBadge status={container.status} />
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}

          {/* System Containers */}
          {filteredData.system_containers.length > 0 && (
            <div className="border border-stone-200 dark:border-stone-700 rounded-lg overflow-hidden">
              <button
                onClick={() => toggleLab('_system')}
                className="w-full flex items-center justify-between px-4 py-3 bg-stone-50 dark:bg-stone-800/50 hover:bg-stone-100 dark:hover:bg-stone-800 transition-colors"
              >
                <div className="flex items-center gap-3">
                  <i className={`fa-solid fa-chevron-${expandedLabs.has('_system') ? 'down' : 'right'} text-xs text-stone-400`} />
                  <i className="fa-solid fa-gear text-stone-500" />
                  <span className="font-medium text-stone-600 dark:text-stone-400">System Containers</span>
                  <span className="text-xs text-stone-500 dark:text-stone-400">
                    {filteredData.system_containers.length} container{filteredData.system_containers.length !== 1 ? 's' : ''}
                  </span>
                </div>
              </button>
              {expandedLabs.has('_system') && (
                <div className="divide-y divide-stone-100 dark:divide-stone-700">
                  {filteredData.system_containers.map((container, idx) => (
                    <div key={idx} className="px-4 py-2 flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <i className="fa-solid fa-cube text-stone-400 text-xs" />
                        <div>
                          <div className="text-sm font-medium text-stone-700 dark:text-stone-300">
                            {container.name}
                          </div>
                          <div className="text-xs text-stone-400">{container.image}</div>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-stone-400 dark:text-stone-500">{container.agent_name}</span>
                        <StatusBadge status={container.status} />
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Empty State */}
          {Object.keys(filteredData.by_lab).length === 0 && filteredData.system_containers.length === 0 && (
            <div className="text-center py-8 text-stone-500 dark:text-stone-400">
              <i className="fa-solid fa-cube text-2xl mb-2" />
              <p className="text-sm">No containers found</p>
            </div>
          )}
        </div>
      ) : (
        <div className="text-center py-8 text-red-500">Failed to load data</div>
      )}
    </DetailPopup>
  );
};

export default ContainersPopup;
