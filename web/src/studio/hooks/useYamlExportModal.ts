import { useCallback, useState } from 'react';
import { Annotation, Node } from '../types';
import { LabSummary } from './useLabDataLoading';

interface UseYamlExportModalOptions {
  activeLab: LabSummary | null;
  nodes: Node[];
  annotations: Annotation[];
  studioRequest: <T>(path: string, options?: RequestInit) => Promise<T>;
  saveLayout: (labId: string, nodes: Node[], annotations: Annotation[]) => Promise<void>;
  handleDownloadBundle: (lab: LabSummary) => Promise<void>;
}

export function useYamlExportModal({
  activeLab,
  nodes,
  annotations,
  studioRequest,
  saveLayout,
  handleDownloadBundle,
}: UseYamlExportModalOptions) {
  const [showYamlModal, setShowYamlModal] = useState(false);
  const [yamlContent, setYamlContent] = useState('');

  const handleExport = useCallback(async () => {
    if (!activeLab) return;
    const data = await studioRequest<{ content: string }>(`/labs/${activeLab.id}/export-yaml`);
    setYamlContent(data.content || '');
    setShowYamlModal(true);
  }, [activeLab, studioRequest]);

  const handleExportFull = useCallback(async () => {
    if (!activeLab) return;
    await saveLayout(activeLab.id, nodes, annotations);
    await handleDownloadBundle(activeLab);
  }, [activeLab, nodes, annotations, saveLayout, handleDownloadBundle]);

  const closeYamlModal = useCallback(() => {
    setShowYamlModal(false);
  }, []);

  return {
    showYamlModal,
    yamlContent,
    handleExport,
    handleExportFull,
    closeYamlModal,
  };
}
