import React, { useState, useEffect, useRef, useCallback } from 'react';
import { API_BASE_URL } from '../api';

interface ISOFileInfo {
  name: string;
  path: string;
  size_bytes: number;
  modified_at: string;
}

interface BrowseResponse {
  upload_dir: string;
  files: ISOFileInfo[];
}

interface UploadInitResponse {
  upload_id: string;
  filename: string;
  total_size: number;
  chunk_size: number;
  total_chunks: number;
  upload_path: string;
}

interface UploadChunkResponse {
  upload_id: string;
  chunk_index: number;
  bytes_received: number;
  total_received: number;
  progress_percent: number;
  is_complete: boolean;
}

interface UploadCompleteResponse {
  upload_id: string;
  filename: string;
  iso_path: string;
  total_size: number;
}

interface ParsedNodeDefinition {
  id: string;
  label: string;
  description: string;
  nature: string;
  vendor: string;
  ram_mb: number;
  cpus: number;
  interfaces: string[];
}

interface ParsedImage {
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

interface ScanResponse {
  session_id: string;
  iso_path: string;
  format: string;
  size_bytes: number;
  node_definitions: ParsedNodeDefinition[];
  images: ParsedImage[];
  parse_errors: string[];
}

interface ImageProgress {
  image_id: string;
  status: string;
  progress_percent: number;
  error_message?: string;
}

interface ISOImportModalProps {
  isOpen: boolean;
  onClose: () => void;
  onImportComplete: () => void;
}

type Step = 'input' | 'uploading' | 'scanning' | 'review' | 'importing' | 'complete';
type InputMode = 'browse' | 'upload' | 'custom';

const CHUNK_SIZE = 10 * 1024 * 1024; // 10MB chunks

const formatBytes = (bytes: number): string => {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
};

const ISOImportModal: React.FC<ISOImportModalProps> = ({ isOpen, onClose, onImportComplete }) => {
  const [step, setStep] = useState<Step>('input');
  const [isoPath, setIsoPath] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [scanResult, setScanResult] = useState<ScanResponse | null>(null);
  const [selectedImages, setSelectedImages] = useState<Set<string>>(new Set());
  const [createDevices, setCreateDevices] = useState(true);
  const [importProgress, setImportProgress] = useState<Record<string, ImageProgress>>({});
  const [overallProgress, setOverallProgress] = useState(0);
  const [importStatus, setImportStatus] = useState<string>('pending');
  const eventSourceRef = useRef<EventSource | null>(null);

  // File browser state
  const [availableISOs, setAvailableISOs] = useState<ISOFileInfo[]>([]);
  const [uploadDir, setUploadDir] = useState<string>('');
  const [loadingISOs, setLoadingISOs] = useState(false);
  const [inputMode, setInputMode] = useState<InputMode>('browse');

  // Chunked upload state
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadStatus, setUploadStatus] = useState<string>('');
  const [uploadId, setUploadId] = useState<string | null>(null);
  const uploadAbortRef = useRef(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const fetchAvailableISOs = useCallback(async () => {
    setLoadingISOs(true);
    try {
      const token = localStorage.getItem('token');
      const response = await fetch(`${API_BASE_URL}/iso/browse`, {
        headers: {
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
      });
      if (response.ok) {
        const data: BrowseResponse = await response.json();
        setAvailableISOs(data.files);
        setUploadDir(data.upload_dir);
      }
    } catch (err) {
      console.error('Failed to fetch ISOs:', err);
    } finally {
      setLoadingISOs(false);
    }
  }, []);

  // Reset state when modal opens
  useEffect(() => {
    if (isOpen) {
      setStep('input');
      setIsoPath('');
      setError(null);
      setScanResult(null);
      setSelectedImages(new Set());
      setCreateDevices(true);
      setImportProgress({});
      setOverallProgress(0);
      setImportStatus('pending');
      setInputMode('browse');
      setSelectedFile(null);
      setUploadProgress(0);
      setUploadStatus('');
      setUploadId(null);
      uploadAbortRef.current = false;
      fetchAvailableISOs();
    }
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
      uploadAbortRef.current = true;
    };
  }, [isOpen, fetchAvailableISOs]);

  const handleScan = async () => {
    if (!isoPath.trim()) {
      setError('Please enter an ISO path');
      return;
    }

    setStep('scanning');
    setError(null);

    try {
      const token = localStorage.getItem('token');
      const response = await fetch(`${API_BASE_URL}/iso/scan`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ iso_path: isoPath.trim() }),
      });

      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || `Scan failed: ${response.status}`);
      }

      const data: ScanResponse = await response.json();
      setScanResult(data);
      // Select all images by default
      setSelectedImages(new Set(data.images.map((img) => img.id)));
      setStep('review');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Scan failed');
      setStep('input');
    }
  };

  const handleImport = async () => {
    if (!scanResult || selectedImages.size === 0) return;

    setStep('importing');
    setError(null);

    try {
      const token = localStorage.getItem('token');
      const response = await fetch(`${API_BASE_URL}/iso/${scanResult.session_id}/import`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          image_ids: Array.from(selectedImages),
          create_devices: createDevices,
        }),
      });

      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || `Import failed: ${response.status}`);
      }

      // Start polling for progress
      pollProgress();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Import failed');
      setStep('review');
    }
  };

  const pollProgress = async () => {
    if (!scanResult) return;

    const token = localStorage.getItem('token');
    const poll = async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/iso/${scanResult.session_id}/progress`, {
          headers: {
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
        });

        if (!response.ok) return;

        const data = await response.json();
        setImportProgress(data.image_progress || {});
        setOverallProgress(data.progress_percent || 0);
        setImportStatus(data.status);

        if (data.status === 'completed') {
          setStep('complete');
          onImportComplete();
        } else if (data.status === 'failed') {
          setError(data.error_message || 'Import failed');
          setStep('review');
        } else if (data.status === 'importing') {
          setTimeout(poll, 1000);
        }
      } catch (err) {
        console.error('Progress poll error:', err);
        setTimeout(poll, 2000);
      }
    };

    poll();
  };

  const handleFileSelect = (file: File) => {
    if (!file.name.toLowerCase().endsWith('.iso')) {
      setError('Please select an ISO file');
      return;
    }
    setSelectedFile(file);
    setError(null);
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (file) {
      handleFileSelect(file);
    }
  };

  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
  };

  const handleUpload = async () => {
    if (!selectedFile) return;

    setStep('uploading');
    setError(null);
    setUploadProgress(0);
    uploadAbortRef.current = false;

    const token = localStorage.getItem('token');
    const headers: Record<string, string> = {};
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    try {
      // Initialize upload
      setUploadStatus('Initializing upload...');
      const initResponse = await fetch(`${API_BASE_URL}/iso/upload/init`, {
        method: 'POST',
        headers: {
          ...headers,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          filename: selectedFile.name,
          total_size: selectedFile.size,
          chunk_size: CHUNK_SIZE,
        }),
      });

      if (!initResponse.ok) {
        const data = await initResponse.json().catch(() => ({}));
        throw new Error(data.detail || `Upload init failed: ${initResponse.status}`);
      }

      const initData: UploadInitResponse = await initResponse.json();
      setUploadId(initData.upload_id);

      // Upload chunks
      const totalChunks = initData.total_chunks;
      let uploadedChunks = 0;

      for (let i = 0; i < totalChunks; i++) {
        if (uploadAbortRef.current) {
          throw new Error('Upload cancelled');
        }

        const start = i * CHUNK_SIZE;
        const end = Math.min(start + CHUNK_SIZE, selectedFile.size);
        const chunk = selectedFile.slice(start, end);

        setUploadStatus(`Uploading chunk ${i + 1} of ${totalChunks}...`);

        const formData = new FormData();
        formData.append('chunk', chunk);

        const chunkResponse = await fetch(
          `${API_BASE_URL}/iso/upload/${initData.upload_id}/chunk?index=${i}`,
          {
            method: 'POST',
            headers,
            body: formData,
          }
        );

        if (!chunkResponse.ok) {
          const data = await chunkResponse.json().catch(() => ({}));
          throw new Error(data.detail || `Chunk ${i} upload failed`);
        }

        const chunkData: UploadChunkResponse = await chunkResponse.json();
        uploadedChunks++;
        setUploadProgress(chunkData.progress_percent);
      }

      // Complete upload
      setUploadStatus('Finalizing upload...');
      const completeResponse = await fetch(
        `${API_BASE_URL}/iso/upload/${initData.upload_id}/complete`,
        {
          method: 'POST',
          headers,
        }
      );

      if (!completeResponse.ok) {
        const data = await completeResponse.json().catch(() => ({}));
        throw new Error(data.detail || 'Upload completion failed');
      }

      const completeData: UploadCompleteResponse = await completeResponse.json();

      // Auto-scan the uploaded ISO
      setIsoPath(completeData.iso_path);
      setUploadStatus('Upload complete! Scanning ISO...');
      setStep('scanning');

      // Now scan the ISO
      const scanResponse = await fetch(`${API_BASE_URL}/iso/scan`, {
        method: 'POST',
        headers: {
          ...headers,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ iso_path: completeData.iso_path }),
      });

      if (!scanResponse.ok) {
        const data = await scanResponse.json().catch(() => ({}));
        throw new Error(data.detail || `Scan failed: ${scanResponse.status}`);
      }

      const scanData: ScanResponse = await scanResponse.json();
      setScanResult(scanData);
      setSelectedImages(new Set(scanData.images.map((img) => img.id)));
      setStep('review');

    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed');
      setStep('input');
      setInputMode('upload');
    }
  };

  const cancelUpload = async () => {
    uploadAbortRef.current = true;
    if (uploadId) {
      const token = localStorage.getItem('token');
      try {
        await fetch(`${API_BASE_URL}/iso/upload/${uploadId}`, {
          method: 'DELETE',
          headers: {
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
        });
      } catch (err) {
        console.error('Failed to cancel upload:', err);
      }
    }
    setStep('input');
    setInputMode('upload');
  };

  const toggleImage = (imageId: string) => {
    const next = new Set(selectedImages);
    if (next.has(imageId)) {
      next.delete(imageId);
    } else {
      next.add(imageId);
    }
    setSelectedImages(next);
  };

  const selectAll = () => {
    if (scanResult) {
      setSelectedImages(new Set(scanResult.images.map((img) => img.id)));
    }
  };

  const selectNone = () => {
    setSelectedImages(new Set());
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} />

      {/* Modal */}
      <div className="relative bg-white dark:bg-stone-900 rounded-xl shadow-2xl w-full max-w-3xl max-h-[85vh] flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="px-6 py-4 border-b border-stone-200 dark:border-stone-800 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-bold text-stone-900 dark:text-white">Import from ISO</h2>
            <p className="text-xs text-stone-500 dark:text-stone-400 mt-0.5">
              Import VM images from vendor ISO files (Cisco RefPlat, etc.)
            </p>
          </div>
          <button
            onClick={onClose}
            className="w-8 h-8 flex items-center justify-center text-stone-400 hover:text-stone-600 dark:hover:text-stone-300 rounded-lg hover:bg-stone-100 dark:hover:bg-stone-800 transition-all"
          >
            <i className="fa-solid fa-xmark" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6">
          {/* Step 1: Input ISO Path */}
          {step === 'input' && (
            <div className="space-y-4">
              {/* Mode tabs */}
              <div className="flex gap-1 p-1 bg-stone-100 dark:bg-stone-800 rounded-lg">
                <button
                  onClick={() => setInputMode('browse')}
                  className={`flex-1 px-3 py-2 text-xs font-bold rounded-md transition-all ${
                    inputMode === 'browse'
                      ? 'bg-white dark:bg-stone-700 text-stone-800 dark:text-white shadow-sm'
                      : 'text-stone-500 hover:text-stone-700 dark:hover:text-stone-300'
                  }`}
                >
                  <i className="fa-solid fa-folder-open mr-2" />
                  Browse Server
                </button>
                <button
                  onClick={() => setInputMode('upload')}
                  className={`flex-1 px-3 py-2 text-xs font-bold rounded-md transition-all ${
                    inputMode === 'upload'
                      ? 'bg-white dark:bg-stone-700 text-stone-800 dark:text-white shadow-sm'
                      : 'text-stone-500 hover:text-stone-700 dark:hover:text-stone-300'
                  }`}
                >
                  <i className="fa-solid fa-upload mr-2" />
                  Upload ISO
                </button>
                <button
                  onClick={() => setInputMode('custom')}
                  className={`flex-1 px-3 py-2 text-xs font-bold rounded-md transition-all ${
                    inputMode === 'custom'
                      ? 'bg-white dark:bg-stone-700 text-stone-800 dark:text-white shadow-sm'
                      : 'text-stone-500 hover:text-stone-700 dark:hover:text-stone-300'
                  }`}
                >
                  <i className="fa-solid fa-keyboard mr-2" />
                  Custom Path
                </button>
              </div>

              {/* Browse Server Mode */}
              {inputMode === 'browse' && (
                <div>
                  <div className="flex items-center justify-between mb-3">
                    <label className="text-xs font-bold text-stone-500 dark:text-stone-400 uppercase tracking-wider">
                      Available ISOs
                    </label>
                    <button
                      onClick={fetchAvailableISOs}
                      className="text-[10px] text-sage-600 dark:text-sage-400 hover:underline font-bold"
                    >
                      <i className="fa-solid fa-rotate mr-1" />
                      Refresh
                    </button>
                  </div>

                  {loadingISOs ? (
                    <div className="flex items-center justify-center py-8">
                      <i className="fa-solid fa-spinner fa-spin text-stone-400 mr-2" />
                      <span className="text-xs text-stone-500">Loading...</span>
                    </div>
                  ) : availableISOs.length > 0 ? (
                    <div className="space-y-2 max-h-48 overflow-y-auto">
                      {availableISOs.map((iso) => (
                        <button
                          key={iso.path}
                          onClick={() => setIsoPath(iso.path)}
                          className={`w-full text-left p-3 rounded-lg border transition-all ${
                            isoPath === iso.path
                              ? 'bg-sage-50 dark:bg-stone-800 border-sage-300 dark:border-sage-600'
                              : 'bg-white dark:bg-stone-800 border-stone-200 dark:border-stone-700 hover:border-stone-300 dark:hover:border-stone-600'
                          }`}
                        >
                          <div className="flex items-center gap-3">
                            <i className="fa-solid fa-compact-disc text-purple-500" />
                            <div className="flex-1 min-w-0">
                              <div className="text-xs font-bold text-stone-700 dark:text-stone-300 truncate">
                                {iso.name}
                              </div>
                              <div className="text-[10px] text-stone-400">
                                {formatBytes(iso.size_bytes)} | {new Date(iso.modified_at).toLocaleDateString()}
                              </div>
                            </div>
                            {isoPath === iso.path && (
                              <i className="fa-solid fa-check text-sage-500" />
                            )}
                          </div>
                        </button>
                      ))}
                    </div>
                  ) : (
                    <div className="text-center py-8 bg-stone-50 dark:bg-stone-800/50 rounded-lg border border-dashed border-stone-300 dark:border-stone-700">
                      <i className="fa-solid fa-folder-open text-2xl text-stone-300 dark:text-stone-600 mb-2" />
                      <p className="text-xs text-stone-500 dark:text-stone-400">No ISOs found in upload directory</p>
                      <p className="text-[10px] text-stone-400 mt-1">
                        Copy ISOs to: <code className="bg-stone-200 dark:bg-stone-700 px-1 rounded">{uploadDir}</code>
                      </p>
                    </div>
                  )}
                </div>
              )}

              {/* Upload Mode */}
              {inputMode === 'upload' && (
                <div>
                  <input
                    type="file"
                    ref={fileInputRef}
                    accept=".iso"
                    className="hidden"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) handleFileSelect(file);
                    }}
                  />

                  <div
                    onDrop={handleDrop}
                    onDragOver={handleDragOver}
                    onClick={() => fileInputRef.current?.click()}
                    className={`cursor-pointer border-2 border-dashed rounded-lg p-8 text-center transition-all ${
                      selectedFile
                        ? 'border-sage-400 dark:border-sage-600 bg-sage-50 dark:bg-stone-800'
                        : 'border-stone-300 dark:border-stone-600 hover:border-sage-400 hover:bg-stone-50 dark:hover:bg-stone-800'
                    }`}
                  >
                    {selectedFile ? (
                      <div>
                        <i className="fa-solid fa-compact-disc text-3xl text-purple-500 mb-3" />
                        <p className="text-sm font-bold text-stone-700 dark:text-stone-300">
                          {selectedFile.name}
                        </p>
                        <p className="text-xs text-stone-500 mt-1">{formatBytes(selectedFile.size)}</p>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setSelectedFile(null);
                          }}
                          className="mt-3 text-xs text-red-500 hover:text-red-600 font-bold"
                        >
                          <i className="fa-solid fa-xmark mr-1" />
                          Remove
                        </button>
                      </div>
                    ) : (
                      <div>
                        <i className="fa-solid fa-cloud-arrow-up text-3xl text-stone-300 dark:text-stone-600 mb-3" />
                        <p className="text-sm font-bold text-stone-600 dark:text-stone-400">
                          Drop ISO file here or click to browse
                        </p>
                        <p className="text-xs text-stone-400 mt-1">
                          Supports resumable chunked uploads for large files
                        </p>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Custom Path Input */}
              {inputMode === 'custom' && (
                <div>
                  <label className="text-xs font-bold text-stone-500 dark:text-stone-400 uppercase tracking-wider block mb-2">
                    Server ISO Path
                  </label>
                  <input
                    type="text"
                    value={isoPath}
                    onChange={(e) => setIsoPath(e.target.value)}
                    placeholder="/path/to/image.iso"
                    className="w-full px-4 py-3 bg-stone-100 dark:bg-stone-800 border border-stone-200 dark:border-stone-700 rounded-lg text-sm text-stone-900 dark:text-white placeholder:text-stone-400 focus:outline-none focus:ring-2 focus:ring-sage-500/50"
                  />
                  <p className="text-[10px] text-stone-400 mt-2">
                    Enter the full path to an ISO file already on the server.
                  </p>
                </div>
              )}

              {error && (
                <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg">
                  <p className="text-xs text-red-600 dark:text-red-400">{error}</p>
                </div>
              )}

              <div className="bg-stone-50 dark:bg-stone-800/50 rounded-lg p-4">
                <h4 className="text-xs font-bold text-stone-600 dark:text-stone-300 mb-2">
                  <i className="fa-solid fa-info-circle mr-2 text-sage-500" />
                  Supported Formats
                </h4>
                <ul className="text-xs text-stone-500 dark:text-stone-400 space-y-1">
                  <li>
                    <i className="fa-solid fa-check text-emerald-500 mr-2" />
                    Cisco VIRL2/CML2 (RefPlat ISOs)
                  </li>
                </ul>
              </div>
            </div>
          )}

          {/* Step 1b: Uploading */}
          {step === 'uploading' && (
            <div className="space-y-6">
              <div className="text-center py-4">
                <i className="fa-solid fa-cloud-arrow-up fa-bounce text-3xl text-sage-500 mb-3" />
                <h3 className="text-sm font-bold text-stone-700 dark:text-stone-300">Uploading ISO...</h3>
                <p className="text-xs text-stone-500 mt-1">
                  {uploadStatus || 'Preparing upload...'}
                </p>
              </div>

              {/* Upload progress */}
              <div>
                <div className="flex justify-between text-xs mb-1">
                  <span className="font-bold text-stone-600 dark:text-stone-400">Upload Progress</span>
                  <span className="text-stone-500">{uploadProgress}%</span>
                </div>
                <div className="h-3 bg-stone-200 dark:bg-stone-700 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-sage-500 transition-all duration-300"
                    style={{ width: `${uploadProgress}%` }}
                  />
                </div>
                {selectedFile && (
                  <p className="text-[10px] text-stone-400 mt-2 text-center">
                    {formatBytes((uploadProgress / 100) * selectedFile.size)} / {formatBytes(selectedFile.size)}
                  </p>
                )}
              </div>

              <div className="text-center">
                <button
                  onClick={cancelUpload}
                  className="px-4 py-2 text-xs font-bold text-red-600 hover:text-red-700 transition-all"
                >
                  <i className="fa-solid fa-xmark mr-2" />
                  Cancel Upload
                </button>
              </div>
            </div>
          )}

          {/* Step 2: Scanning */}
          {step === 'scanning' && (
            <div className="flex flex-col items-center justify-center py-12">
              <i className="fa-solid fa-compact-disc fa-spin text-4xl text-sage-500 mb-4" />
              <h3 className="text-sm font-bold text-stone-700 dark:text-stone-300">Scanning ISO...</h3>
              <p className="text-xs text-stone-500 mt-1">Parsing node definitions and images</p>
            </div>
          )}

          {/* Step 3: Review */}
          {step === 'review' && scanResult && (
            <div className="space-y-6">
              {/* ISO Info */}
              <div className="bg-stone-50 dark:bg-stone-800/50 rounded-lg p-4">
                <div className="flex items-center justify-between">
                  <div>
                    <h4 className="text-xs font-bold text-stone-600 dark:text-stone-300">
                      <i className="fa-solid fa-compact-disc mr-2 text-sage-500" />
                      {scanResult.iso_path.split('/').pop()}
                    </h4>
                    <p className="text-[10px] text-stone-400 mt-1">
                      Format: {scanResult.format.toUpperCase()} | Size: {formatBytes(scanResult.size_bytes)}
                    </p>
                  </div>
                  <div className="text-right">
                    <div className="text-lg font-bold text-stone-700 dark:text-stone-300">
                      {scanResult.images.length}
                    </div>
                    <div className="text-[10px] text-stone-400 uppercase">Images Found</div>
                  </div>
                </div>
              </div>

              {error && (
                <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg">
                  <p className="text-xs text-red-600 dark:text-red-400">{error}</p>
                </div>
              )}

              {/* Node Definitions */}
              {scanResult.node_definitions.length > 0 && (
                <div>
                  <h4 className="text-xs font-bold text-stone-500 dark:text-stone-400 uppercase tracking-wider mb-3">
                    Device Types ({scanResult.node_definitions.length})
                  </h4>
                  <div className="grid grid-cols-2 gap-2">
                    {scanResult.node_definitions.map((nd) => (
                      <div
                        key={nd.id}
                        className="p-3 bg-white dark:bg-stone-800 border border-stone-200 dark:border-stone-700 rounded-lg"
                      >
                        <div className="flex items-center gap-2">
                          <i
                            className={`fa-solid ${
                              nd.nature === 'firewall'
                                ? 'fa-shield-halved text-red-500'
                                : nd.nature === 'router'
                                ? 'fa-arrows-to-dot text-blue-500'
                                : 'fa-server text-stone-500'
                            }`}
                          />
                          <span className="text-xs font-bold text-stone-700 dark:text-stone-300">{nd.label}</span>
                        </div>
                        <p className="text-[10px] text-stone-400 mt-1">
                          {nd.ram_mb}MB RAM | {nd.cpus} vCPUs | {nd.interfaces.length} interfaces
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Images Selection */}
              <div>
                <div className="flex items-center justify-between mb-3">
                  <h4 className="text-xs font-bold text-stone-500 dark:text-stone-400 uppercase tracking-wider">
                    Images to Import ({selectedImages.size} / {scanResult.images.length})
                  </h4>
                  <div className="flex gap-2">
                    <button
                      onClick={selectAll}
                      className="text-[10px] text-sage-600 dark:text-sage-400 hover:underline font-bold"
                    >
                      Select All
                    </button>
                    <button
                      onClick={selectNone}
                      className="text-[10px] text-stone-500 hover:underline font-bold"
                    >
                      Select None
                    </button>
                  </div>
                </div>
                <div className="space-y-2 max-h-64 overflow-y-auto">
                  {scanResult.images.map((img) => {
                    const nd = scanResult.node_definitions.find((n) => n.id === img.node_definition_id);
                    return (
                      <label
                        key={img.id}
                        className={`flex items-center gap-3 p-3 rounded-lg border cursor-pointer transition-all ${
                          selectedImages.has(img.id)
                            ? 'bg-sage-50 dark:bg-stone-800 border-sage-300 dark:border-sage-600'
                            : 'bg-white dark:bg-stone-800 border-stone-200 dark:border-stone-700 hover:border-stone-300 dark:hover:border-stone-600'
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={selectedImages.has(img.id)}
                          onChange={() => toggleImage(img.id)}
                          className="w-4 h-4 rounded border-stone-300 text-sage-600 focus:ring-sage-500"
                        />
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-xs font-bold text-stone-700 dark:text-stone-300 truncate">
                              {img.label || img.id}
                            </span>
                            <span
                              className={`px-1.5 py-0.5 text-[9px] font-bold rounded ${
                                img.image_type === 'qcow2'
                                  ? 'bg-purple-100 dark:bg-purple-900/30 text-purple-600 dark:text-purple-400'
                                  : 'bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400'
                              }`}
                            >
                              {img.image_type.toUpperCase()}
                            </span>
                          </div>
                          <p className="text-[10px] text-stone-400 truncate">
                            {nd?.label || img.node_definition_id} | {img.version || 'unknown version'} |{' '}
                            {formatBytes(img.size_bytes)}
                          </p>
                        </div>
                      </label>
                    );
                  })}
                </div>
              </div>

              {/* Options */}
              <div className="border-t border-stone-200 dark:border-stone-800 pt-4">
                <label className="flex items-center gap-3 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={createDevices}
                    onChange={(e) => setCreateDevices(e.target.checked)}
                    className="w-4 h-4 rounded border-stone-300 text-sage-600 focus:ring-sage-500"
                  />
                  <div>
                    <span className="text-xs font-bold text-stone-700 dark:text-stone-300">
                      Create device types for new definitions
                    </span>
                    <p className="text-[10px] text-stone-400">
                      Automatically create custom device types for node definitions not in the vendor registry
                    </p>
                  </div>
                </label>
              </div>

              {scanResult.parse_errors.length > 0 && (
                <div className="p-3 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg">
                  <h5 className="text-xs font-bold text-amber-600 dark:text-amber-400 mb-1">
                    <i className="fa-solid fa-triangle-exclamation mr-2" />
                    Parse Warnings
                  </h5>
                  <ul className="text-[10px] text-amber-600 dark:text-amber-400 space-y-0.5">
                    {scanResult.parse_errors.map((err, i) => (
                      <li key={i}>{err}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          {/* Step 4: Importing */}
          {step === 'importing' && (
            <div className="space-y-6">
              <div className="text-center py-4">
                <i className="fa-solid fa-download fa-bounce text-3xl text-sage-500 mb-3" />
                <h3 className="text-sm font-bold text-stone-700 dark:text-stone-300">Importing Images...</h3>
                <p className="text-xs text-stone-500 mt-1">
                  This may take a while for large images. Please don't close this window.
                </p>
              </div>

              {/* Overall progress */}
              <div>
                <div className="flex justify-between text-xs mb-1">
                  <span className="font-bold text-stone-600 dark:text-stone-400">Overall Progress</span>
                  <span className="text-stone-500">{overallProgress}%</span>
                </div>
                <div className="h-2 bg-stone-200 dark:bg-stone-700 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-sage-500 transition-all duration-300"
                    style={{ width: `${overallProgress}%` }}
                  />
                </div>
              </div>

              {/* Per-image progress */}
              <div className="space-y-3">
                {Object.entries(importProgress).map(([imageId, progress]) => (
                  <div key={imageId} className="bg-stone-50 dark:bg-stone-800/50 rounded-lg p-3">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs font-medium text-stone-700 dark:text-stone-300 truncate">
                        {imageId}
                      </span>
                      <span
                        className={`text-[10px] font-bold uppercase ${
                          progress.status === 'completed'
                            ? 'text-emerald-500'
                            : progress.status === 'failed'
                            ? 'text-red-500'
                            : 'text-sage-500'
                        }`}
                      >
                        {progress.status === 'extracting' && (
                          <i className="fa-solid fa-spinner fa-spin mr-1" />
                        )}
                        {progress.status}
                      </span>
                    </div>
                    <div className="h-1.5 bg-stone-200 dark:bg-stone-700 rounded-full overflow-hidden">
                      <div
                        className={`h-full transition-all duration-300 ${
                          progress.status === 'completed'
                            ? 'bg-emerald-500'
                            : progress.status === 'failed'
                            ? 'bg-red-500'
                            : 'bg-sage-500'
                        }`}
                        style={{ width: `${progress.progress_percent}%` }}
                      />
                    </div>
                    {progress.error_message && (
                      <p className="text-[10px] text-red-500 mt-1">{progress.error_message}</p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Step 5: Complete */}
          {step === 'complete' && (
            <div className="text-center py-12">
              <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-emerald-100 dark:bg-emerald-900/30 flex items-center justify-center">
                <i className="fa-solid fa-check text-2xl text-emerald-500" />
              </div>
              <h3 className="text-lg font-bold text-stone-700 dark:text-stone-300">Import Complete!</h3>
              <p className="text-xs text-stone-500 mt-2">
                Images have been imported and are ready to use.
              </p>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-stone-200 dark:border-stone-800 flex justify-between">
          <button
            onClick={onClose}
            className="px-4 py-2 text-xs font-bold text-stone-600 dark:text-stone-400 hover:text-stone-800 dark:hover:text-stone-200 transition-all"
          >
            {step === 'complete' ? 'Close' : 'Cancel'}
          </button>

          <div className="flex gap-2">
            {step === 'input' && inputMode !== 'upload' && (
              <button
                onClick={handleScan}
                disabled={!isoPath.trim()}
                className="px-6 py-2 bg-sage-600 hover:bg-sage-500 disabled:bg-stone-300 dark:disabled:bg-stone-700 text-white rounded-lg text-xs font-bold transition-all"
              >
                <i className="fa-solid fa-magnifying-glass mr-2" />
                Scan ISO
              </button>
            )}

            {step === 'input' && inputMode === 'upload' && (
              <button
                onClick={handleUpload}
                disabled={!selectedFile}
                className="px-6 py-2 bg-sage-600 hover:bg-sage-500 disabled:bg-stone-300 dark:disabled:bg-stone-700 text-white rounded-lg text-xs font-bold transition-all"
              >
                <i className="fa-solid fa-upload mr-2" />
                Upload & Scan
              </button>
            )}

            {step === 'review' && (
              <>
                <button
                  onClick={() => setStep('input')}
                  className="px-4 py-2 bg-stone-200 dark:bg-stone-800 hover:bg-stone-300 dark:hover:bg-stone-700 text-stone-700 dark:text-stone-300 rounded-lg text-xs font-bold transition-all"
                >
                  <i className="fa-solid fa-arrow-left mr-2" />
                  Back
                </button>
                <button
                  onClick={handleImport}
                  disabled={selectedImages.size === 0}
                  className="px-6 py-2 bg-sage-600 hover:bg-sage-500 disabled:bg-stone-300 dark:disabled:bg-stone-700 text-white rounded-lg text-xs font-bold transition-all"
                >
                  <i className="fa-solid fa-download mr-2" />
                  Import {selectedImages.size} Image{selectedImages.size !== 1 ? 's' : ''}
                </button>
              </>
            )}

            {step === 'complete' && (
              <button
                onClick={onClose}
                className="px-6 py-2 bg-sage-600 hover:bg-sage-500 text-white rounded-lg text-xs font-bold transition-all"
              >
                Done
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default ISOImportModal;
