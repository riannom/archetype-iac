import { useCallback, useMemo } from 'react';
import {
  Annotation, AnnotationType, CanvasTool, DeviceModel, DeviceNode, ExternalNetworkNode,
  ImageLibraryEntry, Link, Node, TestSpec,
  isDeviceNode, isExternalNetworkNode,
} from '../types';
import { apiRequest } from '../../api';
import { RuntimeStatus } from './useNodeStates';
import { NodeStateEntry } from '../../types/nodeState';
import { TaskLogEntry } from '../components/TaskLogPanel';
import { generateContainerName } from '../studioUtils';
import {
  buildImageCompatibilityAliasMap,
  getAllowedInstantiableImageKinds,
  imageMatchesDeviceId,
  isInstantiableImageKind,
  requiresRunnableImage,
} from '../../utils/deviceModels';

export interface UseTopologyHandlersOptions {
  activeLab: { id: string; name: string } | null;
  nodes: Node[];
  links: Link[];
  annotations: Annotation[];
  deviceModels: DeviceModel[];
  imageLibrary: ImageLibraryEntry[];
  effectiveMode: string;
  runtimeStates: Record<string, RuntimeStatus>;

  setNodes: React.Dispatch<React.SetStateAction<Node[]>>;
  setLinks: React.Dispatch<React.SetStateAction<Link[]>>;
  setAnnotations: React.Dispatch<React.SetStateAction<Annotation[]>>;
  setSelectedId: (id: string | null) => void;
  setActiveTool: (tool: CanvasTool) => void;
  clearSelection: () => void;

  nodesRef: React.MutableRefObject<Node[]>;
  linksRef: React.MutableRefObject<Link[]>;

  triggerLayoutSave: () => void;
  triggerTopologySave: () => void;
  flushTopologySave: () => Promise<void>;

  studioRequest: <T>(path: string, options?: RequestInit) => Promise<T>;
  addTaskLogEntry: (level: TaskLogEntry['level'], message: string, jobId?: string) => void;
  addNotification: (...args: any[]) => void;

  portManager: { getNextInterface: (nodeId: string) => string };

  pendingNodeOps: Set<string>;
  setPendingNodeOps: React.Dispatch<React.SetStateAction<Set<string>>>;
  setNodeStates: React.Dispatch<React.SetStateAction<Record<string, NodeStateEntry>>>;
  optimisticGuardRef: React.MutableRefObject<Map<string, number>>;
  loadJobs: (labId: string, nodes: Node[]) => void;

  setIsTaskLogVisible: React.Dispatch<React.SetStateAction<boolean>>;
  consoleManager: {
    handleOpenConsole: (nodeId: string, setIsTaskLogVisible: React.Dispatch<React.SetStateAction<boolean>>) => void;
    handleDockWindow: (windowId: string, setIsTaskLogVisible: React.Dispatch<React.SetStateAction<boolean>>) => void;
  };

  testRunning: boolean;
  setTestResults: React.Dispatch<React.SetStateAction<any[]>>;
  setTestSummary: React.Dispatch<React.SetStateAction<any>>;
  setTestRunning: React.Dispatch<React.SetStateAction<boolean>>;
  activeScenarioJobId: string | null;
  setActiveScenarioJobId: React.Dispatch<React.SetStateAction<string | null>>;
}

