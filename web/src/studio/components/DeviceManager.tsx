import React, { useMemo, useRef, useState } from 'react';
import { API_BASE_URL, apiRequest } from '../../api';
import { DeviceModel, ImageLibraryEntry } from '../types';
import { DragProvider, useDragContext } from '../contexts/DragContext';
import DeviceCard from './DeviceCard';
import ImageCard from './ImageCard';
import ImageFilterBar, { ImageAssignmentFilter } from './ImageFilterBar';
import FilterChip from './FilterChip';

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
  customDevices: { id: string; label: string }[];
  onAddCustomDevice: (device: { id: string; label: string }) => void;
  onRemoveCustomDevice: (deviceId: string) => void;
  onUploadImage: () => void;
  onUploadQcow2: () => void;
  onRefresh: () => void;
}

const DeviceManagerInner: React.FC<DeviceManagerProps> = ({
  deviceModels,
  imageLibrary,
  customDevices,
  onAddCustomDevice,
  onRemoveCustomDevice,
  onUploadImage,
  onUploadQcow2,
  onRefresh,
}) => {
  const { dragState, unassignImage, assignImageToDevice, deleteImage } = useDragContext();
  const [uploadStatus, setUploadStatus] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [qcow2Progress, setQcow2Progress] = useState<number | null>(null);
  const [customDeviceId, setCustomDeviceId] = useState('');
  const [customDeviceLabel, setCustomDeviceLabel] = useState('');
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const qcow2InputRef = useRef<HTMLInputElement | null>(null);

  // Device filters
  const [deviceSearch, setDeviceSearch] = useState('');
  const [selectedDeviceVendors, setSelectedDeviceVendors] = useState<Set<string>>(new Set());
  const [deviceImageStatus, setDeviceImageStatus] = useState<'all' | 'has_image' | 'no_image'>('all');

  // Image filters
  const [imageSearch, setImageSearch] = useState('');
  const [selectedImageVendors, setSelectedImageVendors] = useState<Set<string>>(new Set());
  const [selectedImageKinds, setSelectedImageKinds] = useState<Set<string>>(new Set());
  const [imageAssignmentFilter, setImageAssignmentFilter] = useState<ImageAssignmentFilter>('all');

  const [selectedDeviceId, setSelectedDeviceId] = useState<string | null>(null);

  // Build device to images map
  const imagesByDevice = useMemo(() => {
    const map = new Map<string, ImageLibraryEntry[]>();
    imageLibrary.forEach((img) => {
      if (!img.device_id) return;
      const list = map.get(img.device_id) || [];
      list.push(img);
      map.set(img.device_id, list);
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

  // Filter devices
  const filteredDevices = useMemo(() => {
    return deviceModels.filter((device) => {
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
  }, [deviceModels, deviceSearch, selectedDeviceVendors, deviceImageStatus, imagesByDevice]);

  // Filter images
  const filteredImages = useMemo(() => {
    return imageLibrary.filter((img) => {
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
  }, [imageLibrary, imageSearch, selectedImageVendors, selectedImageKinds, imageAssignmentFilter]);

  // Group images for display
  const { unassignedImages, assignedImagesByDevice } = useMemo(() => {
    const unassigned: ImageLibraryEntry[] = [];
    const byDevice = new Map<string, ImageLibraryEntry[]>();

    filteredImages.forEach((img) => {
      if (!img.device_id) {
        unassigned.push(img);
      } else {
        const list = byDevice.get(img.device_id) || [];
        list.push(img);
        byDevice.set(img.device_id, list);
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
          reject(new Error(request.responseText || 'Upload failed'));
        }
      };
      request.send(formData);
    });
  }

  async function uploadImage(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    let processingNoticeShown = false;
    try {
      setUploadStatus(`Uploading ${file.name}...`);
      setUploadProgress(0);
      const data = (await uploadWithProgress(`${API_BASE_URL}/images/load`, file, (value) => {
        setUploadProgress(value);
        if (value !== null && value >= 100 && !processingNoticeShown) {
          processingNoticeShown = true;
          setUploadStatus('Upload complete. Importing image (this may take a few minutes for large files)...');
        }
      })) as { output?: string; images?: string[] };
      if (data.images && data.images.length === 0) {
        setUploadStatus('Upload finished, but no images were detected.');
      } else {
        setUploadStatus(data.output || 'Image loaded.');
      }
      onUploadImage();
      onRefresh();
    } catch (error) {
      setUploadStatus(error instanceof Error ? error.message : 'Upload failed');
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
    setSelectedDeviceVendors(new Set());
    setDeviceImageStatus('all');
  };

  const clearImageFilters = () => {
    setImageSearch('');
    setSelectedImageVendors(new Set());
    setSelectedImageKinds(new Set());
    setImageAssignmentFilter('all');
  };

  const hasDeviceFilters =
    deviceSearch.length > 0 || selectedDeviceVendors.size > 0 || deviceImageStatus !== 'all';

  const selectedDevice = selectedDeviceId
    ? deviceModels.find((d) => d.id === selectedDeviceId)
    : null;

  return (
    <div className="flex-1 bg-stone-50 dark:bg-stone-950 flex flex-col overflow-hidden animate-in fade-in duration-300">
      <div className="flex flex-col h-full">
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
                onClick={onRefresh}
                className="px-3 py-2 bg-stone-200 dark:bg-stone-800 hover:bg-stone-300 dark:hover:bg-stone-700 text-stone-700 dark:text-white rounded-lg text-xs font-bold transition-all"
              >
                <i className="fa-solid fa-rotate"></i>
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
        <div className="flex-1 flex overflow-hidden">
          {/* Left panel - Devices (40%) */}
          <div className="w-2/5 border-r border-stone-200 dark:border-stone-800 flex flex-col overflow-hidden">
            {/* Device filters */}
            <div className="p-4 border-b border-stone-200 dark:border-stone-800 bg-stone-100/50 dark:bg-stone-900/30 space-y-3">
              {/* Search */}
              <div className="relative">
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
                {deviceVendors.slice(0, 4).map((vendor) => (
                  <FilterChip
                    key={vendor}
                    label={vendor}
                    isActive={selectedDeviceVendors.has(vendor)}
                    onClick={() => {
                      const next = new Set(selectedDeviceVendors);
                      if (next.has(vendor)) {
                        next.delete(vendor);
                      } else {
                        next.add(vendor);
                      }
                      setSelectedDeviceVendors(next);
                    }}
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

            {/* Custom device form */}
            <div className="p-4 border-b border-stone-200 dark:border-stone-800 bg-white/50 dark:bg-stone-900/30">
              <div className="text-[10px] font-bold text-stone-500 uppercase tracking-widest mb-2">
                Custom Device
              </div>
              <div className="flex gap-2">
                <input
                  className="flex-1 bg-stone-100 dark:bg-stone-950 border border-stone-300 dark:border-stone-700 rounded px-3 py-2 text-xs text-stone-900 dark:text-stone-200"
                  placeholder="device-id"
                  value={customDeviceId}
                  onChange={(e) => setCustomDeviceId(e.target.value)}
                />
                <input
                  className="flex-1 bg-stone-100 dark:bg-stone-950 border border-stone-300 dark:border-stone-700 rounded px-3 py-2 text-xs text-stone-900 dark:text-stone-200"
                  placeholder="label"
                  value={customDeviceLabel}
                  onChange={(e) => setCustomDeviceLabel(e.target.value)}
                />
                <button
                  onClick={() => {
                    if (!customDeviceId.trim()) return;
                    onAddCustomDevice({
                      id: customDeviceId.trim(),
                      label: customDeviceLabel.trim() || customDeviceId.trim(),
                    });
                    setCustomDeviceId('');
                    setCustomDeviceLabel('');
                  }}
                  className="px-3 bg-sage-600 hover:bg-sage-500 text-white text-xs font-bold rounded"
                >
                  Add
                </button>
              </div>
              {customDevices.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-2">
                  {customDevices.map((device) => (
                    <span
                      key={device.id}
                      className="inline-flex items-center gap-1 px-2 py-1 bg-stone-100 dark:bg-stone-800 rounded text-[10px] text-stone-600 dark:text-stone-400"
                    >
                      <span className="font-mono">{device.id}</span>
                      <button
                        onClick={() => onRemoveCustomDevice(device.id)}
                        className="text-red-500 hover:text-red-600"
                      >
                        <i className="fa-solid fa-xmark" />
                      </button>
                    </span>
                  ))}
                </div>
              )}
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
          <div className="flex-1 flex flex-col overflow-hidden">
            {/* Image filter bar */}
            <ImageFilterBar
              images={imageLibrary}
              devices={deviceModels}
              searchQuery={imageSearch}
              onSearchChange={setImageSearch}
              selectedVendors={selectedImageVendors}
              onVendorToggle={(vendor) => {
                const next = new Set(selectedImageVendors);
                if (next.has(vendor)) {
                  next.delete(vendor);
                } else {
                  next.add(vendor);
                }
                setSelectedImageVendors(next);
              }}
              selectedKinds={selectedImageKinds}
              onKindToggle={(kind) => {
                const next = new Set(selectedImageKinds);
                if (next.has(kind)) {
                  next.delete(kind);
                } else {
                  next.add(kind);
                }
                setSelectedImageKinds(next);
              }}
              assignmentFilter={imageAssignmentFilter}
              onAssignmentFilterChange={setImageAssignmentFilter}
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
                        onUnassign={() => handleUnassignImage(img.id)}
                        onDelete={() => handleDeleteImage(img.id)}
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
                          onUnassign={() => handleUnassignImage(img.id)}
                          onSetDefault={() => handleSetDefaultImage(img.id, deviceId)}
                          onDelete={() => handleDeleteImage(img.id)}
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
