import { useCallback, useState } from 'react';

export function useJobLogModal() {
  const [jobLogModalOpen, setJobLogModalOpen] = useState(false);
  const [jobLogModalJobId, setJobLogModalJobId] = useState<string | null>(null);

  const openJobLogModal = useCallback((jobId: string) => {
    setJobLogModalJobId(jobId);
    setJobLogModalOpen(true);
  }, []);

  const closeJobLogModal = useCallback(() => {
    setJobLogModalOpen(false);
    setJobLogModalJobId(null);
  }, []);

  return {
    jobLogModalOpen,
    jobLogModalJobId,
    openJobLogModal,
    closeJobLogModal,
  };
}
