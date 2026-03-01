export interface ISOFileInfo {
  name: string;
  path: string;
  size_bytes: number;
  modified_at: string;
}

export interface BrowseResponse {
  upload_dir: string;
  files: ISOFileInfo[];
}

export interface UploadInitResponse {
  upload_id: string;
  filename: string;
  total_size: number;
  chunk_size: number;
  total_chunks: number;
  upload_path: string;
}

export interface UploadChunkResponse {
  upload_id: string;
  chunk_index: number;
  bytes_received: number;
  total_received: number;
  progress_percent: number;
  is_complete: boolean;
}

export interface UploadCompleteResponse {
  upload_id: string;
  filename: string;
  iso_path: string;
  total_size: number;
}

export interface ParsedNodeDefinition {
  id: string;
  label: string;
  description: string;
  nature: string;
  vendor: string;
  ram_mb: number;
  cpus: number;
  interfaces: string[];
}

export interface ParsedImage {
  id: string;
  node_definition_id: string;
  label: string;
  description: string;
  version: string;
  disk_image_filename: string;
  disk_image_path: string;
  size_bytes: number;
  image_type: string;
}

export interface ScanResponse {
  session_id: string;
  iso_path: string;
  format: string;
  size_bytes: number;
  node_definitions: ParsedNodeDefinition[];
  images: ParsedImage[];
  parse_errors: string[];
}

export interface ImageProgress {
  image_id: string;
  status: string;
  progress_percent: number;
  error_message?: string;
}

export interface ISOImportLogEvent {
  level: 'info' | 'error';
  phase: string;
  message: string;
  filename?: string;
  details?: string;
}

export interface ISOImportModalProps {
  isOpen: boolean;
  onClose: () => void;
  onImportComplete: () => void;
  onLogEvent?: (event: ISOImportLogEvent) => void;
}

export type Step = 'input' | 'uploading' | 'scanning' | 'review' | 'importing' | 'complete';
export type InputMode = 'browse' | 'upload' | 'custom';

export const CHUNK_SIZE = 10 * 1024 * 1024; // 10MB chunks

export const formatBytes = (bytes: number): string => {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
};
