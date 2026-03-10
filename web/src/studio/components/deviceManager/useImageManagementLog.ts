import { useCallback, useEffect, useMemo, useState } from 'react';
import { usePersistedState } from '../../hooks/usePersistedState';
import { useModalState } from '../../../hooks/useModalState';
import {
  ImageManagementLogEntry,
  ImageManagementLogFilter,
  IMAGE_LOG_LIMIT,
} from './deviceManagerTypes';

export function useImageManagementLog() {
  const [imageManagementLogs, setImageManagementLogs] = usePersistedState<ImageManagementLogEntry[]>(
    'archetype:image-management:logs',
    []
  );
  const [imageLogFilter, setImageLogFilter] = usePersistedState<ImageManagementLogFilter>(
    'archetype:image-management:log-filter',
    'all'
  );
  const [imageLogSearch, setImageLogSearch] = useState('');
  const uploadLogsModal = useModalState();
  const [copiedUploadLogId, setCopiedUploadLogId] = useState<string | null>(null);

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

  return {
    imageManagementLogs,
    imageLogFilter,
    setImageLogFilter,
    imageLogSearch,
    setImageLogSearch,
    showUploadLogsModal: uploadLogsModal.isOpen,
    setShowUploadLogsModal: (show: boolean) => show ? uploadLogsModal.open() : uploadLogsModal.close(),
    copiedUploadLogId,
    addImageManagementLog,
    clearImageManagementLogs,
    copyUploadLogEntry,
    imageLogCounts,
    filteredImageManagementLogs,
    uploadErrorCount,
  };
}
