import { useCallback, useState } from 'react';

interface ConfigViewerNode {
  id: string;
  name: string;
}

interface ConfigViewerSnapshot {
  content: string;
  label: string;
}

export function useConfigViewerModal() {
  const [configViewerOpen, setConfigViewerOpen] = useState(false);
  const [configViewerNode, setConfigViewerNode] = useState<ConfigViewerNode | null>(null);
  const [configViewerSnapshot, setConfigViewerSnapshot] = useState<ConfigViewerSnapshot | null>(null);

  const openConfigViewer = useCallback((nodeId?: string, nodeName?: string, snapshotContent?: string, snapshotLabel?: string) => {
    if (nodeId && nodeName) {
      setConfigViewerNode({ id: nodeId, name: nodeName });
    } else {
      setConfigViewerNode(null);
    }
    if (snapshotContent !== undefined && snapshotLabel) {
      setConfigViewerSnapshot({ content: snapshotContent, label: snapshotLabel });
    } else {
      setConfigViewerSnapshot(null);
    }
    setConfigViewerOpen(true);
  }, []);

  const closeConfigViewer = useCallback(() => {
    setConfigViewerOpen(false);
    setConfigViewerNode(null);
    setConfigViewerSnapshot(null);
  }, []);

  return {
    configViewerOpen,
    configViewerNode,
    configViewerSnapshot,
    openConfigViewer,
    closeConfigViewer,
  };
}
