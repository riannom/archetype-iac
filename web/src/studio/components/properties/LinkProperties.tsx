import React from 'react';
import { Node, Link } from '../../types';
import InterfaceSelect from '../InterfaceSelect';
import { PortManager } from '../../hooks/usePortManager';

interface LinkPropertiesProps {
  link: Link;
  nodes: Node[];
  portManager: PortManager;
  onUpdateLink: (id: string, updates: Partial<Link>) => void;
  onDelete: (id: string) => void;
}

const LinkProperties: React.FC<LinkPropertiesProps> = ({
  link,
  nodes,
  portManager,
  onUpdateLink,
  onDelete,
}) => {
  const sourceNode = nodes.find(n => n.id === link.source);
  const targetNode = nodes.find(n => n.id === link.target);
  const sourceAvailable = portManager.getAvailableInterfaces(link.source);
  const targetAvailable = portManager.getAvailableInterfaces(link.target);

  return (
    <div className="w-80 bg-white dark:bg-stone-900 border-l border-stone-200 dark:border-stone-700 overflow-y-auto">
      <div className="p-4 border-b border-stone-200 dark:border-stone-700 flex justify-between items-center bg-stone-100/50 dark:bg-stone-800/50">
        <h2 className="text-sm font-bold uppercase tracking-wider text-sage-600 dark:text-sage-400">Link Properties</h2>
        <button onClick={() => onDelete(link.id)} className="p-1.5 text-red-500 hover:bg-red-100 dark:hover:bg-red-950/30 rounded"><i className="fa-solid fa-trash-can"></i></button>
      </div>
      <div className="p-6 space-y-6">
        <div className="p-3 bg-stone-100 dark:bg-stone-800 rounded border border-stone-200 dark:border-stone-700">
          <div className="text-[10px] text-stone-500 font-bold uppercase mb-2">Topology Context</div>
          <div className="flex items-center justify-between text-xs">
            <span className="text-stone-700 dark:text-stone-300">{sourceNode?.name}</span>
            <i className="fa-solid fa-link text-stone-400 dark:text-stone-600 mx-2"></i>
            <span className="text-stone-700 dark:text-stone-300">{targetNode?.name}</span>
          </div>
        </div>
        <div className="space-y-4 pt-2">
          <div className="space-y-2">
            <label className="text-[11px] font-bold text-stone-500 uppercase">{sourceNode?.name} Interface</label>
            <InterfaceSelect
              value={link.sourceInterface || ''}
              availableInterfaces={sourceAvailable}
              onChange={(value) => onUpdateLink(link.id, { sourceInterface: value })}
              placeholder="Select interface"
            />
          </div>
          <div className="space-y-2">
            <label className="text-[11px] font-bold text-stone-500 uppercase">{targetNode?.name} Interface</label>
            <InterfaceSelect
              value={link.targetInterface || ''}
              availableInterfaces={targetAvailable}
              onChange={(value) => onUpdateLink(link.id, { targetInterface: value })}
              placeholder="Select interface"
            />
          </div>
        </div>
      </div>
    </div>
  );
};

export default LinkProperties;
