import React from 'react';
import { useNavigate } from 'react-router-dom';
import { formatTimestamp } from '../../utils/format';
import AgentMeshSection from './AgentMeshSection';
import { getMtuSyncStatusBadge, getInterfaceTypeBadge, getManagedIfaceSyncBadge } from './badgeHelpers';
import type {
  AgentMeshResponse,
  AgentNetworkConfig,
  HostDetailed,
  ManagedInterface,
  NicGroup,
} from './infrastructureTypes';

interface NetworkTabProps {
  mesh: AgentMeshResponse | null;
  meshLoading: boolean;
  meshError: string | null;
  hosts: HostDetailed[];
  networkConfigs: AgentNetworkConfig[];
  networkConfigsLoading: boolean;
  managedInterfaces: ManagedInterface[];
  showManagedInterfaces: boolean;
  setShowManagedInterfaces: (fn: (prev: boolean) => boolean) => void;
  nicGroups: NicGroup[];
  nicGroupsLoading: boolean;
  mtuValue: number;
  settingsDirty: boolean;
  savingSettings: boolean;
  mtuRecommendation: number | null;
  configuringMtu: string | null;
  testingAll: boolean;
  testingLink: string | null;
  onHandleMtuChange: (value: number) => void;
  onSaveSettings: () => void;
  onLoadNetworkConfigs: () => void;
  onOpenMtuConfigModal: (agentId: string) => void;
  onTestAllLinks: () => void;
  onTestLink: (sourceId: string, targetId: string, testPath: string) => void;
  onOpenNicGroupModal: () => void;
  onOpenNicGroupMemberModal: (group: NicGroup) => void;
}

