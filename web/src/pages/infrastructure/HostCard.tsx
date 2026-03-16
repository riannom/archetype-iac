import React from 'react';
import { formatSize, formatStorageSize, formatTimestamp, formatUptimeFromBoot } from '../../utils/format';
import { getMemoryUsageDisplay } from '../../utils/resourceUsage';
import {
  getCpuColor,
  getMemoryColor,
  getStorageColor,
  getConnectionStatusColor,
  getConnectionStatusText,
  getRoleBadgeColor,
  getRoleLabel,
  type ConnectionStatus,
  type RoleBadgeType,
} from '../../utils/status';
import type { AgentImagesDetailResponse, ContainerDetail, HostDetailed, SyncStrategy, UpdateStatus, VmDetail } from './infrastructureTypes';
import { SYNC_STRATEGY_OPTIONS } from './infrastructureTypes';
import { Select } from '../../components/ui/Select';

interface HostCardProps {
  host: HostDetailed;
  latestVersion: string;
  isUpdateAvailable: boolean;
  expandedLabs: Set<string>;
  expandedContainers: Set<string>;
  expandedVMs: Set<string>;
  expandedImages: Set<string>;
  agentImageDetails: Record<string, AgentImagesDetailResponse>;
  agentImagesLoading: Set<string>;
  agentImagesCleaning: Set<string>;
  updatingAgents: Set<string>;
  updateStatuses: Map<string, UpdateStatus>;
  onToggleLabs: (hostId: string) => void;
  onToggleContainers: (hostId: string) => void;
  onToggleVMs: (hostId: string) => void;
  onToggleImages: (hostId: string) => void;
  onCleanupStaleImages: (hostId: string) => void;
  onUpdateSyncStrategy: (hostId: string, strategy: SyncStrategy) => void;
  onTriggerUpdate: (hostId: string) => void;
  onTriggerRebuild: (hostId: string) => void;
  onCustomUpdate: (hostId: string, hostName: string) => void;
  onDeregister: (host: HostDetailed) => void;
}

