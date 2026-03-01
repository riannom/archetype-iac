import React from 'react';
import type { HostDetailed } from './infrastructureTypes';

interface DeregisterModalProps {
  deregisterHost: HostDetailed;
  deregisterInfo: {
    agent_name: string;
    agent_status: string;
    labs_assigned: number;
    running_labs: { id: string; name: string; state: string }[];
    node_placements: number;
    nodes_assigned: number;
    vxlan_tunnels: number;
    cross_host_links: number;
  } | null;
  deregisterLoading: boolean;
  deregistering: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

const DeregisterModal: React.FC<DeregisterModalProps> = ({
  deregisterHost: _deregisterHost,
  deregisterInfo,
  deregisterLoading,
  deregistering,
  onConfirm,
  onCancel,
}) => {
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white dark:bg-stone-900 rounded-2xl shadow-2xl w-full max-w-md mx-4">
        <div className="p-6 border-b border-stone-200 dark:border-stone-800">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-bold text-stone-900 dark:text-white flex items-center gap-2">
              <i className="fa-solid fa-link-slash text-red-500"></i>
              Deregister Host
            </h2>
            <button
              onClick={onCancel}
              className="text-stone-400 hover:text-stone-600 dark:hover:text-stone-300 transition-colors"
            >
              <i className="fa-solid fa-times text-lg"></i>
            </button>
          </div>
        </div>

        <div className="p-6 space-y-4">
          {deregisterLoading ? (
            <div className="flex items-center justify-center py-8">
              <i className="fa-solid fa-spinner fa-spin text-stone-400 text-xl"></i>
              <span className="ml-3 text-stone-500">Loading impact summary...</span>
            </div>
          ) : deregisterInfo ? (
            <>
              <p className="text-sm text-stone-600 dark:text-stone-400">
                Remove <strong className="text-red-600 dark:text-red-400">{deregisterInfo.agent_name}</strong> from the controller?
              </p>

              <div className="text-xs text-stone-500 dark:text-stone-400 space-y-1.5">
                <p className="font-medium text-stone-600 dark:text-stone-300">This will clean up:</p>
                <ul className="list-disc list-inside space-y-1 pl-1">
                  {deregisterInfo.labs_assigned > 0 && (
                    <li>Unassign {deregisterInfo.labs_assigned} lab{deregisterInfo.labs_assigned !== 1 ? 's' : ''} from this host</li>
                  )}
                  {deregisterInfo.node_placements > 0 && (
                    <li>Remove {deregisterInfo.node_placements} node placement{deregisterInfo.node_placements !== 1 ? 's' : ''}</li>
                  )}
                  {deregisterInfo.nodes_assigned > 0 && (
                    <li>Unassign {deregisterInfo.nodes_assigned} node{deregisterInfo.nodes_assigned !== 1 ? 's' : ''} from this host</li>
                  )}
                  {deregisterInfo.cross_host_links > 0 && (
                    <li>Clear {deregisterInfo.cross_host_links} cross-host link reference{deregisterInfo.cross_host_links !== 1 ? 's' : ''}</li>
                  )}
                  {deregisterInfo.vxlan_tunnels > 0 && (
                    <li>Delete {deregisterInfo.vxlan_tunnels} VXLAN tunnel{deregisterInfo.vxlan_tunnels !== 1 ? 's' : ''}</li>
                  )}
                  {deregisterInfo.labs_assigned === 0 && deregisterInfo.node_placements === 0 && deregisterInfo.nodes_assigned === 0 && deregisterInfo.cross_host_links === 0 && deregisterInfo.vxlan_tunnels === 0 && (
                    <li>No dependent resources found</li>
                  )}
                </ul>
              </div>

              {deregisterInfo.running_labs.length > 0 && (
                <div className="p-3 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg">
                  <div className="flex items-start gap-2">
                    <i className="fa-solid fa-triangle-exclamation text-amber-500 mt-0.5 flex-shrink-0"></i>
                    <div className="text-xs text-amber-700 dark:text-amber-300">
                      <strong>Warning:</strong> This host has {deregisterInfo.running_labs.length} running lab{deregisterInfo.running_labs.length !== 1 ? 's' : ''}.
                      Containers will NOT be stopped automatically.
                      <ul className="mt-1 list-disc list-inside">
                        {deregisterInfo.running_labs.map(lab => (
                          <li key={lab.id}>{lab.name}</li>
                        ))}
                      </ul>
                    </div>
                  </div>
                </div>
              )}

              <p className="text-xs text-stone-400 dark:text-stone-500">
                Topology data (nodes, links, configs) is preserved. The agent can re-register later.
              </p>
            </>
          ) : null}
        </div>

        <div className="p-6 border-t border-stone-200 dark:border-stone-800 flex justify-end gap-3">
          <button
            onClick={onCancel}
            className="px-4 py-2 glass-control text-stone-600 dark:text-stone-400 rounded-lg transition-all text-sm font-medium"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={deregisterLoading || deregistering}
            className={`px-4 py-2 rounded-lg transition-all text-sm font-medium ${
              !deregisterLoading && !deregistering
                ? 'bg-red-600 hover:bg-red-700 text-white'
                : 'bg-stone-200 dark:bg-stone-800 text-stone-400 cursor-not-allowed'
            }`}
          >
            {deregistering ? (
              <>
                <i className="fa-solid fa-spinner fa-spin mr-2"></i>
                Deregistering...
              </>
            ) : (
              <>
                <i className="fa-solid fa-link-slash mr-2"></i>
                Deregister
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
};

export default DeregisterModal;
