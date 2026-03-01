import React, { useCallback, useEffect, useMemo, useState, useRef } from 'react';
import Sidebar, { SidebarTab } from './components/Sidebar';
import Canvas from './components/Canvas';
import TopBar from './components/TopBar';
import PropertiesPanel from './components/PropertiesPanel';
import ConsoleManager from './components/ConsoleManager';
import RuntimeControl from './components/RuntimeControl';
import TaskLogPanel, { TaskLogEntry } from './components/TaskLogPanel';
import Auth from './components/Auth';
import Dashboard from './components/Dashboard';
import SystemStatusStrip from './components/SystemStatusStrip';
import AgentAlertBanner from './components/AgentAlertBanner';
import ConfigViewerModal from './components/ConfigViewerModal';
import JobLogModal from './components/JobLogModal';
import TaskLogEntryModal from './components/TaskLogEntryModal';
import ConfigsView from './components/ConfigsView';
import LogsView from './components/LogsView';
import VerificationPanel from './components/VerificationPanel';
import ScenarioPanel from './components/ScenarioPanel';
import InfraView from './components/InfraView';
import { Annotation, AnnotationType, CanvasTool, DeviceModel, Link, Node, ExternalNetworkNode, DeviceNode, isExternalNetworkNode, isDeviceNode } from './types';
import { API_BASE_URL, apiRequest, rawApiRequest } from '../api';
import { usePortManager } from './hooks/usePortManager';
import { useLabStateWS } from './hooks/useLabStateWS';
import { useTheme } from '../theme/index';
import { useUser } from '../contexts/UserContext';
import { canViewInfrastructure } from '../utils/permissions';
import { useNotifications } from '../contexts/NotificationContext';
import { useImageLibrary } from '../contexts/ImageLibraryContext';
import { downloadBlob } from '../utils/download';
import { useDeviceCatalog } from '../contexts/DeviceCatalogContext';
import {
  buildImageCompatibilityAliasMap,
  getAllowedInstantiableImageKinds,
  imageMatchesDeviceId,
  isInstantiableImageKind,
  requiresRunnableImage,
} from '../utils/deviceModels';
import { generateContainerName } from './studioUtils';
import { useLabTopology } from './hooks/useLabTopology';
import { useNodeStates, RuntimeStatus } from './hooks/useNodeStates';
import { useConsoleManager } from './hooks/useConsoleManager';
import { useJobTracking } from './hooks/useJobTracking';
import { useLabDataLoading, LabSummary } from './hooks/useLabDataLoading';
import './studio.css';
import 'xterm/css/xterm.css';

