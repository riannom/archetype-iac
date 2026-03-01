import { useCallback, useEffect, useRef, useState } from 'react';
import { apiRequest, rawApiRequest } from '../../../api';
import { ImageLibraryEntry } from '../../types';
import type { ISOImportLogEvent } from '../../../components/ISOImportModal';
import {
  ImageChunkUploadCompleteResponse,
  ImageChunkUploadChunkResponse,
  ImageChunkUploadInitResponse,
  ImageManagementLogEntry,
  PendingQcow2Upload,
  Qcow2ConfirmState,
  Qcow2DetectionResult,
  ChunkUploadKind,
  IMAGE_UPLOAD_CHUNK_SIZE,
} from './deviceManagerTypes';
import { parseErrorMessage } from './deviceManagerUtils';

interface UseImageUploadArgs {
  imageLibrary: ImageLibraryEntry[];
  onUploadImage: () => void;
  onUploadQcow2: () => void;
  onRefresh: () => void;
  addImageManagementLog: (entry: Omit<ImageManagementLogEntry, 'id' | 'timestamp'>) => void;
}

export function useImageUpload({
  imageLibrary,
  onUploadImage,
  onUploadQcow2,
  onRefresh,
  addImageManagementLog,
}: UseImageUploadArgs) {
  const [uploadStatus, setUploadStatus] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [qcow2Progress, setQcow2Progress] = useState<number | null>(null);
  const [isQcow2PostProcessing, setIsQcow2PostProcessing] = useState(false);
  const [pendingQcow2Uploads, setPendingQcow2Uploads] = useState<PendingQcow2Upload[]>([]);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const qcow2InputRef = useRef<HTMLInputElement | null>(null);
  const [showISOModal, setShowISOModal] = useState(false);
  // Two-phase qcow2 upload confirmation state
  const [qcow2Confirm, setQcow2Confirm] = useState<Qcow2ConfirmState | null>(null);

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

  function openFilePicker() {
    fileInputRef.current?.click();
  }

  function openQcow2Picker() {
    qcow2InputRef.current?.click();
  }

  async function uploadFileInChunks(
    kind: ChunkUploadKind,
    file: File,
    onProgress: (percent: number, message: string) => void,
    options?: { autoBuild?: boolean; autoConfirm?: boolean }
  ): Promise<ImageChunkUploadCompleteResponse> {
    onProgress(0, 'Initializing upload...');
    const initResponse = await rawApiRequest('/images/upload/init', {
      method: 'POST',
      headers: {
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

      const chunkResponse = await rawApiRequest(`/images/upload/${initData.upload_id}/chunk?index=${i}`, {
        method: 'POST',
        body: formData,
      });

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
    const completeResponse = await rawApiRequest(`/images/upload/${initData.upload_id}/complete`, {
      method: 'POST',
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

      const progressResponse = await rawApiRequest(`/images/load/${completeData.upload_id}/progress`);

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
    rawApiRequest(`/images/upload/${uploadId}`, {
      method: 'DELETE',
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

  return {
    uploadStatus,
    uploadProgress,
    qcow2Progress,
    isQcow2PostProcessing,
    pendingQcow2Uploads,
    fileInputRef,
    qcow2InputRef,
    showISOModal,
    setShowISOModal,
    qcow2Confirm,
    setQcow2Confirm,
    handleIsoLogEvent,
    openFilePicker,
    openQcow2Picker,
    uploadImage,
    uploadQcow2,
    confirmQcow2Upload,
    cancelQcow2Confirm,
    setUploadStatus,
  };
}
