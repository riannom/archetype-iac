import React, { useMemo, useRef, useState } from 'react';
import { API_BASE_URL, apiRequest } from '../../api';
import { DeviceModel, ImageLibraryEntry } from '../types';
import { DragProvider, useDragContext } from '../contexts/DragContext';
import DeviceCard from './DeviceCard';
import ImageCard from './ImageCard';
import ImageFilterBar, { ImageAssignmentFilter, ImageSortOption } from './ImageFilterBar';
import FilterChip from './FilterChip';
import ISOImportModal from '../../components/ISOImportModal';
import { usePersistedState, usePersistedSet } from '../hooks/usePersistedState';
import { getImageDeviceIds } from '../../utils/deviceModels';

interface ImageCatalogEntry {
  clab?: string;
  libvirt?: string;
  virtualbox?: string;
  caveats?: string[];
}

interface DeviceManagerProps {
  deviceModels: DeviceModel[];
  imageCatalog: Record<string, ImageCatalogEntry>;
  imageLibrary: ImageLibraryEntry[];
  onUploadImage: () => void;
  onUploadQcow2: () => void;
  onRefresh: () => void;
  showSyncStatus?: boolean;
}

const DeviceManagerInner: React.FC<DeviceManagerProps> = ({
  deviceModels,
  imageLibrary,
  onUploadImage,
  onUploadQcow2,
  onRefresh,
  showSyncStatus = true,
}) => {
  const { dragState, unassignImage, assignImageToDevice, deleteImage } = useDragContext();
  const [uploadStatus, setUploadStatus] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [qcow2Progress, setQcow2Progress] = useState<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const qcow2InputRef = useRef<HTMLInputElement | null>(null);
  const [showISOModal, setShowISOModal] = useState(false);

  // Device filters (persisted to localStorage)
  const [deviceSearch, setDeviceSearch] = useState('');
  const [selectedDeviceVendors, toggleDeviceVendor, clearDeviceVendors] = usePersistedSet('archetype:filters:device:vendors');
  const [deviceImageStatus, setDeviceImageStatus] = usePersistedState<'all' | 'has_image' | 'no_image'>('archetype:filters:device:imageStatus', 'all');
  const [deviceSort, setDeviceSort] = usePersistedState<'name' | 'vendor' | 'type'>('archetype:filters:device:sort', 'vendor');

  // Image filters (persisted to localStorage)
  const [imageSearch, setImageSearch] = useState('');
  const [selectedImageVendors, toggleImageVendor, clearImageVendors] = usePersistedSet('archetype:filters:image:vendors');
  const [selectedImageKinds, toggleImageKind, clearImageKinds] = usePersistedSet('archetype:filters:image:kinds');
  const [imageAssignmentFilter, setImageAssignmentFilter] = usePersistedState<ImageAssignmentFilter>('archetype:filters:image:assignment', 'all');
  const [imageSort, setImageSort] = usePersistedState<ImageSortOption>('archetype:filters:image:sort', 'vendor');

  const [selectedDeviceId, setSelectedDeviceId] = useState<string | null>(null);

  // Build device to images map (uses compatible_devices for shared images)
  const imagesByDevice = useMemo(() => {
    const map = new Map<string, ImageLibraryEntry[]>();
    imageLibrary.forEach((img) => {
      getImageDeviceIds(img).forEach((devId) => {
        const list = map.get(devId) || [];
        list.push(img);
        map.set(devId, list);
      });
    });
    return map;
  }, [imageLibrary]);

  // Get unique device vendors
  const deviceVendors = useMemo(() => {
    const vendors = new Set<string>();
    deviceModels.forEach((d) => {
      if (d.vendor) vendors.add(d.vendor);
    });
    return Array.from(vendors).sort();
  }, [deviceModels]);

  // Filter and sort devices
  const filteredDevices = useMemo(() => {
    const filtered = deviceModels.filter((device) => {
      // Search filter
      if (deviceSearch) {
        const query = deviceSearch.toLowerCase();
        const matchesName = device.name.toLowerCase().includes(query);
        const matchesVendor = device.vendor?.toLowerCase().includes(query);
        const matchesId = device.id.toLowerCase().includes(query);
        const matchesTags = device.tags?.some((tag) => tag.toLowerCase().includes(query));
        if (!matchesName && !matchesVendor && !matchesId && !matchesTags) {
          return false;
        }
      }

      // Vendor filter
      if (selectedDeviceVendors.size > 0 && !selectedDeviceVendors.has(device.vendor)) {
        return false;
      }

      // Image status filter
      const hasImages = (imagesByDevice.get(device.id)?.length || 0) > 0;
      if (deviceImageStatus === 'has_image' && !hasImages) return false;
      if (deviceImageStatus === 'no_image' && hasImages) return false;

      return true;
    });

    // Sort devices
    return filtered.sort((a, b) => {
      switch (deviceSort) {
        case 'name':
          return a.name.localeCompare(b.name);
        case 'vendor':
          return (a.vendor || '').localeCompare(b.vendor || '') || a.name.localeCompare(b.name);
        case 'type':
          return (a.type || '').localeCompare(b.type || '') || a.name.localeCompare(b.name);
        default:
          return 0;
      }
    });
  }, [deviceModels, deviceSearch, selectedDeviceVendors, deviceImageStatus, imagesByDevice, deviceSort]);

  // Filter and sort images
  const filteredImages = useMemo(() => {
    const filtered = imageLibrary.filter((img) => {
      // Search filter
      if (imageSearch) {
        const query = imageSearch.toLowerCase();
        const matchesFilename = img.filename?.toLowerCase().includes(query);
        const matchesRef = img.reference?.toLowerCase().includes(query);
        const matchesVersion = img.version?.toLowerCase().includes(query);
        const matchesVendor = img.vendor?.toLowerCase().includes(query);
        if (!matchesFilename && !matchesRef && !matchesVersion && !matchesVendor) {
          return false;
        }
      }

      // Vendor filter
      if (selectedImageVendors.size > 0 && (!img.vendor || !selectedImageVendors.has(img.vendor))) {
        return false;
      }

      // Kind filter
      if (selectedImageKinds.size > 0 && !selectedImageKinds.has(img.kind)) {
        return false;
      }

      // Assignment filter
      if (imageAssignmentFilter === 'unassigned' && img.device_id) return false;
      if (imageAssignmentFilter === 'assigned' && !img.device_id) return false;

      return true;
    });

    // Sort images
    return filtered.sort((a, b) => {
      switch (imageSort) {
        case 'name':
          return (a.reference || a.filename || '').localeCompare(b.reference || b.filename || '');
        case 'vendor':
          return (a.vendor || '').localeCompare(b.vendor || '') || (a.reference || '').localeCompare(b.reference || '');
        case 'kind':
          return a.kind.localeCompare(b.kind) || (a.reference || '').localeCompare(b.reference || '');
        case 'date':
          return (b.uploaded_at || '').localeCompare(a.uploaded_at || '');
        default:
          return 0;
      }
    });
  }, [imageLibrary, imageSearch, selectedImageVendors, selectedImageKinds, imageAssignmentFilter, imageSort]);

  // Group images for display (uses compatible_devices for shared images)
  const { unassignedImages, assignedImagesByDevice } = useMemo(() => {
    const unassigned: ImageLibraryEntry[] = [];
    const byDevice = new Map<string, ImageLibraryEntry[]>();
    const seen = new Set<string>(); // avoid duplicating unassigned

    filteredImages.forEach((img) => {
      const deviceIds = getImageDeviceIds(img);
      if (deviceIds.length === 0) {
        unassigned.push(img);
      } else {
        deviceIds.forEach((devId) => {
          const list = byDevice.get(devId) || [];
          list.push(img);
          byDevice.set(devId, list);
        });
      }
    });

    return { unassignedImages: unassigned, assignedImagesByDevice: byDevice };
  }, [filteredImages]);

  function openFilePicker() {
    fileInputRef.current?.click();
  }

  function openQcow2Picker() {
    qcow2InputRef.current?.click();
  }

  /**
   * Parse error message from response, handling HTML error pages gracefully.
   */
  function parseErrorMessage(text: string): string {
    // Check if it's an HTML error page (e.g., nginx 504 timeout)
    if (text.includes('<html>') || text.includes('<!DOCTYPE')) {
      // Try to extract the title
      const titleMatch = text.match(/<title>([^<]+)<\/title>/i);
      if (titleMatch) {
        return titleMatch[1].trim();
      }
      // Try to extract h1 content
      const h1Match = text.match(/<h1>([^<]+)<\/h1>/i);
      if (h1Match) {
        return h1Match[1].trim();
      }
      return 'Server error (check if the operation completed)';
    }
    // Try to parse as JSON error
    try {
      const json = JSON.parse(text);
      return json.detail || json.message || json.error || text;
    } catch {
      return text || 'Upload failed';
    }
  }

  /**
   * Upload file with background processing and progress polling.
   * This provides real-time feedback during the server-side processing phase.
   */
  async function uploadImageWithPolling(
    file: File,
    onProgress: (percent: number, message: string) => void
  ): Promise<{ output?: string; images?: string[] }> {
    const formData = new FormData();
    formData.append('file', file);
    const token = localStorage.getItem('token');
    const headers: Record<string, string> = {};
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    onProgress(0, 'Uploading file...');

    // Start background upload
    const response = await fetch(`${API_BASE_URL}/images/load?background=true`, {
      method: 'POST',
      headers,
      body: formData,
    });

    if (!response.ok) {
      const text = await response.text();
      throw new Error(parseErrorMessage(text));
    }

    const { upload_id } = await response.json();
    if (!upload_id) {
      throw new Error('No upload ID returned');
    }

    onProgress(5, 'Upload started, processing...');

    // Poll for progress
    let lastPercent = 5;
    while (true) {
      await new Promise(resolve => setTimeout(resolve, 500)); // Poll every 500ms

      const progressResponse = await fetch(`${API_BASE_URL}/images/load/${upload_id}/progress`, {
        headers,
      });

      if (!progressResponse.ok) {
        if (progressResponse.status === 404) {
          throw new Error('Upload not found - it may have completed or expired');
        }
        continue; // Retry on other errors
      }

      const progress = await progressResponse.json();

      if (progress.percent !== lastPercent || progress.message) {
        lastPercent = progress.percent;
        onProgress(progress.percent, progress.message || 'Processing...');
      }

      if (progress.error) {
        throw new Error(progress.message || 'Import failed');
      }

      if (progress.complete) {
        return { output: progress.message, images: progress.images };
      }
    }
  }

  /**
   * Fallback upload without streaming (for older behavior).
   */
  function uploadWithProgress(
    url: string,
    file: File,
    onProgress: (value: number | null) => void
  ): Promise<any> {
    return new Promise((resolve, reject) => {
      const formData = new FormData();
      formData.append('file', file);
      const token = localStorage.getItem('token');
      const request = new XMLHttpRequest();
      request.open('POST', url);
      if (token) {
        request.setRequestHeader('Authorization', `Bearer ${token}`);
      }
      const timeout = window.setTimeout(() => {
        request.abort();
        reject(
          new Error('Upload timed out while processing the image. Large images may take several minutes.')
        );
      }, 10 * 60 * 1000);
      request.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          onProgress(Math.round((event.loaded / event.total) * 100));
        }
      };
      request.onerror = () => {
        window.clearTimeout(timeout);
        reject(new Error('Upload failed'));
      };
      request.onload = () => {
        window.clearTimeout(timeout);
        if (request.status >= 200 && request.status < 300) {
          try {
            resolve(JSON.parse(request.responseText));
          } catch {
            resolve({});
          }
        } else {
          reject(new Error(parseErrorMessage(request.responseText)));
        }
      };
      request.send(formData);
    });
  }

  async function uploadImage(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;

    try {
      setUploadStatus(`Uploading ${file.name}...`);
      setUploadProgress(0);

      // Use polling-based upload for reliable progress tracking
      const data = await uploadImageWithPolling(file, (percent, message) => {
        setUploadProgress(percent);
        setUploadStatus(message);
      });

      if (data.images && data.images.length === 0) {
        setUploadStatus('Upload finished, but no images were detected.');
      } else {
        setUploadStatus(data.output || 'Image loaded.');
      }
      onUploadImage();
      onRefresh();
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Upload failed';
      setUploadStatus(errorMessage);
    } finally {
      event.target.value = '';
      setUploadProgress(null);
    }
  }

  async function uploadQcow2(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      setUploadStatus(`Uploading ${file.name}...`);
      setQcow2Progress(0);
      await uploadWithProgress(`${API_BASE_URL}/images/qcow2`, file, setQcow2Progress);
      setUploadStatus('QCOW2 uploaded.');
      onUploadQcow2();
      onRefresh();
    } catch (error) {
      setUploadStatus(error instanceof Error ? error.message : 'Upload failed');
    } finally {
      event.target.value = '';
      setQcow2Progress(null);
    }
  }

  const handleUnassignImage = async (imageId: string) => {
    try {
      await unassignImage(imageId);
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
      alert(error instanceof Error ? error.message : 'Failed to delete image');
    }
  };

  const clearDeviceFilters = () => {
    setDeviceSearch('');
    clearDeviceVendors();
    setDeviceImageStatus('all');
  };

  const clearImageFilters = () => {
    setImageSearch('');
    clearImageVendors();
    clearImageKinds();
    setImageAssignmentFilter('all');
  };

  const hasDeviceFilters =
    deviceSearch.length > 0 || selectedDeviceVendors.size > 0 || deviceImageStatus !== 'all';

  const selectedDevice = selectedDeviceId
    ? deviceModels.find((d) => d.id === selectedDeviceId)
    : null;

  return (
    <div className="h-full bg-stone-50 dark:bg-stone-950 flex flex-col overflow-hidden">
      <div className="flex flex-col h-full min-h-0">
        {/* Header */}
        <header className="px-6 py-4 border-b border-stone-200 dark:border-stone-800 bg-white/50 dark:bg-stone-900/50 backdrop-blur-sm">
          <div className="flex flex-wrap justify-between items-end gap-4">
            <div>
              <h1 className="text-2xl font-black text-stone-900 dark:text-white tracking-tight">
                Image Management
              </h1>
              <p className="text-stone-500 dark:text-stone-400 text-xs mt-1">
                Drag images onto devices to assign them. Drop zones appear when dragging.
              </p>
            </div>
            <div className="flex gap-3">
              <button
                onClick={openFilePicker}
                className="px-4 py-2 bg-sage-600 hover:bg-sage-500 text-white rounded-lg text-xs font-bold transition-all shadow-sm"
              >
                <i className="fa-solid fa-cloud-arrow-up mr-2"></i> Upload Docker
              </button>
              <button
                onClick={openQcow2Picker}
                className="px-4 py-2 bg-stone-200 dark:bg-stone-800 hover:bg-stone-300 dark:hover:bg-stone-700 text-stone-700 dark:text-white rounded-lg border border-stone-300 dark:border-stone-700 text-xs font-bold transition-all"
              >
                <i className="fa-solid fa-hard-drive mr-2"></i> Upload QCOW2
              </button>
              <button
                onClick={() => setShowISOModal(true)}
                className="px-4 py-2 bg-purple-600 hover:bg-purple-500 text-white rounded-lg text-xs font-bold transition-all shadow-sm"
              >
                <i className="fa-solid fa-compact-disc mr-2"></i> Import ISO
              </button>
              <input
                ref={fileInputRef}
                className="hidden"
                type="file"
                accept=".tar,.tgz,.tar.gz,.tar.xz,.txz"
                onChange={uploadImage}
              />
              <input
                ref={qcow2InputRef}
                className="hidden"
                type="file"
                accept=".qcow2,.qcow"
                onChange={uploadQcow2}
              />
            </div>
          </div>

          {/* Upload status */}
          {uploadStatus && (
            <p className="text-xs text-stone-500 dark:text-stone-400 mt-3">{uploadStatus}</p>
          )}
          {uploadProgress !== null && (
            <div className="mt-3">
              <div className="text-[10px] font-bold text-stone-500 uppercase mb-1">
                Image upload {uploadProgress}%
              </div>
              <div className="h-1.5 bg-stone-200 dark:bg-stone-800 rounded-full overflow-hidden">
                <div className="h-full bg-sage-500 transition-all" style={{ width: `${uploadProgress}%` }} />
              </div>
            </div>
          )}
          {qcow2Progress !== null && (
            <div className="mt-3">
              <div className="text-[10px] font-bold text-stone-500 uppercase mb-1">
                QCOW2 upload {qcow2Progress}%
              </div>
              <div className="h-1.5 bg-stone-200 dark:bg-stone-800 rounded-full overflow-hidden">
                <div className="h-full bg-emerald-500 transition-all" style={{ width: `${qcow2Progress}%` }} />
              </div>
            </div>
          )}
        </header>

        {/* Two-panel layout */}
        <div className="flex-1 flex overflow-hidden min-h-0">
          {/* Left panel - Devices (40%) */}
          <div className="w-2/5 border-r border-stone-200 dark:border-stone-800 flex flex-col overflow-hidden min-h-0">
            {/* Device filters */}
            <div className="p-4 border-b border-stone-200 dark:border-stone-800 bg-stone-100/50 dark:bg-stone-900/30 space-y-3">
              {/* Search and sort row */}
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <i className="fa-solid fa-magnifying-glass absolute left-3 top-1/2 -translate-y-1/2 text-stone-400 text-xs" />
                  <input
                    type="text"
                    placeholder="Search devices..."
                    value={deviceSearch}
                    onChange={(e) => setDeviceSearch(e.target.value)}
                    className="w-full pl-9 pr-8 py-2 bg-white dark:bg-stone-800 border border-stone-200 dark:border-stone-700 rounded-lg text-xs text-stone-900 dark:text-stone-100 placeholder:text-stone-400 focus:outline-none focus:ring-2 focus:ring-sage-500/50"
                  />
                  {deviceSearch && (
                    <button
                      onClick={() => setDeviceSearch('')}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-stone-400 hover:text-stone-600"
                    >
                      <i className="fa-solid fa-xmark text-xs" />
                    </button>
                  )}
                </div>
                <select
                  value={deviceSort}
                  onChange={(e) => setDeviceSort(e.target.value as 'name' | 'vendor' | 'type')}
                  className="px-3 py-2 bg-white dark:bg-stone-800 border border-stone-200 dark:border-stone-700 rounded-lg text-xs text-stone-700 dark:text-stone-300 focus:outline-none focus:ring-2 focus:ring-sage-500/50"
                >
                  <option value="vendor">Sort: Vendor</option>
                  <option value="name">Sort: Name</option>
                  <option value="type">Sort: Type</option>
                </select>
              </div>

              {/* Filter chips */}
              <div className="flex flex-wrap gap-1.5">
                <FilterChip
                  label="Has Image"
                  isActive={deviceImageStatus === 'has_image'}
                  onClick={() =>
                    setDeviceImageStatus(deviceImageStatus === 'has_image' ? 'all' : 'has_image')
                  }
                  variant="status"
                  statusColor="green"
                />
                <FilterChip
                  label="No Image"
                  isActive={deviceImageStatus === 'no_image'}
                  onClick={() =>
                    setDeviceImageStatus(deviceImageStatus === 'no_image' ? 'all' : 'no_image')
                  }
                  variant="status"
                  statusColor="amber"
                />
                {deviceVendors.map((vendor) => (
                  <FilterChip
                    key={vendor}
                    label={vendor}
                    isActive={selectedDeviceVendors.has(vendor)}
                    onClick={() => toggleDeviceVendor(vendor)}
                  />
                ))}
                {hasDeviceFilters && (
                  <button
                    onClick={clearDeviceFilters}
                    className="text-[10px] text-red-500 hover:text-red-600 font-bold uppercase"
                  >
                    Clear
                  </button>
                )}
              </div>
            </div>

            {/* Device list */}
            <div className="flex-1 overflow-y-auto p-4 space-y-3 custom-scrollbar">
              {filteredDevices.map((device) => (
                <DeviceCard
                  key={device.id}
                  device={device}
                  assignedImages={imagesByDevice.get(device.id) || []}
                  isSelected={selectedDeviceId === device.id}
                  onSelect={() => setSelectedDeviceId(device.id)}
                  onUnassignImage={handleUnassignImage}
                  onSetDefaultImage={(imageId) => handleSetDefaultImage(imageId, device.id)}
                />
              ))}
              {filteredDevices.length === 0 && (
                <div className="text-center py-8">
                  <i className="fa-solid fa-search text-2xl text-stone-300 dark:text-stone-700 mb-2" />
                  <p className="text-xs text-stone-500">No devices match your filters</p>
                </div>
              )}
            </div>
          </div>

          {/* Right panel - Images (60%) */}
          <div className="flex-1 flex flex-col overflow-hidden min-h-0">
            {/* Image filter bar */}
            <ImageFilterBar
              images={imageLibrary}
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
              {/* Unassigned images section */}
              {unassignedImages.length > 0 && (
                <div className="mb-6">
                  <div className="flex items-center gap-2 mb-3">
                    <span className="w-2 h-2 rounded-full bg-amber-500" />
                    <h3 className="text-xs font-bold text-stone-500 dark:text-stone-400 uppercase tracking-widest">
                      Unassigned Images
                    </h3>
                    <span className="text-[10px] text-stone-400">({unassignedImages.length})</span>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    {unassignedImages.map((img) => (
                      <ImageCard
                        key={img.id}
                        image={img}
                        device={img.device_id ? deviceModels.find((d) => d.id === img.device_id) : undefined}
                        onUnassign={() => handleUnassignImage(img.id)}
                        onDelete={() => handleDeleteImage(img.id)}
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
                          onUnassign={() => handleUnassignImage(img.id)}
                          onSetDefault={() => handleSetDefaultImage(img.id, deviceId)}
                          onDelete={() => handleDeleteImage(img.id)}
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
        </div>
      </div>

      {/* Drag overlay indicator */}
      {dragState.isDragging && (
        <div className="fixed bottom-4 left-1/2 -translate-x-1/2 px-4 py-2 bg-stone-900 dark:bg-white text-white dark:text-stone-900 rounded-lg shadow-lg text-xs font-bold z-50 animate-in fade-in slide-in-from-bottom-2 duration-200">
          <i className="fa-solid fa-hand-pointer mr-2" />
          Drop on a device to assign
        </div>
      )}

      {/* ISO Import Modal */}
      <ISOImportModal
        isOpen={showISOModal}
        onClose={() => setShowISOModal(false)}
        onImportComplete={() => {
          onRefresh();
          setShowISOModal(false);
        }}
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
