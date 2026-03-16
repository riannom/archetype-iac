import React, { useMemo, useState } from 'react';
import { Node, DeviceModel, isDeviceNode } from '../types';
import { RuntimeStatus } from './RuntimeControl';
import FilterChip from './FilterChip';

interface NodeListPanelProps {
  nodes: Node[];
  runtimeStates: Record<string, RuntimeStatus>;
  deviceModels: DeviceModel[];
  selectedId: string | null;
  onFocusNode: (nodeId: string) => void;
  onOpenConsole: (nodeId: string) => void;
  onSelectNode: (nodeId: string) => void;
}

type StateFilter = 'running' | 'booting' | 'stopped' | 'error';

const STATE_FILTERS: { key: StateFilter; label: string; color: 'green' | 'amber' | 'gray' | 'red' }[] = [
  { key: 'running', label: 'Running', color: 'green' },
  { key: 'booting', label: 'Booting', color: 'amber' },
  { key: 'stopped', label: 'Stopped', color: 'gray' },
  { key: 'error', label: 'Error', color: 'red' },
];

const getStatusDotClass = (status: RuntimeStatus | undefined) => {
  switch (status) {
    case 'running': return 'bg-emerald-500';
    case 'booting': return 'bg-amber-500 animate-pulse';
    case 'stopped': return 'bg-stone-500';
    case 'error': return 'bg-red-500';
    default: return 'bg-stone-300 dark:bg-stone-600';
  }
};

const NodeListPanel: React.FC<NodeListPanelProps> = ({
  nodes,
  runtimeStates,
  deviceModels,
  selectedId,
  onFocusNode,
  onOpenConsole,
  onSelectNode,
}) => {
  const [search, setSearch] = useState('');
  const [activeFilters, setActiveFilters] = useState<Set<StateFilter>>(new Set());

  const deviceNodes = useMemo(
    () => nodes.filter(isDeviceNode).sort((a, b) => a.name.localeCompare(b.name)),
    [nodes],
  );

  const modelMap = useMemo(
    () => new Map(deviceModels.map(m => [m.id, m])),
    [deviceModels],
  );

  const stateCounts = useMemo(() => {
    const counts: Record<StateFilter, number> = { running: 0, booting: 0, stopped: 0, error: 0 };
    for (const node of deviceNodes) {
      const s = runtimeStates[node.id];
      if (s === 'running') counts.running++;
      else if (s === 'booting') counts.booting++;
      else if (s === 'error') counts.error++;
      else if (s === 'stopped') counts.stopped++;
      else counts.stopped++; // undeployed counts as stopped
    }
    return counts;
  }, [deviceNodes, runtimeStates]);

  const filtered = useMemo(() => {
    let list = deviceNodes;
    if (search) {
      const q = search.toLowerCase();
      list = list.filter(n => n.name.toLowerCase().includes(q));
    }
    if (activeFilters.size > 0) {
      list = list.filter(n => {
        const s = runtimeStates[n.id];
        if (activeFilters.has('running') && s === 'running') return true;
        if (activeFilters.has('booting') && s === 'booting') return true;
        if (activeFilters.has('error') && s === 'error') return true;
        if (activeFilters.has('stopped') && (s === 'stopped' || !s)) return true;
        return false;
      });
    }
    return list;
  }, [deviceNodes, search, activeFilters, runtimeStates]);

  const toggleFilter = (key: StateFilter) => {
    setActiveFilters(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const isVm = (node: typeof deviceNodes[0]) => {
    const model = modelMap.get(node.model);
    return model?.supportedImageKinds?.includes('qcow2') ?? false;
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Search */}
      <div className="p-3 border-b border-stone-200 dark:border-stone-800">
        <div className="relative">
          <i className="fa-solid fa-search absolute left-2.5 top-1/2 -translate-y-1/2 text-[11px] text-stone-400" />
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search nodes..."
            className="w-full pl-7 pr-3 py-1.5 text-[11px] rounded-md border border-stone-200 dark:border-stone-700 bg-white dark:bg-stone-800 text-stone-800 dark:text-stone-200 placeholder-stone-400 focus:outline-none focus:ring-1 focus:ring-sage-500"
          />
        </div>
      </div>

      {/* State filter chips */}
      <div className="px-3 py-2 flex flex-wrap gap-1.5 border-b border-stone-200 dark:border-stone-800">
        {STATE_FILTERS.map(f => (
          <FilterChip
            key={f.key}
            label={f.label}
            isActive={activeFilters.has(f.key)}
            onClick={() => toggleFilter(f.key)}
            count={stateCounts[f.key]}
            variant="status"
            statusColor={f.color}
          />
        ))}
      </div>

      {/* Node list */}
      <div className="flex-1 overflow-y-auto custom-scrollbar">
        {filtered.length === 0 ? (
          <div className="p-6 text-center">
            {deviceNodes.length === 0 ? (
              <>
                <i className="fa-solid fa-diagram-project text-2xl text-stone-300 dark:text-stone-600 mb-2" />
                <p className="text-xs text-stone-500 dark:text-stone-400">No devices in topology</p>
                <p className="text-[11px] text-stone-400 dark:text-stone-500 mt-1">Use the Library tab to add devices</p>
              </>
            ) : (
              <>
                <i className="fa-solid fa-filter text-2xl text-stone-300 dark:text-stone-600 mb-2" />
                <p className="text-xs text-stone-500 dark:text-stone-400">No nodes match filter</p>
                <button
                  onClick={() => { setSearch(''); setActiveFilters(new Set()); }}
                  className="mt-2 text-[11px] font-bold text-sage-600 hover:text-sage-500"
                >
                  Clear filters
                </button>
              </>
            )}
          </div>
        ) : (
          <div className="divide-y divide-stone-100 dark:divide-stone-800/60">
            {filtered.map(node => {
              const status = runtimeStates[node.id];
              const isSelected = selectedId === node.id;
              const hasState = !!status;
              return (
                <button
                  key={node.id}
                  onClick={() => { onSelectNode(node.id); onFocusNode(node.id); }}
                  className={`w-full text-left px-3 py-2.5 flex items-center gap-2.5 transition-colors group ${
                    isSelected
                      ? 'bg-sage-600/15 border-r-2 border-sage-500'
                      : 'hover:bg-stone-100 dark:hover:bg-stone-800/60'
                  }`}
                >
                  {/* Status dot */}
                  <div className={`w-2 h-2 rounded-full flex-shrink-0 ${getStatusDotClass(status)}`} />

                  {/* Type icon */}
                  <i className={`fa-solid ${isVm(node) ? 'fa-hard-drive' : 'fa-cube'} text-[11px] text-stone-400 dark:text-stone-500 w-3 text-center`} />

                  {/* Name */}
                  <span className="flex-1 text-[11px] font-medium text-stone-700 dark:text-stone-200 truncate">
                    {node.name}
                  </span>

                  {/* Console button */}
                  {hasState && (
                    <button
                      onClick={e => { e.stopPropagation(); onOpenConsole(node.id); }}
                      className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-stone-200 dark:hover:bg-stone-700 transition-all"
                      title="Open console"
                    >
                      <i className="fa-solid fa-terminal text-[11px] text-stone-500 dark:text-stone-400" />
                    </button>
                  )}
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};

export default NodeListPanel;
