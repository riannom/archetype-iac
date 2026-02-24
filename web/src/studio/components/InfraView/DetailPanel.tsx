import React, { useMemo } from 'react';
import type { AgentGraphNode, CrossHostBundle } from './types';
import AgentDetailView from './AgentDetailView';
import TunnelDetailView from './TunnelDetailView';

interface DetailPanelProps {
  selectedIds: Set<string>;
  agentNodes: AgentGraphNode[];
  crossHostBundles: CrossHostBundle[];
  onClose: () => void;
}

const DetailPanel: React.FC<DetailPanelProps> = ({
  selectedIds,
  agentNodes,
  crossHostBundles,
  onClose,
}) => {
  const isVisible = selectedIds.size > 0;

  const selectedAgentNodes = useMemo(
    () => agentNodes.filter(a => selectedIds.has(a.agentId)),
    [agentNodes, selectedIds],
  );

  const relevantBundles = useMemo(() => {
    if (selectedIds.size < 2) return [];
    return crossHostBundles.filter(
      b => selectedIds.has(b.agentA) && selectedIds.has(b.agentB),
    );
  }, [crossHostBundles, selectedIds]);

  const singleAgent = selectedIds.size === 1 ? selectedAgentNodes[0] : null;

  return (
    <div
      className="border-t border-stone-700/50 bg-stone-900/90 backdrop-blur-xl overflow-hidden transition-all duration-300 ease-out"
      style={{ maxHeight: isVisible ? '45%' : '0px' }}
    >
      {isVisible && (
        <div className="flex flex-col h-full p-4">
          {/* Header row */}
          <div className="flex items-center justify-between mb-3 flex-shrink-0">
            <div className="flex items-center gap-3">
              {selectedAgentNodes.map((agent) => (
                <span
                  key={agent.agentId}
                  className="inline-flex items-center gap-1.5 text-xs text-stone-200 font-medium"
                >
                  <span
                    className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                    style={{ backgroundColor: agent.color }}
                  />
                  {agent.agentName}
                  <span className="text-stone-500 font-mono text-[10px]">
                    {agent.stats.nodeCount} nodes ({agent.stats.runningCount} running)
                  </span>
                </span>
              ))}
            </div>
            <button
              onClick={onClose}
              className="text-stone-500 hover:text-stone-300 transition-colors p-1"
              title="Close"
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <path d="M3 3l8 8M11 3l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              </svg>
            </button>
          </div>

          {/* Content */}
          <div className="flex-1 min-h-0 overflow-auto">
            {singleAgent ? (
              <AgentDetailView agent={singleAgent} />
            ) : (
              <TunnelDetailView
                selectedAgentNodes={selectedAgentNodes}
                relevantBundles={relevantBundles}
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default DetailPanel;
