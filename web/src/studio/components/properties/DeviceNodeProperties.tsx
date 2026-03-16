import React, { useState, useEffect } from 'react';
import { Node, Link, DeviceModel, DeviceNode } from '../../types';
import { RuntimeStatus } from '../RuntimeControl';
import InterfaceSelect from '../InterfaceSelect';
import { PortManager } from '../../hooks/usePortManager';
import { getAgentColor } from '../../../utils/agentColors';
import { NodeStateEntry } from '../../../types/nodeState';
import ConfigSnapshotSelector from '../ConfigSnapshotSelector';
import AgentDropdown from './AgentDropdown';
import ValueDropdown from './ValueDropdown';
import { Tooltip } from '../../../components/ui/Tooltip';

interface DeviceNodePropertiesProps {
  node: DeviceNode;
  nodes: Node[];
  links: Link[];
  onUpdateNode: (id: string, updates: Partial<Node>) => void;
  onUpdateLink: (id: string, updates: Partial<Link>) => void;
  onDelete: (id: string) => void;
  onOpenConsole: (nodeId: string) => void;
  runtimeStates: Record<string, RuntimeStatus>;
  onUpdateStatus: (nodeId: string, status: RuntimeStatus) => void;
  deviceModels: DeviceModel[];
  portManager: PortManager;
  onOpenConfigViewer?: (nodeId: string, nodeName: string, snapshotContent?: string, snapshotLabel?: string) => void;
  labId?: string;
  studioRequest?: <T>(path: string, options?: RequestInit) => Promise<T>;
  agents?: { id: string; name: string }[];
  nodeStates?: Record<string, NodeStateEntry>;
}

