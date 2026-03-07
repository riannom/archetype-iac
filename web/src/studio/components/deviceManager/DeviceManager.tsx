import React, { useCallback, useMemo } from 'react';
import { ImageLibraryEntry } from '../../types';
import { DragProvider, useDragContext } from '../../contexts/DragContext';
import { useNotifications } from '../../../contexts/NotificationContext';
import {
  buildImageCompatibilityAliasMap,
  buildResolvedImageDeviceIdsIndex,
  getImageDeviceIds,
  isImageDefaultForDevice,
  isInstantiableImageKind,
} from '../../../utils/deviceModels';
import { DeviceManagerProps } from './deviceManagerTypes';
import { useImageManagementLog } from './useImageManagementLog';
import { useImageUpload } from './useImageUpload';
import { useIolBuildManager } from './useIolBuildManager';
import { useDeviceFilters } from './useDeviceFilters';
import { useImageFilters } from './useImageFilters';
import BuildJobsView from './BuildJobsView';
import DeviceCatalogView from './DeviceCatalogView';
import ImageLibraryView from './ImageLibraryView';
import UploadControls from './UploadControls';
import UploadLogsModal from './UploadLogsModal';

const DeviceManagerInner: React.FC<DeviceManagerProps> = ({
  deviceModels,
  imageLibrary,
  staleAgentSummary,
  onUploadImage,
  onUploadQcow2,
  onRefresh,
  showSyncStatus = true,
  mode = 'images',
}) => {
  const { dragState, unassignImage, assignImageToDevice, deleteImage } = useDragContext();
  const { addNotification } = useNotifications();
  const isBuildJobsMode = mode === 'build-jobs';

  const runnableImageLibrary = useMemo(
    () => imageLibrary.filter((img) => isInstantiableImageKind(img.kind)),
    [imageLibrary]
  );

  const imageCompatibilityAliases = useMemo(
    () => buildImageCompatibilityAliasMap(deviceModels),
    [deviceModels]
  );
  const knownDeviceIds = useMemo(() => deviceModels.map((device) => device.id), [deviceModels]);
  const resolvedImageDeviceIdsByImageId = useMemo(
    () => buildResolvedImageDeviceIdsIndex(runnableImageLibrary, knownDeviceIds, imageCompatibilityAliases),
    [runnableImageLibrary, knownDeviceIds, imageCompatibilityAliases]
  );
  const resolveImageDeviceIds = useCallback((image: ImageLibraryEntry): string[] => {
    return resolvedImageDeviceIdsByImageId.get(image.id) || getImageDeviceIds(image);
  }, [resolvedImageDeviceIdsByImageId]);
  const withDeviceScopedDefault = useCallback(
    (image: ImageLibraryEntry, deviceId: string): ImageLibraryEntry => ({
      ...image,
      is_default: isImageDefaultForDevice(image, deviceId),
    }),
    []
  );

  // Build device to images map (uses compatible_devices for shared images)
  const imagesByDevice = useMemo(() => {
    const map = new Map<string, ImageLibraryEntry[]>();
    runnableImageLibrary.forEach((img) => {
      resolveImageDeviceIds(img).forEach((devId) => {
        const list = map.get(devId) || [];
        list.push(withDeviceScopedDefault(img, devId));
        map.set(devId, list);
      });
    });
    return map;
  }, [runnableImageLibrary, resolveImageDeviceIds, withDeviceScopedDefault]);

  const imageVendorsById = useMemo(() => {
    const deviceVendorById = new Map(
      deviceModels
        .filter((device) => !!device.vendor)
        .map((device) => [device.id, String(device.vendor)])
    );
    const map = new Map<string, string[]>();
    runnableImageLibrary.forEach((img) => {
      const vendors = new Set<string>();
      if (img.vendor) vendors.add(img.vendor);
      getImageDeviceIds(img).forEach((deviceId) => {
        const fallbackVendor = deviceVendorById.get(deviceId);
        if (fallbackVendor) vendors.add(fallbackVendor);
      });
      map.set(img.id, Array.from(vendors).sort());
    });
    return map;
  }, [deviceModels, runnableImageLibrary]);

  // --- Hooks ---

  const logManager = useImageManagementLog();

  const upload = useImageUpload({
    imageLibrary,
    onUploadImage,
    onUploadQcow2,
    onRefresh,
    addImageManagementLog: logManager.addImageManagementLog,
  });

  const iolBuild = useIolBuildManager({
    imageLibrary,
    isBuildJobsMode,
    onRefresh,
    setUploadStatus: upload.setUploadStatus,
  });

  const deviceFilters = useDeviceFilters({
    deviceModels,
    imagesByDevice,
  });

  const imageFilters = useImageFilters({
    imageLibrary,
    deviceModels,
    runnableImageLibrary,
    resolveImageDeviceIds,
    imageVendorsById,
    isBuildJobsMode,
    pendingQcow2Uploads: upload.pendingQcow2Uploads,
  });

  // Group images for display (uses compatible_devices for shared images)
  const { unassignedImages, assignedImagesByDevice } = useMemo(() => {
    const unassigned: ImageLibraryEntry[] = [];
    const byDevice = new Map<string, ImageLibraryEntry[]>();

    imageFilters.filteredImages.forEach((img) => {
      const deviceIds = resolveImageDeviceIds(img);
      if (deviceIds.length === 0) {
        unassigned.push(img);
      } else {
        deviceIds.forEach((devId) => {
          const list = byDevice.get(devId) || [];
          list.push(withDeviceScopedDefault(img, devId));
          byDevice.set(devId, list);
        });
      }
    });

    return { unassignedImages: unassigned, assignedImagesByDevice: byDevice };
  }, [imageFilters.filteredImages, resolveImageDeviceIds, withDeviceScopedDefault]);

  // --- Handlers ---

  const handleUnassignImage = async (imageId: string, deviceId?: string) => {
    try {
      await unassignImage(imageId, deviceId);
      onRefresh();
    } catch (error) {
      console.error('Failed to unassign image:', error);
    }
  };

  const handleSetDefaultImage = async (imageId: string, deviceId: string) => {
    try {
      await assignImageToDevice(imageId, deviceId, true);
      onRefresh();
    } catch (error) {
      console.error('Failed to set default image:', error);
    }
  };

  const handleDeleteImage = async (imageId: string) => {
    try {
      await deleteImage(imageId);
      onRefresh();
    } catch (error) {
      console.error('Failed to delete image:', error);
      addNotification('error', 'Failed to delete image', error instanceof Error ? error.message : undefined);
    }
  };

  // --- Render ---

  if (isBuildJobsMode) {
    return (
      <BuildJobsView
        uploadStatus={upload.uploadStatus}
        iolBuildRows={iolBuild.iolBuildRows}
        hasActiveIolBuilds={iolBuild.hasActiveIolBuilds}
        activeIolBuildCount={iolBuild.activeIolBuildCount}
        currentIolBuildRows={iolBuild.currentIolBuildRows}
        historicalIolBuildRows={iolBuild.historicalIolBuildRows}
        refreshingIolBuilds={iolBuild.refreshingIolBuilds}
        retryingIolImageId={iolBuild.retryingIolImageId}
        ignoringIolImageId={iolBuild.ignoringIolImageId}
        autoRefreshIolBuilds={iolBuild.autoRefreshIolBuilds}
        setAutoRefreshIolBuilds={iolBuild.setAutoRefreshIolBuilds}
        refreshIolBuildStatuses={iolBuild.refreshIolBuildStatuses}
        retryIolBuild={iolBuild.retryIolBuild}
        ignoreIolBuildFailure={iolBuild.ignoreIolBuildFailure}
        openIolDiagnostics={iolBuild.openIolDiagnostics}
        showIolDiagnostics={iolBuild.showIolDiagnostics}
        setShowIolDiagnostics={iolBuild.setShowIolDiagnostics}
        iolDiagnostics={iolBuild.iolDiagnostics}
        iolDiagnosticsLoading={iolBuild.iolDiagnosticsLoading}
        iolDiagnosticsError={iolBuild.iolDiagnosticsError}
      />
    );
  }

  return (
    <div className="h-full bg-transparent flex flex-col overflow-hidden">
      <div className="flex flex-col h-full min-h-0">
        <UploadControls
          uploadStatus={upload.uploadStatus}
          uploadProgress={upload.uploadProgress}
          qcow2Progress={upload.qcow2Progress}
          isQcow2PostProcessing={upload.isQcow2PostProcessing}
          uploadErrorCount={logManager.uploadErrorCount}
          fileInputRef={upload.fileInputRef}
          qcow2InputRef={upload.qcow2InputRef}
          showISOModal={upload.showISOModal}
          setShowISOModal={upload.setShowISOModal}
          qcow2Confirm={upload.qcow2Confirm}
          setQcow2Confirm={upload.setQcow2Confirm}
          openFilePicker={upload.openFilePicker}
          openQcow2Picker={upload.openQcow2Picker}
          uploadImage={upload.uploadImage}
          uploadQcow2={upload.uploadQcow2}
          confirmQcow2Upload={upload.confirmQcow2Upload}
          cancelQcow2Confirm={upload.cancelQcow2Confirm}
          handleIsoLogEvent={upload.handleIsoLogEvent}
          onRefresh={onRefresh}
          onShowUploadLogs={() => logManager.setShowUploadLogsModal(true)}
        />

        {/* Two-panel layout */}
        <div className="flex-1 flex overflow-hidden min-h-0">
          {/* Left panel - Devices (40%) */}
          <DeviceCatalogView
            filteredDevices={deviceFilters.filteredDevices}
            imagesByDevice={imagesByDevice}
            deviceSearch={deviceFilters.deviceSearch}
            setDeviceSearch={deviceFilters.setDeviceSearch}
            deviceSort={deviceFilters.deviceSort}
            setDeviceSort={deviceFilters.setDeviceSort}
            deviceImageStatus={deviceFilters.deviceImageStatus}
            setDeviceImageStatus={deviceFilters.setDeviceImageStatus}
            deviceVendors={deviceFilters.deviceVendors}
            selectedDeviceVendors={deviceFilters.selectedDeviceVendors}
            toggleDeviceVendor={deviceFilters.toggleDeviceVendor}
            hasDeviceFilters={deviceFilters.hasDeviceFilters}
            clearDeviceFilters={deviceFilters.clearDeviceFilters}
            onUnassignImage={handleUnassignImage}
            onSetDefaultImage={handleSetDefaultImage}
          />

          {/* Right panel - Images (60%) */}
          <ImageLibraryView
            runnableImageLibrary={runnableImageLibrary}
            deviceModels={deviceModels}
            staleAgentSummary={staleAgentSummary || null}
            filteredImages={imageFilters.filteredImages}
            unassignedImages={unassignedImages}
            assignedImagesByDevice={assignedImagesByDevice}
            filteredPendingQcow2Uploads={imageFilters.filteredPendingQcow2Uploads}
            imageSearch={imageFilters.imageSearch}
            setImageSearch={imageFilters.setImageSearch}
            selectedImageVendors={imageFilters.selectedImageVendors}
            toggleImageVendor={imageFilters.toggleImageVendor}
            selectedImageKinds={imageFilters.selectedImageKinds}
            toggleImageKind={imageFilters.toggleImageKind}
            imageAssignmentFilter={imageFilters.imageAssignmentFilter}
            setImageAssignmentFilter={imageFilters.setImageAssignmentFilter}
            imageSort={imageFilters.imageSort}
            setImageSort={imageFilters.setImageSort}
            clearImageFilters={imageFilters.clearImageFilters}
            onUnassignImage={handleUnassignImage}
            onSetDefaultImage={handleSetDefaultImage}
            onDeleteImage={handleDeleteImage}
            onRefresh={onRefresh}
            showSyncStatus={showSyncStatus}
          />
        </div>
      </div>

      {/* Drag overlay indicator */}
      {dragState.isDragging && (
        <div className="fixed bottom-4 left-1/2 -translate-x-1/2 px-4 py-2 bg-stone-900 dark:bg-white text-white dark:text-stone-900 rounded-lg shadow-lg text-xs font-bold z-50 animate-in fade-in slide-in-from-bottom-2 duration-200">
          <i className="fa-solid fa-hand-pointer mr-2" />
          Drop on a device to assign
        </div>
      )}

      <UploadLogsModal
        isOpen={logManager.showUploadLogsModal}
        onClose={() => logManager.setShowUploadLogsModal(false)}
        imageManagementLogs={logManager.imageManagementLogs}
        filteredImageManagementLogs={logManager.filteredImageManagementLogs}
        imageLogFilter={logManager.imageLogFilter}
        setImageLogFilter={logManager.setImageLogFilter}
        imageLogSearch={logManager.imageLogSearch}
        setImageLogSearch={logManager.setImageLogSearch}
        imageLogCounts={logManager.imageLogCounts}
        uploadErrorCount={logManager.uploadErrorCount}
        copiedUploadLogId={logManager.copiedUploadLogId}
        clearImageManagementLogs={logManager.clearImageManagementLogs}
        copyUploadLogEntry={logManager.copyUploadLogEntry}
      />
    </div>
  );
};

const DeviceManager: React.FC<DeviceManagerProps> = (props) => {
  return (
    <DragProvider onImageAssigned={props.onRefresh}>
      <DeviceManagerInner {...props} />
    </DragProvider>
  );
};

export default DeviceManager;