const HostCard: React.FC<HostCardProps> = ({
  host,
  latestVersion,
  isUpdateAvailable,
  expandedLabs,
  expandedContainers,
  expandedVMs,
  expandedImages,
  agentImageDetails,
  agentImagesLoading,
  agentImagesCleaning,
  updatingAgents,
  updateStatuses,
  onToggleLabs,
  onToggleContainers,
  onToggleVMs,
  onToggleImages,
  onCleanupStaleImages,
  onUpdateSyncStrategy,
  onTriggerUpdate,
  onTriggerRebuild,
  onCustomUpdate,
  onDeregister,
}) => {
  const isExpanded = expandedLabs.has(host.id);
  const hasMultipleLabs = host.labs.length > 3;
  const imageDetails = agentImageDetails[host.id];
  const staleImages = imageDetails?.stale_images || [];
  const staleArtifactImages = staleImages.filter((img) => img.is_stale);
  const inventoryRefreshedAt = imageDetails?.inventory_refreshed_at;
  const memoryUsage = getMemoryUsageDisplay(host.resource_usage);

  return (
    <div
      className={`glass-surface rounded-2xl p-6 hover:border-sage-500/30 hover:shadow-xl transition-all ${host.status !== 'online' ? 'opacity-50 hover:opacity-100' : ''}`}
    >
      {/* Header */}
      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className={`w-3 h-3 rounded-full ${getConnectionStatusColor(host.status as ConnectionStatus)} ${host.status === 'online' ? 'animate-pulse' : ''}`}></div>
          <div>
            <h3 className="font-bold text-stone-900 dark:text-white">{host.name}</h3>
            <p className="text-xs text-stone-500">{host.address}</p>
          </div>
        </div>
        <span className={`px-2 py-0.5 rounded-md text-[11px] font-bold uppercase border ${getRoleBadgeColor(host.role as RoleBadgeType)}`}>
          {getRoleLabel(host.role as RoleBadgeType)}
        </span>
      </div>

      {/* Status & Version */}
      <div className="flex items-center gap-4 text-xs text-stone-500 dark:text-stone-400 mb-4">
        <span className="flex items-center gap-1">
          <i className="fa-solid fa-circle text-[8px]" style={{ color: host.status === 'online' ? '#22c55e' : '#ef4444' }}></i>
          {getConnectionStatusText(host.status as ConnectionStatus)}
        </span>
        <span className={isUpdateAvailable ? 'text-amber-600 dark:text-amber-400' : ''}>
          <span title={host.git_sha ? `Commit: ${host.git_sha}` : ''}>
            v{host.version}
            {host.git_sha && (
              <span className="ml-1 text-stone-400 dark:text-stone-500 font-mono text-[11px]">
                ({host.git_sha.substring(0, 7)})
              </span>
            )}
          </span>
          {isUpdateAvailable && (
            <i className="fa-solid fa-arrow-up ml-1 text-[11px]" title={`Update available: v${latestVersion}`}></i>
          )}
        </span>
        {host.started_at && (
          <span className="text-stone-400 font-mono" title="System uptime">
            <i className="fa-solid fa-clock mr-1"></i>
            {formatUptimeFromBoot(host.started_at)}
          </span>
        )}
        <span className="text-stone-400">
          <i className="fa-regular fa-clock mr-1"></i>
          {formatTimestamp(host.last_heartbeat)}
        </span>
      </div>

      {/* Error Alert */}
      {host.last_error && (
        <div className="mb-4 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg">
          <div className="flex items-start gap-2">
            <i className="fa-solid fa-triangle-exclamation text-red-500 dark:text-red-400 mt-0.5 flex-shrink-0"></i>
            <div className="flex-1 min-w-0">
              <div className="text-xs font-semibold text-red-700 dark:text-red-300 mb-1">
                Agent Error
                {host.error_since && (
                  <span className="font-normal text-red-500 dark:text-red-400 ml-1">
                    (since {formatTimestamp(host.error_since)})
                  </span>
                )}
              </div>
              <div className="text-xs text-red-600 dark:text-red-400 break-words">
                {host.last_error}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Update Progress or Button */}
      {updatingAgents.has(host.id) ? (
        <div className="mb-4 p-2 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg">
          <div className="flex items-center justify-between text-xs mb-1">
            <span className="text-blue-700 dark:text-blue-300 font-medium">
              <i className="fa-solid fa-spinner fa-spin mr-1.5"></i>
              {host.deployment_mode === 'docker' ? 'Rebuilding...' :
                updateStatuses.get(host.id)?.status === 'downloading' ? 'Downloading...' :
                updateStatuses.get(host.id)?.status === 'installing' ? 'Installing...' :
                updateStatuses.get(host.id)?.status === 'restarting' ? 'Restarting...' :
                'Starting update...'}
            </span>
            {host.deployment_mode !== 'docker' && (
              <span className="text-blue-600 dark:text-blue-400">
                {updateStatuses.get(host.id)?.progress_percent || 0}%
              </span>
            )}
          </div>
          {host.deployment_mode !== 'docker' && (
            <div className="h-1.5 bg-blue-200 dark:bg-blue-800 rounded-full overflow-hidden">
              <div
                className="h-full bg-blue-500 transition-all"
                style={{ width: `${updateStatuses.get(host.id)?.progress_percent || 0}%` }}
              ></div>
            </div>
          )}
        </div>
      ) : isUpdateAvailable && host.status === 'online' ? (
        <div className="mb-4">
          {host.deployment_mode === 'docker' ? (
            <button
              onClick={() => onTriggerRebuild(host.id)}
              className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-purple-100 dark:bg-purple-900/30 hover:bg-purple-200 dark:hover:bg-purple-900/50 text-purple-700 dark:text-purple-400 border border-purple-300 dark:border-purple-700 rounded-lg transition-all text-xs font-medium"
            >
              <i className="fa-solid fa-rotate"></i>
              Rebuild Container
            </button>
          ) : (
            <div className="flex gap-1">
              <button
                onClick={() => onTriggerUpdate(host.id)}
                className="flex-1 flex items-center justify-center gap-2 px-3 py-2 bg-amber-100 dark:bg-amber-900/30 hover:bg-amber-200 dark:hover:bg-amber-900/50 text-amber-700 dark:text-amber-400 border border-amber-300 dark:border-amber-700 rounded-l-lg transition-all text-xs font-medium"
              >
                <i className="fa-solid fa-download"></i>
                Update to v{latestVersion}
              </button>
              <button
                onClick={() => onCustomUpdate(host.id, host.name)}
                className="flex items-center justify-center px-2 py-2 bg-amber-100 dark:bg-amber-900/30 hover:bg-amber-200 dark:hover:bg-amber-900/50 text-amber-700 dark:text-amber-400 border border-l-0 border-amber-300 dark:border-amber-700 rounded-r-lg transition-all text-xs"
                title="Update to custom version or commit"
              >
                <i className="fa-solid fa-ellipsis-vertical"></i>
              </button>
            </div>
          )}
        </div>
      ) : null}

      {/* Resource Bars */}
      <div className="space-y-3 mb-4">
        <div>
          <div className="flex justify-between text-xs mb-1">
            <span className="text-stone-500 dark:text-stone-400 flex items-center gap-1">
              CPU
              {host.resource_usage.cpu_percent >= 95 && (
                <i className="fa-solid fa-circle-exclamation text-red-500" title="CPU critical - deployment may fail"></i>
              )}
              {host.resource_usage.cpu_percent >= 80 && host.resource_usage.cpu_percent < 95 && (
                <i className="fa-solid fa-triangle-exclamation text-amber-500" title="CPU high - consider distributing nodes across agents"></i>
              )}
            </span>
            <span className="font-medium text-stone-700 dark:text-stone-300">{host.resource_usage.cpu_percent.toFixed(0)}%</span>
          </div>
          <div className="h-2 bg-stone-200 dark:bg-stone-700 rounded-full overflow-hidden">
            <div className={`h-full ${getCpuColor(host.resource_usage.cpu_percent)} transition-all`} style={{ width: `${Math.min(host.resource_usage.cpu_percent, 100)}%` }}></div>
          </div>
        </div>

        <div>
          <div className="flex justify-between text-xs mb-1">
            <span className="text-stone-500 dark:text-stone-400 flex items-center gap-1">
              Memory
              {memoryUsage.percent >= 95 && (
                <i className="fa-solid fa-circle-exclamation text-red-500" title="Memory critical - deployment will likely fail"></i>
              )}
              {memoryUsage.percent >= 80 && memoryUsage.percent < 95 && (
                <i className="fa-solid fa-triangle-exclamation text-amber-500" title="Memory high - consider distributing nodes across agents"></i>
              )}
            </span>
            <span className="font-medium text-stone-700 dark:text-stone-300">
              {memoryUsage.hasTotals
                ? `${formatStorageSize(memoryUsage.usedGb)} / ${formatStorageSize(memoryUsage.totalGb)}`
                : `${memoryUsage.percent.toFixed(0)}%`
              }
            </span>
          </div>
          <div className="h-2 bg-stone-200 dark:bg-stone-700 rounded-full overflow-hidden">
            <div className={`h-full ${getMemoryColor(memoryUsage.percent)} transition-all`} style={{ width: `${Math.min(memoryUsage.percent, 100)}%` }}></div>
          </div>
        </div>

        <div>
          <div className="flex justify-between text-xs mb-1">
            <span className="text-stone-500 dark:text-stone-400 flex items-center gap-1">
              Storage
              {host.resource_usage.storage_percent >= 95 && (
                <i className="fa-solid fa-circle-exclamation text-red-500" title="Storage critical - deployment will likely fail"></i>
              )}
              {host.resource_usage.storage_percent >= 85 && host.resource_usage.storage_percent < 95 && (
                <i className="fa-solid fa-triangle-exclamation text-amber-500" title="Storage high - consider freeing space or using another agent"></i>
              )}
            </span>
            <span className="font-medium text-stone-700 dark:text-stone-300">
              {formatStorageSize(host.resource_usage.storage_used_gb)} / {formatStorageSize(host.resource_usage.storage_total_gb)}
            </span>
          </div>
          <div className="h-2 bg-stone-200 dark:bg-stone-700 rounded-full overflow-hidden">
            <div className={`h-full ${getStorageColor(host.resource_usage.storage_percent)} transition-all`} style={{ width: `${Math.min(host.resource_usage.storage_percent, 100)}%` }}></div>
          </div>
        </div>
      </div>

      {/* Containers */}
      {(() => {
        const containers = host.resource_usage.container_details || [];
        const labContainers = containers.filter(c => !c.is_system);
        const systemCount = containers.filter(c => c.is_system).length;
        const isContainersOpen = expandedContainers.has(host.id);
        // Group lab containers by lab
        const byLab = new Map<string, { name: string; items: ContainerDetail[] }>();
        for (const c of labContainers) {
          const key = c.lab_id || '_unknown';
          if (!byLab.has(key)) byLab.set(key, { name: c.lab_name || 'Unknown Lab', items: [] });
          byLab.get(key)!.items.push(c);
        }
        return (
          <div className="py-2 border-t border-stone-100 dark:border-stone-800">
            <button
              onClick={() => onToggleContainers(host.id)}
              className="w-full flex items-center justify-between text-xs text-stone-600 dark:text-stone-400 hover:text-stone-900 dark:hover:text-stone-200 transition-colors"
            >
              <span className="flex items-center gap-1.5">
                <i className="fa-solid fa-cube text-stone-400"></i>
                <strong>{host.resource_usage.containers_running}</strong>/{host.resource_usage.containers_total} containers
                {host.capabilities.providers && host.capabilities.providers.length > 0 && (
                  <span className="flex items-center gap-1 text-[11px] px-1.5 py-0.5 bg-stone-100 dark:bg-stone-800 rounded ml-1">
                    {host.capabilities.providers.join(', ')}
                  </span>
                )}
              </span>
              <i className={`fa-solid fa-chevron-${isContainersOpen ? 'up' : 'down'} text-[11px] text-stone-400`}></i>
            </button>
            {isContainersOpen && (
              <div className="mt-2 space-y-2">
                {Array.from(byLab.entries()).map(([labId, group]) => (
                  <div key={labId}>
                    {byLab.size > 1 && (
                      <div className="text-[11px] font-medium text-stone-400 dark:text-stone-500 mb-1 truncate">{group.name}</div>
                    )}
                    <div className="space-y-0.5">
                      {group.items.map((c, i) => (
                        <div
                          key={i}
                          className={`flex items-center justify-between text-xs py-1 px-2 bg-stone-50 dark:bg-stone-800/50 rounded ${c.status !== 'running' ? 'opacity-40 hover:opacity-100 transition-opacity' : ''}`}
                        >
                          <span className="text-stone-700 dark:text-stone-300 truncate max-w-[160px]">{c.node_name || c.name}</span>
                          <span className="flex items-center gap-1.5">
                            {c.node_kind && (
                              <span className="text-[11px] text-stone-400">{c.node_kind}</span>
                            )}
                            <span className={`w-1.5 h-1.5 rounded-full ${c.status === 'running' ? 'bg-green-500' : 'bg-stone-300 dark:bg-stone-600'}`}></span>
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
                {systemCount > 0 && (
                  <div className="text-[11px] text-stone-400 dark:text-stone-500 px-2">
                    {systemCount} system container{systemCount !== 1 ? 's' : ''}
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })()}

      {/* VMs */}
      {host.resource_usage.vms_total > 0 && (() => {
        const vms = host.resource_usage.vm_details || [];
        const isVMsOpen = expandedVMs.has(host.id);
        const byLab = new Map<string, { name: string; items: VmDetail[] }>();
        for (const v of vms) {
          const key = v.lab_id || '_unknown';
          if (!byLab.has(key)) byLab.set(key, { name: v.lab_name || 'Unknown Lab', items: [] });
          byLab.get(key)!.items.push(v);
        }
        return (
          <div className="py-2 border-t border-stone-100 dark:border-stone-800">
            <button
              onClick={() => onToggleVMs(host.id)}
              className="w-full flex items-center justify-between text-xs text-stone-600 dark:text-stone-400 hover:text-stone-900 dark:hover:text-stone-200 transition-colors"
            >
              <span className="flex items-center gap-1.5">
                <i className="fa-solid fa-desktop text-stone-400"></i>
                <strong>{host.resource_usage.vms_running}</strong>/{host.resource_usage.vms_total} VMs
              </span>
              <i className={`fa-solid fa-chevron-${isVMsOpen ? 'up' : 'down'} text-[11px] text-stone-400`}></i>
            </button>
            {isVMsOpen && (
              <div className="mt-2 space-y-2">
                {Array.from(byLab.entries()).map(([labId, group]) => (
                  <div key={labId}>
                    {byLab.size > 1 && (
                      <div className="text-[11px] font-medium text-stone-400 dark:text-stone-500 mb-1 truncate">{group.name}</div>
                    )}
                    <div className="space-y-0.5">
                      {group.items.map((v, i) => (
                        <div
                          key={i}
                          className={`flex items-center justify-between text-xs py-1 px-2 bg-stone-50 dark:bg-stone-800/50 rounded ${v.status !== 'running' ? 'opacity-40 hover:opacity-100 transition-opacity' : ''}`}
                        >
                          <span className="text-stone-700 dark:text-stone-300 truncate max-w-[160px]">{v.node_name || v.name}</span>
                          <span className="flex items-center gap-1.5">
                            {v.node_kind && (
                              <span className="text-[11px] text-stone-400">{v.node_kind}</span>
                            )}
                            <span className={`w-1.5 h-1.5 rounded-full ${v.status === 'running' ? 'bg-green-500' : 'bg-stone-300 dark:bg-stone-600'}`}></span>
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })()}

      {/* Image Sync Strategy */}
      <div className="py-2 border-t border-stone-100 dark:border-stone-800">
        <div className="flex items-center justify-between">
          <span className="text-xs text-stone-500 dark:text-stone-400">
            <i className="fa-solid fa-sync mr-1.5"></i>
            Image Sync
          </span>
          <Select
            value={host.image_sync_strategy || 'on_demand'}
            onChange={(e) => onUpdateSyncStrategy(host.id, e.target.value as SyncStrategy)}
            disabled={host.status !== 'online'}
            size="sm"
            options={SYNC_STRATEGY_OPTIONS.map(opt => ({ value: opt.value, label: opt.label }))}
          />
        </div>
        <p className="text-[11px] text-stone-400 mt-1">
          {SYNC_STRATEGY_OPTIONS.find(o => o.value === (host.image_sync_strategy || 'on_demand'))?.description}
        </p>
      </div>

      {/* Images */}
      {(host.images.length > 0 || staleImages.length > 0 || agentImagesLoading.has(host.id)) && (() => {
        const isImagesOpen = expandedImages.has(host.id);
        const syncedCount = host.images.filter(img => img.status === 'synced').length;
        const syncingCount = host.images.filter(img => img.status === 'syncing').length;
        const failedCount = host.images.filter(img => img.status === 'failed').length;
        return (
          <div className="py-2 border-t border-stone-100 dark:border-stone-800">
            <button
              onClick={() => onToggleImages(host.id)}
              className="w-full flex items-center justify-between text-xs text-stone-600 dark:text-stone-400 hover:text-stone-900 dark:hover:text-stone-200 transition-colors"
            >
              <span className="flex items-center gap-1.5">
                <i className="fa-solid fa-box-archive text-stone-400"></i>
                <strong>{syncedCount}</strong> image{syncedCount !== 1 ? 's' : ''} synced
                {syncingCount > 0 && (
                  <span className="text-blue-500 dark:text-blue-400 ml-1">{syncingCount} syncing</span>
                )}
                {failedCount > 0 && (
                  <span className="text-red-500 dark:text-red-400 ml-1">{failedCount} failed</span>
                )}
                {staleArtifactImages.length > 0 && (
                  <span className="text-amber-600 dark:text-amber-400 ml-1">{staleArtifactImages.length} stale</span>
                )}
              </span>
              <i className={`fa-solid fa-chevron-${isImagesOpen ? 'up' : 'down'} text-[11px] text-stone-400`}></i>
            </button>
            {isImagesOpen && (
              <div className="mt-2 space-y-0.5">
                {agentImagesLoading.has(host.id) && (
                  <div className="text-[11px] text-stone-400 px-2 py-1">
                    <i className="fa-solid fa-spinner fa-spin mr-1.5"></i>
                    Loading live inventory...
                  </div>
                )}
                {host.images.map((img, i) => (
                  <div
                    key={i}
                    className="flex items-center justify-between text-xs py-1 px-2 bg-stone-50 dark:bg-stone-800/50 rounded"
                  >
                    <span className="font-mono text-stone-700 dark:text-stone-300 truncate max-w-[140px]" title={img.reference}>{img.reference.includes('/') ? img.reference.split('/').pop() : img.reference}</span>
                    <span className="flex items-center gap-1.5">
                      {img.size_bytes != null && (
                        <span className="text-[11px] text-stone-400">{formatSize(img.size_bytes)}</span>
                      )}
                      <span className={`px-1.5 py-0.5 rounded text-[11px] font-medium uppercase ${
                        img.status === 'synced' ? 'bg-green-100 dark:bg-green-900/30 text-green-600 dark:text-green-400' :
                        img.status === 'syncing' ? 'bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400' :
                        'bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400'
                      }`}>
                        {img.status}
                      </span>
                    </span>
                  </div>
                ))}
                {staleImages.length > 0 && (
                  <div className="mt-2 pt-2 border-t border-amber-200/60 dark:border-amber-800/60">
                    <div className="flex items-center justify-between gap-2 px-2 py-1">
                      <div className="text-[11px] font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-400">
                        Stale On Agent
                      </div>
                      {staleArtifactImages.length > 0 && (
                        <button
                          onClick={() => onCleanupStaleImages(host.id)}
                          disabled={agentImagesCleaning.has(host.id) || agentImagesLoading.has(host.id)}
                          className="inline-flex items-center gap-1 rounded border border-amber-300/80 px-2 py-1 text-[11px] font-semibold text-amber-700 transition-colors hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-amber-700/80 dark:text-amber-300 dark:hover:bg-amber-900/30"
                        >
                          <i className={`fa-solid ${agentImagesCleaning.has(host.id) ? 'fa-spinner fa-spin' : 'fa-broom'}`}></i>
                          Clean stale
                        </button>
                      )}
                    </div>
                    {inventoryRefreshedAt && (
                      <div className="px-2 pb-1 text-[11px] text-amber-700/80 dark:text-amber-400/80">
                        Inventory refreshed {formatTimestamp(inventoryRefreshedAt)}
                      </div>
                    )}
                    {staleArtifactImages.map((img, i) => (
                      <div
                        key={`${img.reference}-${i}`}
                        className="flex items-center justify-between text-xs py-1 px-2 bg-amber-50 dark:bg-amber-900/10 rounded"
                      >
                        <span className="font-mono text-amber-900 dark:text-amber-200 truncate max-w-[140px]" title={img.reference}>
                          {img.display_reference.includes('/') ? img.display_reference.split('/').pop() : img.display_reference}
                        </span>
                        <span className="flex items-center gap-1.5">
                          <span className="text-[11px] text-amber-600 dark:text-amber-400 uppercase">{img.kind}</span>
                          {img.size_bytes != null && (
                            <span className="text-[11px] text-stone-400">{formatSize(img.size_bytes)}</span>
                          )}
                        </span>
                      </div>
                    ))}
                    {staleImages.some((img) => !img.is_stale && img.reason) && (
                      <div className="text-[11px] text-amber-700/80 dark:text-amber-400/80 px-2 py-1">
                        {staleImages.find((img) => !img.is_stale && img.reason)?.reason}
                      </div>
                    )}
                    <div className="text-[11px] text-amber-700/80 dark:text-amber-400/80 px-2 py-1">
                      These artifacts are present on the host but not referenced by the catalog or active nodes.
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })()}

      {/* Labs */}
      {host.labs.length > 0 && (
        <div className="pt-2 border-t border-stone-100 dark:border-stone-800">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium text-stone-500 dark:text-stone-400">
              <i className="fa-solid fa-diagram-project mr-1.5"></i>
              {host.lab_count} Lab{host.lab_count !== 1 ? 's' : ''}
            </span>
            {hasMultipleLabs && (
              <button
                onClick={() => onToggleLabs(host.id)}
                className="text-[11px] text-sage-600 dark:text-sage-400 hover:underline"
              >
                {isExpanded ? 'Show less' : `Show all ${host.labs.length}`}
              </button>
            )}
          </div>
          <div className="space-y-1">
            {(isExpanded ? host.labs : host.labs.slice(0, 3)).map((lab) => (
              <div
                key={lab.id}
                className="flex items-center justify-between text-xs py-1 px-2 bg-stone-50 dark:bg-stone-800/50 rounded"
              >
                <span className="text-stone-700 dark:text-stone-300 truncate max-w-[150px]">{lab.name}</span>
                <span className={`px-1.5 py-0.5 rounded text-[11px] font-medium uppercase ${
                  lab.state === 'running' ? 'bg-green-100 dark:bg-green-900/30 text-green-600 dark:text-green-400' :
                  lab.state === 'starting' ? 'bg-amber-100 dark:bg-amber-900/30 text-amber-600 dark:text-amber-400' :
                  'bg-stone-200 dark:bg-stone-700 text-stone-500 dark:text-stone-400'
                }`}>
                  {lab.state}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Deregister */}
      <div className="pt-3 mt-3 border-t border-stone-100 dark:border-stone-800">
        <button
          onClick={() => onDeregister(host)}
          className="w-full flex items-center justify-center gap-2 px-3 py-1.5 text-xs font-medium text-stone-400 dark:text-stone-500 hover:text-red-600 dark:hover:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 border border-transparent hover:border-red-200 dark:hover:border-red-800 rounded-lg transition-all"
        >
          <i className="fa-solid fa-link-slash"></i>
          Deregister Host
        </button>
      </div>
    </div>
  );
};

export default HostCard;
