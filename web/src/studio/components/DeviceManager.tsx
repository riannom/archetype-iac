import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { API_BASE_URL, apiRequest } from '../../api';
import { DeviceModel, ImageLibraryEntry } from '../types';
import { DragProvider, useDragContext } from '../contexts/DragContext';
import DeviceCard from './DeviceCard';
import ImageCard from './ImageCard';
import ImageFilterBar, { ImageAssignmentFilter, ImageSortOption } from './ImageFilterBar';
import FilterChip from './FilterChip';
import ISOImportModal from '../../components/ISOImportModal';
import { Modal } from '../../components/ui/Modal';
import { usePersistedState, usePersistedSet } from '../hooks/usePersistedState';
import { usePolling } from '../hooks/usePolling';
import { getImageDeviceIds } from '../../utils/deviceModels';

interface DeviceManagerProps {
  deviceModels: DeviceModel[];
  imageLibrary: ImageLibraryEntry[];
  onUploadImage: () => void;
  onUploadQcow2: () => void;
  onRefresh: () => void;
  showSyncStatus?: boolean;
  mode?: 'images' | 'build-jobs';
}

interface IolBuildStatusResponse {
  built?: boolean;
  status?: string;
  build_status?: string;
  rq_status?: string | null;
  build_error?: string | null;
  build_job_id?: string | null;
  build_ignored_at?: string | null;
  build_ignored_by?: string | null;
  docker_reference?: string | null;
  docker_image_id?: string | null;
}

interface IolBuildDiagnosticsResponse extends IolBuildStatusResponse {
  image_id: string;
  filename?: string | null;
  reference?: string | null;
  queue_job?: {
    id?: string | null;
    status?: string | null;
    created_at?: string | null;
    enqueued_at?: string | null;
    started_at?: string | null;
    ended_at?: string | null;
    last_heartbeat?: string | null;
    result?: unknown;
    error_log?: string | null;
  } | null;
  recommended_action?: string | null;
}

function normalizeBuildStatus(raw?: string | null): 'queued' | 'building' | 'complete' | 'failed' | 'ignored' | 'not_started' {
  const status = (raw || '').toLowerCase();
  if (status === 'queued') return 'queued';
  if (status === 'building') return 'building';
  if (status === 'complete') return 'complete';
  if (status === 'failed') return 'failed';
  if (status === 'ignored') return 'ignored';
  return 'not_started';
}

function formatBuildTimestamp(value?: string | null): string {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function normalizeBuildStatusError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error || '');
  if (!message) return 'Build status temporarily unavailable.';

  if (/<html[\s>]/i.test(message)) {
    if (/502/i.test(message)) return 'Build status temporarily unavailable (502 Bad Gateway).';
    if (/503/i.test(message)) return 'Build status temporarily unavailable (503 Service Unavailable).';
    if (/504/i.test(message)) return 'Build status request timed out (504 Gateway Timeout).';
    return 'Build status temporarily unavailable.';
  }

  return message;
}

