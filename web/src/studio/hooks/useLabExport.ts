import { useCallback } from 'react';
import { rawApiRequest } from '../../api';
import { downloadBlob } from '../../utils/download';
import { LabSummary } from './useLabDataLoading';
import type { NotificationLevel, Notification } from '../../types/notifications';

interface UseLabExportOptions {
  addNotification: (level: NotificationLevel, title: string, message?: string, options?: Partial<Notification>) => void;
}

export function useLabExport({ addNotification }: UseLabExportOptions) {
  const handleDownloadBundle = useCallback(async (lab: LabSummary) => {
    try {
      const response = await rawApiRequest(`/labs/${lab.id}/download-bundle`);
      if (!response.ok) {
        let errorMessage = 'Bundle download failed';
        try {
          const err = await response.json();
          if (err?.detail) {
            errorMessage = String(err.detail);
          }
        } catch {
          // Ignore parse failures and use default message.
        }
        throw new Error(errorMessage);
      }

      const blob = await response.blob();
      const filename =
        response.headers.get('Content-Disposition')?.split('filename=')[1] ||
        `${lab.name.replace(/\s+/g, '_')}_bundle.zip`;
      downloadBlob(blob, filename);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Bundle download failed';
      addNotification('error', 'Download failed', message);
      console.error('Bundle download failed:', error);
    }
  }, [addNotification]);

  return { handleDownloadBundle };
}
