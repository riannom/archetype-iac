import React, { useMemo } from 'react';
import type { AgentGraphNode, CrossHostBundle } from './types';

interface TunnelDetailViewProps {
  selectedAgentNodes: AgentGraphNode[];
  relevantBundles: CrossHostBundle[];
}

const STATE_ORDER: Record<string, number> = {
  error: 0,
  pending: 1,
  unknown: 2,
  down: 3,
  up: 4,
};

const STATE_DOT_COLORS: Record<string, string> = {
  up: 'bg-green-500',
  down: 'bg-stone-500',
  pending: 'bg-amber-500',
  error: 'bg-red-500',
  unknown: 'bg-stone-600',
};

const STATE_TEXT_COLORS: Record<string, string> = {
  up: 'text-green-400',
  down: 'text-stone-500',
  pending: 'text-amber-400',
  error: 'text-red-400',
  unknown: 'text-stone-500',
};

const ROW_TINTS: Record<string, string> = {
  up: 'bg-green-950/20',
  error: 'bg-red-950/20',
  pending: 'bg-amber-950/15',
};

const TunnelDetailView: React.FC<TunnelDetailViewProps> = ({
  selectedAgentNodes,
  relevantBundles,
}) => {
  const sortedLinks = useMemo(() => {
    const allLinks = relevantBundles.flatMap(b => b.links);
    return allLinks.sort((a, b) => {
      const aOrder = STATE_ORDER[a.actual_state] ?? 5;
      const bOrder = STATE_ORDER[b.actual_state] ?? 5;
      if (aOrder !== bOrder) return aOrder - bOrder;
      return a.link_name.localeCompare(b.link_name);
    });
  }, [relevantBundles]);

  if (sortedLinks.length === 0) {
    return (
      <div className="text-xs text-stone-500 italic py-2">
        No cross-host tunnels between selected agents
      </div>
    );
  }

  return (
    <div className="flex flex-col min-h-0">
      {/* Selected agents chips */}
      <div className="flex items-center gap-2 mb-3">
        <span className="text-[10px] text-stone-500 uppercase font-bold tracking-wider">
          Tunnels between
        </span>
        {selectedAgentNodes.map((agent) => (
          <span
            key={agent.agentId}
            className="inline-flex items-center gap-1.5 text-[11px] text-stone-300 font-mono"
          >
            <span
              className="w-2 h-2 rounded-full flex-shrink-0"
              style={{ backgroundColor: agent.color }}
            />
            {agent.agentName}
          </span>
        ))}
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-[10px] text-stone-500 uppercase border-b border-stone-700/50">
              <th className="px-2 py-1.5 text-left font-semibold">Source</th>
              <th className="px-2 py-1.5 text-center font-semibold">Src VLAN</th>
              <th className="px-2 py-1.5 text-center font-semibold">State</th>
              <th className="px-2 py-1.5 text-center font-semibold">VNI</th>
              <th className="px-2 py-1.5 text-center font-semibold">Tgt VLAN</th>
              <th className="px-2 py-1.5 text-left font-semibold">Target</th>
              <th className="px-2 py-1.5 text-left font-semibold">Error</th>
            </tr>
          </thead>
          <tbody>
            {sortedLinks.map((ls) => {
              const parts = ls.link_name.split('-');
              const srcPart = parts[0] || '';
              const tgtPart = parts.slice(1).join('-') || '';
              const dotColor = STATE_DOT_COLORS[ls.actual_state] || STATE_DOT_COLORS.unknown;
              const textColor = STATE_TEXT_COLORS[ls.actual_state] || STATE_TEXT_COLORS.unknown;

              const rowTint = ROW_TINTS[ls.actual_state] || '';

              return (
                <tr
                  key={ls.link_name}
                  className={`border-b border-stone-800/50 hover:bg-stone-800/30 transition-colors ${rowTint}`}
                >
                  <td className="px-2 py-1.5 font-mono text-stone-300 whitespace-nowrap">
                    {srcPart}
                  </td>
                  <td className="px-2 py-1.5 text-center">
                    {ls.source_vlan_tag != null ? (
                      <span className="font-mono text-[10px] px-1 py-0.5 bg-stone-800 text-stone-400 rounded">
                        {ls.source_vlan_tag}
                      </span>
                    ) : (
                      <span className="text-stone-600">-</span>
                    )}
                  </td>
                  <td className="px-2 py-1.5 text-center">
                    <div className="flex items-center justify-center gap-1">
                      <div className={`w-1.5 h-1.5 rounded-full ${dotColor}`} />
                      <span className={`font-medium ${textColor}`}>{ls.actual_state}</span>
                    </div>
                  </td>
                  <td className="px-2 py-1.5 text-center">
                    {ls.vni != null ? (
                      <span className="font-mono text-[10px] px-1 py-0.5 bg-violet-950/40 text-violet-400 rounded">
                        {ls.vni}
                      </span>
                    ) : (
                      <span className="text-stone-600">-</span>
                    )}
                  </td>
                  <td className="px-2 py-1.5 text-center">
                    {ls.target_vlan_tag != null ? (
                      <span className="font-mono text-[10px] px-1 py-0.5 bg-stone-800 text-stone-400 rounded">
                        {ls.target_vlan_tag}
                      </span>
                    ) : (
                      <span className="text-stone-600">-</span>
                    )}
                  </td>
                  <td className="px-2 py-1.5 font-mono text-stone-300 whitespace-nowrap">
                    {tgtPart}
                  </td>
                  <td className="px-2 py-1.5 max-w-[180px]">
                    {ls.error_message ? (
                      <span
                        className="text-red-400 truncate block"
                        title={ls.error_message}
                      >
                        {ls.error_message}
                      </span>
                    ) : (
                      <span className="text-stone-600">-</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default TunnelDetailView;
