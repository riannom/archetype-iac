
import React, { useState } from 'react';
import { Node, Link, Annotation, DeviceModel } from '../types';
import { RuntimeStatus } from './RuntimeControl';
import InterfaceSelect from './InterfaceSelect';
import { PortManager } from '../hooks/usePortManager';

interface PropertiesPanelProps {
  selectedItem: Node | Link | Annotation | null;
  onUpdateNode: (id: string, updates: Partial<Node>) => void;
  onUpdateLink: (id: string, updates: Partial<Link>) => void;
  onUpdateAnnotation: (id: string, updates: Partial<Annotation>) => void;
  onDelete: (id: string) => void;
  nodes: Node[];
  links: Link[];
  onOpenConsole: (nodeId: string) => void;
  runtimeStates: Record<string, RuntimeStatus>;
  onUpdateStatus: (nodeId: string, status: RuntimeStatus) => void;
  deviceModels: DeviceModel[];
  portManager: PortManager;
  onOpenConfigViewer?: (nodeId: string, nodeName: string) => void;
}

const PropertiesPanel: React.FC<PropertiesPanelProps> = ({
  selectedItem, onUpdateNode, onUpdateLink, onUpdateAnnotation, onDelete, nodes, links, onOpenConsole, runtimeStates, onUpdateStatus, deviceModels, portManager, onOpenConfigViewer
}) => {
  const [activeTab, setActiveTab] = useState<'general' | 'hardware' | 'connectivity' | 'config'>('general');

  if (!selectedItem) {
    return null;
  }

  const isLink = 'source' in selectedItem && 'target' in selectedItem;
  const isNode = 'model' in selectedItem;
  const isAnnotation = !isLink && !isNode;

  if (isAnnotation) {
    const ann = selectedItem as Annotation;
    return (
      <div className="w-80 bg-white dark:bg-stone-900 border-l border-stone-200 dark:border-stone-700 overflow-y-auto">
        <div className="p-4 border-b border-stone-200 dark:border-stone-700 flex justify-between items-center bg-stone-100/50 dark:bg-stone-800/50">
          <h2 className="text-sm font-bold uppercase tracking-wider text-sage-600 dark:text-sage-400">Annotation Settings</h2>
          <button onClick={() => onDelete(ann.id)} className="p-1.5 text-red-500 hover:bg-red-100 dark:hover:bg-red-950/30 rounded transition-all">
            <i className="fa-solid fa-trash-can"></i>
          </button>
        </div>
        <div className="p-6 space-y-6">
          {(ann.type === 'text' || ann.type === 'caption') && (
            <div className="space-y-2">
              <label className="text-[11px] font-bold text-stone-500 uppercase">Text Content</label>
              <textarea
                value={ann.text || ''}
                onChange={(e) => onUpdateAnnotation(ann.id, { text: e.target.value })}
                className="w-full bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-600 rounded px-3 py-2 text-sm text-stone-900 dark:text-stone-100 focus:outline-none focus:border-sage-500 min-h-[80px]"
              />
            </div>
          )}
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <label className="text-[11px] font-bold text-stone-500 uppercase tracking-tighter">Color</label>
              <input type="color" value={ann.color || '#65A30D'} onChange={(e) => onUpdateAnnotation(ann.id, { color: e.target.value })} className="w-full h-10 bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-600 rounded p-1 cursor-pointer" />
            </div>
            {(ann.type === 'text') && (
              <div className="space-y-2">
                <label className="text-[11px] font-bold text-stone-500 uppercase tracking-tighter">Size</label>
                <input type="number" value={ann.fontSize || 14} onChange={(e) => onUpdateAnnotation(ann.id, { fontSize: parseInt(e.target.value) })} className="w-full bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-600 rounded px-3 py-2 text-sm text-stone-900 dark:text-stone-100 focus:outline-none focus:border-sage-500" />
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  if (isLink) {
    const link = selectedItem;
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
  }

  const node = selectedItem as Node;
  const nodeLinks = links.filter(l => l.source === node.id || l.target === node.id);
  const model = deviceModels.find(m => m.id === node.model);
  const status = runtimeStates[node.id] || 'stopped';

  // Check if any nodes are currently running (lab is deployed)
  const hasRunningNodes = nodes.some(n => {
    const s = runtimeStates[n.id];
    return s === 'running' || s === 'booting';
  });

  return (
    <div className="w-80 bg-white dark:bg-stone-900 border-l border-stone-200 dark:border-stone-700 overflow-hidden flex flex-col">
      <div className="p-4 border-b border-stone-200 dark:border-stone-700 flex justify-between items-center bg-stone-100/50 dark:bg-stone-800/50">
        <div>
          <h2 className="text-xs font-black uppercase tracking-widest text-stone-900 dark:text-white">{node.name}</h2>
          <div className="text-[9px] font-bold text-sage-600 dark:text-sage-500 tracking-tighter uppercase">{model?.name}</div>
        </div>
        <button onClick={() => onDelete(node.id)} className="p-1.5 text-stone-500 hover:text-red-500 hover:bg-red-100 dark:hover:bg-red-950/30 rounded transition-all">
          <i className="fa-solid fa-trash-can text-sm"></i>
        </button>
      </div>

      <div className="flex bg-stone-50/50 dark:bg-stone-950/50 border-b border-stone-200 dark:border-stone-800">
        {(['general', 'hardware', 'connectivity', 'config'] as const).map(tab => (
          <button key={tab} onClick={() => setActiveTab(tab)} className={`flex-1 py-3 text-[9px] font-black uppercase tracking-tighter border-b-2 transition-all ${activeTab === tab ? 'text-sage-600 dark:text-sage-500 border-sage-500 bg-sage-500/5' : 'text-stone-500 border-transparent hover:text-stone-700 dark:hover:text-stone-300'}`}>
            {tab}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto p-5 custom-scrollbar">
        {activeTab === 'general' && (
          <div className="space-y-6">
            <div className="p-4 bg-stone-50/50 dark:bg-stone-950/50 rounded-xl border border-stone-200 dark:border-stone-800">
               <div className="flex items-center justify-between mb-2">
                 <span className="text-[10px] font-bold text-stone-500 uppercase tracking-widest">Status</span>
                 <span className={`text-[9px] font-black uppercase px-2 py-0.5 rounded border ${status === 'running' ? 'text-green-600 dark:text-green-500 border-green-500/20 bg-green-500/5' : status === 'booting' ? 'text-yellow-600 dark:text-yellow-500 border-yellow-500/20 bg-yellow-500/5' : 'text-stone-500 border-stone-300 dark:border-stone-700 bg-stone-100 dark:bg-stone-800'}`}>{status}</span>
               </div>
               <div className="grid grid-cols-2 gap-2 mt-4">
                  {status === 'stopped' ? (
                    <button
                      onClick={() => onUpdateStatus(node.id, 'booting')}
                      className="flex items-center justify-center gap-2 py-2 bg-green-600 hover:bg-green-500 text-white text-[10px] font-bold rounded-lg transition-all"
                      title={hasRunningNodes ? "Start this node" : "Deploy lab (starts all nodes)"}
                    >
                      <i className={`fa-solid ${hasRunningNodes ? 'fa-play' : 'fa-rocket'}`}></i> {hasRunningNodes ? 'START' : 'DEPLOY'}
                    </button>
                  ) : (
                    <button onClick={() => onUpdateStatus(node.id, 'stopped')} className="flex items-center justify-center gap-2 py-2 bg-red-600 hover:bg-red-500 text-white text-[10px] font-bold rounded-lg transition-all"><i className="fa-solid fa-power-off"></i> STOP</button>
                  )}
                  {status !== 'stopped' && (
                    <button onClick={() => onUpdateStatus(node.id, 'booting')} className="flex items-center justify-center gap-2 py-2 bg-stone-200 dark:bg-stone-800 hover:bg-stone-300 dark:hover:bg-stone-700 text-stone-700 dark:text-stone-300 text-[10px] font-bold rounded-lg transition-all border border-stone-300 dark:border-stone-700"><i className="fa-solid fa-rotate"></i> RELOAD</button>
                  )}
               </div>
            </div>

            <div className="space-y-2">
              <label className="text-[10px] font-bold text-stone-500 uppercase tracking-widest">Display Name</label>
              <input type="text" value={node.name} onChange={(e) => onUpdateNode(node.id, { name: e.target.value })} className="w-full bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-700 rounded-lg px-3 py-2 text-sm text-stone-900 dark:text-stone-100 focus:outline-none focus:border-sage-500" />
            </div>
            <div className="space-y-2">
              <label className="text-[10px] font-bold text-stone-500 uppercase tracking-widest">Image Version</label>
              <select value={node.version} onChange={(e) => onUpdateNode(node.id, { version: e.target.value })} className="w-full bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-700 rounded-lg px-3 py-2 text-sm text-stone-900 dark:text-stone-100 focus:outline-none focus:border-sage-500 appearance-none">
                {(model?.versions || [node.version]).map(v => <option key={v} value={v}>{v}</option>)}
              </select>
            </div>
            <div className="pt-4 space-y-3">
              <button onClick={() => onOpenConsole(node.id)} className="w-full flex items-center justify-between px-4 py-2.5 bg-sage-600 hover:bg-sage-500 rounded-lg text-xs text-white font-bold transition-all shadow-lg shadow-sage-900/20">
                <span>OPEN CONSOLE</span>
                <i className="fa-solid fa-terminal opacity-50"></i>
              </button>
            </div>
          </div>
        )}

        {activeTab === 'hardware' && (
          <div className="space-y-8">
            <div className="space-y-4">
              <div className="flex justify-between items-end"><label className="text-[10px] font-bold text-stone-500 uppercase tracking-widest">CPU Allocation</label><span className="text-xs font-black text-sage-600 dark:text-sage-400">{node.cpu || 1} Cores</span></div>
              <input type="range" min="1" max="16" step="1" value={node.cpu || 1} onChange={(e) => onUpdateNode(node.id, { cpu: parseInt(e.target.value) })} className="w-full h-1.5 bg-stone-200 dark:bg-stone-800 rounded-lg appearance-none cursor-pointer accent-sage-500" />
              <div className="flex justify-between text-[8px] font-bold text-stone-400 dark:text-stone-600"><span>1 Core</span><span>16 Cores</span></div>
            </div>
            <div className="space-y-4">
              <div className="flex justify-between items-end"><label className="text-[10px] font-bold text-stone-500 uppercase tracking-widest">RAM Allocation</label><span className="text-xs font-black text-sage-600 dark:text-sage-400">{(node.memory || 1024) / 1024} GB</span></div>
              <input type="range" min="512" max="16384" step="512" value={node.memory || 1024} onChange={(e) => onUpdateNode(node.id, { memory: parseInt(e.target.value) })} className="w-full h-1.5 bg-stone-200 dark:bg-stone-800 rounded-lg appearance-none cursor-pointer accent-sage-500" />
              <div className="flex justify-between text-[8px] font-bold text-stone-400 dark:text-stone-600"><span>512MB</span><span>16GB</span></div>
            </div>
          </div>
        )}

        {activeTab === 'connectivity' && (
          <div className="space-y-4">
            <div className="text-[10px] font-bold text-stone-500 uppercase tracking-widest mb-3">Active Interfaces</div>
            {nodeLinks.length > 0 ? nodeLinks.map(link => {
              const otherId = link.source === node.id ? link.target : link.source;
              const otherNode = nodes.find(n => n.id === otherId);
              const isSource = link.source === node.id;
              const currentInterface = isSource ? link.sourceInterface : link.targetInterface;
              const availableInterfaces = portManager.getAvailableInterfaces(node.id);
              return (
                <div key={link.id} className="p-3 bg-stone-100/50 dark:bg-stone-800/50 border border-stone-200 dark:border-stone-800 rounded-xl hover:border-stone-300 dark:hover:border-stone-700 transition-all">
                  <div className="flex items-center justify-between mb-2"><span className="text-[10px] font-black text-stone-600 dark:text-stone-400 uppercase tracking-tighter">Connection to {otherNode?.name}</span><i className="fa-solid fa-link text-[10px] text-sage-500/50"></i></div>
                  <div className="space-y-2">
                    <label className="text-[9px] font-bold text-stone-400 dark:text-stone-600 uppercase">Local Interface</label>
                    <InterfaceSelect
                      value={currentInterface || ''}
                      availableInterfaces={availableInterfaces}
                      onChange={(value) => onUpdateLink(link.id, isSource ? { sourceInterface: value } : { targetInterface: value })}
                      placeholder="Select interface"
                    />
                  </div>
                </div>
              );
            }) : (
              <div className="py-12 flex flex-col items-center justify-center text-stone-400 dark:text-stone-600"><i className="fa-solid fa-circle-nodes text-2xl opacity-10 mb-2"></i><p className="text-[10px] font-bold uppercase tracking-tight">No active links</p></div>
            )}
          </div>
        )}

        {activeTab === 'config' && (
          <div className="h-full flex flex-col">
            <div className="flex items-center justify-between mb-3">
              <label className="text-[10px] font-bold text-stone-500 uppercase tracking-widest">Startup Configuration</label>
              {onOpenConfigViewer && (
                <button
                  onClick={() => onOpenConfigViewer(node.id, node.container_name || node.name)}
                  className="flex items-center gap-1.5 px-2 py-1 text-[9px] font-bold uppercase text-sage-600 dark:text-sage-400 hover:bg-sage-500/10 rounded transition-colors"
                  title="View saved config in larger window"
                >
                  <i className="fa-solid fa-expand" />
                  Expand
                </button>
              )}
            </div>
            <textarea value={node.config || ''} onChange={(e) => onUpdateNode(node.id, { config: e.target.value })} spellCheck={false} className="flex-1 min-h-[300px] bg-stone-50 dark:bg-black text-sage-700 dark:text-sage-400 font-mono text-[11px] p-4 rounded-xl border border-stone-200 dark:border-stone-800 focus:outline-none focus:border-sage-500/50 resize-none" />
          </div>
        )}
      </div>
    </div>
  );
};

export default PropertiesPanel;
