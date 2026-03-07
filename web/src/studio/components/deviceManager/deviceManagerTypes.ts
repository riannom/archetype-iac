export interface DeviceManagerProps {
  deviceModels: import('../../types').DeviceModel[];
  imageLibrary: import('../../types').ImageLibraryEntry[];
  staleAgentSummary?: import('../../../types/agentImages').AgentStaleImageSummaryResponse | null;
  onUploadImage: () => void | Promise<void>;
  onUploadQcow2: () => void | Promise<void>;
  onRefresh: () => void | Promise<void>;
  showSyncStatus?: boolean;
  mode?: 'images' | 'build-jobs';
}

export interface IolBuildStatusResponse {
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

export interface IolBuildDiagnosticsResponse extends IolBuildStatusResponse {
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

export type ChunkUploadKind = 'docker' | 'qcow2';

export interface ImageChunkUploadInitResponse {
  upload_id: string;
  kind: ChunkUploadKind;
  filename: string;
  total_size: number;
  chunk_size: number;
  total_chunks: number;
}

export interface ImageChunkUploadChunkResponse {
  upload_id: string;
  chunk_index: number;
  bytes_received: number;
  total_received: number;
  progress_percent: number;
  is_complete: boolean;
}

export interface ImageChunkUploadCompleteResponse {
  upload_id: string;
  kind: ChunkUploadKind;
  filename: string;
  status: 'completed' | 'processing' | 'failed' | 'awaiting_confirmation';
  result?: Record<string, unknown> | null;
}

export interface Qcow2DetectionResult {
  detected_device_id: string | null;
  detected_version: string | null;
  confidence: 'high' | 'medium' | 'low' | 'none';
  size_bytes: number | null;
  sha256: string | null;
  suggested_metadata: Record<string, unknown>;
}

export interface PendingQcow2Upload {
  tempId: string;
  filename: string;
  progress: number;
  phase: 'uploading' | 'processing' | 'awaiting_confirmation';
  createdAt: number;
}

export interface ImageManagementLogEntry {
  id: string;
  timestamp: string;
  level: 'info' | 'error';
  category: string;
  phase: string;
  message: string;
  filename?: string;
  details?: string;
}

export type ImageManagementLogFilter = 'all' | 'errors' | 'iso' | 'docker' | 'qcow2';

export const IMAGE_LOG_LIMIT = 200;
export const IMAGE_LOG_LEVEL_COLORS: Record<ImageManagementLogEntry['level'], string> = {
  info: 'text-green-600 dark:text-green-400 bg-green-100 dark:bg-green-900/30',
  error: 'text-red-600 dark:text-red-400 bg-red-100 dark:bg-red-900/30',
};
export const IMAGE_LOG_CATEGORY_COLORS: Record<string, string> = {
  iso: 'text-blue-700 dark:text-blue-300 bg-blue-100 dark:bg-blue-900/30',
  docker: 'text-purple-700 dark:text-purple-300 bg-purple-100 dark:bg-purple-900/30',
  qcow2: 'text-emerald-700 dark:text-emerald-300 bg-emerald-100 dark:bg-emerald-900/30',
};
export const IMAGE_UPLOAD_CHUNK_SIZE = 10 * 1024 * 1024;

export interface IolBuildRow {
  image: import('../../types').ImageLibraryEntry;
  status: 'queued' | 'building' | 'complete' | 'failed' | 'ignored' | 'not_started';
  buildError: string | null;
  buildJobId: string | null;
  buildIgnoredAt: string | null;
  buildIgnoredBy: string | null;
  dockerReference: string | null;
  dockerImageId: string | null;
}

export interface Qcow2ConfirmState {
  uploadId: string;
  filename: string;
  detection: Qcow2DetectionResult;
  deviceIdOverride: string;
  versionOverride: string;
  autoBuild: boolean;
}
