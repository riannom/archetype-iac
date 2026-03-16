import React from 'react';
import type { AgentGraphNode } from './types';

interface AgentDetailViewProps {
  agent: AgentGraphNode;
}

const STATE_DOT_COLORS: Record<string, string> = {
  running: 'bg-green-500',
  starting: 'bg-amber-500',
  pending: 'bg-amber-400',
  stopped: 'bg-stone-500',
  stopping: 'bg-amber-500',
  error: 'bg-red-500',
  exited: 'bg-stone-500',
  undeployed: 'bg-stone-600',
};

const LINK_STATE_COLORS: Record<string, string> = {
  up: 'bg-green-500',
  down: 'bg-stone-500',
  pending: 'bg-amber-500',
  error: 'bg-red-500',
  unknown: 'bg-stone-600',
};

const NODE_ROW_TINTS: Record<string, string> = {
  running: 'bg-green-950/20',
  error: 'bg-red-950/20',
  starting: 'bg-amber-950/15',
  pending: 'bg-amber-950/15',
  stopping: 'bg-amber-950/15',
};

const LINK_ROW_TINTS: Record<string, string> = {
  up: 'bg-green-950/20',
  error: 'bg-red-950/20',
  pending: 'bg-amber-950/15',
};

const AgentDetailView: React.FC<AgentDetailViewProps> = ({ agent }) => {
  return (
    <div className="flex gap-6 min-h-0 overflow-hidden">
      {/* Nodes column */}
      <div className="flex-1 min-w-0 flex flex-col">
        <h4 className="text-[11px] font-bold uppercase text-stone-500 mb-2 tracking-wider">
          Nodes
        </h4>
        <div className="flex-1 overflow-y-auto space-y-0.5">
          {agent.nodes.length === 0 ? (
            <div className="text-xs text-stone-600 italic">No nodes deployed</div>
          ) : (
            agent.nodes.map((nws) => {
              const state = nws.state?.actual_state || 'undeployed';
              const dot = STATE_DOT_COLORS[state] || STATE_DOT_COLORS.undeployed;
              const tint = NODE_ROW_TINTS[state] || '';
              return (
                <div
                  key={nws.node.id}
                  className={`flex items-center gap-2 py-1 px-1.5 rounded hover:bg-stone-800/40 ${tint}`}
                >
                  <div className={`w-2 h-2 rounded-full flex-shrink-0 ${dot}`} />
                  <span className="font-mono text-xs text-stone-300 truncate">
                    {nws.node.name}
                  </span>
                  <span className="ml-auto text-[11px] text-stone-500 font-mono">
                    {state}
                  </span>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* Divider */}
      <div className="w-px bg-stone-700/50 flex-shrink-0" />

      {/* Local links column */}
      <div className="flex-1 min-w-0 flex flex-col">
        <h4 className="text-[11px] font-bold uppercase text-stone-500 mb-2 tracking-wider">
          Local Links
        </h4>
        <div className="flex-1 overflow-y-auto space-y-0.5">
          {agent.localLinks.length === 0 ? (
            <div className="text-xs text-stone-600 italic">No local links</div>
          ) : (
            agent.localLinks.map((ls) => {
              const dot = LINK_STATE_COLORS[ls.actual_state] || LINK_STATE_COLORS.unknown;
              const linkTint = LINK_ROW_TINTS[ls.actual_state] || '';
              const parts = ls.link_name.split('-');
              const srcPart = parts[0] || '';
              const tgtPart = parts.slice(1).join('-') || '';

              return (
                <div
                  key={ls.link_name}
                  className={`flex items-center gap-1.5 py-1 px-1.5 rounded hover:bg-stone-800/40 ${linkTint}`}
                >
                  <span
                    className="font-mono text-[11px] text-stone-400 truncate max-w-[100px]"
                    title={srcPart}
                  >
                    {srcPart}
                  </span>
                  {ls.source_vlan_tag != null && (
                    <VlanBadge tag={ls.source_vlan_tag} />
                  )}
                  <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${dot}`} />
                  {ls.target_vlan_tag != null && (
                    <VlanBadge tag={ls.target_vlan_tag} />
                  )}
                  <span
                    className="font-mono text-[11px] text-stone-400 truncate max-w-[100px]"
                    title={tgtPart}
                  >
                    {tgtPart}
                  </span>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
};

const VlanBadge: React.FC<{ tag: number }> = ({ tag }) => (
  <span className="font-mono text-[11px] px-1 py-0.5 bg-stone-800 text-stone-400 rounded flex-shrink-0">
    v{tag}
  </span>
);

export default AgentDetailView;
