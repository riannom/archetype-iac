import React from 'react';
import { formatTimestamp } from '../../utils/format';
import type { AgentMeshResponse, AgentNetworkConfig, HostDetailed, ManagedInterface } from './infrastructureTypes';
import { getStatusBadgeStyle, getPathBadge } from './badgeHelpers';

interface AgentMeshSectionProps {
  mesh: AgentMeshResponse;
  hosts: HostDetailed[];
  networkConfigs: AgentNetworkConfig[];
  managedInterfaces: ManagedInterface[];
  testingAll: boolean;
  testingLink: string | null;
  onTestAllLinks: () => void;
  onTestLink: (sourceId: string, targetId: string, testPath: string) => void;
}

const AgentMeshSection: React.FC<AgentMeshSectionProps> = ({
  mesh,
  hosts,
  networkConfigs,
  managedInterfaces,
  testingAll,
  testingLink,
  onTestAllLinks,
  onTestLink,
}) => {
  return (
    <div className="glass-surface rounded-2xl p-6">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-lg font-bold text-stone-900 dark:text-white flex items-center gap-2">
          <i className="fa-solid fa-diagram-project text-sage-600 dark:text-sage-400"></i>
          Agent Mesh
        </h2>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-4 text-xs text-stone-500">
            <div className="flex items-center gap-1.5">
              <div className="w-3 h-3 rounded-full bg-green-500"></div>
              <span>{mesh?.agents.filter(a => a.status === 'online').length} Online</span>
            </div>
            <div className="flex items-center gap-1.5">
              <div className="w-3 h-3 rounded-full bg-red-500"></div>
              <span>{mesh?.agents.filter(a => a.status !== 'online').length} Offline</span>
            </div>
          </div>
          <button
            onClick={onTestAllLinks}
            disabled={testingAll || (mesh?.agents.filter(a => a.status === 'online').length ?? 0) < 2}
            className={`flex items-center gap-2 px-3 py-1.5 rounded-lg transition-all text-xs font-medium ${
              !testingAll && (mesh?.agents.filter(a => a.status === 'online').length ?? 0) >= 2
                ? 'bg-blue-100 dark:bg-blue-900/30 hover:bg-blue-200 dark:hover:bg-blue-900/50 text-blue-700 dark:text-blue-400'
                : 'bg-stone-200 dark:bg-stone-800 text-stone-400 cursor-not-allowed'
            }`}
          >
            {testingAll ? (
              <>
                <i className="fa-solid fa-spinner fa-spin"></i>
                Testing...
              </>
            ) : (
              <>
                <i className="fa-solid fa-network-wired"></i>
                Test All
              </>
            )}
          </button>
        </div>
      </div>

      {mesh?.agents.length === 0 ? (
        <div className="text-center py-12 text-stone-500">
          <i className="fa-solid fa-server text-4xl mb-3 opacity-30"></i>
          <p>No agents registered</p>
        </div>
      ) : mesh?.agents.length === 1 ? (
        <div className="text-center py-12 text-stone-500">
          <i className="fa-solid fa-link-slash text-4xl mb-3 opacity-30"></i>
          <p>Only one agent registered. Add more agents to see mesh connectivity.</p>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4 mb-6">
            {mesh?.agents.map((agent) => {
              const hostDetail = hosts.find(h => h.id === agent.id);
              const netConfig = networkConfigs.find(c => c.host_id === agent.id);
              const transportIfaces = managedInterfaces.filter(i => i.host_id === agent.id && i.interface_type === 'transport');
              const hasTransport = netConfig?.transport_mode !== 'management' && netConfig?.transport_mode;
              return (
                <div
                  key={agent.id}
                  className={`p-4 border rounded-xl ${
                    hasTransport
                      ? 'bg-blue-50/50 dark:bg-blue-900/10 border-blue-200 dark:border-blue-800/50'
                      : 'bg-stone-50 dark:bg-stone-800/50 border-stone-200 dark:border-stone-700'
                  }`}
                >
                  <div className="flex items-center gap-2 mb-2">
                    <div className={`w-3 h-3 rounded-full ${agent.status === 'online' ? 'bg-green-500 animate-pulse' : 'bg-red-500'}`}></div>
                    <span className="font-medium text-stone-900 dark:text-white truncate">{agent.name}</span>
                  </div>
                  <p className="text-xs text-stone-500 truncate">
                    <span className="text-stone-400">management:</span> {agent.address}
                  </p>
                  {hostDetail?.data_plane_address && (
                    <p className="text-xs text-blue-500 dark:text-blue-400 truncate mt-0.5">
                      <span className="text-blue-400 dark:text-blue-500">data:</span> {hostDetail.data_plane_address}
                    </p>
                  )}
                  {hasTransport && (
                    <div className="mt-2 pt-2 border-t border-blue-200/50 dark:border-blue-800/30">
                      <div className="flex items-center gap-1.5">
                        <span className={`px-1 py-0.5 rounded text-[11px] font-bold uppercase ${
                          netConfig?.transport_mode === 'subinterface'
                            ? 'bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400'
                            : 'bg-purple-100 dark:bg-purple-900/30 text-purple-600 dark:text-purple-400'
                        }`}>
                          {netConfig?.transport_mode === 'subinterface' ? 'VLAN' : 'NIC'}
                        </span>
                        {transportIfaces.length > 0 ? (
                          <span className="text-[11px] font-mono text-stone-500 dark:text-stone-400 truncate">
                            {transportIfaces[0].name}
                          </span>
                        ) : netConfig?.data_plane_interface && (
                          <span className="text-[11px] font-mono text-stone-500 dark:text-stone-400 truncate">
                            {netConfig.data_plane_interface}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-1 mt-1">
                        <span className="text-[11px] text-stone-400">MTU:</span>
                        <span className={`text-[11px] font-mono ${
                          netConfig?.current_mtu && netConfig.current_mtu >= netConfig.desired_mtu
                            ? 'text-green-600 dark:text-green-400'
                            : 'text-stone-500 dark:text-stone-400'
                        }`}>
                          {netConfig?.current_mtu || '?'}/{netConfig?.desired_mtu || '?'}
                        </span>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Transport readiness summary */}
          {(() => {
            const totalAgents = mesh?.agents.length ?? 0;
            const transportAgents = (mesh?.agents ?? []).filter(a => {
              const nc = networkConfigs.find(c => c.host_id === a.id);
              return nc?.transport_mode && nc.transport_mode !== 'management';
            }).length;
            const dpLinks = mesh?.links.filter(l => l.test_path === 'data_plane') ?? [];
            const dpPassed = dpLinks.filter(l => l.test_status === 'success').length;

            if (transportAgents === 0) return null;

            return (
              <div className={`mb-4 px-4 py-2.5 rounded-lg border text-xs flex items-center gap-3 ${
                transportAgents === totalAgents && dpPassed === dpLinks.length && dpLinks.length > 0
                  ? 'bg-green-50 dark:bg-green-900/10 border-green-200 dark:border-green-800/50 text-green-700 dark:text-green-400'
                  : 'bg-blue-50 dark:bg-blue-900/10 border-blue-200 dark:border-blue-800/50 text-blue-700 dark:text-blue-400'
              }`}>
                <i className={`fa-solid ${
                  transportAgents === totalAgents && dpPassed === dpLinks.length && dpLinks.length > 0
                    ? 'fa-circle-check' : 'fa-info-circle'
                }`}></i>
                <span>
                  <strong>{transportAgents}/{totalAgents}</strong> agents have data plane transport
                  {dpLinks.length > 0 && (
                    <span className="ml-2">
                      &middot; <strong>{dpPassed}/{dpLinks.length}</strong> data plane links verified
                    </span>
                  )}
                </span>
              </div>
            );
          })()}

          <h3 className="text-sm font-semibold text-stone-700 dark:text-stone-300 mb-3">Link Details</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-stone-200 dark:border-stone-700">
                  <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Path</th>
                  <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Type</th>
                  <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">MTU</th>
                  <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Latency</th>
                  <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Status</th>
                  <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Last Test</th>
                  <th className="text-right py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Action</th>
                </tr>
              </thead>
              <tbody>
                {mesh?.links.map((link) => {
                  const linkKey = `${link.source_agent_id}-${link.target_agent_id}-${link.test_path}`;
                  const isTesting = testingLink === linkKey;
                  const pathBadge = getPathBadge(link.test_path);

                  return (
                    <tr key={link.id} className="border-b border-stone-100 dark:border-stone-800 hover:bg-stone-50 dark:hover:bg-stone-800/30">
                      <td className="py-2 px-3">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-stone-700 dark:text-stone-300">{link.source_agent_name}</span>
                          <i className="fa-solid fa-arrow-right text-stone-400 text-xs"></i>
                          <span className="font-medium text-stone-700 dark:text-stone-300">{link.target_agent_name}</span>
                        </div>
                      </td>
                      <td className="py-2 px-3">
                        <span className={`px-2 py-0.5 rounded text-xs font-medium ${pathBadge.color}`}>
                          {pathBadge.label}
                        </span>
                      </td>
                      <td className="py-2 px-3 text-stone-600 dark:text-stone-400">
                        <div className="flex items-center gap-1.5">
                          {link.tested_mtu ? (
                            <span className={link.tested_mtu >= link.configured_mtu ? 'text-green-600 dark:text-green-400' : 'text-amber-600 dark:text-amber-400'}>
                              {link.tested_mtu}
                            </span>
                          ) : (
                            <span className="text-stone-400">-</span>
                          )}
                          <span className="text-stone-400 text-xs">/ {link.configured_mtu}</span>
                        </div>
                      </td>
                      <td className="py-2 px-3 text-stone-600 dark:text-stone-400">
                        {link.latency_ms !== null ? (
                          <span>{link.latency_ms.toFixed(2)} ms</span>
                        ) : (
                          <span className="text-stone-400">-</span>
                        )}
                      </td>
                      <td className="py-2 px-3">
                        <span className={`px-2 py-0.5 rounded text-xs font-medium border ${getStatusBadgeStyle(link.test_status)}`}>
                          {link.test_status}
                        </span>
                        {link.test_error && (
                          <span className="ml-2 text-xs text-red-500" title={link.test_error}>
                            <i className="fa-solid fa-circle-exclamation"></i>
                          </span>
                        )}
                      </td>
                      <td className="py-2 px-3 text-xs text-stone-400">
                        {link.last_test_at ? formatTimestamp(link.last_test_at) : '-'}
                      </td>
                      <td className="py-2 px-3 text-right">
                        <button
                          onClick={() => onTestLink(link.source_agent_id, link.target_agent_id, link.test_path)}
                          disabled={isTesting || testingAll}
                          className={`px-2 py-1 rounded text-xs font-medium transition-all ${
                            !isTesting && !testingAll
                              ? 'glass-control text-stone-600 dark:text-stone-400'
                              : 'bg-stone-100 dark:bg-stone-800 text-stone-400 cursor-not-allowed'
                          }`}
                        >
                          {isTesting ? (
                            <i className="fa-solid fa-spinner fa-spin"></i>
                          ) : (
                            <i className="fa-solid fa-play"></i>
                          )}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {mesh?.links.length === 0 && (
            <div className="text-center py-8 text-stone-500">
              <p className="text-sm">No links to display. Links are created automatically when agents are registered.</p>
            </div>
          )}
        </>
      )}
    </div>
  );
};

export default AgentMeshSection;
