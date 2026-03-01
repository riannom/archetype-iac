import React from 'react';
import { Node, isExternalNetworkNode } from '../../types';
import { RuntimeStatus } from '../RuntimeControl';
import { ContextMenu } from './types';

interface ContextMenuOverlayProps {
  contextMenu: ContextMenu;
  nodeMap: Map<string, Node>;
  runtimeStates: Record<string, RuntimeStatus>;
  onAction: (action: string) => void;
}

export const ContextMenuOverlay: React.FC<ContextMenuOverlayProps> = ({
  contextMenu,
  nodeMap,
  runtimeStates,
  onAction,
}) => {
  return (
    <div className="fixed z-[100] w-52 bg-white dark:bg-stone-900 border border-stone-200 dark:border-stone-700 rounded-xl shadow-2xl py-2 animate-in fade-in zoom-in duration-100" style={{ left: contextMenu.x, top: contextMenu.y }} onMouseDown={(e) => e.stopPropagation()}>
      <div className="px-4 py-2 border-b border-stone-100 dark:border-stone-800 mb-1 flex items-center justify-between">
        <span className="text-[10px] font-black text-stone-400 dark:text-stone-500 uppercase tracking-widest">
          {contextMenu.type === 'node'
            ? (isExternalNetworkNode(nodeMap.get(contextMenu.id)!) ? 'External Network' : 'Node Actions')
            : 'Link Actions'}
        </span>
      </div>
      {contextMenu.type === 'node' && (() => {
        const contextNode = nodeMap.get(contextMenu.id);
        // External network nodes only have delete action
        if (contextNode && isExternalNetworkNode(contextNode)) {
          return null;
        }
        const nodeStatus = runtimeStates[contextMenu.id] || 'stopped';
        const isNodeRunning = nodeStatus === 'running' || nodeStatus === 'booting';
        return (
          <>
            <button onClick={() => onAction('console')} className="w-full flex items-center gap-3 px-4 py-2 text-xs text-stone-700 dark:text-stone-300 hover:bg-sage-600 hover:text-white transition-colors">
              <i className="fa-solid fa-terminal w-4"></i> Open Console
            </button>
            <button onClick={() => onAction('extract-config')} className="w-full flex items-center gap-3 px-4 py-2 text-xs text-stone-700 dark:text-stone-300 hover:bg-sage-600 hover:text-white transition-colors">
              <i className="fa-solid fa-download w-4"></i> Extract Config
            </button>
            {!isNodeRunning && (
              <button onClick={() => onAction('start')} className="w-full flex items-center gap-3 px-4 py-2 text-xs text-green-600 dark:text-green-400 hover:bg-green-600 hover:text-white transition-colors">
                <i className="fa-solid fa-play w-4"></i> Start Node
              </button>
            )}
            {isNodeRunning && (
              <button onClick={() => onAction('stop')} className="w-full flex items-center gap-3 px-4 py-2 text-xs text-red-600 dark:text-red-400 hover:bg-red-600 hover:text-white transition-colors">
                <i className="fa-solid fa-power-off w-4"></i> Stop Node
              </button>
            )}
            <div className="h-px bg-stone-100 dark:bg-stone-800 my-1 mx-2"></div>
          </>
        );
      })()}
      <button onClick={() => onAction('delete')} className="w-full flex items-center gap-3 px-4 py-2 text-xs text-red-600 dark:text-red-500 hover:bg-red-600 hover:text-white transition-colors">
        <i className="fa-solid fa-trash-can w-4"></i>
        {contextMenu.type === 'node'
          ? (nodeMap.get(contextMenu.id) && isExternalNetworkNode(nodeMap.get(contextMenu.id)!)
              ? 'Remove External Network'
              : 'Remove Device')
          : 'Delete Connection'}
      </button>
    </div>
  );
};