const StudioPage: React.FC = () => {
  const { effectiveMode } = useTheme();
  const { user, refreshUser, clearUser } = useUser();
  const { addNotification, preferences } = useNotifications();
  const { imageLibrary } = useImageLibrary();
  const { deviceModels, deviceCategories, refresh: refreshDeviceCatalog } = useDeviceCatalog();
  const showAdminStrip = canViewInfrastructure(user ?? null);
  const [activeLab, setActiveLab] = useState<LabSummary | null>(null);
  const [view, setView] = useState<'designer' | 'configs' | 'logs' | 'runtime' | 'tests' | 'scenarios' | 'infra'>('designer');
  const isDesignerView = view === 'designer';
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarTab, setSidebarTab] = useState<SidebarTab>('library');
  const [focusNodeId, setFocusNodeId] = useState<string | null>(null);
  const [activeTool, setActiveTool] = useState<CanvasTool>('pointer');
  const [showYamlModal, setShowYamlModal] = useState(false);
  const [yamlContent, setYamlContent] = useState('');
  const [showAgentIndicators, setShowAgentIndicators] = useState<boolean>(() => {
    return localStorage.getItem('archetype_show_agent_indicators') !== 'false';
  });
  const [authRequired, setAuthRequired] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [authLoading, setAuthLoading] = useState(false);
  // Config viewer modal state
  const [configViewerOpen, setConfigViewerOpen] = useState(false);
  const [configViewerNode, setConfigViewerNode] = useState<{ id: string; name: string } | null>(null);
  const [configViewerSnapshot, setConfigViewerSnapshot] = useState<{ content: string; label: string } | null>(null);
  // Job log modal state
  const [jobLogModalOpen, setJobLogModalOpen] = useState(false);
  const [jobLogModalJobId, setJobLogModalJobId] = useState<string | null>(null);
  // Task log entry modal (for non-job entries)
  const [taskLogEntryModalOpen, setTaskLogEntryModalOpen] = useState(false);
  const [taskLogEntryModalEntry, setTaskLogEntryModalEntry] = useState<TaskLogEntry | null>(null);

  const initialNodeSyncLabRef = useRef<string | null>(null);

  const isUnauthorized = (error: unknown) => error instanceof Error && error.message.toLowerCase().includes('unauthorized');

  const studioRequest = useCallback(
    async <T,>(path: string, options: RequestInit = {}) => {
      try {
        return await apiRequest<T>(path, options);
      } catch (error) {
        if (isUnauthorized(error)) {
          setAuthRequired(true);
        }
        throw error;
      }
    },
    []
  );

  // --- Hook compositions ---

  const {
    labs, setLabs, agents, labStatuses, systemMetrics,
    loadLabs,
  } = useLabDataLoading({ studioRequest, activeLab });

  const {
    loadJobs,
    addTaskLogEntry, clearTaskLog, filteredTaskLog,
    isTaskLogVisible, setIsTaskLogVisible,
    taskLogAutoRefresh, setTaskLogAutoRefresh,
    handleWSJobProgress, handleWSTestResult, handleWSScenarioStep,
    testResults, setTestResults, testSummary, setTestSummary,
    testRunning, setTestRunning,
    scenarioSteps, activeScenarioJobId, setActiveScenarioJobId,
    resetJobTracking,
  } = useJobTracking({ studioRequest, addNotification });

  const {
    nodes, setNodes, links, setLinks, annotations, setAnnotations,
    nodesRef, linksRef, layoutDirtyRef,
    saveLayout, triggerLayoutSave, triggerTopologySave, flushTopologySave,
  } = useLabTopology({
    activeLab,
    deviceModels,
    studioRequest,
    addTaskLogEntry,
  });

  const {
    nodeStates, setNodeStates, runtimeStates,
    nodeReadinessHints, pendingNodeOps, setPendingNodeOps,
    optimisticGuardRef,
    handleWSNodeStateChange,
    loadNodeStates, loadNodeReadiness, refreshNodeStatesFromAgent,
  } = useNodeStates({
    activeLabId: activeLab?.id || null,
    studioRequest,
    addNotification,
  });

  const consoleManager = useConsoleManager({
    nodes,
    preferences,
  });

  // Port manager for interface auto-assignment
  const portManager = usePortManager(nodes, links);

  // WebSocket hook for real-time state updates
  const {
    isConnected: wsConnected,
    reconnectAttempts: wsReconnectAttempts,
    linkStates,
  } = useLabStateWS(activeLab?.id || null, {
    onNodeStateChange: handleWSNodeStateChange,
    onJobProgress: handleWSJobProgress,
    onTestResult: handleWSTestResult,
    onScenarioStep: handleWSScenarioStep,
    enabled: !!activeLab,
  });

  // ESC key returns to pointer tool
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && activeTool !== 'pointer') {
        setActiveTool('pointer');
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [activeTool]);

  // Derive canvas highlights from the currently-running scenario step
  const activeScenarioHighlights = useMemo(() => {
    if (!activeScenarioJobId) return undefined;
    const runningStep = scenarioSteps.find(s => s.status === 'running' && s.step_index >= 0);
    if (!runningStep || !runningStep.step_data) return undefined;

    const activeNodeNames = new Set<string>();
    let activeLinkName: string | null = null;
    const stepType = runningStep.step_type;
    const sd = runningStep.step_data;

    if (stepType === 'link_down' || stepType === 'link_up') {
      const link = (sd.link as string) || '';
      activeLinkName = link;
      // Extract node names from "node1:iface1 <-> node2:iface2"
      const parts = link.split(' <-> ');
      parts.forEach(p => {
        const nodeName = p.trim().split(':')[0];
        if (nodeName) activeNodeNames.add(nodeName);
      });
    } else if (stepType === 'node_stop' || stepType === 'node_start' || stepType === 'exec') {
      const node = (sd.node as string) || '';
      if (node) activeNodeNames.add(node);
    } else if (stepType === 'verify') {
      const specs = (sd.specs as Array<Record<string, unknown>>) || [];
      specs.forEach(spec => {
        if (spec.source) activeNodeNames.add(spec.source as string);
        if (spec.node) activeNodeNames.add(spec.node as string);
        if (spec.node_name) activeNodeNames.add(spec.node_name as string);
      });
    }

    if (activeNodeNames.size === 0 && !activeLinkName) return undefined;
    return { activeNodeNames, activeLinkName, stepName: runningStep.step_name };
  }, [activeScenarioJobId, scenarioSteps]);

  // Prevent fixed-position console windows from causing document-level horizontal scrollbar
  useEffect(() => {
    document.documentElement.style.overflowX = 'hidden';
    return () => { document.documentElement.style.overflowX = ''; };
  }, []);

  // On lab entry, force a deterministic sync sequence:
  // refresh from agent first, then load node states/jobs.
  // This avoids UI showing stale DB state on initial render.
  useEffect(() => {
    if (!activeLab) {
      initialNodeSyncLabRef.current = null;
      return;
    }
    let cancelled = false;
    const labId = activeLab.id;
    if (initialNodeSyncLabRef.current === labId) return;
    initialNodeSyncLabRef.current = labId;

    const syncOnOpen = async () => {
      await refreshNodeStatesFromAgent(labId);
      if (cancelled) return;
      await loadNodeStates(labId, nodes);
      if (cancelled) return;
      await loadJobs(labId, nodes);
      if (cancelled) return;
      await loadNodeReadiness(labId);
    };

    void syncOnOpen();
    return () => {
      cancelled = true;
    };
  }, [activeLab, refreshNodeStatesFromAgent, loadNodeStates, loadJobs, loadNodeReadiness, nodes]);

  useEffect(() => {
    if (!activeLab || nodes.length === 0) return;
    loadNodeStates(activeLab.id, nodes);
    loadJobs(activeLab.id, nodes);
    loadNodeReadiness(activeLab.id);
  }, [activeLab, nodes, loadNodeStates, loadJobs, loadNodeReadiness]);

  // Poll for node state and job updates
  // When WebSocket is connected, poll less frequently as a fallback
  // When disconnected, poll at normal rate for state updates
  useEffect(() => {
    if (!activeLab || nodes.length === 0) return;
    // Use longer interval when WebSocket is connected (fallback only)
    // Shorter interval when disconnected (primary update mechanism)
    const interval = wsConnected ? 15000 : 4000;
    const timer = setInterval(() => {
      loadNodeStates(activeLab.id, nodes);
      loadJobs(activeLab.id, nodes);
      loadNodeReadiness(activeLab.id);
    }, interval);
    return () => clearInterval(timer);
  }, [activeLab, nodes, loadNodeStates, loadJobs, loadNodeReadiness, wsConnected]);

  // --- Event handlers ---

  const handleCreateLab = async () => {
    const name = `Project_${labs.length + 1}`;
    await studioRequest('/labs', { method: 'POST', body: JSON.stringify({ name }) });
    loadLabs();
  };

  const handleSelectLab = (lab: LabSummary) => {
    // Save pending layout changes before switching
    if (activeLab && layoutDirtyRef.current && nodes.length > 0) {
      saveLayout(activeLab.id, nodes, annotations);
    }
    // Reset job tracking for new lab context
    resetJobTracking();
    setActiveLab(lab);
    setAnnotations([]);
    consoleManager.resetConsoles();
    setSelectedId(null);
    setView('designer');
  };

  const handleExitLab = useCallback(() => {
    if (!activeLab) return;
    const confirmed = window.confirm('Are you sure you want to exit the lab?');
    if (!confirmed) return;
    consoleManager.resetConsoles();
    setActiveLab(null);
  }, [activeLab, consoleManager]);

  const handleLogout = useCallback(() => {
    localStorage.removeItem('token');
    clearUser();
    setAuthRequired(true);
    setAuthError(null);
    setActiveLab(null);
    consoleManager.resetConsoles();
  }, [clearUser, consoleManager]);

  const handleDeleteLab = async (labId: string) => {
    await studioRequest(`/labs/${labId}`, { method: 'DELETE' });
    if (activeLab?.id === labId) {
      setActiveLab(null);
      setNodes([]);
      setLinks([]);
    }
    loadLabs();
  };

  const handleRenameLab = async (labId: string, newName: string) => {
    await studioRequest(`/labs/${labId}`, {
      method: 'PUT',
      body: JSON.stringify({ name: newName }),
    });
    // Update local state
    setLabs((prev) => prev.map((lab) => (lab.id === labId ? { ...lab, name: newName } : lab)));
    if (activeLab?.id === labId) {
      setActiveLab((prev) => (prev ? { ...prev, name: newName } : prev));
    }
  };

  const imageCompatibilityAliases = useMemo(
    () => buildImageCompatibilityAliasMap(deviceModels),
    [deviceModels]
  );

  const hasInstantiableImageForModel = useCallback((model: DeviceModel): boolean => {
    const allowedKinds = getAllowedInstantiableImageKinds(model);

    return imageLibrary.some((img) => {
      if (!isInstantiableImageKind(img.kind)) {
        return false;
      }
      const imageKind = (img.kind || '').toLowerCase();
      if (!allowedKinds.has(imageKind)) {
        return false;
      }
      return imageMatchesDeviceId(img, model.id, imageCompatibilityAliases);
    });
  }, [imageLibrary, imageCompatibilityAliases]);

  const handleAddDevice = (model: DeviceModel, x?: number, y?: number) => {
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
      // Generate immutable container_name at creation time
      // This name is used by containerlab and never changes even if display name changes
      container_name: generateContainerName(displayName),
      type: model.type,
      model: model.id,
      version: model.versions[0],
      x: x ?? 300 + Math.random() * 50,
      y: y ?? 200 + Math.random() * 50,
      cpu: model.cpu || 1,
      memory: model.memory || 1024,
      // Don't bake vendor defaults into per-node config; let the backend
      // resolve disk_driver/nic_driver/machine_type through its layered
      // resolution (vendor config -> image manifest -> device overrides).
    };
    setNodes((prev) => [...prev, newNode]);
    // Don't set any status for new nodes - they should show no status icon until deployed
    setSelectedId(id);
    // Auto-save topology after a delay
    setTimeout(() => triggerTopologySave(), 100);
  };

  const handleAddExternalNetwork = (x?: number, y?: number) => {
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
    // Auto-save topology after a delay
    setTimeout(() => triggerTopologySave(), 100);
  };

  const handleSelectTool = useCallback((tool: CanvasTool) => {
    setActiveTool(tool);
    if (tool !== 'pointer') {
      setSelectedId(null);
    }
  }, []);

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
  }, [effectiveMode, triggerLayoutSave, setAnnotations]);

  const handleUpdateStatus = useCallback(async (nodeId: string, status: RuntimeStatus): Promise<void> => {
    if (!activeLab) return;

    // Block if operation already pending for this node
    if (pendingNodeOps.has(nodeId)) {
      addTaskLogEntry('info', 'Operation already in progress for this node');
      return;
    }

    const node = nodes.find((n) => n.id === nodeId);
    if (!node) return;
    const nodeName = node.name;

    // Map RuntimeStatus to desired state
    const desiredState = status === 'stopped' ? 'stopped' : 'running';
    const action = desiredState === 'running' ? 'start' : 'stop';

    // Mark operation as pending
    setPendingNodeOps((prev) => new Set(prev).add(nodeId));
    addTaskLogEntry('info', `Setting "${nodeName}" to ${desiredState}...`);

    try {
      if (desiredState === 'running') {
        await flushTopologySave();
      }

      // Optimistically update nodeStates -- runtimeStates derived automatically
      const transitionalState = status === 'stopped' ? 'stopping' : 'starting';
      optimisticGuardRef.current.set(nodeId, Date.now() + 5000);
      setNodeStates((prev) => ({
        ...prev,
        [nodeId]: {
          ...prev[nodeId],
          actual_state: transitionalState,
          desired_state: desiredState as 'stopped' | 'running',
          display_state: transitionalState,
        },
      }));

      // Set desired state - this now auto-triggers sync
      await studioRequest(`/labs/${activeLab.id}/nodes/${encodeURIComponent(nodeId)}/desired-state`, {
        method: 'PUT',
        body: JSON.stringify({ state: desiredState }),
      });

      addTaskLogEntry('success', `${action === 'start' ? 'Starting' : 'Stopping'} "${nodeName}"...`);
      loadJobs(activeLab.id, nodes);
    } catch (error) {
      let message = error instanceof Error ? error.message : 'Action failed';

      // Handle specific HTTP error codes with user-friendly messages
      if (error instanceof Error) {
        // Check for 409 Conflict (operation already in progress)
        if (message.includes('409') || message.toLowerCase().includes('already in progress') || message.toLowerCase().includes('conflict')) {
          message = 'Another operation is already in progress for this lab';
          addTaskLogEntry('warning', `Cannot ${action} "${nodeName}": ${message}`);
          // Don't set error state - just inform the user
          return;
        }
        // Check for 503 Service Unavailable (agent busy / lock timeout)
        if (message.includes('503') || message.toLowerCase().includes('try again later')) {
          message = 'Service temporarily unavailable, please try again';
          addTaskLogEntry('warning', `Cannot ${action} "${nodeName}": ${message}`);
          return;
        }
      }

      console.error('Node action failed:', error);
      setNodeStates((prev) => ({
        ...prev,
        [nodeId]: { ...prev[nodeId], actual_state: 'error', error_message: message },
      }));
      addTaskLogEntry('error', `Node ${action} failed for "${nodeName}": ${message}`);
    } finally {
      // Clear pending operation
      setPendingNodeOps((prev) => {
        const next = new Set(prev);
        next.delete(nodeId);
        return next;
      });
    }
  }, [activeLab, nodes, pendingNodeOps, studioRequest, addTaskLogEntry, loadNodeStates, loadJobs, flushTopologySave, setPendingNodeOps, setNodeStates, optimisticGuardRef]);

  const handleOpenConsole = useCallback((nodeId: string) => {
    consoleManager.handleOpenConsole(nodeId, setIsTaskLogVisible);
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

  const handleDockWindow = useCallback((windowId: string) => {
    consoleManager.handleDockWindow(windowId, setIsTaskLogVisible);
  }, [consoleManager, setIsTaskLogVisible]);

  const handleOpenConfigViewer = useCallback((nodeId?: string, nodeName?: string, snapshotContent?: string, snapshotLabel?: string) => {
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

  const handleToggleAgentIndicators = useCallback(() => {
    setShowAgentIndicators(prev => {
      const next = !prev;
      localStorage.setItem('archetype_show_agent_indicators', next ? 'true' : 'false');
      return next;
    });
  }, []);


  const handleStartTests = useCallback(async (specs?: import('./types').TestSpec[]) => {
    if (!activeLab?.id || testRunning) return;
    setTestResults([]);
    setTestSummary(null);
    setTestRunning(true);
    try {
      const options: RequestInit = { method: 'POST' };
      if (specs && specs.length > 0) {
        options.body = JSON.stringify({ specs });
      }
      await apiRequest(`/labs/${activeLab.id}/tests/run`, options);
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

  const handleCloseConfigViewer = useCallback(() => {
    setConfigViewerOpen(false);
    setConfigViewerNode(null);
    setConfigViewerSnapshot(null);
  }, []);

  const handleTaskLogEntryClick = useCallback((entry: TaskLogEntry) => {
    if (entry.jobId) {
      setJobLogModalJobId(entry.jobId);
      setJobLogModalOpen(true);
      return;
    }
    setTaskLogEntryModalEntry(entry);
    setTaskLogEntryModalOpen(true);
  }, []);

  const handleCloseJobLogModal = useCallback(() => {
    setJobLogModalOpen(false);
    setJobLogModalJobId(null);
  }, []);

  const handleCloseTaskLogEntryModal = useCallback(() => {
    setTaskLogEntryModalOpen(false);
    setTaskLogEntryModalEntry(null);
  }, []);

  const handleNodeMove = useCallback((id: string, x: number, y: number) => {
    setNodes((prev) => prev.map((node) => (node.id === id ? { ...node, x, y } : node)));
    triggerLayoutSave();
  }, [triggerLayoutSave, setNodes]);

  const handleAnnotationMove = useCallback((id: string, x: number, y: number) => {
    setAnnotations((prev) => prev.map((ann) => (ann.id === id ? { ...ann, x, y } : ann)));
    triggerLayoutSave();
  }, [triggerLayoutSave, setAnnotations]);

  const handleConnect = (sourceId: string, targetId: string) => {
    const exists = links.find(
      (link) => (link.source === sourceId && link.target === targetId) || (link.source === targetId && link.target === sourceId)
    );
    if (exists) return;

    // Auto-assign next available interfaces
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
    // Auto-save topology
    triggerTopologySave();
  };

  const handleUpdateNode = (id: string, updates: Partial<Node>) => {
    setNodes((prev) => {
      const next = prev.map((node) => (node.id === id ? { ...node, ...updates } as Node : node));
      // Keep ref in sync immediately so flushTopologySave() sees latest host assignment.
      nodesRef.current = next;
      return next;
    });
    // Auto-save topology if name, model, version, or host changed (device nodes only)
    const deviceUpdates = updates as Partial<DeviceNode>;
    if (updates.name || deviceUpdates.model || deviceUpdates.version || deviceUpdates.host
        || deviceUpdates.cpu !== undefined || deviceUpdates.memory !== undefined) {
      triggerTopologySave();
    }
    // Also save if external network fields change
    const extUpdates = updates as Partial<ExternalNetworkNode>;
    if (extUpdates.managedInterfaceId !== undefined || extUpdates.connectionType || extUpdates.parentInterface || extUpdates.vlanId || extUpdates.bridgeName || extUpdates.host) {
      triggerTopologySave();
    }
  };

  const handleUpdateLink = (id: string, updates: Partial<Link>) => {
    const prevLink = linksRef.current.find((link) => link.id === id);
    if (!prevLink) return;

    const nextLink = { ...prevLink, ...updates };
    setLinks((prev) => prev.map((link) => (link.id === id ? nextLink : link)));

    // Auto-save topology if interface assignments changed
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

    if (!isRunning) {
      return;
    }

    const sourceName = sourceNode.container_name || sourceNode.name;
    const targetName = targetNode.container_name || targetNode.name;

    const oldSourceIface = prevLink.sourceInterface || '';
    const oldTargetIface = prevLink.targetInterface || '';
    const newSourceIface = nextLink.sourceInterface || '';
    const newTargetIface = nextLink.targetInterface || '';

    if (oldSourceIface === newSourceIface && oldTargetIface === newTargetIface) {
      return;
    }

    if (!newSourceIface || !newTargetIface) {
      return;
    }

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
  };

  const handleUpdateAnnotation = (id: string, updates: Partial<Annotation>) => {
    setAnnotations((prev) => prev.map((ann) => (ann.id === id ? { ...ann, ...updates } : ann)));
    triggerLayoutSave();
  };

  const handleCanvasSelect = useCallback((id: string | null) => {
    setSelectedId(id);
    setSelectedIds(new Set());
  }, []);

  const handleSelectMultiple = useCallback((ids: Set<string>) => {
    setSelectedIds(ids);
    setSelectedId(null);
  }, []);

  const handleDelete = (id: string) => {
    const isAnnotation = annotations.some((ann) => ann.id === id);
    const isNode = nodes.some((node) => node.id === id);
    const isLink = links.some((link) => link.id === id);
    setNodes((prev) => prev.filter((node) => node.id !== id));
    setLinks((prev) => prev.filter((link) => link.id !== id && link.source !== id && link.target !== id));
    setAnnotations((prev) => prev.filter((ann) => ann.id !== id));
    setSelectedId(null);
    setSelectedIds(new Set());
    // Trigger layout save if an annotation was deleted
    if (isAnnotation) {
      triggerLayoutSave();
    }
    // Trigger topology save if a node or link was deleted
    if (isNode || isLink) {
      triggerTopologySave();
    }
  };

  const handleExport = async () => {
    if (!activeLab) return;
    const data = await studioRequest<{ content: string }>(`/labs/${activeLab.id}/export-yaml`);
    setYamlContent(data.content || '');
    setShowYamlModal(true);
  };

  const handleDownloadBundle = async (lab: LabSummary) => {
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
  };

  const handleExportFull = async () => {
    if (!activeLab) return;
    // Save layout first to include the latest canvas state.
    await saveLayout(activeLab.id, nodes, annotations);
    await handleDownloadBundle(activeLab);
  };

  const handleLogin = async (username: string, password?: string) => {
    setAuthError(null);
    setAuthLoading(true);
    try {
      const body = new URLSearchParams();
      body.set('username', username);
      body.set('password', password || '');
      const response = await fetch(`${API_BASE_URL}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: body.toString(),
      });
      if (!response.ok) {
        const message = await response.text();
        throw new Error(message || 'Login failed');
      }
      const data = (await response.json()) as { access_token?: string };
      if (!data.access_token) {
        throw new Error('Login failed');
      }
      localStorage.setItem('token', data.access_token);
      setAuthRequired(false);
      await refreshUser();
      await loadLabs();
      await refreshDeviceCatalog();
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : 'Login failed');
    } finally {
      setAuthLoading(false);
    }
  };

  const selectedItem = nodes.find((node) => node.id === selectedId) || links.find((link) => link.id === selectedId) || annotations.find((ann) => ann.id === selectedId) || null;

  // Handle extract configs for ConfigsView
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

  const renderView = () => {
    switch (view) {
      case 'configs':
        return (
          <ConfigsView
            labId={activeLab?.id || ''}
            nodes={nodes}
            runtimeStates={runtimeStates}
            studioRequest={studioRequest}
            onExtractConfigs={handleExtractConfigs}
          />
        );
      case 'logs':
        return (
          <LogsView
            labId={activeLab?.id || ''}
            studioRequest={studioRequest}
            realtimeEntries={filteredTaskLog}
          />
        );
      case 'runtime':
        return (
          <RuntimeControl
            labId={activeLab?.id || ''}
            nodes={nodes}
            runtimeStates={runtimeStates}
            nodeStates={nodeStates}
            deviceModels={deviceModels}
            onUpdateStatus={handleUpdateStatus}
            onSetRuntimeStatus={(nodeId, status) => {
              // Map RuntimeStatus back to actual_state for optimistic updates
              const actualStateMap: Record<string, string> = {
                booting: 'starting', stopping: 'stopping', running: 'running', stopped: 'stopped', error: 'error',
              };
              const actualState = actualStateMap[status] || status;
              // Set optimistic guard: protect transitional states from being
              // overwritten by stale polling/WS data for 5 seconds
              if (actualState === 'stopping' || actualState === 'starting') {
                optimisticGuardRef.current.set(nodeId, Date.now() + 5000);
              }
              setNodeStates(prev => ({
                ...prev,
                [nodeId]: {
                  ...prev[nodeId],
                  actual_state: actualState,
                  desired_state: status === 'stopping' ? 'stopped' : status === 'booting' ? 'running' : prev[nodeId]?.desired_state,
                  display_state: actualState === 'stopping' ? 'stopping' : actualState === 'starting' ? 'starting' : undefined,
                },
              }));
            }}
            onRefreshStates={async () => {
              if (activeLab) {
                await refreshNodeStatesFromAgent(activeLab.id);
                await loadNodeStates(activeLab.id, nodes);
              }
            }}
            studioRequest={studioRequest}
            agents={agents}
            onUpdateNode={handleUpdateNode}
            pendingNodeOps={pendingNodeOps}
            onFlushTopologySave={flushTopologySave}
          />
        );
      case 'tests':
        return (
          <VerificationPanel
            labId={activeLab?.id || ''}
            testResults={testResults}
            testSummary={testSummary}
            isRunning={testRunning}
            onStartTests={handleStartTests}
            nodes={nodes}
            links={links}
          />
        );
      case 'scenarios':
        return (
          <ScenarioPanel
            labId={activeLab?.id || ''}
            scenarioSteps={scenarioSteps}
            activeScenarioJobId={activeScenarioJobId}
            onStartScenario={handleStartScenario}
          />
        );
      case 'infra':
        return (
          <InfraView
            labId={activeLab?.id || ''}
            nodes={nodes}
            nodeStates={nodeStates}
            linkStates={linkStates}
            agents={agents}
            deviceModels={deviceModels}
          />
        );
      default:
        return (
          <>
            <div className={`shrink-0 transition-all duration-300 ease-in-out overflow-hidden ${sidebarCollapsed ? 'w-0' : 'w-64'}`}>
              <Sidebar
                categories={deviceCategories}
                onAddDevice={handleAddDevice}
                onSelectTool={handleSelectTool}
                activeTool={activeTool}
                onAddExternalNetwork={handleAddExternalNetwork}
                imageLibrary={imageLibrary}
                activeTab={sidebarTab}
                onTabChange={setSidebarTab}
                nodes={nodes}
                runtimeStates={runtimeStates}
                deviceModels={deviceModels}
                selectedId={selectedId}
                onFocusNode={(id) => { setFocusNodeId(id); }}
                onOpenConsole={handleOpenConsole}
                onSelectNode={(id) => { setSelectedId(id); setSelectedIds(new Set()); }}
                collapsed={sidebarCollapsed}
                onToggleCollapse={() => setSidebarCollapsed(c => !c)}
              />
            </div>
            {sidebarCollapsed && (
              <button
                onClick={() => setSidebarCollapsed(false)}
                className="absolute left-2 top-2 z-20 w-8 h-8 rounded-lg bg-white/80 dark:bg-stone-800/80 backdrop-blur border border-stone-200 dark:border-stone-700 flex items-center justify-center text-stone-500 hover:text-stone-700 dark:hover:text-stone-200 shadow-sm transition-colors"
                title="Expand sidebar"
              >
                <i className="fa-solid fa-chevron-right text-[10px]" />
              </button>
            )}
            <Canvas
              nodes={nodes}
              links={links}
              annotations={annotations}
              runtimeStates={runtimeStates}
              nodeStates={nodeStates}
              linkStates={linkStates}
              scenarioHighlights={activeScenarioHighlights}
              deviceModels={deviceModels}
              labId={activeLab?.id}
              agents={agents}
              showAgentIndicators={showAgentIndicators}
              onToggleAgentIndicators={handleToggleAgentIndicators}
              activeTool={activeTool}
              onToolCreate={handleCanvasToolCreate}
              onNodeMove={handleNodeMove}
              onAnnotationMove={handleAnnotationMove}
              onConnect={handleConnect}
              selectedId={selectedId}
              onSelect={handleCanvasSelect}
              onOpenConsole={handleOpenConsole}
              onExtractConfig={handleExtractNodeConfig}
              onUpdateStatus={handleUpdateStatus}
              onDelete={handleDelete}
              onDropDevice={handleAddDevice}
              onDropExternalNetwork={handleAddExternalNetwork}
              onUpdateAnnotation={handleUpdateAnnotation}
              selectedIds={selectedIds}
              onSelectMultiple={handleSelectMultiple}
              focusNodeId={focusNodeId}
              onFocusHandled={() => setFocusNodeId(null)}
            />
            <div
              className={`shrink-0 transition-all duration-300 ease-in-out overflow-hidden ${
                selectedItem ? 'w-80' : 'w-0'
              }`}
            >
              <div className="w-80 h-full">
                <PropertiesPanel
                  selectedItem={selectedItem}
                  onUpdateNode={handleUpdateNode}
                  onUpdateLink={handleUpdateLink}
                  onUpdateAnnotation={handleUpdateAnnotation}
                  onDelete={handleDelete}
                  nodes={nodes}
                  links={links}
                  annotations={annotations}
                  onOpenConsole={handleOpenConsole}
                  runtimeStates={runtimeStates}
                  deviceModels={deviceModels}
                  onUpdateStatus={handleUpdateStatus}
                  portManager={portManager}
              onOpenConfigViewer={handleOpenConfigViewer}
              labId={activeLab?.id || ''}
              studioRequest={studioRequest}
              agents={agents}
              nodeStates={nodeStates}
              nodeReadinessHints={nodeReadinessHints}
            />
              </div>
            </div>
          </>
        );
    }
  };

  const backgroundGradient =
    effectiveMode === 'dark'
      ? 'bg-gradient-to-br from-stone-950/20 via-stone-900/12 to-stone-950/20 bg-gradient-animate'
      : 'bg-gradient-to-br from-stone-50/20 via-white/15 to-stone-100/20 bg-gradient-animate';

  if (authRequired) {
    return <Auth onLogin={handleLogin} error={authError} loading={authLoading} />;
  }

  if (!activeLab) {
    return (
      <Dashboard
        labs={labs}
        labStatuses={labStatuses}
        systemMetrics={systemMetrics}
        onSelect={handleSelectLab}
        onDownload={handleDownloadBundle}
        onCreate={handleCreateLab}
        onDelete={handleDeleteLab}
        onRename={handleRenameLab}
        onLogout={handleLogout}
      />
    );
  }

  return (
    <div className={`flex flex-col h-screen overflow-hidden select-none transition-colors duration-500 ${view === 'designer' ? '' : backgroundGradient}`}>
      <TopBar labName={activeLab.name} onExport={handleExport} onExportFull={handleExportFull} onExit={handleExitLab} onRename={(newName) => handleRenameLab(activeLab.id, newName)} />
      <div className="h-10 bg-white/35 dark:bg-black/35 backdrop-blur-md border-b border-stone-200/70 dark:border-black/70 flex px-6 items-center gap-1 shrink-0">
        <button
          onClick={() => setView('designer')}
          className={`h-full px-4 text-[10px] font-black uppercase border-b-2 transition-all ${
            view === 'designer'
              ? 'text-sage-700 dark:text-sage-500 border-sage-700 dark:border-sage-500'
              : 'text-stone-700 dark:text-stone-300 border-transparent hover:text-stone-900 dark:hover:text-stone-100'
          }`}
        >
          Designer
        </button>
        <button
          onClick={() => setView('runtime')}
          className={`h-full px-4 text-[10px] font-black uppercase border-b-2 transition-all ${
            view === 'runtime'
              ? 'text-sage-700 dark:text-sage-500 border-sage-700 dark:border-sage-500'
              : 'text-stone-700 dark:text-stone-300 border-transparent hover:text-stone-900 dark:hover:text-stone-100'
          }`}
        >
          Runtime
        </button>
        <button
          onClick={() => setView('configs')}
          className={`h-full px-4 text-[10px] font-black uppercase border-b-2 transition-all ${
            view === 'configs'
              ? 'text-sage-700 dark:text-sage-500 border-sage-700 dark:border-sage-500'
              : 'text-stone-700 dark:text-stone-300 border-transparent hover:text-stone-900 dark:hover:text-stone-100'
          }`}
        >
          Configs
        </button>
        <button
          onClick={() => setView('logs')}
          className={`h-full px-4 text-[10px] font-black uppercase border-b-2 transition-all ${
            view === 'logs'
              ? 'text-sage-700 dark:text-sage-500 border-sage-700 dark:border-sage-500'
              : 'text-stone-700 dark:text-stone-300 border-transparent hover:text-stone-900 dark:hover:text-stone-100'
          }`}
        >
          Logs
        </button>
        <button
          onClick={() => setView('tests')}
          className={`h-full px-4 text-[10px] font-black uppercase border-b-2 transition-all ${
            view === 'tests'
              ? 'text-sage-700 dark:text-sage-500 border-sage-700 dark:border-sage-500'
              : 'text-stone-700 dark:text-stone-300 border-transparent hover:text-stone-900 dark:hover:text-stone-100'
          }`}
        >
          Tests
        </button>
        <button
          onClick={() => setView('scenarios')}
          className={`h-full px-4 text-[10px] font-black uppercase border-b-2 transition-all ${
            view === 'scenarios'
              ? 'text-sage-700 dark:text-sage-500 border-sage-700 dark:border-sage-500'
              : 'text-stone-700 dark:text-stone-300 border-transparent hover:text-stone-900 dark:hover:text-stone-100'
          }`}
        >
          Scenarios
        </button>
        {agents.length > 1 && (
          <button
            onClick={() => setView('infra')}
            className={`h-full px-4 text-[10px] font-black uppercase border-b-2 transition-all ${
              view === 'infra'
                ? 'text-sage-700 dark:text-sage-500 border-sage-700 dark:border-sage-500'
                : 'text-stone-700 dark:text-stone-300 border-transparent hover:text-stone-900 dark:hover:text-stone-100'
            }`}
          >
            Infra
          </button>
        )}
      </div>
      {showAdminStrip && <SystemStatusStrip metrics={systemMetrics} />}
      <AgentAlertBanner />
      <div className="flex flex-1 overflow-hidden relative">
        {renderView()}
        <div className={isDesignerView ? '' : 'hidden'} aria-hidden={!isDesignerView}>
          <ConsoleManager
            labId={activeLab.id}
            windows={consoleManager.consoleWindows}
            nodes={nodes}
            nodeStates={nodeStates}
            isVisible={isDesignerView}
            onCloseWindow={consoleManager.handleCloseConsoleWindow}
            onCloseTab={consoleManager.handleCloseConsoleTab}
            onSetActiveTab={consoleManager.handleSetActiveConsoleTab}
            onUpdateWindowPos={consoleManager.handleUpdateConsoleWindowPos}
            onMergeWindows={consoleManager.handleMergeWindows}
            onSplitTab={consoleManager.handleSplitTab}
            onReorderTab={consoleManager.handleReorderTab}
            onToggleMinimize={consoleManager.handleToggleMinimize}
            onDockWindow={handleDockWindow}
          />
        </div>
      </div>
      <TaskLogPanel
        entries={filteredTaskLog}
        isVisible={isTaskLogVisible}
        onToggle={() => setIsTaskLogVisible(!isTaskLogVisible)}
        onClear={clearTaskLog}
        autoUpdateEnabled={taskLogAutoRefresh}
        onToggleAutoUpdate={setTaskLogAutoRefresh}
        onEntryClick={handleTaskLogEntryClick}
        showConsoles={isDesignerView}
        consoleTabs={consoleManager.dockedConsoles}
        activeTabId={consoleManager.activeBottomTabId}
        onSelectTab={consoleManager.setActiveBottomTabId}
        onCloseConsoleTab={consoleManager.handleCloseDockedConsole}
        onUndockConsole={consoleManager.handleUndockConsole}
        onReorderTab={consoleManager.handleReorderDockedTab}
        labId={activeLab?.id}
        nodeStates={nodeStates}
        wsConnected={wsConnected}
        reconnectAttempts={wsReconnectAttempts}
      />
      {showYamlModal && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/80 backdrop-blur-md">
          <div className="bg-white dark:bg-stone-900 border border-stone-200 dark:border-stone-700 rounded-2xl w-[700px] max-h-[85vh] flex flex-col overflow-hidden shadow-2xl">
            <div className="p-5 border-b border-stone-100 dark:border-stone-800 flex justify-between items-center">
              <h3 className="text-stone-900 dark:text-stone-100 font-bold text-sm uppercase">YAML Preview</h3>
              <button onClick={() => setShowYamlModal(false)} className="text-stone-500 hover:text-stone-900 dark:hover:text-white">
                <i className="fa-solid fa-times"></i>
              </button>
            </div>
            <div className="flex-1 p-6 overflow-y-auto bg-stone-50 dark:bg-stone-950/50 font-mono text-[11px] text-sage-700 dark:text-sage-300 whitespace-pre">
              {yamlContent}
            </div>
            <div className="p-5 border-t border-stone-100 dark:border-stone-800 flex justify-end gap-3">
              <button onClick={() => setShowYamlModal(false)} className="px-6 py-2 bg-sage-600 text-white font-black rounded-lg">
                DONE
              </button>
            </div>
          </div>
        </div>
      )}
      <ConfigViewerModal
        isOpen={configViewerOpen}
        onClose={handleCloseConfigViewer}
        labId={activeLab?.id || ''}
        nodeId={configViewerNode?.id}
        nodeName={configViewerNode?.name}
        studioRequest={studioRequest}
        snapshotContent={configViewerSnapshot?.content}
        snapshotLabel={configViewerSnapshot?.label}
      />
      <JobLogModal
        isOpen={jobLogModalOpen}
        onClose={handleCloseJobLogModal}
        labId={activeLab?.id || ''}
        jobId={jobLogModalJobId || ''}
        studioRequest={studioRequest}
      />
      <TaskLogEntryModal
        isOpen={taskLogEntryModalOpen}
        onClose={handleCloseTaskLogEntryModal}
        entry={taskLogEntryModalEntry}
      />
    </div>
  );
};

export default StudioPage;
