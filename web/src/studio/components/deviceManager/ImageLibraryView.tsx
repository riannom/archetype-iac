import React from 'react';
import { DeviceModel, ImageLibraryEntry } from '../../types';
import ImageCard from '../ImageCard';
import ImageFilterBar, { ImageAssignmentFilter, ImageSortOption } from '../ImageFilterBar';
import { PendingQcow2Upload } from './deviceManagerTypes';
import type { AgentStaleImageSummaryResponse } from '../../../types/agentImages';

interface ImageLibraryViewProps {
  runnableImageLibrary: ImageLibraryEntry[];
  deviceModels: DeviceModel[];
  staleAgentSummary: AgentStaleImageSummaryResponse | null;
  filteredImages: ImageLibraryEntry[];
  unassignedImages: ImageLibraryEntry[];
  assignedImagesByDevice: Map<string, ImageLibraryEntry[]>;
  filteredPendingQcow2Uploads: PendingQcow2Upload[];
  imageSearch: string;
  setImageSearch: (value: string) => void;
  selectedImageVendors: Set<string>;
  toggleImageVendor: (vendor: string) => void;
  selectedImageKinds: Set<string>;
  toggleImageKind: (kind: string) => void;
  imageAssignmentFilter: ImageAssignmentFilter;
  setImageAssignmentFilter: (value: ImageAssignmentFilter) => void;
  imageSort: ImageSortOption;
  setImageSort: (value: ImageSortOption) => void;
  clearImageFilters: () => void;
  onUnassignImage: (imageId: string, deviceId?: string) => Promise<void>;
  onSetDefaultImage: (imageId: string, deviceId: string) => Promise<void>;
  onDeleteImage: (imageId: string) => Promise<void>;
  onRefresh: () => void;
  showSyncStatus: boolean;
}