const DeviceManagerInner: React.FC<DeviceManagerProps> = ({
  deviceModels,
  imageLibrary,
  onUploadImage,
  onUploadQcow2,
  onRefresh,
  showSyncStatus = true,
  mode = 'images',
}) => {
  const { dragState, unassignImage, assignImageToDevice, deleteImage } = useDragContext();
  const [uploadStatus, setUploadStatus] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [qcow2Progress, setQcow2Progress] = useState<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const qcow2InputRef = useRef<HTMLInputElement | null>(null);
  const completedIolBuildsRef = useRef<Set<string>>(new Set());
  const [showISOModal, setShowISOModal] = useState(false);
  const [iolBuildStatuses, setIolBuildStatuses] = useState<Record<string, IolBuildStatusResponse>>({});
  const [refreshingIolBuilds, setRefreshingIolBuilds] = useState(false);
  const [retryingIolImageId, setRetryingIolImageId] = useState<string | null>(null);
  const [ignoringIolImageId, setIgnoringIolImageId] = useState<string | null>(null);
  const [showIolDiagnostics, setShowIolDiagnostics] = useState(false);
  const [iolDiagnostics, setIolDiagnostics] = useState<IolBuildDiagnosticsResponse | null>(null);
  const [iolDiagnosticsLoading, setIolDiagnosticsLoading] = useState(false);
  const [iolDiagnosticsError, setIolDiagnosticsError] = useState<string | null>(null);
  const [autoRefreshIolBuilds, setAutoRefreshIolBuilds] = usePersistedState<boolean>(
    'archetype:iol-build:auto-refresh',
    true
  );

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
  const isBuildJobsMode = mode === 'build-jobs';

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

  const iolSourceImages = useMemo(
    () => imageLibrary.filter((img) => (img.kind || '').toLowerCase() === 'iol'),
    [imageLibrary]
  );

  const builtDockerBySource = useMemo(() => {
    const map = new Map<string, ImageLibraryEntry>();
    imageLibrary.forEach((img) => {
      if ((img.kind || '').toLowerCase() !== 'docker') return;
      if (!img.built_from) return;
      map.set(img.built_from, img);
    });
    return map;
  }, [imageLibrary]);

  useEffect(() => {
    iolSourceImages.forEach((img) => {
      if (builtDockerBySource.has(img.id)) {
        completedIolBuildsRef.current.add(img.id);
      }
    });
  }, [iolSourceImages, builtDockerBySource]);

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

  const refreshIolBuildStatuses = useCallback(async () => {
    if (iolSourceImages.length === 0) {
      setIolBuildStatuses({});
      return;
    }
    setRefreshingIolBuilds(true);
    try {
      const entries = await Promise.all(
        iolSourceImages.map(async (img) => {
          try {
            const data = await apiRequest<IolBuildStatusResponse>(
              `/images/library/${encodeURIComponent(img.id)}/build-status`
            );
            return [img.id, data, null] as const;
          } catch (error) {
            return [img.id, null, normalizeBuildStatusError(error)] as const;
          }
        })
      );
      setIolBuildStatuses((prev) => {
        const next: Record<string, IolBuildStatusResponse> = {};
        const validIds = new Set(iolSourceImages.map((img) => img.id));

        Object.entries(prev).forEach(([id, value]) => {
          if (validIds.has(id)) next[id] = value;
        });

        entries.forEach(([imageId, data, fetchError]) => {
          if (data) {
            next[imageId] = data;
            return;
          }
          if (fetchError) {
            next[imageId] = {
              ...(next[imageId] || {}),
              build_error: fetchError,
            };
          }
        });

        return next;
      });

      const newlyCompletedIds = entries
        .filter(([, status]) => !!status && normalizeBuildStatus(status.status || status.build_status) === 'complete')
        .map(([imageId]) => imageId)
        .filter((imageId) => !completedIolBuildsRef.current.has(imageId));

      if (newlyCompletedIds.length > 0) {
        newlyCompletedIds.forEach((imageId) => completedIolBuildsRef.current.add(imageId));
        onRefresh();
      }
    } finally {
      setRefreshingIolBuilds(false);
    }
  }, [iolSourceImages, onRefresh]);

  const retryIolBuild = useCallback(async (imageId: string, forceRebuild: boolean = false) => {
    setRetryingIolImageId(imageId);
    try {
      await apiRequest<{ build_status: string; build_job_id: string }>(
        `/images/library/${encodeURIComponent(imageId)}/retry-build?force_rebuild=${forceRebuild}`,
        { method: 'POST' }
      );
      setUploadStatus(forceRebuild ? 'Forced IOL rebuild queued.' : 'IOL build retry queued.');
      await refreshIolBuildStatuses();
      onRefresh();
    } catch (error) {
      setUploadStatus(error instanceof Error ? error.message : 'Failed to retry IOL build');
    } finally {
      setRetryingIolImageId(null);
    }
  }, [onRefresh, refreshIolBuildStatuses]);

  const ignoreIolBuildFailure = useCallback(async (imageId: string) => {
    setIgnoringIolImageId(imageId);
    try {
      await apiRequest<{ build_status: string }>(
        `/images/library/${encodeURIComponent(imageId)}/ignore-build-failure`,
        { method: 'POST' }
      );
      setUploadStatus('IOL build failure ignored.');
      await refreshIolBuildStatuses();
      onRefresh();
    } catch (error) {
      setUploadStatus(error instanceof Error ? error.message : 'Failed to ignore IOL build failure');
    } finally {
      setIgnoringIolImageId(null);
    }
  }, [onRefresh, refreshIolBuildStatuses]);

  const openIolDiagnostics = useCallback(async (imageId: string) => {
    setShowIolDiagnostics(true);
    setIolDiagnosticsLoading(true);
    setIolDiagnosticsError(null);
    setIolDiagnostics(null);
    try {
      const details = await apiRequest<IolBuildDiagnosticsResponse>(
        `/images/library/${encodeURIComponent(imageId)}/build-diagnostics`
      );
      setIolDiagnostics(details);
    } catch (error) {
      setIolDiagnosticsError(error instanceof Error ? error.message : 'Failed to load build diagnostics');
    } finally {
      setIolDiagnosticsLoading(false);
    }
  }, []);

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

  const iolBuildRows = useMemo(() => {
    return iolSourceImages.map((img) => {
      const liveStatus = iolBuildStatuses[img.id];
      const builtDocker = builtDockerBySource.get(img.id);
      const effectiveStatus = normalizeBuildStatus(
        liveStatus?.status || liveStatus?.build_status || (builtDocker ? 'complete' : img.build_status)
      );
      return {
        image: img,
        status: effectiveStatus,
        buildError: liveStatus?.build_error || img.build_error || null,
        buildJobId: liveStatus?.build_job_id || img.build_job_id || null,
        buildIgnoredAt: liveStatus?.build_ignored_at || img.build_ignored_at || null,
        buildIgnoredBy: liveStatus?.build_ignored_by || img.build_ignored_by || null,
        dockerReference: liveStatus?.docker_reference || builtDocker?.reference || null,
        dockerImageId: liveStatus?.docker_image_id || builtDocker?.id || null,
      };
    });
  }, [iolSourceImages, iolBuildStatuses, builtDockerBySource]);

  const hasActiveIolBuilds = useMemo(
    () => iolBuildRows.some((row) => row.status === 'queued' || row.status === 'building'),
    [iolBuildRows]
  );
  const activeIolBuildCount = useMemo(
    () => iolBuildRows.filter((row) => row.status === 'queued' || row.status === 'building').length,
    [iolBuildRows]
  );
  const currentIolBuildRows = useMemo(
    () => iolBuildRows.filter((row) => row.status !== 'complete'),
    [iolBuildRows]
  );
  const historicalIolBuildRows = useMemo(
    () => iolBuildRows.filter((row) => row.status === 'complete'),
    [iolBuildRows]
  );

  useEffect(() => {
    if (!isBuildJobsMode) {
      setIolBuildStatuses({});
      return;
    }
    if (iolSourceImages.length === 0) {
      setIolBuildStatuses({});
      return;
    }
    void refreshIolBuildStatuses();
  }, [isBuildJobsMode, iolSourceImages, refreshIolBuildStatuses]);

  usePolling(refreshIolBuildStatuses, 5000, isBuildJobsMode && autoRefreshIolBuilds && hasActiveIolBuilds);

  if (isBuildJobsMode) {
    return (
      <div className="h-full overflow-auto p-6">
        <div className="max-w-5xl mx-auto space-y-4">
          <div>
            <h2 className="text-lg font-bold text-stone-900 dark:text-white">Build Jobs</h2>
            <p className="text-xs text-stone-500 dark:text-stone-400 mt-1">
              Track and manage background IOL Docker image builds
            </p>
            {hasActiveIolBuilds && (
              <div className="mt-2 inline-flex items-center gap-2 rounded-full border border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-900/20 px-3 py-1 text-[11px] font-semibold text-blue-700 dark:text-blue-300">
                <i className="fa-solid fa-circle-notch fa-spin" />
                {activeIolBuildCount} build{activeIolBuildCount === 1 ? '' : 's'} in progress
              </div>
            )}
          </div>

          {uploadStatus && (
            <p className="text-xs text-stone-500 dark:text-stone-400">{uploadStatus}</p>
          )}

          {iolBuildRows.length === 0 ? (
            <div className="rounded-xl border border-dashed border-stone-300 dark:border-stone-700 bg-white/50 dark:bg-stone-900/40 p-8 text-center">
              <i className="fa-solid fa-compact-disc text-3xl text-stone-300 dark:text-stone-600 mb-3" />
              <h3 className="text-sm font-bold text-stone-600 dark:text-stone-300">No IOL Build Jobs</h3>
              <p className="text-xs text-stone-500 dark:text-stone-400 mt-1">
                Import an ISO or upload an IOL binary in Image Management to create build jobs.
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              <div className="rounded-lg border border-stone-200 dark:border-stone-800 bg-stone-50/70 dark:bg-stone-900/40">
                <div className="px-3 py-2 border-b border-stone-200 dark:border-stone-800 flex items-center justify-between">
                  <div>
                    <h3 className="text-[11px] font-bold text-stone-700 dark:text-stone-300 uppercase tracking-wide">
                      Current Jobs
                    </h3>
                    <p className="text-[10px] text-stone-500 dark:text-stone-400 mt-0.5">
                      {hasActiveIolBuilds ? 'Live updates active' : 'No active builds'}
                    </p>
                  </div>
                  <div className="flex items-center gap-3">
                    <label className="flex items-center gap-1 text-[10px] text-stone-500 dark:text-stone-400">
                      <input
                        type="checkbox"
                        checked={autoRefreshIolBuilds}
                        onChange={(e) => setAutoRefreshIolBuilds(e.target.checked)}
                        className="w-3 h-3 rounded border-stone-300 dark:border-stone-600 text-sage-600 focus:ring-sage-500"
                      />
                      Auto
                    </label>
                    <button
                      onClick={refreshIolBuildStatuses}
                      disabled={refreshingIolBuilds}
                      className="text-[10px] font-bold text-sage-600 hover:text-sage-500 disabled:text-stone-400 transition-colors"
                    >
                      <i className={`fa-solid fa-rotate mr-1 ${refreshingIolBuilds ? 'fa-spin' : ''}`} />
                      Refresh
                    </button>
                  </div>
                </div>
                <div className="p-3 space-y-2 max-h-[50vh] overflow-y-auto custom-scrollbar">
                  {currentIolBuildRows.length === 0 ? (
                    <div className="rounded-md border border-dashed border-stone-300 dark:border-stone-700 bg-white/60 dark:bg-stone-900/30 px-3 py-2 text-xs text-stone-500 dark:text-stone-400">
                      No pending or failed jobs. Completed builds are listed in History below.
                    </div>
                  ) : (
                    currentIolBuildRows.map((row) => {
                      const statusTone =
                        row.status === 'failed'
                          ? 'text-red-600 dark:text-red-400'
                          : row.status === 'ignored'
                          ? 'text-stone-500 dark:text-stone-300'
                          : row.status === 'building' || row.status === 'queued'
                          ? 'text-blue-600 dark:text-blue-400'
                          : 'text-amber-600 dark:text-amber-400';
                      const statusLabel =
                        row.status === 'failed'
                          ? 'Failed'
                          : row.status === 'ignored'
                          ? 'Ignored'
                          : row.status === 'building'
                          ? 'Building'
                          : row.status === 'queued'
                          ? 'Queued'
                          : 'Not Started';
                      const isRetrying = retryingIolImageId === row.image.id;
                      const isIgnoring = ignoringIolImageId === row.image.id;

                      return (
                        <div
                          key={row.image.id}
                          className="rounded-md border border-stone-200 dark:border-stone-800 bg-white/70 dark:bg-stone-800/30 p-2.5"
                        >
                          <div className="flex items-start justify-between gap-2">
                            <div className="min-w-0">
                              <div className="text-xs font-semibold text-stone-800 dark:text-stone-200 truncate">
                                {row.image.filename || row.image.reference}
                              </div>
                              <div className={`text-[10px] font-bold uppercase ${statusTone}`}>
                                {row.status === 'building' && <i className="fa-solid fa-spinner fa-spin mr-1" />}
                                {statusLabel}
                              </div>
                              {row.dockerReference && (
                                <div className="text-[10px] text-stone-500 dark:text-stone-400 mt-0.5 truncate">
                                  Docker: {row.dockerReference}
                                </div>
                              )}
                              {row.buildJobId && (
                                <div className="text-[10px] text-stone-400 dark:text-stone-500 truncate">
                                  Job: {row.buildJobId}
                                </div>
                              )}
                              {row.status === 'ignored' && (
                                <div className="text-[10px] text-stone-400 dark:text-stone-500 mt-0.5 truncate">
                                  Ignored by {row.buildIgnoredBy || 'user'} at {formatBuildTimestamp(row.buildIgnoredAt)}
                                </div>
                              )}
                              {row.buildError && (
                                <div className="text-[10px] text-red-500 mt-1 whitespace-pre-wrap break-words">
                                  {row.buildError}
                                </div>
                              )}
                            </div>
                            <div className="flex flex-wrap items-center justify-end gap-1.5 shrink-0 max-w-[320px]">
                              <button
                                onClick={() => openIolDiagnostics(row.image.id)}
                                className="px-2 py-1 rounded text-[10px] font-bold glass-control text-stone-700 dark:text-stone-300 transition-colors"
                              >
                                Details
                              </button>
                              <button
                                onClick={() => retryIolBuild(row.image.id, false)}
                                disabled={isRetrying || isIgnoring || row.status === 'queued' || row.status === 'building'}
                                className="px-2 py-1 rounded text-[10px] font-bold bg-sage-600 hover:bg-sage-500 disabled:bg-stone-300 dark:disabled:bg-stone-700 text-white transition-colors"
                              >
                                {isRetrying ? 'Retrying...' : 'Retry'}
                              </button>
                              <button
                                onClick={() => retryIolBuild(row.image.id, true)}
                                disabled={isRetrying || isIgnoring || row.status === 'queued' || row.status === 'building'}
                                className="px-2 py-1 rounded text-[10px] font-bold glass-control text-stone-700 dark:text-stone-300 disabled:text-stone-400 transition-colors"
                              >
                                Force
                              </button>
                              <button
                                onClick={() => ignoreIolBuildFailure(row.image.id)}
                                disabled={isRetrying || isIgnoring || row.status !== 'failed'}
                                className="px-2 py-1 rounded text-[10px] font-bold glass-control text-stone-700 dark:text-stone-300 disabled:text-stone-400 transition-colors"
                              >
                                {isIgnoring ? 'Ignoring...' : 'Ignore'}
                              </button>
                            </div>
                          </div>
                        </div>
                      );
                    })
                  )}
                </div>
              </div>

              {historicalIolBuildRows.length > 0 && (
                <div className="rounded-lg border border-stone-200 dark:border-stone-800 bg-stone-50/50 dark:bg-stone-900/25">
                  <div className="px-3 py-2 border-b border-stone-200 dark:border-stone-800 flex items-center justify-between">
                    <h3 className="text-[11px] font-bold text-stone-700 dark:text-stone-300 uppercase tracking-wide">
                      Build History
                    </h3>
                    <span className="text-[10px] text-stone-500 dark:text-stone-400">
                      {historicalIolBuildRows.length} completed
                    </span>
                  </div>
                  <div className="p-3 space-y-2 max-h-56 overflow-y-auto custom-scrollbar">
                    {historicalIolBuildRows.map((row) => (
                      <div
                        key={`history-${row.image.id}`}
                        className="rounded-md border border-stone-200 dark:border-stone-800 bg-white/70 dark:bg-stone-800/30 p-2.5"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <div className="text-xs font-semibold text-stone-800 dark:text-stone-200 truncate">
                              {row.image.filename || row.image.reference}
                            </div>
                            <div className="text-[10px] font-bold uppercase text-emerald-600 dark:text-emerald-400">
                              Ready
                            </div>
                            {row.dockerReference && (
                              <div className="text-[10px] text-stone-500 dark:text-stone-400 mt-0.5 truncate">
                                Docker: {row.dockerReference}
                              </div>
                            )}
                            {row.buildJobId && (
                              <div className="text-[10px] text-stone-400 dark:text-stone-500 truncate">
                                Job: {row.buildJobId}
                              </div>
                            )}
                          </div>
                          <span className="text-[10px] text-stone-400 dark:text-stone-500 whitespace-nowrap">
                            Completed
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        <Modal
          isOpen={showIolDiagnostics}
          onClose={() => setShowIolDiagnostics(false)}
          title="IOL Build Diagnostics"
          size="lg"
        >
          {iolDiagnosticsLoading && (
            <div className="py-8 text-center">
              <i className="fa-solid fa-spinner fa-spin text-xl text-stone-400" />
            </div>
          )}

          {!iolDiagnosticsLoading && iolDiagnosticsError && (
            <div className="p-3 rounded bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300 text-sm">
              {iolDiagnosticsError}
            </div>
          )}

          {!iolDiagnosticsLoading && !iolDiagnosticsError && iolDiagnostics && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3 text-xs">
                <div className="text-stone-500 dark:text-stone-400">
                  File
                  <div className="font-semibold text-stone-800 dark:text-stone-100 break-all">
                    {iolDiagnostics.filename || iolDiagnostics.reference || iolDiagnostics.image_id}
                  </div>
                </div>
                <div className="text-stone-500 dark:text-stone-400">
                  Status
                  <div className="font-semibold text-stone-800 dark:text-stone-100 uppercase">
                    {iolDiagnostics.status || 'unknown'}
                  </div>
                </div>
                <div className="text-stone-500 dark:text-stone-400">
                  Job ID
                  <div className="font-mono text-stone-700 dark:text-stone-200 break-all">
                    {iolDiagnostics.queue_job?.id || iolDiagnostics.build_job_id || '-'}
                  </div>
                </div>
                <div className="text-stone-500 dark:text-stone-400">
                  Queue Status
                  <div className="font-semibold text-stone-800 dark:text-stone-100">
                    {iolDiagnostics.queue_job?.status || iolDiagnostics.rq_status || '-'}
                  </div>
                </div>
                <div className="text-stone-500 dark:text-stone-400">
                  Started
                  <div className="text-stone-700 dark:text-stone-200">
                    {formatBuildTimestamp(iolDiagnostics.queue_job?.started_at)}
                  </div>
                </div>
                <div className="text-stone-500 dark:text-stone-400">
                  Ended
                  <div className="text-stone-700 dark:text-stone-200">
                    {formatBuildTimestamp(iolDiagnostics.queue_job?.ended_at)}
                  </div>
                </div>
              </div>

              {iolDiagnostics.recommended_action && (
                <div className="p-3 rounded border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20 text-amber-800 dark:text-amber-200 text-xs">
                  {iolDiagnostics.recommended_action}
                </div>
              )}

              {iolDiagnostics.build_error && (
                <div>
                  <div className="text-xs font-semibold text-stone-600 dark:text-stone-300 mb-1">Build Error</div>
                  <pre className="p-3 rounded bg-stone-900 text-stone-200 text-[11px] whitespace-pre-wrap break-words max-h-40 overflow-auto">
                    {iolDiagnostics.build_error}
                  </pre>
                </div>
              )}

              {iolDiagnostics.queue_job?.error_log && (
                <div>
                  <div className="text-xs font-semibold text-stone-600 dark:text-stone-300 mb-1">Worker Traceback</div>
                  <pre className="p-3 rounded bg-stone-950 text-stone-200 text-[11px] whitespace-pre-wrap break-words max-h-56 overflow-auto">
                    {iolDiagnostics.queue_job.error_log}
                  </pre>
                </div>
              )}
            </div>
          )}
        </Modal>
      </div>
    );
  }

  return (
    <div className="h-full bg-transparent flex flex-col overflow-hidden">
      <div className="flex flex-col h-full min-h-0">
        {/* Header */}
        <header className="px-6 py-4 border-b border-stone-200 dark:border-stone-800 glass-surface">
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
                className="px-4 py-2 glass-control text-stone-700 dark:text-white rounded-lg border border-stone-300 dark:border-stone-700 text-xs font-bold transition-all"
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
            <div className="p-4 border-b border-stone-200 dark:border-stone-800 glass-surface space-y-3">
              {/* Search and sort row */}
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <i className="fa-solid fa-magnifying-glass absolute left-3 top-1/2 -translate-y-1/2 text-stone-400 text-xs" />
                  <input
                    type="text"
                    placeholder="Search devices..."
                    value={deviceSearch}
                    onChange={(e) => setDeviceSearch(e.target.value)}
                    className="w-full pl-9 pr-8 py-2 glass-control rounded-lg text-xs text-stone-900 dark:text-stone-100 placeholder:text-stone-400 focus:outline-none focus:ring-2 focus:ring-sage-500/50"
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
                  className="px-3 py-2 glass-control rounded-lg text-xs text-stone-700 dark:text-stone-300 focus:outline-none focus:ring-2 focus:ring-sage-500/50"
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
