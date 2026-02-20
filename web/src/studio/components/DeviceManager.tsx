import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { API_BASE_URL, apiRequest } from '../../api';
import { DeviceModel, ImageLibraryEntry } from '../types';
import { DragProvider, useDragContext } from '../contexts/DragContext';
import DeviceCard from './DeviceCard';
import ImageCard from './ImageCard';
import ImageFilterBar, { ImageAssignmentFilter, ImageSortOption } from './ImageFilterBar';
import FilterChip from './FilterChip';
import ISOImportModal from '../../components/ISOImportModal';
import type { ISOImportLogEvent } from '../../components/ISOImportModal';
import { Modal } from '../../components/ui/Modal';
import { usePersistedState, usePersistedSet } from '../hooks/usePersistedState';
import { usePolling } from '../hooks/usePolling';
import { getImageDeviceIds, isImageDefaultForDevice, isInstantiableImageKind } from '../../utils/deviceModels';

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

type ChunkUploadKind = 'docker' | 'qcow2';

interface ImageChunkUploadInitResponse {
  upload_id: string;
  kind: ChunkUploadKind;
  filename: string;
  total_size: number;
  chunk_size: number;
  total_chunks: number;
}

interface ImageChunkUploadChunkResponse {
  upload_id: string;
  chunk_index: number;
  bytes_received: number;
  total_received: number;
  progress_percent: number;
  is_complete: boolean;
}

interface ImageChunkUploadCompleteResponse {
  upload_id: string;
  kind: ChunkUploadKind;
  filename: string;
  status: 'completed' | 'processing' | 'failed' | 'awaiting_confirmation';
  result?: Record<string, unknown> | null;
}

interface Qcow2DetectionResult {
  detected_device_id: string | null;
  detected_version: string | null;
  confidence: 'high' | 'medium' | 'low' | 'none';
  size_bytes: number | null;
  sha256: string | null;
  suggested_metadata: Record<string, unknown>;
}

interface PendingQcow2Upload {
  tempId: string;
  filename: string;
  progress: number;
  phase: 'uploading' | 'processing' | 'awaiting_confirmation';
  createdAt: number;
}

interface ImageManagementLogEntry {
  id: string;
  timestamp: string;
  level: 'info' | 'error';
  category: string;
  phase: string;
  message: string;
  filename?: string;
  details?: string;
}

type ImageManagementLogFilter = 'all' | 'errors' | 'iso' | 'docker' | 'qcow2';

const IMAGE_LOG_LIMIT = 200;
const IMAGE_COMPAT_ALIASES: Record<string, string[]> = {
  'cat9000v-uadp': ['cisco_cat9kv'],
  'cat9000v-q200': ['cisco_cat9kv'],
  'cat9000v_uadp': ['cisco_cat9kv'],
  'cat9000v_q200': ['cisco_cat9kv'],
  c8000v: ['cisco_c8000v'],
  ftdv: ['cisco_ftdv'],
};
const IMAGE_LOG_LEVEL_COLORS: Record<ImageManagementLogEntry['level'], string> = {
  info: 'text-green-600 dark:text-green-400 bg-green-100 dark:bg-green-900/30',
  error: 'text-red-600 dark:text-red-400 bg-red-100 dark:bg-red-900/30',
};
const IMAGE_LOG_CATEGORY_COLORS: Record<string, string> = {
  iso: 'text-blue-700 dark:text-blue-300 bg-blue-100 dark:bg-blue-900/30',
  docker: 'text-purple-700 dark:text-purple-300 bg-purple-100 dark:bg-purple-900/30',
  qcow2: 'text-emerald-700 dark:text-emerald-300 bg-emerald-100 dark:bg-emerald-900/30',
};
const IMAGE_UPLOAD_CHUNK_SIZE = 10 * 1024 * 1024;

