import { useCallback } from 'react';
import { useModalState } from '../../hooks/useModalState';
import { TaskLogEntry } from '../components/TaskLogPanel';

interface ConfigViewerData {
  node: { id: string; name: string } | null;
  snapshot: { content: string; label: string } | null;
}

interface JobLogData {
  jobId: string;
}

export function useStudioModals() {
  const configViewer = useModalState<ConfigViewerData>();
  const jobLog = useModalState<JobLogData>();
  const taskLogEntry = useModalState<TaskLogEntry>();
  const yamlPreview = useModalState<string>();

  const handleOpenConfigViewer = useCallback(
    (nodeId?: string, nodeName?: string, snapshotContent?: string, snapshotLabel?: string) => {
      const node = nodeId && nodeName ? { id: nodeId, name: nodeName } : null;
      const snapshot =
        snapshotContent !== undefined && snapshotLabel
          ? { content: snapshotContent, label: snapshotLabel }
          : null;
      configViewer.open({ node, snapshot });
    },
    [configViewer]
  );

  const handleTaskLogEntryClick = useCallback(
    (entry: TaskLogEntry) => {
      if (entry.jobId) {
        jobLog.open({ jobId: entry.jobId });
        return;
      }
      taskLogEntry.open(entry);
    },
    [jobLog, taskLogEntry]
  );

  return {
    // Config viewer
    configViewerOpen: configViewer.isOpen,
    configViewerNode: configViewer.data?.node ?? null,
    configViewerSnapshot: configViewer.data?.snapshot ?? null,
    handleOpenConfigViewer,
    handleCloseConfigViewer: configViewer.close,

    // Job log modal
    jobLogModalOpen: jobLog.isOpen,
    jobLogModalJobId: jobLog.data?.jobId ?? null,
    handleCloseJobLogModal: jobLog.close,

    // Task log entry modal
    taskLogEntryModalOpen: taskLogEntry.isOpen,
    taskLogEntryModalEntry: taskLogEntry.data ?? null,
    handleCloseTaskLogEntryModal: taskLogEntry.close,

    // Shared handler
    handleTaskLogEntryClick,

    // YAML preview modal
    showYamlModal: yamlPreview.isOpen,
    yamlContent: yamlPreview.data ?? '',
    openYamlPreview: yamlPreview.open,
    closeYamlPreview: yamlPreview.close,
  };
}