export function useTopologyHandlers(options: UseTopologyHandlersOptions) {
  const {
    activeLab, nodes, links, annotations, deviceModels, imageLibrary, effectiveMode,
    runtimeStates,
    setNodes, setLinks, setAnnotations, setSelectedId, setActiveTool, clearSelection,
    nodesRef, linksRef,
    triggerLayoutSave, triggerTopologySave, flushTopologySave,
    studioRequest, addTaskLogEntry, addNotification,
    portManager,
    pendingNodeOps, setPendingNodeOps, setNodeStates, optimisticGuardRef,
    loadJobs,
    setIsTaskLogVisible, consoleManager,
    testRunning, setTestResults, setTestSummary, setTestRunning,
    activeScenarioJobId, setActiveScenarioJobId,
  } = options;

  const imageCompatibilityAliases = useMemo(
    () => buildImageCompatibilityAliasMap(deviceModels),
    [deviceModels]
  );

  const hasInstantiableImageForModel = useCallback((model: DeviceModel): boolean => {
    const allowedKinds = getAllowedInstantiableImageKinds(model);
    return imageLibrary.some((img) => {
      if (!isInstantiableImageKind(img.kind)) return false;
      const imageKind = (img.kind || '').toLowerCase();
      if (!allowedKinds.has(imageKind)) return false;
      return imageMatchesDeviceId(img, model.id, imageCompatibilityAliases);
    });
  }, [imageLibrary, imageCompatibilityAliases]);

  const handleAddDevice = useCallback((model: DeviceModel, x?: number, y?: number) => {
    if (requiresRunnableImage(model) && !hasInstantiableImageForModel(model)) {
      addNotification(
        'warning',
        'No runnable image assigned',
        `${model.name} has no associated Docker or qcow2 image and cannot be instantiated.`,
      );
      return;
    }

    const id = Math.random().toString(36).slice(2, 9);
    const displayName = `${model.id.toUpperCase()}-${nodes.length + 1}`;
    const newNode: DeviceNode = {
      id,
      nodeType: 'device',
      name: displayName,
      container_name: generateContainerName(displayName),
      type: model.type,
      model: model.id,
      version: model.versions[0],
      x: x ?? 300 + Math.random() * 50,
      y: y ?? 200 + Math.random() * 50,
      cpu: model.cpu || 1,
      memory: model.memory || 1024,
    };
    setNodes((prev) => [...prev, newNode]);
    setSelectedId(id);
    setTimeout(() => triggerTopologySave(), 100);
  }, [nodes.length, hasInstantiableImageForModel, addNotification, setNodes, setSelectedId, triggerTopologySave]);

  const handleAddExternalNetwork = useCallback((x?: number, y?: number) => {
    const id = Math.random().toString(36).slice(2, 9);
    const extNetCount = nodes.filter((n) => isExternalNetworkNode(n)).length;
    const newNode: ExternalNetworkNode = {
      id,
      nodeType: 'external',
      name: `External-${extNetCount + 1}`,
      x: x ?? 350 + Math.random() * 50,
      y: y ?? 250 + Math.random() * 50,
    };
    setNodes((prev) => [...prev, newNode]);
    setSelectedId(id);
    setTimeout(() => triggerTopologySave(), 100);
  }, [nodes, setNodes, setSelectedId, triggerTopologySave]);

  const handleCanvasToolCreate = useCallback((type: AnnotationType, x: number, y: number, opts?: { width?: number; height?: number; targetX?: number; targetY?: number }) => {
    const id = Math.random().toString(36).slice(2, 9);
    const newAnn: Annotation = {
      id,
      type,
      x,
      y,
      text: '',
      color: effectiveMode === 'dark' ? '#3b82f6' : '#2563eb',
      width: opts?.width ?? (type === 'rect' || type === 'circle' ? 100 : undefined),
      height: opts?.height ?? (type === 'rect' ? 60 : undefined),
      targetX: opts?.targetX,
      targetY: opts?.targetY,
    };
    setAnnotations((prev) => [...prev, newAnn]);
    setSelectedId(id);
    setActiveTool('pointer');
    triggerLayoutSave();
  }, [effectiveMode, triggerLayoutSave, setAnnotations, setSelectedId, setActiveTool]);

  const handleNodeMove = useCallback((id: string, x: number, y: number) => {
    setNodes((prev) => prev.map((node) => (node.id === id ? { ...node, x, y } : node)));
    triggerLayoutSave();
  }, [triggerLayoutSave, setNodes]);

  const handleAnnotationMove = useCallback((id: string, x: number, y: number) => {
    setAnnotations((prev) => prev.map((ann) => (ann.id === id ? { ...ann, x, y } : ann)));
    triggerLayoutSave();
  }, [triggerLayoutSave, setAnnotations]);

  const handleConnect = useCallback((sourceId: string, targetId: string) => {
    const exists = links.find(
      (link) => (link.source === sourceId && link.target === targetId) || (link.source === targetId && link.target === sourceId)
    );
    if (exists) return;

    const sourceInterface = portManager.getNextInterface(sourceId);
    const targetInterface = portManager.getNextInterface(targetId);

    const newLink: Link = {
      id: Math.random().toString(36).slice(2, 9),
      source: sourceId,
      target: targetId,
      type: 'p2p',
      sourceInterface,
      targetInterface,
    };
    setLinks((prev) => [...prev, newLink]);
    setSelectedId(newLink.id);
    triggerTopologySave();
  }, [links, portManager, setLinks, setSelectedId, triggerTopologySave]);

  const handleUpdateNode = useCallback((id: string, updates: Partial<Node>) => {
    setNodes((prev) => {
      const next = prev.map((node) => (node.id === id ? { ...node, ...updates } as Node : node));
      nodesRef.current = next;
      return next;
    });
    const deviceUpdates = updates as Partial<DeviceNode>;
    if (updates.name || deviceUpdates.model || deviceUpdates.version || deviceUpdates.host
        || deviceUpdates.cpu !== undefined || deviceUpdates.memory !== undefined) {
      triggerTopologySave();
    }
    const extUpdates = updates as Partial<ExternalNetworkNode>;
    if (extUpdates.managedInterfaceId !== undefined || extUpdates.connectionType || extUpdates.parentInterface || extUpdates.vlanId || extUpdates.bridgeName || extUpdates.host) {
      triggerTopologySave();
    }
  }, [setNodes, nodesRef, triggerTopologySave]);

  const handleUpdateLink = useCallback((id: string, updates: Partial<Link>) => {
    const prevLink = linksRef.current.find((link) => link.id === id);
    if (!prevLink) return;

    const nextLink = { ...prevLink, ...updates };
    setLinks((prev) => prev.map((link) => (link.id === id ? nextLink : link)));

    const interfacesChanged = updates.sourceInterface || updates.targetInterface;
    if (interfacesChanged) {
      triggerTopologySave();
    }

    if (!interfacesChanged || !activeLab) return;

    const sourceNode = nodesRef.current.find((node) => node.id === nextLink.source);
    const targetNode = nodesRef.current.find((node) => node.id === nextLink.target);
    if (!sourceNode || !targetNode) return;
    if (!isDeviceNode(sourceNode) || !isDeviceNode(targetNode)) return;

    const sourceState = runtimeStates[sourceNode.id];
    const targetState = runtimeStates[targetNode.id];
    const isRunning = sourceState === 'running' && targetState === 'running';

    if (!isRunning) return;

    const sourceName = sourceNode.container_name || sourceNode.name;
    const targetName = targetNode.container_name || targetNode.name;

    const oldSourceIface = prevLink.sourceInterface || '';
    const oldTargetIface = prevLink.targetInterface || '';
    const newSourceIface = nextLink.sourceInterface || '';
    const newTargetIface = nextLink.targetInterface || '';

    if (oldSourceIface === newSourceIface && oldTargetIface === newTargetIface) return;
    if (!newSourceIface || !newTargetIface) return;

    const hotSwapLink = async () => {
      try {
        if (oldSourceIface && oldTargetIface) {
          const oldLinkId = `${sourceName}:${oldSourceIface}-${targetName}:${oldTargetIface}`;
          await studioRequest(`/labs/${activeLab.id}/hot-disconnect/${encodeURIComponent(oldLinkId)}`, {
            method: 'DELETE',
          });
        }

        const response = await studioRequest<{ success: boolean; error?: string }>(
          `/labs/${activeLab.id}/hot-connect`,
          {
            method: 'POST',
            body: JSON.stringify({
              source_node: sourceName,
              source_interface: newSourceIface,
              target_node: targetName,
              target_interface: newTargetIface,
            }),
          }
        );

        if (!response.success) {
          throw new Error(response.error || 'Hot-connect failed');
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Hot-connect failed';
        console.error('Hot-connect failed:', error);
        addTaskLogEntry('error', `Hot-connect failed for ${sourceName} ↔ ${targetName}: ${message}`);
        addNotification('error', 'Hot-connect failed', message);

        setLinks((prev) => prev.map((link) => (link.id === id ? prevLink : link)));
        triggerTopologySave();

        if (oldSourceIface && oldTargetIface) {
          try {
            await studioRequest(`/labs/${activeLab.id}/hot-connect`, {
              method: 'POST',
              body: JSON.stringify({
                source_node: sourceName,
                source_interface: oldSourceIface,
                target_node: targetName,
                target_interface: oldTargetIface,
              }),
            });
          } catch (restoreError) {
            console.error('Failed to restore previous link after hot-connect failure:', restoreError);
          }
        }
      }
    };

    void hotSwapLink();
  }, [activeLab, runtimeStates, linksRef, nodesRef, setLinks, triggerTopologySave, studioRequest, addTaskLogEntry, addNotification]);

  const handleUpdateAnnotation = useCallback((id: string, updates: Partial<Annotation>) => {
    setAnnotations((prev) => prev.map((ann) => (ann.id === id ? { ...ann, ...updates } : ann)));
    triggerLayoutSave();
  }, [setAnnotations, triggerLayoutSave]);

  const handleDelete = useCallback((id: string) => {
    const isAnnotation = annotations.some((ann) => ann.id === id);
    const isNode = nodes.some((node) => node.id === id);
    const isLink = links.some((link) => link.id === id);
    setNodes((prev) => prev.filter((node) => node.id !== id));
    setLinks((prev) => prev.filter((link) => link.id !== id && link.source !== id && link.target !== id));
    setAnnotations((prev) => prev.filter((ann) => ann.id !== id));
    clearSelection();
    if (isAnnotation) {
      triggerLayoutSave();
    }
    if (isNode || isLink) {
      triggerTopologySave();
    }
  }, [annotations, nodes, links, setNodes, setLinks, setAnnotations, clearSelection, triggerLayoutSave, triggerTopologySave]);

  const handleUpdateStatus = useCallback(async (nodeId: string, status: RuntimeStatus): Promise<void> => {
    if (!activeLab) return;

    if (pendingNodeOps.has(nodeId)) {
      addTaskLogEntry('info', 'Operation already in progress for this node');
      return;
    }

    const node = nodes.find((n) => n.id === nodeId);
    if (!node) return;
    const nodeName = node.name;

    const desiredState = status === 'stopped' ? 'stopped' : 'running';
    const action = desiredState === 'running' ? 'start' : 'stop';

    setPendingNodeOps((prev) => new Set(prev).add(nodeId));
    addTaskLogEntry('info', `Setting "${nodeName}" to ${desiredState}...`);

    try {
      if (desiredState === 'running') {
        await flushTopologySave();
      }

      const transitionalState = status === 'stopped' ? 'stopping' : 'starting';
      optimisticGuardRef.current.set(nodeId, Date.now() + 5000);
      setNodeStates((prev: Record<string, any>) => ({
        ...prev,
        [nodeId]: {
          ...prev[nodeId],
          actual_state: transitionalState,
          desired_state: desiredState as 'stopped' | 'running',
          display_state: transitionalState,
        },
      }));

      await studioRequest(`/labs/${activeLab.id}/nodes/${encodeURIComponent(nodeId)}/desired-state`, {
        method: 'PUT',
        body: JSON.stringify({ state: desiredState }),
      });

      addTaskLogEntry('success', `${action === 'start' ? 'Starting' : 'Stopping'} "${nodeName}"...`);
      loadJobs(activeLab.id, nodes);
    } catch (error) {
      let message = error instanceof Error ? error.message : 'Action failed';

      if (error instanceof Error) {
        if (message.includes('409') || message.toLowerCase().includes('already in progress') || message.toLowerCase().includes('conflict')) {
          message = 'Another operation is already in progress for this lab';
          addTaskLogEntry('warning', `Cannot ${action} "${nodeName}": ${message}`);
          return;
        }
        if (message.includes('503') || message.toLowerCase().includes('try again later')) {
          message = 'Service temporarily unavailable, please try again';
          addTaskLogEntry('warning', `Cannot ${action} "${nodeName}": ${message}`);
          return;
        }
      }

      console.error('Node action failed:', error);
      setNodeStates((prev: Record<string, any>) => ({
        ...prev,
        [nodeId]: { ...prev[nodeId], actual_state: 'error', error_message: message },
      }));
      addTaskLogEntry('error', `Node ${action} failed for "${nodeName}": ${message}`);
    } finally {
      setPendingNodeOps((prev) => {
        const next = new Set(prev);
        next.delete(nodeId);
        return next;
      });
    }
  }, [activeLab, nodes, pendingNodeOps, studioRequest, addTaskLogEntry, loadJobs, flushTopologySave, setPendingNodeOps, setNodeStates, optimisticGuardRef]);

  const handleOpenConsole = useCallback((nodeId: string) => {
    consoleManager.handleOpenConsole(nodeId, setIsTaskLogVisible);
  }, [consoleManager, setIsTaskLogVisible]);

  const handleDockWindow = useCallback((windowId: string) => {
    consoleManager.handleDockWindow(windowId, setIsTaskLogVisible);
  }, [consoleManager, setIsTaskLogVisible]);

  const handleExtractNodeConfig = useCallback(async (nodeId: string) => {
    if (!activeLab) return;
    const node = nodes.find((n) => n.id === nodeId);
    if (!node || !isDeviceNode(node)) return;

    addTaskLogEntry('info', `Extracting config for "${node.name}"...`);
    try {
      const result = await studioRequest<{ message: string }>(
        `/labs/${activeLab.id}/nodes/${encodeURIComponent(nodeId)}/extract-config?create_snapshot=true&snapshot_type=manual`,
        { method: 'POST' }
      );
      addTaskLogEntry('success', `Config extracted successfully for "${node.name}"`);
      addNotification(
        'success',
        `Config extracted: ${node.name}`,
        result.message || `Config extracted successfully for "${node.name}"`,
        { labId: activeLab.id, category: 'config-extract-node-success' }
      );
      if (result.message) {
        addTaskLogEntry('info', result.message);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Extract failed';
      addTaskLogEntry('error', `Config extraction failed for "${node.name}": ${message}`);
      addNotification(
        'error',
        `Config extract failed: ${node.name}`,
        message,
        { labId: activeLab.id, category: 'config-extract-node-failed' }
      );
    }
  }, [activeLab, nodes, studioRequest, addTaskLogEntry, addNotification]);

  const handleStartTests = useCallback(async (specs?: TestSpec[]) => {
    if (!activeLab?.id || testRunning) return;
    setTestResults([]);
    setTestSummary(null);
    setTestRunning(true);
    try {
      const requestOptions: RequestInit = { method: 'POST' };
      if (specs && specs.length > 0) {
        requestOptions.body = JSON.stringify({ specs });
      }
      await apiRequest(`/labs/${activeLab.id}/tests/run`, requestOptions);
    } catch (e: any) {
      setTestRunning(false);
      addNotification('error', 'Test run failed', e.message || 'Failed to start tests');
    }
  }, [activeLab?.id, testRunning, addNotification, setTestResults, setTestSummary, setTestRunning]);

  const handleStartScenario = useCallback(async (filename: string) => {
    if (!activeLab?.id || activeScenarioJobId) return;
    try {
      const data = await apiRequest<{ job_id: string }>(`/labs/${activeLab.id}/scenarios/${filename}/execute`, { method: 'POST' });
      setActiveScenarioJobId(data.job_id);
    } catch (e: any) {
      addNotification('error', 'Scenario failed', e.message || 'Failed to start scenario');
    }
  }, [activeLab?.id, activeScenarioJobId, addNotification, setActiveScenarioJobId]);

  const handleExtractConfigs = useCallback(async () => {
    if (!activeLab) return;
    addTaskLogEntry('info', 'Extracting configs...');
    try {
      const result = await studioRequest<{ success: boolean; extracted_count: number; snapshots_created: number; message: string }>(
        `/labs/${activeLab.id}/extract-configs?create_snapshot=true&snapshot_type=manual`,
        { method: 'POST' }
      );
      addTaskLogEntry('success', result.message);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Extract failed';
      addTaskLogEntry('error', `Extract failed: ${message}`);
      throw error;
    }
  }, [activeLab, studioRequest, addTaskLogEntry]);

  return {
    handleAddDevice,
    handleAddExternalNetwork,
    handleCanvasToolCreate,
    handleNodeMove,
    handleAnnotationMove,
    handleConnect,
    handleUpdateNode,
    handleUpdateLink,
    handleUpdateAnnotation,
    handleDelete,
    handleUpdateStatus,
    handleOpenConsole,
    handleDockWindow,
    handleExtractNodeConfig,
    handleStartTests,
    handleStartScenario,
    handleExtractConfigs,
  };
}