const DeviceNodeProperties: React.FC<DeviceNodePropertiesProps> = ({
  node,
  nodes,
  links,
  onUpdateNode,
  onUpdateLink,
  onDelete,
  onOpenConsole,
  runtimeStates,
  onUpdateStatus,
  deviceModels,
  portManager,
  onOpenConfigViewer,
  labId,
  studioRequest,
  agents = [],
  nodeStates = {},
}) => {
  const [activeTab, setActiveTab] = useState<'general' | 'hardware' | 'connectivity' | 'config'>('general');
  const [isHardwareLocked, setIsHardwareLocked] = useState(true);

  useEffect(() => {
    setIsHardwareLocked(true);
  }, [node.id]);

  const nodeLinks = links.filter(l => l.source === node.id || l.target === node.id);
  const model = deviceModels.find(m => m.id === node.model);
  const status = runtimeStates[node.id] || 'stopped';
  const nodeState = nodeStates[node.id];
  const imageSyncStatus = nodeState?.image_sync_status;
  const imageSyncMessage = nodeState?.image_sync_message;
  const defaultCpu = model?.cpu || 1;
  const defaultMemory = model?.memory || 1024;

  return (
    <div className="w-80 bg-white dark:bg-stone-900 border-l border-stone-200 dark:border-stone-700 overflow-hidden flex flex-col">
      <div className="p-4 border-b border-stone-200 dark:border-stone-700 flex justify-between items-center bg-stone-100/50 dark:bg-stone-800/50">
        <div>
          <h2 className="text-xs font-black uppercase tracking-widest text-stone-900 dark:text-white">{node.name}</h2>
          <div className="text-[11px] font-bold text-sage-600 dark:text-sage-500 tracking-tighter uppercase">{model?.name}</div>
        </div>
        <button onClick={() => onDelete(node.id)} className="p-1.5 text-stone-500 hover:text-red-500 hover:bg-red-100 dark:hover:bg-red-950/30 rounded transition-all">
          <i className="fa-solid fa-trash-can text-sm"></i>
        </button>
      </div>

      <div className="flex bg-stone-50/50 dark:bg-stone-950/50 border-b border-stone-200 dark:border-stone-800">
        {(['general', 'hardware', 'connectivity', 'config'] as const).map(tab => (
          <button key={tab} onClick={() => setActiveTab(tab)} className={`flex-1 py-3 text-[11px] font-black uppercase tracking-tighter border-b-2 transition-all ${activeTab === tab ? 'text-sage-600 dark:text-sage-500 border-sage-500 bg-sage-500/5' : 'text-stone-500 border-transparent hover:text-stone-700 dark:hover:text-stone-300'}`}>
            {tab}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto p-5 custom-scrollbar">
        {activeTab === 'general' && (
          <div className="space-y-6">
            <div className="p-4 bg-stone-50/50 dark:bg-stone-950/50 rounded-xl border border-stone-200 dark:border-stone-800">
               <div className="flex items-center justify-between mb-2">
                 <span className="text-[11px] font-bold text-stone-500 uppercase tracking-widest">Status</span>
                 <span className={`text-[11px] font-black uppercase px-2 py-0.5 rounded border ${status === 'running' ? 'text-green-600 dark:text-green-500 border-green-500/20 bg-green-500/5' : status === 'booting' ? 'text-yellow-600 dark:text-yellow-500 border-yellow-500/20 bg-yellow-500/5' : status === 'stopping' ? 'text-orange-600 dark:text-orange-500 border-orange-500/20 bg-orange-500/5' : status === 'error' ? 'text-red-600 dark:text-red-500 border-red-500/20 bg-red-500/5' : 'text-stone-500 border-stone-300 dark:border-stone-700 bg-stone-100 dark:bg-stone-800'}`}>{status}</span>
               </div>
               {/* Image sync status indicator */}
               {imageSyncStatus && (
                 <div className={`flex items-center gap-2 mt-2 p-2 rounded-lg text-[11px] ${
                   imageSyncStatus === 'syncing' || imageSyncStatus === 'checking'
                     ? 'bg-blue-500/10 border border-blue-500/20 text-blue-600 dark:text-blue-400'
                     : imageSyncStatus === 'failed'
                     ? 'bg-red-500/10 border border-red-500/20 text-red-600 dark:text-red-400'
                     : 'bg-green-500/10 border border-green-500/20 text-green-600 dark:text-green-400'
                 }`}>
                   <i className={`fa-solid ${
                     imageSyncStatus === 'syncing' ? 'fa-cloud-arrow-up fa-beat-fade' :
                     imageSyncStatus === 'checking' ? 'fa-magnifying-glass fa-beat-fade' :
                     imageSyncStatus === 'failed' ? 'fa-circle-exclamation' :
                     'fa-circle-check'
                   }`} />
                   <div className="flex-1">
                     <div className="font-bold uppercase">
                       {imageSyncStatus === 'syncing' ? 'Pushing Image' :
                        imageSyncStatus === 'checking' ? 'Checking Image' :
                        imageSyncStatus === 'failed' ? 'Image Sync Failed' :
                        'Image Ready'}
                     </div>
                     {imageSyncMessage && (
                       <div className="text-[11px] opacity-75 mt-0.5">{imageSyncMessage}</div>
                     )}
                   </div>
                 </div>
               )}
               <div className="grid grid-cols-2 gap-2 mt-4">
                  {status === 'stopping' ? (
                    <Tooltip content="Stopping...">
                      <button
                        disabled
                        className="flex items-center justify-center gap-2 py-2 bg-orange-600/50 text-white text-[11px] font-bold rounded-lg cursor-not-allowed col-span-2"
                      >
                        <i className="fa-solid fa-spinner fa-spin"></i> STOPPING...
                      </button>
                    </Tooltip>
                  ) : status === 'stopped' ? (
                    <Tooltip content="Start this node">
                      <button
                        onClick={() => onUpdateStatus(node.id, 'booting')}
                        className="flex items-center justify-center gap-2 py-2 bg-green-600 hover:bg-green-500 text-white text-[11px] font-bold rounded-lg transition-all"
                      >
                        <i className="fa-solid fa-play"></i> START
                      </button>
                    </Tooltip>
                  ) : (
                    <button onClick={() => onUpdateStatus(node.id, 'stopped')} className="flex items-center justify-center gap-2 py-2 bg-red-600 hover:bg-red-500 text-white text-[11px] font-bold rounded-lg transition-all"><i className="fa-solid fa-power-off"></i> STOP</button>
                  )}
                  {status !== 'stopped' && status !== 'stopping' && (
                    <button onClick={() => onUpdateStatus(node.id, 'booting')} className="flex items-center justify-center gap-2 py-2 glass-control text-stone-700 dark:text-stone-300 text-[11px] font-bold rounded-lg transition-all border border-stone-300 dark:border-stone-700"><i className="fa-solid fa-rotate"></i> RELOAD</button>
                  )}
               </div>
            </div>

            {/* Agent Placement - only show when multiple agents available */}
            {agents.length > 1 && (
              <AgentDropdown
                value={node.host || ''}
                onChange={(value) => onUpdateNode(node.id, { host: value || undefined })}
                disabled={status === 'running' || status === 'booting' || status === 'stopping'}
                agents={agents}
              />
            )}

            {/* Running On - show when multiple agents and node is running/booting/stopping with a host assigned */}
            {agents.length > 1 && (status === 'running' || status === 'booting' || status === 'stopping') && nodeState?.host_name && (
              <div className="space-y-2">
                <label className="text-[11px] font-bold text-stone-500 uppercase tracking-widest">Running On</label>
                <div className="flex items-center gap-2 px-3 py-2 bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-700 rounded-lg">
                  <div
                    className="w-2 h-2 rounded-full"
                    style={{ backgroundColor: getAgentColor(nodeState.host_id || '') }}
                  />
                  <span className="text-sm text-stone-700 dark:text-stone-300">{nodeState.host_name}</span>
                </div>
              </div>
            )}

            <div className="space-y-2">
              <label className="text-[11px] font-bold text-stone-500 uppercase tracking-widest">Display Name</label>
              <input type="text" value={node.name} onChange={(e) => onUpdateNode(node.id, { name: e.target.value })} className="w-full bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-700 rounded-lg px-3 py-2 text-sm text-stone-900 dark:text-stone-100 focus:outline-none focus:border-sage-500" />
            </div>
            <div className="space-y-2">
              <ValueDropdown
                label="Image Version"
                value={node.version}
                options={model?.versions?.length ? model.versions : [node.version]}
                onChange={(value) => onUpdateNode(node.id, { version: value })}
              />
            </div>
            <div className="pt-4 space-y-3">
              <button onClick={() => onOpenConsole(node.id)} className="w-full flex items-center justify-between px-4 py-2.5 bg-sage-600 hover:bg-sage-500 rounded-lg text-xs text-white font-bold transition-all shadow-lg shadow-sage-900/20">
                <span>OPEN CONSOLE</span>
                <i className="fa-solid fa-terminal opacity-50"></i>
              </button>
            </div>
            {model?.defaultCredentials && (() => {
              const parts = model.defaultCredentials.split(' / ');
              const username = parts[0]?.trim() || '';
              const password = parts.length > 1 ? parts.slice(1).join(' / ').trim() : '';
              return (
                <div className="flex flex-col gap-1.5 p-3 rounded-lg border border-stone-600/40 dark:border-stone-600/50 bg-stone-500/10 dark:bg-stone-800/50 backdrop-blur-md">
                  <div className="flex items-center gap-2 text-[11px] font-bold text-sage-600 dark:text-sage-400 uppercase tracking-tight">
                    <i className="fa-solid fa-key"></i>
                    <span>Default Credentials</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="flex items-center gap-1 flex-1 min-w-0">
                      <span className="text-[11px] text-stone-500 dark:text-stone-500 shrink-0">user</span>
                      <code className="text-[11px] text-stone-700 dark:text-stone-300 font-mono truncate">{username}</code>
                      <Tooltip content="Copy username">
                        <button
                          onClick={() => navigator.clipboard.writeText(username)}
                          className="shrink-0 p-0.5 text-stone-400 hover:text-sage-500 transition-colors"
                        >
                          <i className="fa-regular fa-copy text-[11px]"></i>
                        </button>
                      </Tooltip>
                    </div>
                    {password && (
                      <div className="flex items-center gap-1 flex-1 min-w-0">
                        <span className="text-[11px] text-stone-500 dark:text-stone-500 shrink-0">pass</span>
                        <code className="text-[11px] text-stone-700 dark:text-stone-300 font-mono truncate">{password}</code>
                        <Tooltip content="Copy password">
                          <button
                            onClick={() => navigator.clipboard.writeText(password)}
                            className="shrink-0 p-0.5 text-stone-400 hover:text-sage-500 transition-colors"
                          >
                            <i className="fa-regular fa-copy text-[11px]"></i>
                          </button>
                        </Tooltip>
                      </div>
                    )}
                  </div>
                </div>
              );
            })()}
          </div>
        )}

        {activeTab === 'hardware' && (
          <div className="space-y-8">
            <div className="flex items-center justify-between">
              <div className="text-[11px] font-bold text-stone-500 uppercase tracking-widest">Hardware Defaults</div>
              <div className="flex items-center gap-2">
                <Tooltip content={isHardwareLocked ? 'Unlock hardware settings' : 'Lock hardware settings'}>
                  <button
                    onClick={() => setIsHardwareLocked((prev) => !prev)}
                    className="flex items-center gap-1.5 px-2 py-1 text-[11px] font-bold uppercase text-stone-600 dark:text-stone-300 glass-control rounded-lg transition-colors"
                  >
                    <i className={`fa-solid ${isHardwareLocked ? 'fa-lock' : 'fa-lock-open'}`} />
                    {isHardwareLocked ? 'Locked' : 'Unlocked'}
                  </button>
                </Tooltip>
                <Tooltip content="Reset CPU, RAM, and hardware defaults for this device type">
                  <button
                    onClick={() => onUpdateNode(node.id, {
                      cpu: defaultCpu,
                      memory: defaultMemory,
                      disk_driver: model?.diskDriver,
                      nic_driver: model?.nicDriver,
                      machine_type: model?.machineType,
                    })}
                    disabled={isHardwareLocked}
                    className={`px-3 py-1 text-[11px] font-bold uppercase rounded-lg transition-colors ${
                      isHardwareLocked
                        ? 'text-stone-400 dark:text-stone-600 bg-stone-100 dark:bg-stone-800 cursor-not-allowed'
                        : 'text-stone-600 dark:text-stone-300 glass-control'
                    }`}
                  >
                    Reset
                  </button>
                </Tooltip>
              </div>
            </div>
            <div className="space-y-4">
              <div className="flex justify-between items-end"><label className="text-[11px] font-bold text-stone-500 uppercase tracking-widest">CPU Allocation</label><span className="text-xs font-black text-sage-600 dark:text-sage-400">{node.cpu || 1} Cores</span></div>
              <input
                type="range"
                min="1"
                max="16"
                step="1"
                value={node.cpu || 1}
                onChange={(e) => onUpdateNode(node.id, { cpu: parseInt(e.target.value) })}
                disabled={isHardwareLocked}
                className={`w-full h-1.5 bg-stone-200 dark:bg-stone-800 rounded-lg appearance-none accent-sage-500 ${
                  isHardwareLocked ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'
                }`}
              />
              <div className="flex justify-between text-[8px] font-bold text-stone-400 dark:text-stone-600"><span>1 Core</span><span>16 Cores</span></div>
            </div>
            <div className="space-y-4">
              <div className="flex justify-between items-end">
                <label className="text-[11px] font-bold text-stone-500 uppercase tracking-widest">RAM Allocation</label>
                <div className="flex items-center">
                  <input
                    type="number"
                    min="512"
                    max="65536"
                    step="512"
                    value={node.memory || 1024}
                    onChange={(e) => { const v = parseInt(e.target.value); if (v >= 512 && v <= 65536) onUpdateNode(node.id, { memory: v }); }}
                    disabled={isHardwareLocked}
                    className={`w-16 text-right text-xs font-black text-sage-600 dark:text-sage-400 bg-transparent border border-stone-300 dark:border-stone-700 rounded px-1 ${
                      isHardwareLocked ? 'cursor-not-allowed opacity-60' : ''
                    }`}
                  />
                  <span className="text-xs font-black text-sage-600 dark:text-sage-400 ml-1">MB</span>
                </div>
              </div>
              <input
                type="range"
                min="512"
                max="32768"
                step="1024"
                value={node.memory || 1024}
                onChange={(e) => onUpdateNode(node.id, { memory: parseInt(e.target.value) })}
                disabled={isHardwareLocked}
                className={`w-full h-1.5 bg-stone-200 dark:bg-stone-800 rounded-lg appearance-none accent-sage-500 ${
                  isHardwareLocked ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'
                }`}
              />
              <div className="flex justify-between text-[8px] font-bold text-stone-400 dark:text-stone-600"><span>512MB</span><span>32GB</span></div>
            </div>
          </div>
        )}

        {activeTab === 'connectivity' && (
          <div className="space-y-4">
            <div className="text-[11px] font-bold text-stone-500 uppercase tracking-widest mb-3">Active Interfaces</div>
            {nodeLinks.length > 0 ? nodeLinks.map(link => {
              const otherId = link.source === node.id ? link.target : link.source;
              const otherNode = nodes.find(n => n.id === otherId);
              const isSource = link.source === node.id;
              const currentInterface = isSource ? link.sourceInterface : link.targetInterface;
              const availableInterfaces = portManager.getAvailableInterfaces(node.id);
              return (
                <div key={link.id} className="p-3 bg-stone-100/50 dark:bg-stone-800/50 border border-stone-200 dark:border-stone-800 rounded-xl hover:border-stone-300 dark:hover:border-stone-700 transition-all">
                  <div className="flex items-center justify-between mb-2"><span className="text-[11px] font-black text-stone-600 dark:text-stone-400 uppercase tracking-tighter">Connection to {otherNode?.name}</span><i className="fa-solid fa-link text-[11px] text-sage-500/50"></i></div>
                  <div className="space-y-2">
                    <label className="text-[11px] font-bold text-stone-400 dark:text-stone-600 uppercase">Local Interface</label>
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
              <div className="py-12 flex flex-col items-center justify-center text-stone-400 dark:text-stone-600"><i className="fa-solid fa-circle-nodes text-2xl opacity-10 mb-2"></i><p className="text-[11px] font-bold uppercase tracking-tight">No active links</p></div>
            )}
          </div>
        )}

        {activeTab === 'config' && (
          <div className="h-full flex flex-col">
            {labId && studioRequest ? (
              <ConfigSnapshotSelector
                labId={labId}
                nodeName={node.container_name || node.name}
                nodeId={node.id}
                studioRequest={studioRequest}
                onOpenConfigViewer={onOpenConfigViewer}
                onUpdateStatus={onUpdateStatus}
                nodeState={nodeStates[node.id]}
              />
            ) : (
              <>
                <div className="flex items-center justify-between mb-3">
                  <label className="text-[11px] font-bold text-stone-500 uppercase tracking-widest">Startup Configuration</label>
                  {onOpenConfigViewer && (
                    <Tooltip content="View saved config in larger window">
                      <button
                        onClick={() => onOpenConfigViewer(node.id, node.container_name || node.name)}
                        className="flex items-center gap-1.5 px-2 py-1 text-[11px] font-bold uppercase text-sage-600 dark:text-sage-400 hover:bg-sage-500/10 rounded transition-colors"
                      >
                        <i className="fa-solid fa-expand" />
                        Expand
                      </button>
                    </Tooltip>
                  )}
                </div>
                <textarea value={node.config || ''} onChange={(e) => onUpdateNode(node.id, { config: e.target.value })} spellCheck={false} className="flex-1 min-h-[300px] bg-stone-50 dark:bg-black text-sage-700 dark:text-sage-400 font-mono text-[11px] p-4 rounded-xl border border-stone-200 dark:border-stone-800 focus:outline-none focus:border-sage-500/50 resize-none" />
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default DeviceNodeProperties;
