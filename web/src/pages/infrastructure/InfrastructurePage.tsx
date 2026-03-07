import React, { useCallback, useState } from 'react';
import { Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { useTheme, ThemeSelector } from '../../theme/index';
import { useUser } from '../../contexts/UserContext';
import { useNotifications } from '../../contexts/NotificationContext';
import { canViewInfrastructure } from '../../utils/permissions';
import { apiRequest } from '../../api';
import { ArchetypeIcon } from '../../components/icons';
import AdminMenuButton from '../../components/AdminMenuButton';
import { useInfrastructureData } from './useInfrastructureData';
import { useAgentUpdates } from './useAgentUpdates';
import { useMtuConfig } from './useMtuConfig';
import { useNicGroups } from './useNicGroups';
import HostsTab from './HostsTab';
import NetworkTab from './NetworkTab';
import DeregisterModal from './DeregisterModal';
import MtuConfigModal from './MtuConfigModal';
import { NicGroupCreateModal, NicGroupMemberModal } from './NicGroupModal';
import type { HostDetailed, SyncStrategy, TabType } from './infrastructureTypes';

// ============================================================================
// Component
// ============================================================================

const InfrastructurePage: React.FC = () => {
  const { effectiveMode, toggleMode } = useTheme();
  const { user, loading: userLoading } = useUser();
  const { addNotification } = useNotifications();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const notifyError = useCallback((title: string, err: unknown) => {
    addNotification('error', title, err instanceof Error ? err.message : undefined);
  }, [addNotification]);

  // Tab state from URL
  const activeTab = (searchParams.get('tab') as TabType) || 'hosts';
  const setActiveTab = (tab: TabType) => {
    setSearchParams({ tab });
  };

  const [showThemeSelector, setShowThemeSelector] = useState(false);

  // Managed interfaces show/hide
  const [showManagedInterfaces, setShowManagedInterfaces] = useState(false);

  // Data loading
  const data = useInfrastructureData();

  // Agent updates
  const updates = useAgentUpdates(data.hosts, data.loadHosts);

  // MTU config
  const mtuConfig = useMtuConfig(
    data.hosts,
    data.networkConfigs,
    data.managedInterfaces,
    data.mesh,
    data.mtuValue,
    data.loadMesh,
    data.loadNetworkConfigs,
  );

  // NIC groups
  const nicGroupActions = useNicGroups(data.hosts, data.managedInterfaces, data.loadNicGroups);

  // Expansion toggles for host cards
  const [expandedLabs, setExpandedLabs] = useState<Set<string>>(new Set());
  const [expandedContainers, setExpandedContainers] = useState<Set<string>>(new Set());
  const [expandedVMs, setExpandedVMs] = useState<Set<string>>(new Set());
  const [expandedImages, setExpandedImages] = useState<Set<string>>(new Set());

  // Deregister state
  const [deregisterHost, setDeregisterHost] = useState<HostDetailed | null>(null);
  const [deregisterInfo, setDeregisterInfo] = useState<{
    agent_name: string;
    agent_status: string;
    labs_assigned: number;
    running_labs: { id: string; name: string; state: string }[];
    node_placements: number;
    nodes_assigned: number;
    vxlan_tunnels: number;
    cross_host_links: number;
  } | null>(null);
  const [deregisterLoading, setDeregisterLoading] = useState(false);
  const [deregistering, setDeregistering] = useState(false);

  // Settings form state
  const [savingSettings, setSavingSettings] = useState(false);

  // ============================================================================
  // Auth Check
  // ============================================================================

  if (!userLoading && user && !canViewInfrastructure(user)) {
    return <Navigate to="/" replace />;
  }

  if (!userLoading && !user) {
    return <Navigate to="/" replace />;
  }

  // ============================================================================
  // Settings Handlers
  // ============================================================================

  const handleMtuChange = (value: number) => {
    data.setMtuValue(value);
    data.setSettingsDirty(value !== data.mesh?.settings.overlay_mtu);
  };

  const saveSettings = async () => {
    setSavingSettings(true);
    try {
      await apiRequest('/infrastructure/settings', {
        method: 'PATCH',
        body: JSON.stringify({
          overlay_mtu: data.mtuValue,
        }),
      });
      data.setSettingsDirty(false);
      await data.loadMesh();
    } catch (err) {
      notifyError('Failed to save settings', err);
    } finally {
      setSavingSettings(false);
    }
  };

  // ============================================================================
  // Host Handlers
  // ============================================================================

  const toggleSet = (setter: React.Dispatch<React.SetStateAction<Set<string>>>) => (hostId: string) => {
    setter(prev => {
      const next = new Set(prev);
      if (next.has(hostId)) next.delete(hostId); else next.add(hostId);
      return next;
    });
  };

  const toggleImages = async (hostId: string) => {
    const opening = !expandedImages.has(hostId);
    setExpandedImages(prev => {
      const next = new Set(prev);
      if (next.has(hostId)) next.delete(hostId); else next.add(hostId);
      return next;
    });
    if (opening) {
      try {
        await data.loadAgentImageDetails(hostId);
      } catch (err) {
        notifyError('Failed to load agent image inventory', err);
      }
    }
  };

  const updateSyncStrategy = async (hostId: string, strategy: SyncStrategy) => {
    try {
      await apiRequest(`/agents/${hostId}/sync-strategy`, {
        method: 'PUT',
        body: JSON.stringify({ strategy }),
      });
      data.setHosts(prev => prev.map(h =>
        h.id === hostId ? { ...h, image_sync_strategy: strategy } : h
      ));
    } catch (err) {
      console.error('Failed to update sync strategy:', err);
      notifyError('Failed to update sync strategy', err);
    }
  };

  // ============================================================================
  // Deregister Handlers
  // ============================================================================

  const handleDeregisterClick = async (host: HostDetailed) => {
    setDeregisterHost(host);
    setDeregisterInfo(null);
    setDeregisterLoading(true);
    try {
      const info = await apiRequest<typeof deregisterInfo>(`/agents/${host.id}/deregister-info`);
      setDeregisterInfo(info);
    } catch (err) {
      console.error('Failed to load deregister info:', err);
      setDeregisterInfo({
        agent_name: host.name,
        agent_status: host.status,
        labs_assigned: host.labs.length,
        running_labs: host.labs.filter(l => l.state === 'running' || l.state === 'starting'),
        node_placements: 0,
        nodes_assigned: 0,
        vxlan_tunnels: 0,
        cross_host_links: 0,
      });
    } finally {
      setDeregisterLoading(false);
    }
  };

  const handleConfirmDeregister = async () => {
    if (!deregisterHost) return;
    setDeregistering(true);
    try {
      await apiRequest(`/agents/${deregisterHost.id}`, { method: 'DELETE' });
      setDeregisterHost(null);
      setDeregisterInfo(null);
      await data.loadHosts();
    } catch (err) {
      notifyError('Failed to deregister host', err);
    } finally {
      setDeregistering(false);
    }
  };

  const handleCancelDeregister = () => {
    setDeregisterHost(null);
    setDeregisterInfo(null);
  };

  // ============================================================================
  // Helpers
  // ============================================================================

  const outdatedCount = data.hosts.filter(
    h => h.status === 'online' && updates.isUpdateAvailable(h, data.latestVersion)
  ).length;

  // ============================================================================
  // Render
  // ============================================================================

  return (
    <>
      <div className="min-h-screen bg-stone-50/72 dark:bg-stone-900/72 backdrop-blur-[1px] flex flex-col overflow-hidden">
        <header className="h-20 border-b border-stone-200 dark:border-stone-800 bg-white/30 dark:bg-stone-900/30 flex items-center justify-between px-10">
          <div className="flex items-center gap-4">
            <ArchetypeIcon size={40} className="text-sage-600 dark:text-sage-400" />
            <div>
              <h1 className="text-xl font-black text-stone-900 dark:text-white tracking-tight">ARCHETYPE</h1>
              <p className="text-[10px] text-sage-600 dark:text-sage-500 font-bold uppercase tracking-widest">Infrastructure</p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate('/')}
              className="flex items-center gap-2 px-3 py-2 glass-control text-stone-600 dark:text-stone-300 rounded-lg transition-all"
            >
              <i className="fa-solid fa-arrow-left text-xs"></i>
              <span className="text-[10px] font-bold uppercase">Back</span>
            </button>

            <AdminMenuButton />

            <button
              onClick={() => setShowThemeSelector(true)}
              className="w-9 h-9 flex items-center justify-center glass-control text-stone-600 dark:text-stone-400 hover:text-sage-600 dark:hover:text-sage-400 rounded-lg transition-all"
              title="Theme Settings"
            >
              <i className="fa-solid fa-palette text-sm"></i>
            </button>

            <button
              onClick={toggleMode}
              className="w-9 h-9 flex items-center justify-center glass-control text-stone-600 dark:text-stone-400 hover:text-sage-600 dark:hover:text-sage-400 rounded-lg transition-all"
              title={`Switch to ${effectiveMode === 'dark' ? 'light' : 'dark'} mode`}
            >
              <i className={`fa-solid ${effectiveMode === 'dark' ? 'fa-sun' : 'fa-moon'} text-sm`}></i>
            </button>

          </div>
        </header>

        {/* Tab Bar */}
        <div className="border-b border-stone-200 dark:border-stone-800 glass-surface px-10">
          <div className="flex gap-1">
            <button
              onClick={() => setActiveTab('hosts')}
              className={`px-4 py-3 text-sm font-medium border-b-2 transition-all ${
                activeTab === 'hosts'
                  ? 'text-sage-600 dark:text-sage-400 border-sage-600 dark:border-sage-400'
                  : 'text-stone-500 dark:text-stone-400 border-transparent hover:text-stone-700 dark:hover:text-stone-300'
              }`}
            >
              <i className="fa-solid fa-server mr-2"></i>
              Hosts
              <span className="ml-2 px-1.5 py-0.5 text-xs rounded-full bg-stone-200 dark:bg-stone-700">
                {data.hosts.length}
              </span>
            </button>
            <button
              onClick={() => setActiveTab('network')}
              className={`px-4 py-3 text-sm font-medium border-b-2 transition-all ${
                activeTab === 'network'
                  ? 'text-sage-600 dark:text-sage-400 border-sage-600 dark:border-sage-400'
                  : 'text-stone-500 dark:text-stone-400 border-transparent hover:text-stone-700 dark:hover:text-stone-300'
              }`}
            >
              <i className="fa-solid fa-network-wired mr-2"></i>
              Network
            </button>
          </div>
        </div>

        <main className="flex-1 overflow-y-auto p-10 custom-scrollbar">
          {activeTab === 'hosts' ? (
            <HostsTab
              hosts={data.hosts}
              hostsLoading={data.hostsLoading}
              hostsError={data.hostsError}
              latestVersion={data.latestVersion}
              outdatedCount={outdatedCount}
              expandedLabs={expandedLabs}
              expandedContainers={expandedContainers}
              expandedVMs={expandedVMs}
              expandedImages={expandedImages}
              agentImageDetails={data.agentImageDetails}
              agentImagesLoading={data.agentImagesLoading}
              agentImagesCleaning={data.agentImagesCleaning}
              updatingAgents={updates.updatingAgents}
              updateStatuses={updates.updateStatuses}
              isUpdateAvailable={(host) => updates.isUpdateAvailable(host, data.latestVersion)}
              onToggleLabs={toggleSet(setExpandedLabs)}
              onToggleContainers={toggleSet(setExpandedContainers)}
              onToggleVMs={toggleSet(setExpandedVMs)}
              onToggleImages={toggleImages}
              onCleanupStaleImages={async (hostId) => {
                try {
                  const result = await data.cleanupStaleAgentImages(hostId);
                  if (result.deleted.length > 0) {
                    addNotification('success', 'Removed stale agent images', `${result.deleted.length} artifact${result.deleted.length !== 1 ? 's' : ''} removed.`);
                  } else if (result.failed.length > 0) {
                    addNotification('warning', 'Stale cleanup incomplete', result.failed[0].error);
                  } else {
                    addNotification('info', 'No stale images removed');
                  }
                } catch (err) {
                  notifyError('Failed to clean stale agent images', err);
                }
              }}
              onUpdateSyncStrategy={updateSyncStrategy}
              onTriggerUpdate={(hostId) => updates.triggerUpdate(hostId)}
              onTriggerRebuild={updates.triggerRebuild}
              onTriggerBulkUpdate={() => updates.triggerBulkUpdate(data.latestVersion)}
              onCustomUpdate={(hostId, hostName) => {
                updates.setCustomUpdateTarget({ hostId, hostName });
                updates.setCustomVersion('');
              }}
              onDeregister={handleDeregisterClick}
            />
          ) : (
            <NetworkTab
              mesh={data.mesh}
              meshLoading={data.meshLoading}
              meshError={data.meshError}
              hosts={data.hosts}
              networkConfigs={data.networkConfigs}
              networkConfigsLoading={data.networkConfigsLoading}
              managedInterfaces={data.managedInterfaces}
              showManagedInterfaces={showManagedInterfaces}
              setShowManagedInterfaces={setShowManagedInterfaces}
              nicGroups={data.nicGroups}
              nicGroupsLoading={data.nicGroupsLoading}
              mtuValue={data.mtuValue}
              settingsDirty={data.settingsDirty}
              savingSettings={savingSettings}
              mtuRecommendation={mtuConfig.mtuRecommendation}
              configuringMtu={mtuConfig.configuringMtu}
              testingAll={mtuConfig.testingAll}
              testingLink={mtuConfig.testingLink}
              onHandleMtuChange={handleMtuChange}
              onSaveSettings={saveSettings}
              onLoadNetworkConfigs={data.loadNetworkConfigs}
              onOpenMtuConfigModal={mtuConfig.openMtuConfigModal}
              onTestAllLinks={mtuConfig.testAllLinks}
              onTestLink={mtuConfig.testLink}
              onOpenNicGroupModal={nicGroupActions.openNicGroupModal}
              onOpenNicGroupMemberModal={nicGroupActions.openNicGroupMemberModal}
            />
          )}
        </main>

        <footer className="h-10 border-t border-stone-200 dark:border-stone-900 glass-surface flex items-center px-10 justify-between text-[10px] text-stone-500 dark:text-stone-600 font-medium">
          <span>Archetype Infrastructure Management</span>
          <span>Auto-refresh: {activeTab === 'hosts' ? '10s' : '30s'}{activeTab === 'network' ? ' (network)' : ''}</span>
        </footer>
      </div>

      <ThemeSelector
        isOpen={showThemeSelector}
        onClose={() => setShowThemeSelector(false)}
      />

      {/* Deregister Confirmation Modal */}
      {deregisterHost && (
        <DeregisterModal
          deregisterHost={deregisterHost}
          deregisterInfo={deregisterInfo}
          deregisterLoading={deregisterLoading}
          deregistering={deregistering}
          onConfirm={handleConfirmDeregister}
          onCancel={handleCancelDeregister}
        />
      )}

      {/* MTU Configuration Modal */}
      {mtuConfig.configModalData && (
        <MtuConfigModal
          configModalData={mtuConfig.configModalData}
          managedInterfaces={data.managedInterfaces}
          mesh={data.mesh}
          mtuValue={data.mtuValue}
          selectedInterface={mtuConfig.selectedInterface}
          setSelectedInterface={mtuConfig.setSelectedInterface}
          desiredMtu={mtuConfig.desiredMtu}
          setDesiredMtu={mtuConfig.setDesiredMtu}
          savingMtuConfig={mtuConfig.savingMtuConfig}
          selectedTransportMode={mtuConfig.selectedTransportMode}
          setSelectedTransportMode={mtuConfig.setSelectedTransportMode}
          selectedTransportInterface={mtuConfig.selectedTransportInterface}
          setSelectedTransportInterface={mtuConfig.setSelectedTransportInterface}
          useTransportInterface={mtuConfig.useTransportInterface}
          setUseTransportInterface={mtuConfig.setUseTransportInterface}
          onSave={mtuConfig.saveMtuConfig}
          onClose={mtuConfig.closeMtuConfigModal}
        />
      )}

      {/* NIC Group Create Modal */}
      {nicGroupActions.showNicGroupModal && (
        <NicGroupCreateModal
          hosts={data.hosts}
          newNicGroupHostId={nicGroupActions.newNicGroupHostId}
          setNewNicGroupHostId={nicGroupActions.setNewNicGroupHostId}
          newNicGroupName={nicGroupActions.newNicGroupName}
          setNewNicGroupName={nicGroupActions.setNewNicGroupName}
          newNicGroupDescription={nicGroupActions.newNicGroupDescription}
          setNewNicGroupDescription={nicGroupActions.setNewNicGroupDescription}
          creatingNicGroup={nicGroupActions.creatingNicGroup}
          onCreate={nicGroupActions.createNicGroup}
          onClose={nicGroupActions.closeNicGroupModal}
        />
      )}

      {/* NIC Group Member Modal */}
      {nicGroupActions.showNicGroupMemberModal && nicGroupActions.memberGroup && (
        <NicGroupMemberModal
          memberGroup={nicGroupActions.memberGroup}
          managedInterfaces={data.managedInterfaces}
          memberInterfaceId={nicGroupActions.memberInterfaceId}
          setMemberInterfaceId={nicGroupActions.setMemberInterfaceId}
          memberRole={nicGroupActions.memberRole}
          setMemberRole={nicGroupActions.setMemberRole}
          addingNicGroupMember={nicGroupActions.addingNicGroupMember}
          onAdd={nicGroupActions.addNicGroupMember}
          onClose={nicGroupActions.closeNicGroupMemberModal}
        />
      )}

      {/* Custom Version Update Modal */}
      {updates.customUpdateTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
          <div className="bg-white dark:bg-stone-900 border border-stone-200 dark:border-stone-700 rounded-xl shadow-xl w-full max-w-md mx-4">
            <div className="p-5 border-b border-stone-200 dark:border-stone-800">
              <h3 className="text-lg font-semibold text-stone-800 dark:text-stone-200">
                Custom Update Target
              </h3>
              <p className="text-xs text-stone-500 dark:text-stone-400 mt-1">
                Update <span className="font-medium">{updates.customUpdateTarget.hostName}</span> to a specific version, branch, or commit
              </p>
            </div>
            <div className="p-5">
              <input
                type="text"
                value={updates.customVersion}
                onChange={e => updates.setCustomVersion(e.target.value)}
                placeholder="e.g. 0.3.7, abc1234, or main"
                className="w-full px-3 py-2 bg-stone-50 dark:bg-stone-800 border border-stone-300 dark:border-stone-600 rounded-lg text-sm text-stone-800 dark:text-stone-200 placeholder-stone-400 focus:outline-none focus:ring-2 focus:ring-amber-500 dark:focus:ring-amber-400 font-mono"
                autoFocus
                onKeyDown={e => {
                  if (e.key === 'Enter' && updates.customVersion.trim()) {
                    updates.triggerUpdate(updates.customUpdateTarget!.hostId, updates.customVersion.trim());
                    updates.setCustomUpdateTarget(null);
                  } else if (e.key === 'Escape') {
                    updates.setCustomUpdateTarget(null);
                  }
                }}
              />
              <p className="text-[10px] text-stone-400 dark:text-stone-500 mt-2">
                Accepts version tags (0.3.7), commit SHAs (abc1234), or branch names (main)
              </p>
            </div>
            <div className="p-4 border-t border-stone-200 dark:border-stone-800 flex justify-end gap-3">
              <button
                onClick={() => updates.setCustomUpdateTarget(null)}
                className="px-4 py-2 glass-control text-stone-600 dark:text-stone-400 rounded-lg transition-all text-sm font-medium"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  if (updates.customVersion.trim()) {
                    updates.triggerUpdate(updates.customUpdateTarget!.hostId, updates.customVersion.trim());
                    updates.setCustomUpdateTarget(null);
                  }
                }}
                disabled={!updates.customVersion.trim()}
                className={`px-4 py-2 rounded-lg transition-all text-sm font-medium ${
                  updates.customVersion.trim()
                    ? 'bg-amber-500 hover:bg-amber-600 text-white'
                    : 'bg-stone-200 dark:bg-stone-800 text-stone-400 cursor-not-allowed'
                }`}
              >
                <i className="fa-solid fa-download mr-2"></i>
                Update
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};

export default InfrastructurePage;
