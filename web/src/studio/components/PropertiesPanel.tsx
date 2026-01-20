
import React, { useState } from 'react';
import { Node, Link, Annotation, DeviceModel } from '../types';
import { RuntimeStatus } from './RuntimeControl';

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
}

const PropertiesPanel: React.FC<PropertiesPanelProps> = ({ 
  selectedItem, onUpdateNode, onUpdateLink, onUpdateAnnotation, onDelete, nodes, links, onOpenConsole, runtimeStates, onUpdateStatus, deviceModels
}) => {
  const [activeTab, setActiveTab] = useState<'general' | 'hardware' | 'connectivity' | 'config'>('general');

  if (!selectedItem) {
    return (
      <div className="w-80 bg-slate-900 border-l border-slate-700 p-6 flex flex-col items-center justify-center text-slate-500 text-sm italic">
        <i className="fa-solid fa-i-cursor text-3xl mb-4 opacity-10"></i>
        Select an element to edit properties
      </div>
    );
  }

  const isLink = 'source' in selectedItem;
  const isAnnotation = 'type' in selectedItem && !('model' in selectedItem);

  if (isAnnotation) {
    const ann = selectedItem as Annotation;
    return (
      <div className="w-80 bg-slate-900 border-l border-slate-700 overflow-y-auto">
        <div className="p-4 border-b border-slate-700 flex justify-between items-center bg-slate-800/50">
          <h2 className="text-sm font-bold uppercase tracking-wider text-blue-400">Annotation Settings</h2>
          <button onClick={() => onDelete(ann.id)} className="p-1.5 text-red-500 hover:bg-red-950/30 rounded transition-all">
            <i className="fa-solid fa-trash-can"></i>
          </button>
        </div>
        <div className="p-6 space-y-6">
          {(ann.type === 'text' || ann.type === 'caption') && (
            <div className="space-y-2">
              <label className="text-[11px] font-bold text-slate-500 uppercase">Text Content</label>
              <textarea
                value={ann.text || ''}
                onChange={(e) => onUpdateAnnotation(ann.id, { text: e.target.value })}
                className="w-full bg-slate-800 border border-slate-600 rounded px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-blue-500 min-h-[80px]"
              />
            </div>
          )}
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <label className="text-[11px] font-bold text-slate-500 uppercase tracking-tighter">Color</label>
              <input type="color" value={ann.color || '#3b82f6'} onChange={(e) => onUpdateAnnotation(ann.id, { color: e.target.value })} className="w-full h-10 bg-slate-800 border border-slate-600 rounded p-1 cursor-pointer" />
            </div>
            {(ann.type === 'text') && (
              <div className="space-y-2">
                <label className="text-[11px] font-bold text-slate-500 uppercase tracking-tighter">Size</label>
                <input type="number" value={ann.fontSize || 14} onChange={(e) => onUpdateAnnotation(ann.id, { fontSize: parseInt(e.target.value) })} className="w-full bg-slate-800 border border-slate-600 rounded px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-blue-500" />
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  if (isLink) {
    const link = selectedItem as Link;
    const sourceNode = nodes.find(n => n.id === link.source);
    const targetNode = nodes.find(n => n.id === link.target);
    return (
      <div className="w-80 bg-slate-900 border-l border-slate-700 overflow-y-auto">
        <div className="p-4 border-b border-slate-700 flex justify-between items-center bg-slate-800/50">
          <h2 className="text-sm font-bold uppercase tracking-wider text-blue-400">Link Properties</h2>
          <button onClick={() => onDelete(link.id)} className="p-1.5 text-red-500 hover:bg-red-950/30 rounded"><i className="fa-solid fa-trash-can"></i></button>
        </div>
        <div className="p-6 space-y-6">
          <div className="p-3 bg-slate-800 rounded border border-slate-700">
            <div className="text-[10px] text-slate-500 font-bold uppercase mb-2">Topology Context</div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-slate-300">{sourceNode?.name}</span>
              <i className="fa-solid fa-link text-slate-600 mx-2"></i>
              <span className="text-slate-300">{targetNode?.name}</span>
            </div>
          </div>
          <div className="space-y-4 pt-2">
            <div className="space-y-2">
              <label className="text-[11px] font-bold text-slate-500 uppercase">Source Interface</label>
              <input type="text" value={link.sourceInterface || ''} placeholder="e.g. eth0" onChange={(e) => onUpdateLink(link.id, { sourceInterface: e.target.value })} className="w-full bg-slate-800 border border-slate-600 rounded px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-blue-500" />
            </div>
            <div className="space-y-2">
              <label className="text-[11px] font-bold text-slate-500 uppercase">Target Interface</label>
              <input type="text" value={link.targetInterface || ''} placeholder="e.g. eth0" onChange={(e) => onUpdateLink(link.id, { targetInterface: e.target.value })} className="w-full bg-slate-800 border border-slate-600 rounded px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-blue-500" />
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

  return (
    <div className="w-80 bg-slate-900 border-l border-slate-700 overflow-hidden flex flex-col">
      <div className="p-4 border-b border-slate-700 flex justify-between items-center bg-slate-800/50">
        <div>
          <h2 className="text-xs font-black uppercase tracking-widest text-white">{node.name}</h2>
          <div className="text-[9px] font-bold text-blue-500 tracking-tighter uppercase">{model?.name}</div>
        </div>
        <button onClick={() => onDelete(node.id)} className="p-1.5 text-slate-500 hover:text-red-500 hover:bg-red-950/30 rounded transition-all">
          <i className="fa-solid fa-trash-can text-sm"></i>
        </button>
      </div>

      <div className="flex bg-slate-950/50 border-b border-slate-800">
        {(['general', 'hardware', 'connectivity', 'config'] as const).map(tab => (
          <button key={tab} onClick={() => setActiveTab(tab)} className={`flex-1 py-3 text-[9px] font-black uppercase tracking-tighter border-b-2 transition-all ${activeTab === tab ? 'text-blue-500 border-blue-500 bg-blue-500/5' : 'text-slate-500 border-transparent hover:text-slate-300'}`}>
            {tab}
          </button>
        ))}
      </div>
      
      <div className="flex-1 overflow-y-auto p-5 custom-scrollbar">
        {activeTab === 'general' && (
          <div className="space-y-6">
            <div className="p-4 bg-slate-950/50 rounded-xl border border-slate-800">
               <div className="flex items-center justify-between mb-2">
                 <span className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">Status</span>
                 <span className={`text-[9px] font-black uppercase px-2 py-0.5 rounded border ${status === 'running' ? 'text-green-500 border-green-500/20 bg-green-500/5' : status === 'booting' ? 'text-yellow-500 border-yellow-500/20 bg-yellow-500/5' : 'text-slate-500 border-slate-700 bg-slate-800'}`}>{status}</span>
               </div>
               <div className="grid grid-cols-2 gap-2 mt-4">
                  {status === 'stopped' ? (
                    <button onClick={() => onUpdateStatus(node.id, 'booting')} className="flex items-center justify-center gap-2 py-2 bg-green-600 hover:bg-green-500 text-white text-[10px] font-bold rounded-lg transition-all"><i className="fa-solid fa-play"></i> START</button>
                  ) : (
                    <button onClick={() => onUpdateStatus(node.id, 'stopped')} className="flex items-center justify-center gap-2 py-2 bg-red-600 hover:bg-red-500 text-white text-[10px] font-bold rounded-lg transition-all"><i className="fa-solid fa-power-off"></i> STOP</button>
                  )}
                  <button onClick={() => onUpdateStatus(node.id, 'booting')} className="flex items-center justify-center gap-2 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 text-[10px] font-bold rounded-lg transition-all border border-slate-700"><i className="fa-solid fa-rotate"></i> RELOAD</button>
               </div>
            </div>

            <div className="space-y-2">
              <label className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">Display Name</label>
              <input type="text" value={node.name} onChange={(e) => onUpdateNode(node.id, { name: e.target.value })} className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-blue-500" />
            </div>
            <div className="space-y-2">
              <label className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">Image Version</label>
              <select value={node.version} onChange={(e) => onUpdateNode(node.id, { version: e.target.value })} className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-blue-500 appearance-none">
                {(model?.versions || [node.version]).map(v => <option key={v} value={v}>{v}</option>)}
              </select>
            </div>
            <div className="pt-4 space-y-3">
              <button onClick={() => onOpenConsole(node.id)} className="w-full flex items-center justify-between px-4 py-2.5 bg-blue-600 hover:bg-blue-500 rounded-lg text-xs text-white font-bold transition-all shadow-lg shadow-blue-900/20">
                <span>OPEN CONSOLE</span>
                <i className="fa-solid fa-terminal opacity-50"></i>
              </button>
            </div>
          </div>
        )}

        {activeTab === 'hardware' && (
          <div className="space-y-8">
            <div className="space-y-4">
              <div className="flex justify-between items-end"><label className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">CPU Allocation</label><span className="text-xs font-black text-blue-400">{node.cpu || 1} Cores</span></div>
              <input type="range" min="1" max="16" step="1" value={node.cpu || 1} onChange={(e) => onUpdateNode(node.id, { cpu: parseInt(e.target.value) })} className="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-blue-500" />
              <div className="flex justify-between text-[8px] font-bold text-slate-600"><span>1 Core</span><span>16 Cores</span></div>
            </div>
            <div className="space-y-4">
              <div className="flex justify-between items-end"><label className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">RAM Allocation</label><span className="text-xs font-black text-blue-400">{(node.memory || 1024) / 1024} GB</span></div>
              <input type="range" min="512" max="16384" step="512" value={node.memory || 1024} onChange={(e) => onUpdateNode(node.id, { memory: parseInt(e.target.value) })} className="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-blue-500" />
              <div className="flex justify-between text-[8px] font-bold text-slate-600"><span>512MB</span><span>16GB</span></div>
            </div>
          </div>
        )}

        {activeTab === 'connectivity' && (
          <div className="space-y-4">
            <div className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-3">Active Interfaces</div>
            {nodeLinks.length > 0 ? nodeLinks.map(link => {
              const otherId = link.source === node.id ? link.target : link.source;
              const otherNode = nodes.find(n => n.id === otherId);
              const isSource = link.source === node.id;
              return (
                <div key={link.id} className="p-3 bg-slate-800/50 border border-slate-800 rounded-xl hover:border-slate-700 transition-all">
                  <div className="flex items-center justify-between mb-2"><span className="text-[10px] font-black text-slate-400 uppercase tracking-tighter">Connection to {otherNode?.name}</span><i className="fa-solid fa-link text-[10px] text-blue-500/50"></i></div>
                  <div className="space-y-2">
                    <label className="text-[9px] font-bold text-slate-600 uppercase">Local Interface</label>
                    <input type="text" value={(isSource ? link.sourceInterface : link.targetInterface) || ''} placeholder="e.g. eth0" onChange={(e) => onUpdateLink(link.id, isSource ? { sourceInterface: e.target.value } : { targetInterface: e.target.value })} className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-[11px] text-blue-300 focus:outline-none focus:border-blue-500" />
                  </div>
                </div>
              );
            }) : (
              <div className="py-12 flex flex-col items-center justify-center text-slate-600"><i className="fa-solid fa-circle-nodes text-2xl opacity-10 mb-2"></i><p className="text-[10px] font-bold uppercase tracking-tight">No active links</p></div>
            )}
          </div>
        )}

        {activeTab === 'config' && (
          <div className="h-full flex flex-col">
            <label className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-3">Startup Configuration</label>
            <textarea value={node.config || ''} onChange={(e) => onUpdateNode(node.id, { config: e.target.value })} spellCheck={false} className="flex-1 min-h-[300px] bg-black text-blue-400 font-mono text-[11px] p-4 rounded-xl border border-slate-800 focus:outline-none focus:border-blue-500/50 resize-none" />
          </div>
        )}
      </div>
    </div>
  );
};

export default PropertiesPanel;