const NetworkTab: React.FC<NetworkTabProps> = ({
  mesh,
  meshLoading,
  meshError,
  hosts,
  networkConfigs,
  networkConfigsLoading,
  managedInterfaces,
  showManagedInterfaces,
  setShowManagedInterfaces,
  nicGroups,
  nicGroupsLoading,
  mtuValue,
  settingsDirty,
  savingSettings,
  mtuRecommendation,
  configuringMtu,
  testingAll,
  testingLink,
  onHandleMtuChange,
  onSaveSettings,
  onLoadNetworkConfigs,
  onOpenMtuConfigModal,
  onTestAllLinks,
  onTestLink,
  onOpenNicGroupModal,
  onOpenNicGroupMemberModal,
}) => {
  const navigate = useNavigate();

  if (meshLoading && !mesh) {
    return (
      <div className="max-w-6xl mx-auto">
        <div className="flex items-center justify-center py-20">
          <i className="fa-solid fa-spinner fa-spin text-stone-400 text-2xl"></i>
          <span className="ml-3 text-stone-500">Loading infrastructure data...</span>
        </div>
      </div>
    );
  }

  if (meshError) {
    return (
      <div className="max-w-6xl mx-auto">
        <div className="text-center py-20 text-red-500">
          <i className="fa-solid fa-exclamation-circle text-3xl mb-3"></i>
          <p>{meshError}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto">
      <div className="space-y-8">
        {/* Settings Panel */}
        <div className="glass-surface rounded-2xl p-6">
          <h2 className="text-lg font-bold text-stone-900 dark:text-white mb-4 flex items-center gap-2">
            <i className="fa-solid fa-sliders text-sage-600 dark:text-sage-400"></i>
            Global Settings
          </h2>

          <div>
            <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-2">
              Overlay MTU
              <span className="text-stone-400 font-normal ml-2">(VXLAN tunnel MTU)</span>
            </label>
            <div className="flex items-center gap-3">
              <input
                type="number"
                min={68}
                max={9000}
                value={mtuValue}
                onChange={(e) => onHandleMtuChange(parseInt(e.target.value) || 1450)}
                className="w-32 px-3 py-2 bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-700 rounded-lg text-stone-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-sage-500"
              />
              <span className="text-xs text-stone-500">bytes (68-9000)</span>
            </div>
            <p className="text-xs text-stone-400 mt-1">
              Default is 1450 to account for ~50 byte VXLAN overhead on 1500 MTU networks.
              Use 8950 for jumbo frame networks.
            </p>

            {mtuRecommendation && (
              <div className="mt-3 p-3 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg">
                <div className="flex items-center justify-between">
                  <div className="flex items-start gap-2">
                    <i className="fa-solid fa-lightbulb text-blue-500 mt-0.5"></i>
                    <div className="text-xs text-blue-700 dark:text-blue-300">
                      <strong>Recommended: {mtuRecommendation}</strong>
                      <span className="ml-1">— All data plane links support jumbo frames. Overlay MTU can be increased.</span>
                    </div>
                  </div>
                  <button
                    onClick={() => onHandleMtuChange(mtuRecommendation)}
                    className="ml-3 px-2 py-1 text-xs font-medium bg-blue-100 dark:bg-blue-900/40 hover:bg-blue-200 dark:hover:bg-blue-900/60 text-blue-700 dark:text-blue-400 rounded transition-colors"
                  >
                    Apply
                  </button>
                </div>
              </div>
            )}
          </div>

          <div className="mt-6 flex items-center gap-4">
            <button
              onClick={onSaveSettings}
              disabled={!settingsDirty || savingSettings}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg transition-all text-sm font-medium ${
                settingsDirty && !savingSettings
                  ? 'bg-sage-600 hover:bg-sage-700 text-white'
                  : 'bg-stone-200 dark:bg-stone-800 text-stone-400 cursor-not-allowed'
              }`}
            >
              {savingSettings ? (
                <i className="fa-solid fa-spinner fa-spin"></i>
              ) : (
                <i className="fa-solid fa-save"></i>
              )}
              Save Settings
            </button>
            {settingsDirty && (
              <span className="text-xs text-amber-600 dark:text-amber-400">
                <i className="fa-solid fa-circle-exclamation mr-1"></i>
                Unsaved changes
              </span>
            )}
            {mesh?.settings.updated_at && (
              <span className="text-xs text-stone-400">
                Last updated: {formatTimestamp(mesh.settings.updated_at)}
              </span>
            )}
          </div>
        </div>

        {/* Host Network Configuration */}
        <div className="glass-surface rounded-2xl p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-bold text-stone-900 dark:text-white flex items-center gap-2">
              <i className="fa-solid fa-ethernet text-sage-600 dark:text-sage-400"></i>
              Host Network
            </h2>
            <div className="flex items-center gap-2">
              <button
                onClick={() => navigate('/admin/interfaces')}
                className="flex items-center gap-2 px-3 py-1.5 text-xs font-medium glass-control text-sage-700 dark:text-sage-400 border border-stone-200 dark:border-stone-700 rounded-lg transition-colors"
              >
                <i className="fa-solid fa-network-wired"></i>
                Manage Interfaces
              </button>
              <button
                onClick={onLoadNetworkConfigs}
                disabled={networkConfigsLoading}
                className="flex items-center gap-2 px-2 py-1.5 text-xs text-stone-500 hover:text-stone-700 dark:hover:text-stone-300 transition-colors"
              >
                <i className={`fa-solid fa-sync ${networkConfigsLoading ? 'fa-spin' : ''}`}></i>
              </button>
            </div>
          </div>

          <p className="text-sm text-stone-500 dark:text-stone-400 mb-4">
            Configure transport mode and MTU for each agent. Separate data plane traffic (VXLAN tunnels)
            from management traffic using subinterfaces or dedicated NICs for jumbo frame support.
          </p>

          {hosts.length === 0 ? (
            <div className="text-center py-8 text-stone-500">
              <i className="fa-solid fa-server text-3xl mb-3 opacity-30"></i>
              <p>No agents registered</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-stone-200 dark:border-stone-700">
                    <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Agent</th>
                    <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Transport</th>
                    <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Interface</th>
                    <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Data Plane IP</th>
                    <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">MTU</th>
                    <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Status</th>
                    <th className="text-right py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {hosts.map((host) => {
                    const config = networkConfigs.find(c => c.host_id === host.id);
                    const statusBadge = getMtuSyncStatusBadge(config?.sync_status || 'unconfigured');
                    const isLoading = configuringMtu === host.id;
                    const needsAttention = config?.sync_status === 'mismatch' || config?.sync_status === 'error';
                    const effectiveDataPlaneIp = host.data_plane_address
                      || (config?.transport_ip ? config.transport_ip.split('/')[0] : '');
                    const requiredMtu = mesh?.settings?.overlay_mtu && mesh.settings.overlay_mtu > 0
                      ? mesh.settings.overlay_mtu
                      : 1500;
                    const hasDataPlaneMtuTest = !!mesh?.links?.some(link =>
                      link.test_path === 'data_plane'
                      && link.test_status === 'success'
                      && link.tested_mtu !== null
                      && link.tested_mtu >= requiredMtu
                      && (link.source_agent_id === host.id || link.target_agent_id === host.id)
                    );

                    return (
                      <tr
                        key={host.id}
                        className={`border-b border-stone-100 dark:border-stone-800 hover:bg-stone-50 dark:hover:bg-stone-800/30 ${
                          needsAttention ? 'bg-amber-50/50 dark:bg-amber-900/10' : ''
                        }`}
                      >
                        <td className="py-2 px-3">
                          <div className="flex items-center gap-2">
                            <div className={`w-2 h-2 rounded-full ${host.status === 'online' ? 'bg-green-500' : 'bg-red-500'}`}></div>
                            <span className="font-medium text-stone-700 dark:text-stone-300">{host.name}</span>
                          </div>
                        </td>
                        <td className="py-2 px-3">
                          <div className="flex flex-col gap-0.5">
                            <span className={`inline-flex items-center self-start px-1.5 py-0.5 rounded text-xs font-medium ${
                              config?.transport_mode === 'subinterface' ? 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400'
                              : config?.transport_mode === 'dedicated' ? 'bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-400'
                              : 'bg-stone-100 dark:bg-stone-800 text-stone-500 dark:text-stone-400'
                            }`}>
                              {config?.transport_mode === 'subinterface' ? 'Subinterface' : config?.transport_mode === 'dedicated' ? 'Dedicated' : 'Management'}
                            </span>
                            {config?.transport_mode && config.transport_mode !== 'management' && !hasDataPlaneMtuTest && (
                              <span className="text-[11px] text-amber-600 dark:text-amber-400">
                                Run MTU test to enable transport
                              </span>
                            )}
                            {config?.transport_mode && config.transport_mode !== 'management' && hasDataPlaneMtuTest && (
                              <span className="text-[11px] text-emerald-600 dark:text-emerald-400">
                                Transport enabled
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="py-2 px-3 font-mono text-xs text-stone-600 dark:text-stone-400">
                          {config?.transport_mode === 'subinterface' && config?.parent_interface && config?.vlan_id
                            ? `${config.parent_interface}.${config.vlan_id}`
                            : config?.data_plane_interface
                              ? config.data_plane_interface
                              : '-'}
                        </td>
                        <td className="py-2 px-3 font-mono text-xs text-stone-600 dark:text-stone-400">
                          <div className="flex flex-col">
                            <span>{effectiveDataPlaneIp || '-'}</span>
                            {config?.transport_mode === 'management' && effectiveDataPlaneIp && (
                              <span className="text-[11px] text-amber-600 dark:text-amber-400">
                                Auto-selected transport IP
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="py-2 px-3">
                          <div className="flex items-center gap-1.5">
                            {config?.current_mtu ? (
                              <span className={`font-mono text-xs ${
                                config.current_mtu >= (config.desired_mtu || 9000)
                                  ? 'text-green-600 dark:text-green-400'
                                  : 'text-amber-600 dark:text-amber-400'
                              }`}>
                                {config.current_mtu}
                              </span>
                            ) : (
                              <span className="text-stone-400 text-xs">-</span>
                            )}
                            <span className="text-stone-300 dark:text-stone-600">/</span>
                            <span className="font-mono text-xs text-stone-500 dark:text-stone-400">
                              {config?.desired_mtu || 9000}
                            </span>
                          </div>
                        </td>
                        <td className="py-2 px-3">
                          <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${statusBadge.color}`}>
                            <i className={`fa-solid ${statusBadge.icon} text-[11px]`}></i>
                            {statusBadge.text}
                          </span>
                          {config?.sync_error && (
                            <span className="ml-2 text-xs text-red-500" title={config.sync_error}>
                              <i className="fa-solid fa-circle-exclamation"></i>
                            </span>
                          )}
                        </td>
                        <td className="py-2 px-3 text-right">
                          <button
                            onClick={() => onOpenMtuConfigModal(host.id)}
                            disabled={host.status !== 'online' || isLoading}
                            className={`px-2 py-1 rounded text-xs font-medium transition-all ${
                              host.status === 'online' && !isLoading
                                ? 'glass-control text-stone-600 dark:text-stone-400'
                                : 'bg-stone-100 dark:bg-stone-800 text-stone-400 cursor-not-allowed'
                            }`}
                          >
                            {isLoading ? (
                              <i className="fa-solid fa-spinner fa-spin"></i>
                            ) : (
                              <>
                                <i className="fa-solid fa-cog mr-1"></i>
                                Configure
                              </>
                            )}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {networkConfigs.some(c => c.sync_status === 'mismatch' || c.sync_status === 'error') && (
            <div className="mt-4 p-3 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg">
              <div className="flex items-start gap-2">
                <i className="fa-solid fa-triangle-exclamation text-amber-500 mt-0.5"></i>
                <div className="text-xs text-amber-700 dark:text-amber-300">
                  <strong>Attention:</strong> Some agents have MTU configurations that need attention.
                  Click Configure to update the interface MTU.
                </div>
              </div>
            </div>
          )}

          {/* Managed Interfaces (collapsible) */}
          {managedInterfaces.length > 0 && (
            <div className="mt-6 pt-6 border-t border-stone-200 dark:border-stone-800">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-semibold text-stone-700 dark:text-stone-300 flex items-center gap-2">
                  <i className="fa-solid fa-plug text-stone-400 text-xs"></i>
                  Managed Interfaces
                  <span className="text-xs font-normal text-stone-400">({managedInterfaces.length})</span>
                </h3>
                <button
                  onClick={() => setShowManagedInterfaces((prev) => !prev)}
                  className="px-2 py-1 rounded text-xs font-medium transition-all glass-control text-stone-600 dark:text-stone-400"
                >
                  <i className={`fa-solid ${showManagedInterfaces ? 'fa-chevron-up' : 'fa-chevron-down'} mr-1`}></i>
                  {showManagedInterfaces ? 'Hide' : 'Show'}
                </button>
              </div>
              {showManagedInterfaces && (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-stone-200 dark:border-stone-700">
                        <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Agent</th>
                        <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Transport</th>
                        <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Interface</th>
                        <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Data Plane IP</th>
                        <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">MTU</th>
                        <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Status</th>
                        <th className="text-right py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {managedInterfaces.map((iface) => {
                        const typeBadge = getInterfaceTypeBadge(iface.interface_type);
                        const syncBadge = getManagedIfaceSyncBadge(iface.sync_status);
                        return (
                          <tr key={iface.id} className="border-b border-stone-100 dark:border-stone-800 hover:bg-stone-50 dark:hover:bg-stone-800/30">
                            <td className="py-2 px-3">
                              <div className="flex items-center gap-2">
                                <span className="font-medium text-stone-700 dark:text-stone-300">
                                  {iface.host_name || iface.host_id.slice(0, 8)}
                                </span>
                              </div>
                            </td>
                            <td className="py-2 px-3">
                              <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium ${typeBadge.color}`}>
                                {typeBadge.text}
                              </span>
                            </td>
                            <td className="py-2 px-3 font-mono text-xs text-stone-600 dark:text-stone-400">
                              {iface.name}
                            </td>
                            <td className="py-2 px-3 font-mono text-xs text-stone-600 dark:text-stone-400">
                              {iface.ip_address || '-'}
                            </td>
                            <td className="py-2 px-3">
                              <div className="flex items-center gap-1.5">
                                {iface.current_mtu ? (
                                  <span className={`font-mono text-xs ${
                                    iface.current_mtu >= iface.desired_mtu
                                      ? 'text-green-600 dark:text-green-400'
                                      : 'text-amber-600 dark:text-amber-400'
                                  }`}>
                                    {iface.current_mtu}
                                  </span>
                                ) : (
                                  <span className="text-stone-400 text-xs">-</span>
                                )}
                                <span className="text-stone-300 dark:text-stone-600">/</span>
                                <span className="font-mono text-xs text-stone-500 dark:text-stone-400">
                                  {iface.desired_mtu}
                                </span>
                              </div>
                            </td>
                            <td className="py-2 px-3">
                              <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${syncBadge.color}`}>
                                <i className={`fa-solid ${syncBadge.icon} text-[11px]`}></i>
                                {syncBadge.text}
                              </span>
                              {iface.sync_error && (
                                <span className="ml-2 text-xs text-red-500" title={iface.sync_error}>
                                  <i className="fa-solid fa-circle-exclamation"></i>
                                </span>
                              )}
                            </td>
                            <td className="py-2 px-3 text-right">
                              <button
                                onClick={() => navigate('/admin/interfaces')}
                                className="px-2 py-1 rounded text-xs font-medium transition-all glass-control text-stone-600 dark:text-stone-400"
                              >
                                <i className="fa-solid fa-pen-to-square mr-1"></i>
                                Edit
                              </button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* NIC Groups (Preview) */}
          <div className="mt-6 pt-6 border-t border-stone-200 dark:border-stone-800">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-stone-700 dark:text-stone-300 flex items-center gap-2">
                <i className="fa-solid fa-layer-group text-stone-400 text-xs"></i>
                NIC Groups (Preview)
                <span className="text-xs font-normal text-stone-400">({nicGroups.length})</span>
              </h3>
              <button
                onClick={onOpenNicGroupModal}
                disabled={hosts.length === 0}
                className={`px-2 py-1 rounded text-xs font-medium transition-all ${
                  hosts.length > 0
                    ? 'glass-control text-stone-600 dark:text-stone-400'
                    : 'bg-stone-100 dark:bg-stone-800 text-stone-400 cursor-not-allowed'
                }`}
              >
                <i className="fa-solid fa-plus mr-1"></i>
                Create Group
              </button>
            </div>
            <p className="text-xs text-stone-500 dark:text-stone-400 mb-3">
              Group managed interfaces per host. This is a skeleton for future NIC/VLAN affinity and external network placement.
            </p>

            {nicGroupsLoading ? (
              <div className="flex items-center gap-2 text-xs text-stone-500">
                <i className="fa-solid fa-spinner fa-spin"></i>
                Loading NIC groups...
              </div>
            ) : nicGroups.length === 0 ? (
              <div className="text-xs text-stone-500">
                No NIC groups yet.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-stone-200 dark:border-stone-700">
                      <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Agent</th>
                      <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Group</th>
                      <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Members</th>
                      <th className="text-right py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {nicGroups.map((group) => {
                      const hostLabel = group.host_name || group.host_id.slice(0, 8);
                      const availableIfaces = managedInterfaces.filter(i => i.host_id === group.host_id);
                      return (
                        <tr key={group.id} className="border-b border-stone-100 dark:border-stone-800 hover:bg-stone-50 dark:hover:bg-stone-800/30">
                          <td className="py-2 px-3">
                            <span className="font-medium text-stone-700 dark:text-stone-300">{hostLabel}</span>
                          </td>
                          <td className="py-2 px-3">
                            <div className="flex flex-col">
                              <span className="font-medium text-stone-700 dark:text-stone-300">{group.name}</span>
                              {group.description && (
                                <span className="text-[11px] text-stone-500 dark:text-stone-400">{group.description}</span>
                              )}
                            </div>
                          </td>
                          <td className="py-2 px-3">
                            {group.members.length === 0 ? (
                              <span className="text-xs text-stone-400">No members</span>
                            ) : (
                              <div className="flex flex-wrap gap-1">
                                {group.members.map(member => (
                                  <span
                                    key={member.id}
                                    className="inline-flex items-center px-1.5 py-0.5 rounded text-[11px] font-medium bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-400"
                                  >
                                    {member.interface_name || member.managed_interface_id.slice(0, 8)}
                                    {member.role ? ` (${member.role})` : ''}
                                  </span>
                                ))}
                              </div>
                            )}
                          </td>
                          <td className="py-2 px-3 text-right">
                            <button
                              onClick={() => onOpenNicGroupMemberModal(group)}
                              disabled={availableIfaces.length === 0}
                              className={`px-2 py-1 rounded text-xs font-medium transition-all ${
                                availableIfaces.length > 0
                                  ? 'glass-control text-stone-600 dark:text-stone-400'
                                  : 'bg-stone-100 dark:bg-stone-800 text-stone-400 cursor-not-allowed'
                              }`}
                            >
                              <i className="fa-solid fa-plus mr-1"></i>
                              Add Member
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>

        {/* Agent Mesh */}
        {mesh && (
          <AgentMeshSection
            mesh={mesh}
            hosts={hosts}
            networkConfigs={networkConfigs}
            managedInterfaces={managedInterfaces}
            testingAll={testingAll}
            testingLink={testingLink}
            onTestAllLinks={onTestAllLinks}
            onTestLink={onTestLink}
          />
        )}
      </div>
    </div>
  );
};

export default NetworkTab;