function formatImageLogTime(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return timestamp;
  return date.toLocaleTimeString('en-US', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function formatImageLogDate(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
  });
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
  const [isQcow2PostProcessing, setIsQcow2PostProcessing] = useState(false);
  const [pendingQcow2Uploads, setPendingQcow2Uploads] = useState<PendingQcow2Upload[]>([]);
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
  const [showUploadLogsModal, setShowUploadLogsModal] = useState(false);
  const [copiedUploadLogId, setCopiedUploadLogId] = useState<string | null>(null);
  const [imageManagementLogs, setImageManagementLogs] = usePersistedState<ImageManagementLogEntry[]>(
    'archetype:image-management:logs',
    []
  );
  const [imageLogFilter, setImageLogFilter] = usePersistedState<ImageManagementLogFilter>(
    'archetype:image-management:log-filter',
    'all'
  );
  const [imageLogSearch, setImageLogSearch] = useState('');
  const [autoRefreshIolBuilds, setAutoRefreshIolBuilds] = usePersistedState<boolean>(
    'archetype:iol-build:auto-refresh',
    true
  );
  // Two-phase qcow2 upload confirmation state
  const [qcow2Confirm, setQcow2Confirm] = useState<{
    uploadId: string;
    filename: string;
    detection: Qcow2DetectionResult;
    deviceIdOverride: string;
    versionOverride: string;
    autoBuild: boolean;
  } | null>(null);

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
  const runnableImageLibrary = useMemo(
    () => imageLibrary.filter((img) => isInstantiableImageKind(img.kind)),
    [imageLibrary]
  );
  const selectedRunnableImageKinds = useMemo(() => {
    const kinds = new Set<string>();
    selectedImageKinds.forEach((kind) => {
      if (isInstantiableImageKind(kind)) kinds.add(kind);
    });
    return kinds;
  }, [selectedImageKinds]);
  const resolveImageDeviceIds = useCallback((image: ImageLibraryEntry): string[] => {
    const rawDeviceIds = getImageDeviceIds(image);
    if (rawDeviceIds.length === 0) return [];

    const normalizedRawIds = new Set(rawDeviceIds.map((id) => String(id).toLowerCase()));
    const resolved = new Set<string>(rawDeviceIds);

    deviceModels.forEach((device) => {
      const modelId = String(device.id || '').toLowerCase();
      const aliases = IMAGE_COMPAT_ALIASES[modelId] || [];
      const matchesById = normalizedRawIds.has(modelId);
      const matchesByAlias = aliases.some((alias) => normalizedRawIds.has(alias));
      if (matchesById || matchesByAlias) {
        resolved.add(device.id);
      }
    });

    return Array.from(resolved);
  }, [deviceModels]);
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

  useEffect(() => {
    if (pendingQcow2Uploads.length === 0) return;
    const knownQcow2Filenames = new Set(
      imageLibrary
        .filter((img) => (img.kind || '').toLowerCase() === 'qcow2')
        .map((img) => img.filename || img.reference?.split('/').pop() || '')
        .filter(Boolean)
    );
    setPendingQcow2Uploads((prev) => prev.filter((item) => !knownQcow2Filenames.has(item.filename)));
  }, [imageLibrary, pendingQcow2Uploads.length]);

  const addImageManagementLog = useCallback(
    (entry: Omit<ImageManagementLogEntry, 'id' | 'timestamp'>) => {
      setImageManagementLogs((prev) => [
        {
          ...entry,
          id: `img-log-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
          timestamp: new Date().toISOString(),
        },
        ...prev,
      ].slice(0, IMAGE_LOG_LIMIT));
    },
    [setImageManagementLogs]
  );

  const clearImageManagementLogs = useCallback(() => {
    setImageManagementLogs([]);
  }, [setImageManagementLogs]);

  const handleIsoLogEvent = useCallback((event: ISOImportLogEvent) => {
    addImageManagementLog({
      level: event.level,
      category: 'iso',
      phase: event.phase,
      message: event.message,
      filename: event.filename,
      details: event.details,
    });
  }, [addImageManagementLog]);

  const formatUploadLogEntry = useCallback((entry: ImageManagementLogEntry): string => {
    const lines = [
      `timestamp: ${entry.timestamp}`,
      `level: ${entry.level}`,
      `category: ${entry.category}`,
      `phase: ${entry.phase}`,
      `message: ${entry.message}`,
    ];
    if (entry.filename) lines.push(`filename: ${entry.filename}`);
    if (entry.details) {
      lines.push('details:');
      lines.push(entry.details);
    }
    return lines.join('\n');
  }, []);

  const copyUploadLogEntry = useCallback(async (entry: ImageManagementLogEntry) => {
    const value = formatUploadLogEntry(entry);
    try {
      if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
        await navigator.clipboard.writeText(value);
      } else {
        const ta = document.createElement('textarea');
        ta.value = value;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        const success = document.execCommand('copy');
        document.body.removeChild(ta);
        if (!success) throw new Error('Copy failed');
      }
      setCopiedUploadLogId(entry.id);
    } catch (error) {
      console.error('Failed to copy upload log entry:', error);
      setCopiedUploadLogId(null);
    }
  }, [formatUploadLogEntry]);

  useEffect(() => {
    if (!copiedUploadLogId) return;
    const timeout = window.setTimeout(() => setCopiedUploadLogId(null), 1500);
    return () => window.clearTimeout(timeout);
  }, [copiedUploadLogId]);

  // Get unique device vendors
  const deviceVendors = useMemo(() => {
    const vendors = new Set<string>();
    deviceModels.forEach((d) => {
      if (d.vendor) vendors.add(d.vendor);
    });
    return Array.from(vendors).sort();
  }, [deviceModels]);

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
    const filtered = runnableImageLibrary.filter((img) => {
      const imgVendors = imageVendorsById.get(img.id) || [];

      // Search filter
      if (imageSearch) {
        const query = imageSearch.toLowerCase();
        const matchesFilename = img.filename?.toLowerCase().includes(query);
        const matchesRef = img.reference?.toLowerCase().includes(query);
        const matchesVersion = img.version?.toLowerCase().includes(query);
        const matchesVendor = imgVendors.some((vendor) => vendor.toLowerCase().includes(query));
        if (!matchesFilename && !matchesRef && !matchesVersion && !matchesVendor) {
          return false;
        }
      }

      // Vendor filter
      if (selectedImageVendors.size > 0 && !imgVendors.some((vendor) => selectedImageVendors.has(vendor))) {
        return false;
      }

      // Kind filter
      if (selectedRunnableImageKinds.size > 0 && !selectedRunnableImageKinds.has(img.kind)) {
        return false;
      }

      // Assignment filter
      if (imageAssignmentFilter === 'unassigned' && img.device_id) return false;
      if (imageAssignmentFilter === 'assigned' && !img.device_id) return false;

      return true;
    });

    // Sort images
    return filtered.sort((a, b) => {
      const aPrimaryVendor = (imageVendorsById.get(a.id) || [])[0] || '';
      const bPrimaryVendor = (imageVendorsById.get(b.id) || [])[0] || '';
      switch (imageSort) {
        case 'name':
          return (a.reference || a.filename || '').localeCompare(b.reference || b.filename || '');
        case 'vendor':
          return aPrimaryVendor.localeCompare(bPrimaryVendor) || (a.reference || '').localeCompare(b.reference || '');
        case 'kind':
          return a.kind.localeCompare(b.kind) || (a.reference || '').localeCompare(b.reference || '');
        case 'date':
          return (b.uploaded_at || '').localeCompare(a.uploaded_at || '');
        default:
          return 0;
      }
    });
  }, [
    runnableImageLibrary,
    imageSearch,
    selectedImageVendors,
    selectedRunnableImageKinds,
    imageAssignmentFilter,
    imageSort,
    imageVendorsById,
  ]);

  const filteredPendingQcow2Uploads = useMemo(() => {
    if (isBuildJobsMode) return [];
    if (imageAssignmentFilter === 'assigned') return [];
    if (selectedImageVendors.size > 0) return [];
    if (selectedRunnableImageKinds.size > 0 && !selectedRunnableImageKinds.has('qcow2')) return [];

    const query = imageSearch.trim().toLowerCase();
    return pendingQcow2Uploads
      .filter((item) => !query || item.filename.toLowerCase().includes(query))
      .sort((a, b) => b.createdAt - a.createdAt);
  }, [
    isBuildJobsMode,
    imageAssignmentFilter,
    selectedImageVendors,
    selectedRunnableImageKinds,
    imageSearch,
    pendingQcow2Uploads,
  ]);

  const imageLogCounts = useMemo(() => ({
    all: imageManagementLogs.length,
    errors: imageManagementLogs.filter((entry) => entry.level === 'error').length,
    iso: imageManagementLogs.filter((entry) => entry.category === 'iso').length,
    docker: imageManagementLogs.filter((entry) => entry.category === 'docker').length,
    qcow2: imageManagementLogs.filter((entry) => entry.category === 'qcow2').length,
  }), [imageManagementLogs]);

  const filteredImageManagementLogs = useMemo(() => {
    let filtered: ImageManagementLogEntry[];
    if (imageLogFilter === 'all') {
      filtered = imageManagementLogs;
    } else if (imageLogFilter === 'errors') {
      filtered = imageManagementLogs.filter((entry) => entry.level === 'error');
    } else {
      filtered = imageManagementLogs.filter((entry) => entry.category === imageLogFilter);
    }

    const query = imageLogSearch.trim().toLowerCase();
    if (!query) return filtered;

    return filtered.filter((entry) => {
      const haystack = [
        entry.message,
        entry.category,
        entry.phase,
        entry.filename || '',
        entry.details || '',
      ].join('\n').toLowerCase();
      return haystack.includes(query);
    });
  }, [imageManagementLogs, imageLogFilter, imageLogSearch]);

  const uploadErrorCount = useMemo(
    () => imageManagementLogs.filter((entry) => entry.level === 'error').length,
    [imageManagementLogs]
  );

  // Group images for display (uses compatible_devices for shared images)
  const { unassignedImages, assignedImagesByDevice } = useMemo(() => {
    const unassigned: ImageLibraryEntry[] = [];
    const byDevice = new Map<string, ImageLibraryEntry[]>();
    const seen = new Set<string>(); // avoid duplicating unassigned

    filteredImages.forEach((img) => {
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
  }, [filteredImages, resolveImageDeviceIds, withDeviceScopedDefault]);

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

  function getAuthHeaders(): Record<string, string> {
    const token = localStorage.getItem('token');
    return token ? { Authorization: `Bearer ${token}` } : {};
  }

  async function uploadFileInChunks(
    kind: ChunkUploadKind,
    file: File,
    onProgress: (percent: number, message: string) => void,
    options?: { autoBuild?: boolean; autoConfirm?: boolean }
  ): Promise<ImageChunkUploadCompleteResponse> {
    const headers = getAuthHeaders();

    onProgress(0, 'Initializing upload...');
    const initResponse = await fetch(`${API_BASE_URL}/images/upload/init`, {
      method: 'POST',
      headers: {
        ...headers,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        kind,
        filename: file.name,
        total_size: file.size,
        chunk_size: IMAGE_UPLOAD_CHUNK_SIZE,
        auto_build: options?.autoBuild ?? true,
        auto_confirm: options?.autoConfirm ?? true,
      }),
    });

    if (!initResponse.ok) {
      const text = await initResponse.text();
      throw new Error(parseErrorMessage(text));
    }

    const initData = await initResponse.json() as ImageChunkUploadInitResponse;
    const totalChunks = initData.total_chunks;

    for (let i = 0; i < totalChunks; i++) {
      const start = i * initData.chunk_size;
      const end = Math.min(start + initData.chunk_size, file.size);
      const chunk = file.slice(start, end);
      const formData = new FormData();
      formData.append('chunk', chunk);

      const chunkResponse = await fetch(
        `${API_BASE_URL}/images/upload/${initData.upload_id}/chunk?index=${i}`,
        {
          method: 'POST',
          headers,
          body: formData,
        }
      );

      if (!chunkResponse.ok) {
        const text = await chunkResponse.text();
        throw new Error(parseErrorMessage(text));
      }

      const chunkData = await chunkResponse.json() as ImageChunkUploadChunkResponse;
      onProgress(
        Math.max(0, Math.min(100, chunkData.progress_percent)),
        `Uploading chunk ${i + 1} of ${totalChunks}...`
      );
    }

    onProgress(100, 'Upload complete. Finalizing...');
    const completeResponse = await fetch(`${API_BASE_URL}/images/upload/${initData.upload_id}/complete`, {
      method: 'POST',
      headers,
    });

    if (!completeResponse.ok) {
      const text = await completeResponse.text();
      throw new Error(parseErrorMessage(text));
    }

    return await completeResponse.json() as ImageChunkUploadCompleteResponse;
  }

  /**
   * Upload Docker archive via chunked transport, then poll import/build progress.
   */
  async function uploadImageWithPolling(
    file: File,
    onProgress: (percent: number, message: string) => void
  ): Promise<{ output?: string; images?: string[] }> {
    const headers = getAuthHeaders();
    const completeData = await uploadFileInChunks('docker', file, (percent, message) => {
      const scaled = Math.round(percent * 0.5);
      onProgress(Math.max(0, Math.min(50, scaled)), message);
    });

    if (!completeData.upload_id) {
      throw new Error('No upload ID returned');
    }

    if (completeData.status !== 'processing') {
      throw new Error('Docker upload did not start processing');
    }

    onProgress(55, 'Upload complete, processing Docker archive...');

    let lastPercent = 55;
    while (true) {
      await new Promise((resolve) => setTimeout(resolve, 500));

      const progressResponse = await fetch(`${API_BASE_URL}/images/load/${completeData.upload_id}/progress`, {
        headers,
      });

      if (!progressResponse.ok) {
        if (progressResponse.status === 404) {
          addImageManagementLog({
            level: 'error',
            category: 'docker',
            phase: 'processing',
            message: 'Upload progress record not found (may have expired)',
            filename: file.name,
          });
          throw new Error('Upload not found - it may have completed or expired');
        }
        continue;
      }

      const progress = await progressResponse.json();
      const mappedPercent = Math.max(55, Math.min(100, 50 + Math.round((Number(progress.percent) || 0) * 0.5)));
      if (mappedPercent !== lastPercent || progress.message) {
        lastPercent = mappedPercent;
        onProgress(mappedPercent, progress.message || 'Processing...');
      }

      if (progress.error) {
        addImageManagementLog({
          level: 'error',
          category: 'docker',
          phase: progress.phase || 'processing',
          message: progress.message || 'Import failed',
          filename: file.name,
        });
        throw new Error(progress.message || 'Import failed');
      }

      if (progress.complete) {
        return { output: progress.message, images: progress.images };
      }
    }
  }

  async function uploadImage(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;

    try {
      addImageManagementLog({
        level: 'info',
        category: 'docker',
        phase: 'uploading',
        message: 'Started Docker image upload',
        filename: file.name,
      });
      setUploadStatus(`Uploading ${file.name}...`);
      setUploadProgress(0);

      // Use polling-based upload for reliable progress tracking
      const data = await uploadImageWithPolling(file, (percent, message) => {
        setUploadProgress(percent);
        setUploadStatus(message);
      });

      if (data.images && data.images.length === 0) {
        setUploadStatus('Upload finished, but no images were detected.');
        addImageManagementLog({
          level: 'error',
          category: 'docker',
          phase: 'processing',
          message: 'Upload finished but no images were detected',
          filename: file.name,
          details: data.output || '',
        });
      } else {
        setUploadStatus(data.output || 'Image loaded.');
        addImageManagementLog({
          level: 'info',
          category: 'docker',
          phase: 'complete',
          message: data.output || 'Docker image loaded successfully',
          filename: file.name,
          details: data.images?.join(', ') || '',
        });
      }
      onUploadImage();
      onRefresh();
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Upload failed';
      setUploadStatus(errorMessage);
      addImageManagementLog({
        level: 'error',
        category: 'docker',
        phase: 'failed',
        message: errorMessage,
        filename: file.name,
        details: error instanceof Error ? error.stack || error.message : String(error),
      });
    } finally {
      event.target.value = '';
      setUploadProgress(null);
    }
  }

  async function uploadQcow2(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    const pendingId = `pending-qcow2:${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    let processingLogged = false;
    setPendingQcow2Uploads((prev) => [
      {
        tempId: pendingId,
        filename: file.name,
        progress: 0,
        phase: 'uploading',
        createdAt: Date.now(),
      },
      ...prev,
    ]);
    try {
      addImageManagementLog({
        level: 'info',
        category: 'qcow2',
        phase: 'uploading',
        message: 'Started QCOW2 upload',
        filename: file.name,
      });
      setUploadStatus(`Uploading ${file.name}...`);
      setQcow2Progress(0);
      setIsQcow2PostProcessing(false);
      const completeData = await uploadFileInChunks('qcow2', file, (percent, message) => {
        const nextPercent = Math.max(0, Math.min(100, percent));
        setQcow2Progress(nextPercent);
        if (nextPercent >= 100) {
          setIsQcow2PostProcessing(true);
          setUploadStatus(message || `Upload complete for ${file.name}. Validating and finalizing image...`);
          if (!processingLogged) {
            processingLogged = true;
            addImageManagementLog({
              level: 'info',
              category: 'qcow2',
              phase: 'processing',
              message: 'Upload bytes complete; validating and finalizing QCOW2 image',
              filename: file.name,
            });
          }
        }
        setPendingQcow2Uploads((prev) =>
          prev.map((item) =>
            item.tempId === pendingId
              ? {
                  ...item,
                  progress: nextPercent,
                  phase: nextPercent >= 100 ? 'processing' : 'uploading',
                }
              : item
          )
        );
      }, { autoConfirm: false });

      // Two-phase: show confirmation dialog with detection results.
      if (completeData.status === 'awaiting_confirmation') {
        const detection = completeData.result as unknown as Qcow2DetectionResult;
        setPendingQcow2Uploads((prev) =>
          prev.map((item) =>
            item.tempId === pendingId
              ? { ...item, progress: 100, phase: 'awaiting_confirmation' }
              : item
          )
        );
        setQcow2Confirm({
          uploadId: completeData.upload_id,
          filename: completeData.filename,
          detection,
          deviceIdOverride: detection.detected_device_id || '',
          versionOverride: detection.detected_version || '',
          autoBuild: true,
        });
        setIsQcow2PostProcessing(false);
        setQcow2Progress(null);
        setUploadStatus(null);
        return;
      }

      if (completeData.status !== 'completed') {
        throw new Error('QCOW2 upload did not complete');
      }

      setIsQcow2PostProcessing(true);
      setPendingQcow2Uploads((prev) =>
        prev.map((item) =>
          item.tempId === pendingId
            ? {
                ...item,
                progress: 100,
                phase: 'processing',
              }
            : item
        )
      );
      setUploadStatus(`Finalizing ${file.name} in image library...`);
      await Promise.resolve(onUploadQcow2());
      await Promise.resolve(onRefresh());
      setUploadStatus('QCOW2 uploaded.');
      addImageManagementLog({
        level: 'info',
        category: 'qcow2',
        phase: 'complete',
        message: 'QCOW2 upload and processing completed',
        filename: file.name,
      });
    } catch (error) {
      setPendingQcow2Uploads((prev) => prev.filter((item) => item.tempId !== pendingId));
      const errorMessage = error instanceof Error ? error.message : 'Upload failed';
      setUploadStatus(errorMessage);
      addImageManagementLog({
        level: 'error',
        category: 'qcow2',
        phase: processingLogged ? 'processing' : 'uploading',
        message: errorMessage,
        filename: file.name,
        details: error instanceof Error ? error.stack || error.message : String(error),
      });
    } finally {
      event.target.value = '';
      setIsQcow2PostProcessing(false);
      setQcow2Progress(null);
    }
  }

  async function confirmQcow2Upload() {
    if (!qcow2Confirm) return;
    const { uploadId, filename, deviceIdOverride, versionOverride, autoBuild } = qcow2Confirm;
    setQcow2Confirm(null);
    setUploadStatus(`Confirming ${filename}...`);

    // Remove from pending list
    setPendingQcow2Uploads((prev) =>
      prev.filter((item) => item.phase !== 'awaiting_confirmation')
    );

    try {
      const response = await apiRequest<ImageChunkUploadCompleteResponse>(
        `/images/upload/${uploadId}/confirm`,
        {
          method: 'POST',
          body: JSON.stringify({
            device_id: deviceIdOverride || null,
            version: versionOverride || null,
            auto_build: autoBuild,
          }),
        }
      );
      if (response.status !== 'completed') {
        throw new Error(`Confirmation failed: ${response.status}`);
      }
      await Promise.resolve(onUploadQcow2());
      await Promise.resolve(onRefresh());
      setUploadStatus('QCOW2 uploaded.');
      addImageManagementLog({
        level: 'info',
        category: 'qcow2',
        phase: 'complete',
        message: `QCOW2 confirmed as ${deviceIdOverride || 'auto-detected'}`,
        filename,
      });
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Confirmation failed';
      setUploadStatus(errorMessage);
      addImageManagementLog({
        level: 'error',
        category: 'qcow2',
        phase: 'confirm',
        message: errorMessage,
        filename,
        details: error instanceof Error ? error.stack || error.message : String(error),
      });
    }
  }

  function cancelQcow2Confirm() {
    if (!qcow2Confirm) return;
    const { uploadId, filename } = qcow2Confirm;
    setQcow2Confirm(null);
    setPendingQcow2Uploads((prev) =>
      prev.filter((item) => item.phase !== 'awaiting_confirmation')
    );
    // Cancel the upload session on the server
    fetch(`${API_BASE_URL}/images/upload/${uploadId}`, {
      method: 'DELETE',
      headers: getAuthHeaders(),
    }).catch(() => {});
    setUploadStatus(null);
    addImageManagementLog({
      level: 'info',
      category: 'qcow2',
      phase: 'cancelled',
      message: 'QCOW2 upload cancelled by user',
      filename,
    });
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
                  <div className="p-3 space-y-2 max-h-[50vh] overflow-y-auto custom-scrollbar">
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
            <div className="flex flex-wrap items-center gap-3">
              <div className="flex flex-wrap items-center gap-3">
                <button
                  onClick={openFilePicker}
                  className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-xs font-bold transition-all shadow-sm"
                >
                  <i className="fa-solid fa-cloud-arrow-up mr-2"></i> Upload Docker
                </button>
                <button
                  onClick={openQcow2Picker}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-xs font-bold transition-all shadow-sm"
                >
                  <i className="fa-solid fa-hard-drive mr-2"></i> Upload QCOW2
                </button>
                <button
                  onClick={() => setShowISOModal(true)}
                  className="px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white rounded-lg text-xs font-bold transition-all shadow-sm"
                >
                  <i className="fa-solid fa-compact-disc mr-2"></i> Import ISO
                </button>
              </div>
              <div className="ml-2 pl-3 border-l border-stone-200 dark:border-stone-700">
                <button
                  onClick={() => setShowUploadLogsModal(true)}
                  className="px-4 py-2 glass-control text-stone-700 dark:text-white rounded-lg border border-stone-300 dark:border-stone-700 text-xs font-bold transition-all"
                  title="View image upload and processing logs"
                >
                  <i className="fa-solid fa-file-lines mr-2"></i> Logs
                  {uploadErrorCount > 0 && (
                    <span className="ml-2 inline-flex items-center justify-center min-w-[1.1rem] h-[1.1rem] px-1 rounded-full bg-red-100 dark:bg-red-900/40 text-red-700 dark:text-red-300 text-[9px] font-black">
                      {uploadErrorCount}
                    </span>
                  )}
                </button>
              </div>
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
              <div className="flex items-center gap-2 text-[10px] font-bold text-stone-500 uppercase mb-1">
                <span>
                  {isQcow2PostProcessing
                    ? 'QCOW2 upload complete. Processing image...'
                    : `QCOW2 upload ${qcow2Progress}%`}
                </span>
                {isQcow2PostProcessing && (
                  <i className="fa-solid fa-circle-notch fa-spin text-stone-400" />
                )}
              </div>
              <div className="h-1.5 bg-stone-200 dark:bg-stone-800 rounded-full overflow-hidden">
                <div
                  className={`h-full bg-emerald-500 ${isQcow2PostProcessing ? 'animate-pulse' : 'transition-all'}`}
                  style={{ width: isQcow2PostProcessing ? '100%' : `${qcow2Progress}%` }}
                />
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
              <div className="flex flex-wrap items-center gap-3">
                <div className="flex items-center gap-1.5">
                  <span className="text-[10px] font-bold text-stone-400 uppercase mr-1">Status:</span>
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
                </div>
                {deviceVendors.length > 0 && <div className="h-6 w-px bg-stone-200 dark:bg-stone-700" />}
                {deviceVendors.length > 0 && (
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="text-[10px] font-bold text-stone-400 uppercase mr-1">Vendor:</span>
                    {deviceVendors.map((vendor) => (
                      <FilterChip
                        key={vendor}
                        label={vendor}
                        isActive={selectedDeviceVendors.has(vendor)}
                        onClick={() => toggleDeviceVendor(vendor)}
                      />
                    ))}
                  </div>
                )}
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
                          onUnassign={() => handleUnassignImage(img.id, deviceId)}
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

      {/* QCOW2 Confirmation Modal */}
      {qcow2Confirm && (
        <Modal
          isOpen={true}
          onClose={cancelQcow2Confirm}
          title="Confirm QCOW2 Image"
          size="md"
        >
          <div className="space-y-4">
            <div className="text-sm text-stone-600 dark:text-stone-300">
              <span className="font-medium">{qcow2Confirm.filename}</span>
              {qcow2Confirm.detection.size_bytes != null && (
                <span className="ml-2 text-stone-400">
                  ({(qcow2Confirm.detection.size_bytes / (1024 * 1024 * 1024)).toFixed(1)} GB)
                </span>
              )}
            </div>

            {qcow2Confirm.detection.confidence !== 'none' && (
              <div className={`text-xs px-2 py-1 rounded inline-block ${
                qcow2Confirm.detection.confidence === 'high'
                  ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                  : 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400'
              }`}>
                Detection confidence: {qcow2Confirm.detection.confidence}
              </div>
            )}

            <div>
              <label className="block text-xs font-medium text-stone-500 dark:text-stone-400 mb-1">
                Device Type
              </label>
              <input
                type="text"
                value={qcow2Confirm.deviceIdOverride}
                onChange={(e) => setQcow2Confirm((prev) => prev ? { ...prev, deviceIdOverride: e.target.value } : null)}
                placeholder="e.g. cisco_n9kv"
                className="w-full px-3 py-1.5 text-sm bg-stone-50 dark:bg-stone-800 border border-stone-300 dark:border-stone-600 rounded-md"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-stone-500 dark:text-stone-400 mb-1">
                Version
              </label>
              <input
                type="text"
                value={qcow2Confirm.versionOverride}
                onChange={(e) => setQcow2Confirm((prev) => prev ? { ...prev, versionOverride: e.target.value } : null)}
                placeholder="e.g. 10.3.1"
                className="w-full px-3 py-1.5 text-sm bg-stone-50 dark:bg-stone-800 border border-stone-300 dark:border-stone-600 rounded-md"
              />
            </div>

            {Object.keys(qcow2Confirm.detection.suggested_metadata).length > 0 && (
              <div>
                <div className="text-xs font-medium text-stone-500 dark:text-stone-400 mb-1">
                  Vendor Defaults
                </div>
                <div className="grid grid-cols-2 gap-1 text-xs text-stone-500 dark:text-stone-400 bg-stone-50 dark:bg-stone-800 rounded-md p-2">
                  {Object.entries(qcow2Confirm.detection.suggested_metadata).map(([key, value]) => (
                    <React.Fragment key={key}>
                      <span className="font-mono">{key}</span>
                      <span>{String(value)}</span>
                    </React.Fragment>
                  ))}
                </div>
              </div>
            )}

            <label className="flex items-center gap-2 text-sm text-stone-600 dark:text-stone-300">
              <input
                type="checkbox"
                checked={qcow2Confirm.autoBuild}
                onChange={(e) => setQcow2Confirm((prev) => prev ? { ...prev, autoBuild: e.target.checked } : null)}
                className="rounded"
              />
              Auto-build Docker image (vrnetlab)
            </label>

            <div className="flex justify-end gap-2 pt-2 border-t border-stone-200 dark:border-stone-700">
              <button
                onClick={cancelQcow2Confirm}
                className="px-3 py-1.5 text-sm text-stone-600 dark:text-stone-300 hover:bg-stone-100 dark:hover:bg-stone-700 rounded-md"
              >
                Cancel
              </button>
              <button
                onClick={confirmQcow2Upload}
                className="px-3 py-1.5 text-sm bg-indigo-600 text-white hover:bg-indigo-700 rounded-md"
              >
                Confirm Import
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* ISO Import Modal */}
      <ISOImportModal
        isOpen={showISOModal}
        onClose={() => setShowISOModal(false)}
        onLogEvent={handleIsoLogEvent}
        onImportComplete={() => {
          onRefresh();
          setShowISOModal(false);
        }}
      />

      <Modal
        isOpen={showUploadLogsModal}
        onClose={() => setShowUploadLogsModal(false)}
        title="Image Upload Logs"
        size="xl"
      >
        <div className="flex flex-col h-[70vh]">
          <div className="flex flex-wrap items-center gap-3 pb-4 border-b border-stone-200 dark:border-stone-700">
            <div className="flex items-center gap-2">
              <label className="text-xs font-medium text-stone-500 dark:text-stone-400">Filter:</label>
              <select
                aria-label="Image log filter"
                value={imageLogFilter}
                onChange={(e) => setImageLogFilter(e.target.value as ImageManagementLogFilter)}
                className="px-2 py-1 text-sm bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-600 rounded-md text-stone-700 dark:text-stone-200"
              >
                <option value="all">All ({imageLogCounts.all})</option>
                <option value="errors">Errors ({imageLogCounts.errors})</option>
                <option value="iso">ISO ({imageLogCounts.iso})</option>
                <option value="docker">Docker ({imageLogCounts.docker})</option>
                <option value="qcow2">QCOW2 ({imageLogCounts.qcow2})</option>
              </select>
            </div>

            <div className="flex-1 min-w-[220px]">
              <input
                aria-label="Search image logs"
                type="text"
                value={imageLogSearch}
                onChange={(e) => setImageLogSearch(e.target.value)}
                placeholder="Search logs..."
                className="w-full px-3 py-1 text-sm bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-600 rounded-md text-stone-700 dark:text-stone-200 placeholder-stone-400"
              />
            </div>

            <button
              onClick={clearImageManagementLogs}
              disabled={imageManagementLogs.length === 0}
              className="px-3 py-1.5 rounded-md text-xs font-semibold bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-600 text-stone-700 dark:text-stone-300 disabled:opacity-50 transition-colors"
            >
              Clear History
            </button>
          </div>

          <div className="flex items-center justify-between pt-3 text-xs text-stone-500 dark:text-stone-400">
            <span>
              Showing {filteredImageManagementLogs.length} of {imageManagementLogs.length} entries
            </span>
            {uploadErrorCount > 0 && (
              <span className="text-red-600 dark:text-red-400 font-semibold">
                {uploadErrorCount} errors
              </span>
            )}
          </div>

          <div className="flex-1 overflow-auto mt-3">
            {imageManagementLogs.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full text-stone-400 dark:text-stone-500">
                <i className="fa-solid fa-file-lines text-4xl mb-3 opacity-30"></i>
                <p className="text-sm">No logs found</p>
                <p className="text-xs mt-1">No upload or processing events recorded yet.</p>
              </div>
            ) : filteredImageManagementLogs.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full text-stone-400 dark:text-stone-500">
                <i className="fa-solid fa-filter-circle-xmark text-3xl mb-3 opacity-40"></i>
                <p className="text-sm">No matching logs</p>
                <p className="text-xs mt-1">No log entries match the current filter.</p>
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-stone-100 dark:bg-stone-800 z-10">
                  <tr>
                    <th className="px-3 py-2 text-left text-xs font-medium text-stone-500 dark:text-stone-400 uppercase tracking-wider w-20">Time</th>
                    <th className="px-3 py-2 text-left text-xs font-medium text-stone-500 dark:text-stone-400 uppercase tracking-wider w-20">Level</th>
                    <th className="px-3 py-2 text-left text-xs font-medium text-stone-500 dark:text-stone-400 uppercase tracking-wider w-24">Category</th>
                    <th className="px-3 py-2 text-left text-xs font-medium text-stone-500 dark:text-stone-400 uppercase tracking-wider w-32">Phase</th>
                    <th className="px-3 py-2 text-left text-xs font-medium text-stone-500 dark:text-stone-400 uppercase tracking-wider">Message</th>
                    <th className="px-3 py-2 text-left text-xs font-medium text-stone-500 dark:text-stone-400 uppercase tracking-wider w-24">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-stone-200 dark:divide-stone-700">
                  {filteredImageManagementLogs.map((entry) => (
                    <tr
                      key={entry.id}
                      className="hover:bg-stone-50 dark:hover:bg-stone-800/50 align-top"
                    >
                      <td className="px-3 py-2 text-stone-500 dark:text-stone-400 whitespace-nowrap font-mono text-xs">
                        <div>{formatImageLogTime(entry.timestamp)}</div>
                        <div className="text-[10px] text-stone-400 dark:text-stone-500">{formatImageLogDate(entry.timestamp)}</div>
                      </td>
                      <td className="px-3 py-2">
                        <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium uppercase ${IMAGE_LOG_LEVEL_COLORS[entry.level]}`}>
                          {entry.level}
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-semibold uppercase ${IMAGE_LOG_CATEGORY_COLORS[entry.category] || 'text-stone-700 dark:text-stone-300 bg-stone-200 dark:bg-stone-700'}`}>
                          {entry.category}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-[11px] text-stone-600 dark:text-stone-300 font-mono">
                        {entry.phase}
                      </td>
                      <td className="px-3 py-2 text-stone-700 dark:text-stone-200 font-mono text-xs">
                        <div>{entry.message}</div>
                        {entry.filename && (
                          <div className="text-[10px] text-stone-500 dark:text-stone-400 mt-1">
                            file: {entry.filename}
                          </div>
                        )}
                        {entry.details && (
                          <pre className="mt-1 p-2 bg-stone-100 dark:bg-stone-900 rounded text-[10px] text-stone-600 dark:text-stone-300 whitespace-pre-wrap break-words max-h-20 overflow-auto custom-scrollbar">
                            {entry.details}
                          </pre>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        <button
                          onClick={() => copyUploadLogEntry(entry)}
                          className="px-2 py-1 rounded text-[10px] font-bold glass-control text-stone-700 dark:text-stone-300 transition-colors whitespace-nowrap"
                        >
                          <i className={`fa-solid ${copiedUploadLogId === entry.id ? 'fa-check' : 'fa-copy'} mr-1`} />
                          {copiedUploadLogId === entry.id ? 'Copied' : 'Copy'}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </Modal>
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
