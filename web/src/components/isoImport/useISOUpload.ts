import { useState, useRef, useCallback } from 'react';
import { rawApiRequest } from '../../api';
import {
  ISOImportLogEvent,
  ISOFileInfo,
  BrowseResponse,
  UploadInitResponse,
  UploadChunkResponse,
  UploadCompleteResponse,
  ScanResponse,
  ImageProgress,
  Step,
  InputMode,
  CHUNK_SIZE,
} from './types';

interface UseISOUploadArgs {
  logEvent: (event: ISOImportLogEvent) => void;
}

export function useISOUpload({ logEvent }: UseISOUploadArgs) {
  const [step, setStep] = useState<Step>('input');
  const [isoPath, setIsoPath] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [scanResult, setScanResult] = useState<ScanResponse | null>(null);
  const [selectedImages, setSelectedImages] = useState<Set<string>>(new Set());
  const [createDevices, setCreateDevices] = useState(true);
  const [importProgress, setImportProgress] = useState<Record<string, ImageProgress>>({});
  const [overallProgress, setOverallProgress] = useState(0);
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
  const lastLoggedImportStatusRef = useRef<string | null>(null);

  const fetchAvailableISOs = useCallback(async () => {
    setLoadingISOs(true);
    try {
      const response = await rawApiRequest('/iso/browse');
      if (response.ok) {
        const data: BrowseResponse = await response.json();
        setAvailableISOs(data.files);
        setUploadDir(data.upload_dir);
      }
    } catch (err) {
      console.error('Failed to fetch ISOs:', err);
      logEvent({
        level: 'error',
        phase: 'browse',
        message: 'Failed to fetch available ISOs',
        details: err instanceof Error ? err.stack || err.message : String(err),
      });
    } finally {
      setLoadingISOs(false);
    }
  }, [logEvent]);

  const resetState = useCallback(() => {
    setStep('input');
    setIsoPath('');
    setError(null);
    setScanResult(null);
    setSelectedImages(new Set());
    setCreateDevices(true);
    setImportProgress({});
    setOverallProgress(0);
    setInputMode('browse');
    setSelectedFile(null);
    setUploadProgress(0);
    setUploadStatus('');
    setUploadId(null);
    uploadAbortRef.current = false;
    lastLoggedImportStatusRef.current = null;
  }, []);

  const cleanup = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }
    uploadAbortRef.current = true;
  }, []);

  const handleScan = async () => {
    if (!isoPath.trim()) {
      setError('Please enter an ISO path');
      return;
    }

    setStep('scanning');
    setError(null);
    logEvent({
      level: 'info',
      phase: 'scan_start',
      message: 'Started ISO scan',
      filename: isoPath.split('/').pop() || isoPath,
      details: isoPath.trim(),
    });

    try {
      const response = await rawApiRequest('/iso/scan', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
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
      logEvent({
        level: 'info',
        phase: 'scan_complete',
        message: `ISO scan complete (${data.images.length} images found)`,
        filename: data.iso_path.split('/').pop() || data.iso_path,
        details:
          data.parse_errors.length > 0
            ? `Warnings:\n${data.parse_errors.join('\n')}`
            : `Session: ${data.session_id}`,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Scan failed');
      setStep('input');
      logEvent({
        level: 'error',
        phase: 'scan_failed',
        message: err instanceof Error ? err.message : 'ISO scan failed',
        filename: isoPath.split('/').pop() || isoPath,
        details: err instanceof Error ? err.stack || err.message : String(err),
      });
    }
  };

  const handleImport = async (onImportComplete: () => void) => {
    if (!scanResult || selectedImages.size === 0) return;

    setStep('importing');
    setError(null);
    lastLoggedImportStatusRef.current = null;
    logEvent({
      level: 'info',
      phase: 'import_start',
      message: `Started ISO import (${selectedImages.size} selected image${selectedImages.size === 1 ? '' : 's'})`,
      filename: scanResult.iso_path.split('/').pop() || scanResult.iso_path,
      details: `create_devices=${createDevices}`,
    });

    try {
      const response = await rawApiRequest(`/iso/${scanResult.session_id}/import`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
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

      // Start polling for progress — wrap in a watcher that calls onImportComplete
      const scanResultRef = scanResult;
      const pollInner = async () => {
        try {
          const response = await rawApiRequest(`/iso/${scanResultRef.session_id}/progress`);

          if (!response.ok) return;

          const data = await response.json();
          setImportProgress(data.image_progress || {});
          setOverallProgress(data.progress_percent || 0);

          if (data.status && data.status !== lastLoggedImportStatusRef.current) {
            lastLoggedImportStatusRef.current = data.status;
            if (data.status === 'importing') {
              logEvent({
                level: 'info',
                phase: 'importing',
                message: `ISO import in progress (${data.progress_percent || 0}%)`,
                filename: scanResultRef.iso_path.split('/').pop() || scanResultRef.iso_path,
              });
            } else if (data.status === 'completed') {
              logEvent({
                level: 'info',
                phase: 'import_complete',
                message: 'ISO import completed',
                filename: scanResultRef.iso_path.split('/').pop() || scanResultRef.iso_path,
              });
            } else if (data.status === 'failed') {
              logEvent({
                level: 'error',
                phase: 'import_failed',
                message: data.error_message || 'ISO import failed',
                filename: scanResultRef.iso_path.split('/').pop() || scanResultRef.iso_path,
              });
            }
          }

          if (data.status === 'completed') {
            setStep('complete');
            onImportComplete();
          } else if (data.status === 'failed') {
            setError(data.error_message || 'Import failed');
            setStep('review');
          } else if (data.status === 'importing') {
            setTimeout(pollInner, 1000);
          }
        } catch (err) {
          console.error('Progress poll error:', err);
          logEvent({
            level: 'error',
            phase: 'import_poll_error',
            message: 'ISO import progress poll failed',
            filename: scanResultRef.iso_path.split('/').pop() || scanResultRef.iso_path,
            details: err instanceof Error ? err.stack || err.message : String(err),
          });
          setTimeout(pollInner, 2000);
        }
      };

      pollInner();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Import failed');
      setStep('review');
      logEvent({
        level: 'error',
        phase: 'import_start_failed',
        message: err instanceof Error ? err.message : 'Import failed',
        filename: scanResult.iso_path.split('/').pop() || scanResult.iso_path,
        details: err instanceof Error ? err.stack || err.message : String(err),
      });
    }
  };

  const handleFileSelect = (file: File) => {
    if (!file.name.toLowerCase().endsWith('.iso')) {
      setError('Please select an ISO file');
      logEvent({
        level: 'error',
        phase: 'upload_file_validation',
        message: 'Rejected selected file: not an ISO',
        filename: file.name,
      });
      return;
    }
    setSelectedFile(file);
    setError(null);
    logEvent({
      level: 'info',
      phase: 'upload_file_selected',
      message: 'ISO file selected for upload',
      filename: file.name,
      details: `size=${file.size}`,
    });
  };

  const handleUpload = async () => {
    if (!selectedFile) return;

    setStep('uploading');
    setError(null);
    setUploadProgress(0);
    uploadAbortRef.current = false;
    logEvent({
      level: 'info',
      phase: 'upload_start',
      message: 'Started ISO chunked upload',
      filename: selectedFile.name,
      details: `size=${selectedFile.size}`,
    });

    try {
      // Initialize upload
      setUploadStatus('Initializing upload...');
      const initResponse = await rawApiRequest('/iso/upload/init', {
        method: 'POST',
        headers: {
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
      logEvent({
        level: 'info',
        phase: 'upload_initialized',
        message: 'ISO upload session initialized',
        filename: selectedFile.name,
        details: `upload_id=${initData.upload_id}, chunks=${initData.total_chunks}`,
      });

      // Upload chunks
      const totalChunks = initData.total_chunks;

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

        const chunkResponse = await rawApiRequest(`/iso/upload/${initData.upload_id}/chunk?index=${i}`, {
          method: 'POST',
          body: formData,
        });

        if (!chunkResponse.ok) {
          const data = await chunkResponse.json().catch(() => ({}));
          throw new Error(data.detail || `Chunk ${i} upload failed`);
        }

        const chunkData: UploadChunkResponse = await chunkResponse.json();
        setUploadProgress(chunkData.progress_percent);
      }

      // Complete upload
      setUploadStatus('Finalizing upload...');
      const completeResponse = await rawApiRequest(`/iso/upload/${initData.upload_id}/complete`, {
        method: 'POST',
      });

      if (!completeResponse.ok) {
        const data = await completeResponse.json().catch(() => ({}));
        throw new Error(data.detail || 'Upload completion failed');
      }

      const completeData: UploadCompleteResponse = await completeResponse.json();
      logEvent({
        level: 'info',
        phase: 'upload_complete',
        message: 'ISO upload completed; starting scan',
        filename: completeData.filename || selectedFile.name,
        details: completeData.iso_path,
      });

      // Auto-scan the uploaded ISO
      setIsoPath(completeData.iso_path);
      setUploadStatus('Upload complete! Scanning ISO...');
      setStep('scanning');

      // Now scan the ISO
      const scanResponse = await rawApiRequest('/iso/scan', {
        method: 'POST',
        headers: {
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
      logEvent({
        level: 'info',
        phase: 'upload_scan_complete',
        message: `Uploaded ISO scanned successfully (${scanData.images.length} images found)`,
        filename: scanData.iso_path.split('/').pop() || scanData.iso_path,
      });

    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed');
      setStep('input');
      setInputMode('upload');
      logEvent({
        level: 'error',
        phase: 'upload_failed',
        message: err instanceof Error ? err.message : 'ISO upload failed',
        filename: selectedFile.name,
        details: err instanceof Error ? err.stack || err.message : String(err),
      });
    }
  };

  const cancelUpload = async () => {
    uploadAbortRef.current = true;
    const cancelledUploadId = uploadId;
    const cancelledFilename = selectedFile?.name;
    if (uploadId) {
      try {
        await rawApiRequest(`/iso/upload/${uploadId}`, {
          method: 'DELETE',
        });
      } catch (err) {
        console.error('Failed to cancel upload:', err);
        logEvent({
          level: 'error',
          phase: 'upload_cancel_failed',
          message: 'Failed to cancel ISO upload on server',
          filename: cancelledFilename,
          details: err instanceof Error ? err.stack || err.message : String(err),
        });
      }
    }
    setStep('input');
    setInputMode('upload');
    logEvent({
      level: 'info',
      phase: 'upload_cancelled',
      message: 'ISO upload cancelled by user',
      filename: cancelledFilename,
      details: cancelledUploadId ? `upload_id=${cancelledUploadId}` : undefined,
    });
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

  return {
    step,
    setStep,
    isoPath,
    setIsoPath,
    error,
    scanResult,
    selectedImages,
    createDevices,
    setCreateDevices,
    importProgress,
    overallProgress,
    availableISOs,
    uploadDir,
    loadingISOs,
    inputMode,
    setInputMode,
    selectedFile,
    setSelectedFile,
    uploadProgress,
    uploadStatus,
    fileInputRef,
    fetchAvailableISOs,
    resetState,
    cleanup,
    handleScan,
    handleImport,
    handleFileSelect,
    handleUpload,
    cancelUpload,
    toggleImage,
    selectAll,
    selectNone,
  };
}
