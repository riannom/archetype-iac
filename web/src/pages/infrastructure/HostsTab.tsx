import React from 'react';
import HostCard from './HostCard';
import { SkeletonCard } from '../../components/ui/Skeleton';
import type { AgentImagesDetailResponse, HostDetailed, SyncStrategy, UpdateStatus } from './infrastructureTypes';

interface HostsTabProps {
  hosts: HostDetailed[];
  hostsLoading: boolean;
  hostsError: string | null;
  latestVersion: string;
  outdatedCount: number;
  expandedLabs: Set<string>;
  expandedContainers: Set<string>;
  expandedVMs: Set<string>;
  expandedImages: Set<string>;
  agentImageDetails: Record<string, AgentImagesDetailResponse>;
  agentImagesLoading: Set<string>;
  agentImagesCleaning: Set<string>;
  agentImagesBulkCleaning: boolean;
  updatingAgents: Set<string>;
  updateStatuses: Map<string, UpdateStatus>;
  isUpdateAvailable: (host: HostDetailed) => boolean;
  onToggleLabs: (hostId: string) => void;
  onToggleContainers: (hostId: string) => void;
  onToggleVMs: (hostId: string) => void;
  onToggleImages: (hostId: string) => void;
  onCleanupStaleImages: (hostId: string) => void;
  onCleanupAllStaleImages: () => void;
  onUpdateSyncStrategy: (hostId: string, strategy: SyncStrategy) => void;
  onTriggerUpdate: (hostId: string) => void;
  onTriggerRebuild: (hostId: string) => void;
  onTriggerBulkUpdate: () => void;
  onCustomUpdate: (hostId: string, hostName: string) => void;
  onDeregister: (host: HostDetailed) => void;
}

const HostsTab: React.FC<HostsTabProps> = ({
  hosts,
  hostsLoading,
  hostsError,
  latestVersion,
  outdatedCount,
  expandedLabs,
  expandedContainers,
  expandedVMs,
  expandedImages,
  agentImageDetails,
  agentImagesLoading,
  agentImagesCleaning,
  agentImagesBulkCleaning,
  updatingAgents,
  updateStatuses,
  isUpdateAvailable,
  onToggleLabs,
  onToggleContainers,
  onToggleVMs,
  onToggleImages,
  onCleanupStaleImages,
  onCleanupAllStaleImages,
  onUpdateSyncStrategy,
  onTriggerUpdate,
  onTriggerRebuild,
  onTriggerBulkUpdate,
  onCustomUpdate,
  onDeregister,
}) => {
  return (
    <div className="max-w-7xl mx-auto">
      <div className="flex justify-between items-center mb-8">
        <div>
          <h2 className="text-2xl font-bold text-stone-900 dark:text-white">Compute Hosts</h2>
          <p className="text-stone-500 text-sm mt-1">
            Monitor and manage infrastructure agents across your environment.
            {latestVersion && <span className="ml-2 text-sage-600 dark:text-sage-400">Latest: v{latestVersion}</span>}
          </p>
        </div>
        <div className="flex items-center gap-4 text-sm text-stone-600 dark:text-stone-400">
          <button
            onClick={onCleanupAllStaleImages}
            disabled={agentImagesBulkCleaning}
            className="flex items-center gap-2 px-3 py-1.5 bg-amber-100 dark:bg-amber-900/30 hover:bg-amber-200 dark:hover:bg-amber-900/50 text-amber-700 dark:text-amber-400 rounded-lg transition-all text-xs font-medium disabled:cursor-not-allowed disabled:opacity-50"
          >
            <i className={`fa-solid ${agentImagesBulkCleaning ? 'fa-spinner fa-spin' : 'fa-broom'}`}></i>
            Clean Stale Images
          </button>
          {outdatedCount > 0 && (
            <button
              onClick={onTriggerBulkUpdate}
              className="flex items-center gap-2 px-3 py-1.5 bg-amber-100 dark:bg-amber-900/30 hover:bg-amber-200 dark:hover:bg-amber-900/50 text-amber-700 dark:text-amber-400 rounded-lg transition-all text-xs font-medium"
            >
              <i className="fa-solid fa-download"></i>
              Update {outdatedCount} Agent{outdatedCount !== 1 ? 's' : ''}
            </button>
          )}
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 rounded-full bg-green-500"></div>
            <span>{hosts.filter(h => h.status === 'online').length} Online</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 rounded-full bg-red-500"></div>
            <span>{hosts.filter(h => h.status !== 'online').length} Offline</span>
          </div>
        </div>
      </div>

      {hostsLoading && hosts.length === 0 ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-6">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
      ) : hostsError ? (
        <div className="text-center py-20 text-red-500">
          <i className="fa-solid fa-exclamation-circle text-3xl mb-3"></i>
          <p>{hostsError}</p>
        </div>
      ) : hosts.length === 0 ? (
        <div className="col-span-full py-20 bg-stone-100/50 dark:bg-stone-900/30 border-2 border-dashed border-stone-300 dark:border-stone-800 rounded-3xl flex flex-col items-center justify-center text-stone-500 dark:text-stone-600">
          <i className="fa-solid fa-server text-5xl mb-4 opacity-10"></i>
          <h3 className="text-lg font-bold text-stone-500 dark:text-stone-400">No Hosts Registered</h3>
          <p className="text-sm max-w-xs text-center mt-1">Start an agent to register hosts with the controller.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-6">
          {hosts.map((host) => (
            <HostCard
              key={host.id}
              host={host}
              latestVersion={latestVersion}
              isUpdateAvailable={isUpdateAvailable(host)}
              expandedLabs={expandedLabs}
              expandedContainers={expandedContainers}
              expandedVMs={expandedVMs}
              expandedImages={expandedImages}
              agentImageDetails={agentImageDetails}
              agentImagesLoading={agentImagesLoading}
              agentImagesCleaning={agentImagesCleaning}
              updatingAgents={updatingAgents}
              updateStatuses={updateStatuses}
              onToggleLabs={onToggleLabs}
              onToggleContainers={onToggleContainers}
              onToggleVMs={onToggleVMs}
              onToggleImages={onToggleImages}
              onCleanupStaleImages={onCleanupStaleImages}
              onUpdateSyncStrategy={onUpdateSyncStrategy}
              onTriggerUpdate={onTriggerUpdate}
              onTriggerRebuild={onTriggerRebuild}
              onCustomUpdate={onCustomUpdate}
              onDeregister={onDeregister}
            />
          ))}
        </div>
      )}
    </div>
  );
};

export default HostsTab;