const ImageLibraryView: React.FC<ImageLibraryViewProps> = ({
  runnableImageLibrary,
  deviceModels,
  staleAgentSummary,
  filteredImages,
  unassignedImages,
  assignedImagesByDevice,
  filteredPendingQcow2Uploads,
  imageSearch,
  setImageSearch,
  selectedImageVendors,
  toggleImageVendor,
  selectedImageKinds,
  toggleImageKind,
  imageAssignmentFilter,
  setImageAssignmentFilter,
  imageSort,
  setImageSort,
  clearImageFilters,
  onUnassignImage,
  onSetDefaultImage,
  onDeleteImage,
  onRefresh,
  showSyncStatus,
}) => {
  const staleHosts = staleAgentSummary?.hosts.filter((host) => host.stale_image_count > 0) || [];

  return (
    <div className="flex-1 flex flex-col overflow-hidden min-h-0">
      {/* Image filter bar */}
      <ImageFilterBar
        images={runnableImageLibrary}
        devices={deviceModels}
        searchQuery={imageSearch}
        onSearchChange={setImageSearch}
        selectedVendors={selectedImageVendors}
        onVendorToggle={toggleImageVendor}
        selectedKinds={selectedImageKinds}
        onKindToggle={toggleImageKind}
        assignmentFilter={imageAssignmentFilter}
        onAssignmentFilterChange={setImageAssignmentFilter}
        sortOption={imageSort}
        onSortChange={setImageSort}
        onClearAll={clearImageFilters}
      />

      {/* Image grid */}
      <div className="flex-1 overflow-y-auto p-4 custom-scrollbar">
        {staleAgentSummary && staleAgentSummary.total_stale_images > 0 && (
          <div className="mb-4 rounded-xl border border-amber-300/70 bg-amber-50/80 px-4 py-3 text-sm text-amber-900 dark:border-amber-700/70 dark:bg-amber-900/20 dark:text-amber-100">
            <div className="flex items-start gap-3">
              <i className="fa-solid fa-triangle-exclamation mt-0.5 text-amber-600 dark:text-amber-400" />
              <div className="min-w-0">
                <div className="font-semibold">
                  {staleAgentSummary.total_stale_images} stale agent image artifact{staleAgentSummary.total_stale_images !== 1 ? 's' : ''} detected
                </div>
                <div className="mt-1 text-xs text-amber-800/90 dark:text-amber-200/90">
                  {staleAgentSummary.affected_agents} host{staleAgentSummary.affected_agents !== 1 ? 's' : ''} currently report unreferenced Docker or libvirt image artifacts.
                </div>
                <div className="mt-2 text-xs text-amber-800/90 dark:text-amber-200/90">
                  {staleHosts.map((host) => `${host.agent_name}: ${host.stale_image_count}`).join(', ')}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Unassigned images section */}
        {(unassignedImages.length > 0 || filteredPendingQcow2Uploads.length > 0) && (
          <div className="mb-6">
            <div className="flex items-center gap-2 mb-3">
              <span className="w-2 h-2 rounded-full bg-amber-500" />
              <h3 className="text-xs font-bold text-stone-500 dark:text-stone-400 uppercase tracking-widest">
                Unassigned Images
              </h3>
              <span className="text-[10px] text-stone-400">
                ({unassignedImages.length + filteredPendingQcow2Uploads.length})
              </span>
            </div>
            <div className="grid grid-cols-2 gap-3">
              {filteredPendingQcow2Uploads.map((pending) => (
                <ImageCard
                  key={pending.tempId}
                  image={{
                    id: pending.tempId,
                    kind: 'qcow2',
                    reference: pending.filename,
                    filename: pending.filename,
                    device_id: null,
                    uploaded_at: new Date(pending.createdAt).toISOString(),
                    vendor: null,
                    version: null,
                  }}
                  isPending
                  pendingMessage={
                    pending.phase === 'uploading'
                      ? `Uploading ${pending.progress}%`
                      : 'Processing image (validation and metadata)...'
                  }
                />
              ))}
              {unassignedImages.map((img) => (
                <ImageCard
                  key={img.id}
                  image={img}
                  device={img.device_id ? deviceModels.find((d) => d.id === img.device_id) : undefined}
                  onUnassign={() => onUnassignImage(img.id)}
                  onDelete={() => onDeleteImage(img.id)}
                  onSync={onRefresh}
                  showSyncStatus={showSyncStatus}
                />
              ))}
            </div>
          </div>
        )}

        {/* Assigned images by device */}
        {Array.from(assignedImagesByDevice.entries()).map(([deviceId, images]) => {
          const device = deviceModels.find((d) => d.id === deviceId);
          return (
            <div key={deviceId} className="mb-6">
              <div className="flex items-center gap-2 mb-3">
                <span className="w-2 h-2 rounded-full bg-emerald-500" />
                <h3 className="text-xs font-bold text-stone-700 dark:text-stone-300">
                  {device?.name || deviceId}
                </h3>
                <span className="text-[10px] text-stone-400">({images.length})</span>
              </div>
              <div className="grid grid-cols-2 gap-3">
                {images.map((img) => (
                  <ImageCard
                    key={img.id}
                    image={img}
                    device={device}
                    onUnassign={() => onUnassignImage(img.id, deviceId)}
                    onSetDefault={() => onSetDefaultImage(img.id, deviceId)}
                    onDelete={() => onDeleteImage(img.id)}
                    onSync={onRefresh}
                    showSyncStatus={showSyncStatus}
                  />
                ))}
              </div>
            </div>
          );
        })}

        {filteredImages.length === 0 && (
          <div className="text-center py-12">
            <i className="fa-solid fa-images text-4xl text-stone-300 dark:text-stone-700 mb-4" />
            <h3 className="text-sm font-bold text-stone-500 dark:text-stone-400">No images found</h3>
            <p className="text-xs text-stone-400 mt-1">
              Upload Docker or QCOW2 images to get started
            </p>
          </div>
        )}
      </div>
    </div>
  );
};

export default ImageLibraryView;
