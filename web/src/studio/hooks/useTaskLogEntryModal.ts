import { useCallback, useState } from 'react';
import { TaskLogEntry } from '../components/TaskLogPanel';

interface UseTaskLogEntryModalOptions {
  openJobLogModal: (jobId: string) => void;
}

export function useTaskLogEntryModal({ openJobLogModal }: UseTaskLogEntryModalOptions) {
  const [taskLogEntryModalOpen, setTaskLogEntryModalOpen] = useState(false);
  const [taskLogEntryModalEntry, setTaskLogEntryModalEntry] = useState<TaskLogEntry | null>(null);

  const handleTaskLogEntryClick = useCallback((entry: TaskLogEntry) => {
    if (entry.jobId) {
      openJobLogModal(entry.jobId);
      return;
    }
    setTaskLogEntryModalEntry(entry);
    setTaskLogEntryModalOpen(true);
  }, [openJobLogModal]);

  const closeTaskLogEntryModal = useCallback(() => {
    setTaskLogEntryModalOpen(false);
    setTaskLogEntryModalEntry(null);
  }, []);

  return {
    taskLogEntryModalOpen,
    taskLogEntryModalEntry,
    handleTaskLogEntryClick,
    closeTaskLogEntryModal,
  };
}
