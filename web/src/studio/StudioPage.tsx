import React, { useCallback, useEffect, useMemo, useState, useRef } from 'react';
import Sidebar from './components/Sidebar';
import Canvas from './components/Canvas';
import TopBar from './components/TopBar';
import PropertiesPanel from './components/PropertiesPanel';
import ConsoleManager from './components/ConsoleManager';
import RuntimeControl from './components/RuntimeControl';
import StatusBar from './components/StatusBar';
import TaskLogPanel, { TaskLogEntry, DockedConsole } from './components/TaskLogPanel';
import Dashboard from './components/Dashboard';
import SystemStatusStrip from './components/SystemStatusStrip';
import AgentAlertBanner from './components/AgentAlertBanner';
import ConfigViewerModal from './components/ConfigViewerModal';
import JobLogModal from './components/JobLogModal';
import ConfigsView from './components/ConfigsView';
import LogsView from './components/LogsView';
import { Annotation, AnnotationType, ConsoleWindow, DeviceModel, DeviceType, LabLayout, Link, Node, ExternalNetworkNode, DeviceNode, isExternalNetworkNode, isDeviceNode } from './types';
import { API_BASE_URL, apiRequest } from '../api';
import { TopologyGraph } from '../types';
import { usePortManager } from './hooks/usePortManager';
import { usePersistedState } from './hooks/usePersistedState';
import { useLabStateWS, JobProgressData } from './hooks/useLabStateWS';
import { NodeStateEntry, NodeStateData, NodeRuntimeStatus, mapActualToRuntime } from '../types/nodeState';
import { useTheme } from '../theme/index';
import { useUser } from '../contexts/UserContext';
import { canViewInfrastructure } from '../utils/permissions';
import { useNotifications } from '../contexts/NotificationContext';
import { useImageLibrary } from '../contexts/ImageLibraryContext';
import { useDeviceCatalog } from '../contexts/DeviceCatalogContext';
import { ArchetypeIcon } from '../components/icons';
import './studio.css';
import 'xterm/css/xterm.css';

interface LabSummary {
  id: string;
  name: string;
  created_at?: string;
}

// RuntimeStatus is an alias for NodeRuntimeStatus for backward compatibility
type RuntimeStatus = NodeRuntimeStatus;

const DEFAULT_ICON = 'fa-microchip';

/**
 * Generate a container name from a display name.
 * Container names must be valid for containerlab (lowercase, alphanumeric + underscore).
 * This name is immutable after first creation - display names can change freely.
 */
const generateContainerName = (displayName: string): string => {
  return displayName
    .toLowerCase()
    .replace(/[^a-z0-9_]/g, '_')  // Replace invalid chars with underscore
    .replace(/^[^a-z_]/, 'n')     // Ensure starts with letter or underscore
    .replace(/_+/g, '_')          // Collapse multiple underscores
    .substring(0, 20);            // Limit length
};

const guessDeviceType = (id: string, label: string): DeviceType => {
  const token = `${id} ${label}`.toLowerCase();
  if (token.includes('switch')) return DeviceType.SWITCH;
  if (token.includes('router')) return DeviceType.ROUTER;
  if (token.includes('firewall')) return DeviceType.FIREWALL;
  if (token.includes('linux') || token.includes('server') || token.includes('host')) return DeviceType.HOST;
  return DeviceType.CONTAINER;
};

const buildGraphNodes = (graph: TopologyGraph, models: DeviceModel[]): Node[] => {
  const modelMap = new Map(models.map((model) => [model.id, model]));
  return graph.nodes.map((node, index) => {
    const column = index % 5;
    const row = Math.floor(index / 5);

    // Handle external network nodes
    if ((node as any).node_type === 'external') {
      const extNode: ExternalNetworkNode = {
        id: node.id,
        nodeType: 'external',
        name: node.name || node.id,
        managedInterfaceId: (node as any).managed_interface_id,
        managedInterfaceName: (node as any).managed_interface_name,
        managedInterfaceHostId: (node as any).managed_interface_host_id,
        managedInterfaceHostName: (node as any).managed_interface_host_name,
        connectionType: (node as any).connection_type || undefined,
        parentInterface: (node as any).parent_interface,
        vlanId: (node as any).vlan_id,
        bridgeName: (node as any).bridge_name,
        host: (node as any).host,
        x: 220 + column * 160,
        y: 180 + row * 140,
      };
      return extNode;
    }

    // Handle device nodes
    const modelId = node.device || node.id;
    const model = modelMap.get(modelId);
    const deviceNode: DeviceNode = {
      id: node.id,
      nodeType: 'device',
      name: node.name || node.id,
      container_name: node.container_name || undefined, // Preserve container_name from backend
      type: model?.type || DeviceType.CONTAINER,
      model: model?.id || modelId,
      version: node.version || model?.versions?.[0] || 'default',
      x: 220 + column * 160,
      y: 180 + row * 140,
      cpu: 1,
      memory: 1024,
      host: (node as any).host, // Preserve host from backend for multi-host placement
    };
    return deviceNode;
  });
};

const buildGraphLinks = (graph: TopologyGraph): Link[] => {
  return graph.links
    .map((link, index) => {
      if (!link.endpoints || link.endpoints.length < 2) return null;
      const [source, target] = link.endpoints;
      return {
        id: `link-${index}-${source.node}-${target.node}`,
        source: source.node,
        target: target.node,
        type: 'p2p',
        sourceInterface: source.ifname || undefined,
        targetInterface: target.ifname || undefined,
      };
    })
    .filter(Boolean) as Link[];
};

const buildStatusMap = (jobs: any[], nodes: Node[], deployedNodeNames: Set<string>) => {
  const map = new Map<string, RuntimeStatus>();
  let globalStatus: RuntimeStatus | undefined;

  // Build a name-to-id lookup for nodes
  const nameToId = new Map<string, string>();
  nodes.forEach((node) => nameToId.set(node.name, node.id));

  for (const job of jobs) {
    if (typeof job.action !== 'string') continue;
    if (job.action.startsWith('node:')) {
      const [, nodeAction, nodeName] = job.action.split(':', 3);
      // Find the node ID for this node name
      const nodeId = nameToId.get(nodeName);
      if (!nodeId || map.has(nodeId)) continue;
      const status = resolveNodeStatus(nodeAction, job.status);
      if (status) {
        map.set(nodeId, status);
      }
      continue;
    }
    if (!globalStatus && ['up', 'down', 'restart'].includes(job.action)) {
      globalStatus = resolveNodeStatus(job.action, job.status);
    }
  }

  // Only apply global status to nodes that are actually deployed
  if (globalStatus) {
    for (const node of nodes) {
      if (!map.has(node.id) && deployedNodeNames.has(node.name)) {
        map.set(node.id, globalStatus);
      }
    }
  }

  return map;
};

const resolveNodeStatus = (action: string, status: string): RuntimeStatus | undefined => {
  if (status === 'failed') return 'error';
  const isDone = status === 'completed';
  if (action === 'start' || action === 'up') {
    return isDone ? 'running' : 'booting';
  }
  if (action === 'stop' || action === 'down') {
    return 'stopped';
  }
  if (action === 'restart') {
    return isDone ? 'running' : 'booting';
  }
  return undefined;
};

const StudioPage: React.FC = () => {
  const { effectiveMode } = useTheme();
  const { user, refreshUser } = useUser();
  const { addNotification, preferences } = useNotifications();
  const { imageLibrary } = useImageLibrary();
  const { deviceModels, deviceCategories, imageCatalog, refresh: refreshDeviceCatalog } = useDeviceCatalog();
  const showAdminStrip = canViewInfrastructure(user ?? null);
  const [labs, setLabs] = useState<LabSummary[]>([]);
  const [activeLab, setActiveLab] = useState<LabSummary | null>(null);
  const [view, setView] = useState<'designer' | 'configs' | 'logs' | 'runtime'>('designer');
  const [nodes, setNodes] = useState<Node[]>([]);
  const [links, setLinks] = useState<Link[]>([]);
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [nodeStates, setNodeStates] = useState<Record<string, NodeStateEntry>>({});
  // Derive runtimeStates from nodeStates — single source of truth
  const runtimeStates = useMemo(() => {
    const result: Record<string, RuntimeStatus> = {};
    for (const [nodeId, state] of Object.entries(nodeStates)) {
      const status = mapActualToRuntime(state.actual_state, state.desired_state, state.will_retry, state.display_state);
      if (status !== null) result[nodeId] = status;
    }
    return result;
  }, [nodeStates]);
  const [consoleWindows, setConsoleWindows] = useState<ConsoleWindow[]>([]);
  const [dockedConsoles, setDockedConsoles] = useState<DockedConsole[]>([]);
  const [activeBottomTabId, setActiveBottomTabId] = useState<string>('log');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showYamlModal, setShowYamlModal] = useState(false);
  const [yamlContent, setYamlContent] = useState('');
  const [agents, setAgents] = useState<{ id: string; name: string }[]>(() => {
    return [];
  });
  const [showAgentIndicators, setShowAgentIndicators] = useState<boolean>(() => {
    return localStorage.getItem('archetype_show_agent_indicators') !== 'false';
  });
  const [authRequired, setAuthRequired] = useState(false);
  const [authEmail, setAuthEmail] = useState('');
  const [authPassword, setAuthPassword] = useState('');
  const [authError, setAuthError] = useState<string | null>(null);
  const [authLoading, setAuthLoading] = useState(false);
  const [taskLog, setTaskLog] = useState<TaskLogEntry[]>([]);
  const [isTaskLogVisible, setIsTaskLogVisible] = usePersistedState('archetype-tasklog-visible', true);
  const [taskLogClearedAt, setTaskLogClearedAt] = useState<number>(() => {
    const stored = localStorage.getItem('archetype_tasklog_cleared_at');
    return stored ? parseInt(stored, 10) : 0;
  });
  const [jobs, setJobs] = useState<any[]>([]);
  const prevJobsRef = useRef<Map<string, string>>(new Map());
  const isInitialJobLoadRef = useRef(true);
  const [labStatuses, setLabStatuses] = useState<Record<string, { running: number; total: number }>>({});
  // Config viewer modal state
  const [configViewerOpen, setConfigViewerOpen] = useState(false);
  const [configViewerNode, setConfigViewerNode] = useState<{ id: string; name: string } | null>(null);
  // Job log modal state
  const [jobLogModalOpen, setJobLogModalOpen] = useState(false);
  const [jobLogModalJobId, setJobLogModalJobId] = useState<string | null>(null);
  // Track pending node operations to prevent race conditions from rapid clicks
  const [pendingNodeOps, setPendingNodeOps] = useState<Set<string>>(new Set());
  // Tracks optimistic updates: nodeId -> expiry timestamp. Prevents polling/WS from
  // overwriting transitional states (stopping/starting) before the server catches up.
  const optimisticGuardRef = useRef<Map<string, number>>(new Map());
  const layoutDirtyRef = useRef(false);
  const saveLayoutTimeoutRef = useRef<number | null>(null);
  const topologyDirtyRef = useRef(false);
  const saveTopologyTimeoutRef = useRef<number | null>(null);
  // Refs to track current state for debounced saves (avoids stale closure issues)
  const nodesRef = useRef<Node[]>([]);
  const linksRef = useRef<Link[]>([]);
  const annotationsRef = useRef<Annotation[]>([]);
  const [systemMetrics, setSystemMetrics] = useState<{
    agents: { online: number; total: number };
    containers: { running: number; total: number };
    cpu_percent: number;
    memory_percent: number;
    memory?: {
      used_gb: number;
      total_gb: number;
      percent: number;
    };
    storage?: {
      used_gb: number;
      total_gb: number;
      percent: number;
    };
    labs_running: number;
    labs_total: number;
    per_host?: {
      id: string;
      name: string;
      cpu_percent: number;
      memory_percent: number;
      memory_used_gb: number;
      memory_total_gb: number;
      storage_percent: number;
      storage_used_gb: number;
      storage_total_gb: number;
      containers_running: number;
      started_at: string | null;
    }[];
    is_multi_host?: boolean;
  } | null>(null);

  // Port manager for interface auto-assignment
  const portManager = usePortManager(nodes, links);

  // WebSocket hook for real-time state updates
  // Updates nodeStates; runtimeStates are derived automatically via useMemo
  const handleWSNodeStateChange = useCallback((nodeId: string, wsState: NodeStateData) => {
    // Update nodeStates record — runtimeStates derived automatically
    setNodeStates((prev) => {
      const guardExpiry = optimisticGuardRef.current.get(nodeId);
      const isGuarded = guardExpiry && Date.now() < guardExpiry;
      const localState = prev[nodeId];

      // During optimistic guard window: only accept WS updates that show
      // equal or forward progress (e.g., stopping→stopped is OK, stopping→running is not)
      if (isGuarded && localState) {
        const localActual = localState.actual_state;
        const wsDisplay = wsState.display_state;
        // If local state is transitional and WS shows a non-matching transitional or
        // the wrong terminal state, preserve the local optimistic state
        if (localActual === 'stopping' && wsDisplay && wsDisplay !== 'stopping' && wsDisplay !== 'stopped' && wsDisplay !== 'error') {
          return prev;
        }
        if (localActual === 'starting' && wsDisplay && wsDisplay !== 'starting' && wsDisplay !== 'running' && wsDisplay !== 'error') {
          return prev;
        }
        // Forward progress — clear the guard since server has caught up
        optimisticGuardRef.current.delete(nodeId);
      }

      return {
        ...prev,
        [nodeId]: {
          ...prev[nodeId],
          id: wsState.node_id,
          lab_id: activeLab?.id || '',
          node_id: wsState.node_id,
          node_name: wsState.node_name,
          desired_state: wsState.desired_state,
          actual_state: wsState.actual_state,
          error_message: wsState.error_message,
          is_ready: wsState.is_ready,
          host_id: wsState.host_id,
          host_name: wsState.host_name,
          image_sync_status: wsState.image_sync_status,
          image_sync_message: wsState.image_sync_message,
          will_retry: wsState.will_retry,
          enforcement_attempts: wsState.enforcement_attempts,
          max_enforcement_attempts: wsState.max_enforcement_attempts,
          display_state: wsState.display_state,
          starting_started_at: wsState.starting_started_at,
          created_at: prev[nodeId]?.created_at || new Date().toISOString(),
          updated_at: new Date().toISOString(),
        } as NodeStateEntry,
      };
    });

    // Show toast for nodes entering error state (suppress if will_retry)
    if (wsState.actual_state === 'error' && wsState.error_message && !wsState.will_retry) {
      addNotification(
        'error',
        `Node Error: ${wsState.node_name}`,
        wsState.error_message
      );
    }
  }, [activeLab?.id, addNotification]);

  // Handle WebSocket job progress - show toast for job failures/completions
  const handleWSJobProgress = useCallback((job: JobProgressData) => {
    const prefix = job.action.startsWith('sync:') ? 'sync-' : '';
    if (job.status === 'failed' && job.error_message) {
      addNotification(
        'error',
        `Job Failed: ${job.action}`,
        job.error_message,
        { jobId: job.job_id, category: `${prefix}job-failed` }
      );
    } else if (job.status === 'completed') {
      addNotification(
        'success',
        `Job Completed: ${job.action}`,
        job.progress_message || 'Operation completed successfully',
        { jobId: job.job_id, category: `${prefix}job-complete` }
      );
    }
  }, [addNotification]);

  const {
    isConnected: wsConnected,
    reconnectAttempts: wsReconnectAttempts,
    refresh: wsRefresh,
  } = useLabStateWS(activeLab?.id || null, {
    onNodeStateChange: handleWSNodeStateChange,
    onJobProgress: handleWSJobProgress,
    enabled: !!activeLab,
  });

  // Keep refs in sync with state for debounced saves (avoids stale closure issues)
  useEffect(() => { nodesRef.current = nodes; }, [nodes]);
  useEffect(() => { linksRef.current = links; }, [links]);
  useEffect(() => { annotationsRef.current = annotations; }, [annotations]);

  const addTaskLogEntry = useCallback((level: TaskLogEntry['level'], message: string, jobId?: string) => {
    const id = `log-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    setTaskLog((prev) => [...prev.slice(-99), { id, timestamp: new Date(), level, message, jobId }]);
  }, []);

  const clearTaskLog = useCallback(() => {
    const now = Date.now();
    setTaskLogClearedAt(now);
    localStorage.setItem('archetype_tasklog_cleared_at', now.toString());
  }, []);

  const filteredTaskLog = useMemo(
    () => taskLog.filter((entry) => entry.timestamp.getTime() > taskLogClearedAt),
    [taskLog, taskLogClearedAt]
  );

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

  // Build current layout from state
  const buildLayoutFromState = useCallback(
    (currentNodes: Node[], currentAnnotations: Annotation[]): LabLayout => {
      const nodeLayouts: Record<string, { x: number; y: number; label?: string; color?: string }> = {};
      currentNodes.forEach((node) => {
        nodeLayouts[node.id] = {
          x: node.x,
          y: node.y,
          label: node.label,
        };
      });
      return {
        version: 1,
        nodes: nodeLayouts,
        annotations: currentAnnotations.map((ann) => ({
          id: ann.id,
          type: ann.type,
          x: ann.x,
          y: ann.y,
          width: ann.width,
          height: ann.height,
          text: ann.text,
          color: ann.color,
          fontSize: ann.fontSize,
          targetX: ann.targetX,
          targetY: ann.targetY,
          zIndex: ann.zIndex,
        })),
      };
    },
    []
  );

  // Save layout to backend (debounced)
  const saveLayout = useCallback(
    async (labId: string, currentNodes: Node[], currentAnnotations: Annotation[]) => {
      if (currentNodes.length === 0) return;
      const layout = buildLayoutFromState(currentNodes, currentAnnotations);
      try {
        await studioRequest(`/labs/${labId}/layout`, {
          method: 'PUT',
          body: JSON.stringify(layout),
        });
        layoutDirtyRef.current = false;
      } catch (error) {
        console.error('Failed to save layout:', error);
      }
    },
    [buildLayoutFromState, studioRequest]
  );

  // Trigger debounced layout save
  // Uses refs to read current state at save time, avoiding stale closure issues
  const triggerLayoutSave = useCallback(() => {
    if (!activeLab) return;
    layoutDirtyRef.current = true;
    if (saveLayoutTimeoutRef.current) {
      window.clearTimeout(saveLayoutTimeoutRef.current);
    }
    const labId = activeLab.id;
    saveLayoutTimeoutRef.current = window.setTimeout(() => {
      if (layoutDirtyRef.current) {
        // Read current state from refs to get latest values
        saveLayout(labId, nodesRef.current, annotationsRef.current);
      }
    }, 500);
  }, [activeLab, saveLayout]);

  // Save topology to backend (auto-save on changes)
  const saveTopology = useCallback(
    async (labId: string, currentNodes: Node[], currentLinks: Link[]) => {
      if (currentNodes.length === 0) return;
      const graph: TopologyGraph = {
        nodes: currentNodes.map((node) => {
          // Handle external network nodes
          if (isExternalNetworkNode(node)) {
            return {
              id: node.id,
              name: node.name,
              node_type: 'external',
              managed_interface_id: node.managedInterfaceId,
              connection_type: node.connectionType,
              parent_interface: node.parentInterface,
              vlan_id: node.vlanId,
              bridge_name: node.bridgeName,
              host: node.host,
            };
          }
          // Handle device nodes
          const deviceNode = node as DeviceNode;
          return {
            id: node.id,
            name: node.name,
            node_type: 'device',
            // Include container_name for backend container identity (immutable after first save)
            container_name: deviceNode.container_name,
            device: deviceNode.model,
            version: deviceNode.version,
            host: deviceNode.host,
          };
        }),
        links: currentLinks.map((link) => ({
          endpoints: [
            { node: link.source, ifname: link.sourceInterface },
            { node: link.target, ifname: link.targetInterface },
          ],
        })),
      };
      try {
        await studioRequest(`/labs/${labId}/update-topology`, {
          method: 'POST',
          body: JSON.stringify(graph),
        });
        topologyDirtyRef.current = false;
        addTaskLogEntry('info', 'Topology auto-saved');
      } catch (error) {
        console.error('Failed to save topology:', error);
      }
    },
    [studioRequest, addTaskLogEntry]
  );

  // Trigger debounced topology save
  // Uses refs to read current state at save time, avoiding stale closure issues
  const triggerTopologySave = useCallback(() => {
    if (!activeLab) return;
    topologyDirtyRef.current = true;
    if (saveTopologyTimeoutRef.current) {
      window.clearTimeout(saveTopologyTimeoutRef.current);
    }
    const labId = activeLab.id;
    saveTopologyTimeoutRef.current = window.setTimeout(() => {
      if (topologyDirtyRef.current) {
        // Read current state from refs to get latest values
        saveTopology(labId, nodesRef.current, linksRef.current);
      }
    }, 2000); // 2 second debounce for topology saves
  }, [activeLab, saveTopology]);

  // Flush any pending topology save immediately (for use before deploy)
  // Returns a promise that resolves when the save is complete
  const flushTopologySave = useCallback(async () => {
    if (!activeLab) return;
    // Cancel pending debounced save
    if (saveTopologyTimeoutRef.current) {
      window.clearTimeout(saveTopologyTimeoutRef.current);
      saveTopologyTimeoutRef.current = null;
    }
    // If dirty, save immediately
    if (topologyDirtyRef.current) {
      await saveTopology(activeLab.id, nodesRef.current, linksRef.current);
    }
  }, [activeLab, saveTopology]);

  const loadLabs = useCallback(async () => {
    const data = await studioRequest<{ labs: LabSummary[] }>('/labs');
    setLabs(data.labs || []);
  }, [studioRequest]);

  const loadAgents = useCallback(async () => {
    try {
      const data = await studioRequest<{ id: string; name: string; address: string; status: string }[]>('/agents');
      setAgents((data || []).filter((a) => a.status === 'online').map((a) => ({ id: a.id, name: a.name })));
    } catch {
      // Agents may not be available
    }
  }, [studioRequest]);

  const loadSystemMetrics = useCallback(async () => {
    try {
      const data = await studioRequest<{
        agents: { online: number; total: number };
        containers: { running: number; total: number };
        cpu_percent: number;
        memory_percent: number;
        memory?: { used_gb: number; total_gb: number; percent: number };
        storage?: { used_gb: number; total_gb: number; percent: number };
        labs_running: number;
        labs_total: number;
        per_host?: {
          id: string;
          name: string;
          cpu_percent: number;
          memory_percent: number;
          memory_used_gb: number;
          memory_total_gb: number;
          storage_percent: number;
          storage_used_gb: number;
          storage_total_gb: number;
          containers_running: number;
          started_at: string | null;
        }[];
        is_multi_host?: boolean;
      }>('/dashboard/metrics');
      setSystemMetrics(data);
    } catch {
      // Metrics endpoint may fail - that's ok
    }
  }, [studioRequest]);

  const loadLabStatuses = useCallback(async (labIds: string[]) => {
    const statuses: Record<string, { running: number; total: number }> = {};
    await Promise.all(
      labIds.map(async (labId) => {
        try {
          const statusData = await studioRequest<{ nodes?: { name: string; status: string }[] }>(`/labs/${labId}/status`);
          if (statusData.nodes) {
            const running = statusData.nodes.filter((n) => n.status === 'running').length;
            statuses[labId] = { running, total: statusData.nodes.length };
          }
        } catch {
          // Lab may not be deployed - that's ok
        }
      })
    );
    setLabStatuses(statuses);
  }, [studioRequest]);

  const loadLayout = useCallback(async (labId: string): Promise<LabLayout | null> => {
    try {
      return await studioRequest<LabLayout>(`/labs/${labId}/layout`);
    } catch {
      // Layout not found is expected for new labs
      return null;
    }
  }, [studioRequest]);

  const loadGraph = useCallback(async (labId: string) => {
    try {
      const graph = await studioRequest<TopologyGraph>(`/labs/${labId}/export-graph`);
      const layout = await loadLayout(labId);

      // Build nodes with layout positions if available
      let newNodes = buildGraphNodes(graph, deviceModels);
      if (layout?.nodes) {
        newNodes = newNodes.map((node) => {
          const nodeLayout = layout.nodes[node.id];
          if (nodeLayout) {
            return {
              ...node,
              x: nodeLayout.x,
              y: nodeLayout.y,
              label: nodeLayout.label ?? node.label,
            };
          }
          return node;
        });
      }
      setNodes(newNodes);
      setLinks(buildGraphLinks(graph));

      // Restore annotations from layout
      if (layout?.annotations && layout.annotations.length > 0) {
        setAnnotations(
          layout.annotations.map((ann) => ({
            id: ann.id,
            type: ann.type as AnnotationType,
            x: ann.x,
            y: ann.y,
            width: ann.width,
            height: ann.height,
            text: ann.text,
            color: ann.color,
            fontSize: ann.fontSize,
            targetX: ann.targetX,
            targetY: ann.targetY,
            zIndex: ann.zIndex,
          }))
        );
      } else {
        setAnnotations([]);
      }

      layoutDirtyRef.current = false;
    } catch {
      // New lab with no topology - clear state
      setNodes([]);
      setLinks([]);
      setAnnotations([]);
      layoutDirtyRef.current = false;
    }
  }, [deviceModels, studioRequest, loadLayout]);

  // Load node states from the backend (per-node desired/actual state)
  // runtimeStates are derived automatically via useMemo
  const loadNodeStates = useCallback(async (labId: string, currentNodes: Node[]) => {
    try {
      const data = await studioRequest<{ nodes: NodeStateEntry[] }>(`/labs/${labId}/nodes/states`);
      const statesByNodeId: Record<string, NodeStateEntry> = {};

      (data.nodes || []).forEach((state) => {
        statesByNodeId[state.node_id] = state;
      });

      // Merge with existing state, preserving optimistic transitional states
      // that the server hasn't caught up with yet
      setNodeStates(prev => {
        const now = Date.now();
        const merged = { ...statesByNodeId };
        for (const [id, guardExpiry] of optimisticGuardRef.current.entries()) {
          if (now < guardExpiry && prev[id] && merged[id]) {
            const localActual = prev[id].actual_state;
            const serverDisplay = merged[id].display_state;
            // Keep local optimistic state if server hasn't reflected the transition yet
            if (localActual === 'stopping' && serverDisplay !== 'stopping' && serverDisplay !== 'stopped' && serverDisplay !== 'error') {
              merged[id] = { ...merged[id], actual_state: 'stopping', display_state: 'stopping' };
            } else if (localActual === 'starting' && serverDisplay !== 'starting' && serverDisplay !== 'running' && serverDisplay !== 'error') {
              merged[id] = { ...merged[id], actual_state: 'starting', display_state: 'starting' };
            } else {
              // Server caught up — clear the guard
              optimisticGuardRef.current.delete(id);
            }
          }
        }
        return merged;
      });
    } catch {
      // Node states endpoint may fail for new labs - use job-based fallback
    }
  }, [studioRequest]);

  const loadJobs = useCallback(async (labId: string, currentNodes: Node[]) => {
    // Also load jobs for job log display
    const data = await studioRequest<{ jobs: any[] }>(`/labs/${labId}/jobs`);
    setJobs(data.jobs || []);
  }, [studioRequest]);

  // Refresh node states from the agent (queries actual container status)
  // This is called once when entering a lab to ensure states are fresh
  const refreshNodeStatesFromAgent = useCallback(async (labId: string) => {
    try {
      await studioRequest(`/labs/${labId}/nodes/refresh`, { method: 'POST' });
    } catch {
      // Agent may be unavailable - states will still load from DB
      console.warn('Failed to refresh node states from agent');
    }
  }, [studioRequest]);

  useEffect(() => {
    loadLabs();
    loadSystemMetrics();
    loadAgents();
  }, [loadLabs, loadSystemMetrics, loadAgents]);

  // Load lab statuses when labs change
  useEffect(() => {
    if (labs.length > 0 && !activeLab) {
      loadLabStatuses(labs.map((lab) => lab.id));
    }
  }, [labs, activeLab, loadLabStatuses]);

  // Poll for system metrics (both dashboard and lab views)
  useEffect(() => {
    const timer = setInterval(() => {
      loadSystemMetrics();
      // Only poll lab statuses when on dashboard
      if (!activeLab && labs.length > 0) {
        loadLabStatuses(labs.map((lab) => lab.id));
      }
    }, 10000);
    return () => clearInterval(timer);
  }, [activeLab, labs, loadSystemMetrics, loadLabStatuses]);

  useEffect(() => {
    if (!activeLab) return;
    loadGraph(activeLab.id);
  }, [activeLab, loadGraph]);

  // Refresh node states from agent when entering a lab
  // This ensures we have accurate container status, especially after restarts
  useEffect(() => {
    if (!activeLab) return;
    refreshNodeStatesFromAgent(activeLab.id);
  }, [activeLab, refreshNodeStatesFromAgent]);

  useEffect(() => {
    if (!activeLab || nodes.length === 0) return;
    loadNodeStates(activeLab.id, nodes);
    loadJobs(activeLab.id, nodes);
  }, [activeLab, nodes, loadNodeStates, loadJobs]);

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
    }, interval);
    return () => clearInterval(timer);
  }, [activeLab, nodes, loadNodeStates, loadJobs, wsConnected]);

  // Cleanup: save layout/topology and clear timeouts on unmount
  useEffect(() => {
    return () => {
      if (saveLayoutTimeoutRef.current) {
        window.clearTimeout(saveLayoutTimeoutRef.current);
      }
      if (saveTopologyTimeoutRef.current) {
        window.clearTimeout(saveTopologyTimeoutRef.current);
      }
    };
  }, []);

  // Track job status changes, log them, and trigger notifications
  useEffect(() => {
    const prevStatuses = prevJobsRef.current;
    const newStatuses = new Map<string, string>();

    // On initial load, just populate the ref without logging
    // This prevents re-logging all existing jobs on page refresh
    if (isInitialJobLoadRef.current && jobs.length > 0) {
      for (const job of jobs) {
        newStatuses.set(job.id, job.status);
      }
      prevJobsRef.current = newStatuses;
      isInitialJobLoadRef.current = false;
      return;
    }

    // Determine notification category prefix based on job action
    const categoryPrefix = (action: string) => action.startsWith('sync:') ? 'sync-' : '';

    for (const job of jobs) {
      const jobKey = job.id;
      const prevStatus = prevStatuses.get(jobKey);
      newStatuses.set(jobKey, job.status);
      const prefix = categoryPrefix(job.action);

      if (prevStatus && prevStatus !== job.status) {
        const actionLabel = job.action.startsWith('node:')
          ? `Node ${job.action.split(':')[1]} (${job.action.split(':')[2]})`
          : job.action.toUpperCase();

        if (job.status === 'running') {
          addTaskLogEntry('info', `Job running: ${actionLabel}`, job.id);
          addNotification('info', `${actionLabel} started`, undefined, {
            jobId: job.id, labId: job.lab_id, category: `${prefix}job-start`,
          });
        } else if (job.status === 'completed') {
          addTaskLogEntry('success', `Job completed: ${actionLabel}`, job.id);
          addNotification('success', `${actionLabel} completed`, undefined, {
            jobId: job.id, labId: job.lab_id, category: `${prefix}job-complete`,
          });
        } else if (job.status === 'failed') {
          const errorDetail = job.error_summary ? `: ${job.error_summary}` : '';
          addTaskLogEntry('error', `Job failed: ${actionLabel}${errorDetail}`, job.id);
          addNotification('error', `${actionLabel} failed`, job.error_summary || 'Check logs for details', {
            jobId: job.id, labId: job.lab_id, category: `${prefix}job-failed`,
          });
        }
      } else if (!prevStatus) {
        // New job - log based on its initial status
        const actionLabel = job.action.startsWith('node:')
          ? `Node ${job.action.split(':')[1]} (${job.action.split(':')[2]})`
          : job.action.toUpperCase();

        if (job.status === 'queued') {
          addTaskLogEntry('info', `Job queued: ${actionLabel}`, job.id);
        } else if (job.status === 'running') {
          addTaskLogEntry('info', `Job running: ${actionLabel}`, job.id);
          addNotification('info', `${actionLabel} started`, undefined, {
            jobId: job.id, labId: job.lab_id, category: `${prefix}job-start`,
          });
        } else if (job.status === 'completed') {
          addTaskLogEntry('success', `Job completed: ${actionLabel}`, job.id);
          addNotification('success', `${actionLabel} completed`, undefined, {
            jobId: job.id, labId: job.lab_id, category: `${prefix}job-complete`,
          });
        } else if (job.status === 'failed') {
          const errorDetail = job.error_summary ? `: ${job.error_summary}` : '';
          addTaskLogEntry('error', `Job failed: ${actionLabel}${errorDetail}`, job.id);
          addNotification('error', `${actionLabel} failed`, job.error_summary || 'Check logs for details', {
            jobId: job.id, labId: job.lab_id, category: `${prefix}job-failed`,
          });
        }
      }
    }

    prevJobsRef.current = newStatuses;
  }, [jobs, addTaskLogEntry, addNotification]);

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
    // Clear any pending save timeout
    if (saveLayoutTimeoutRef.current) {
      window.clearTimeout(saveLayoutTimeoutRef.current);
      saveLayoutTimeoutRef.current = null;
    }
    // Reset job tracking for new lab context
    setJobs([]);
    prevJobsRef.current = new Map();
    isInitialJobLoadRef.current = true;
    setActiveLab(lab);
    setAnnotations([]);
    setConsoleWindows([]);
    setDockedConsoles([]);
    setActiveBottomTabId('log');
    setSelectedId(null);
    setView('designer');
  };

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

  const handleAddDevice = (model: DeviceModel, x?: number, y?: number) => {
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
      cpu: 1,
      memory: 1024,
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

  const handleAddAnnotation = (type: AnnotationType) => {
    const id = Math.random().toString(36).slice(2, 9);
    const newAnn: Annotation = {
      id,
      type,
      x: 400,
      y: 300,
      text: type === 'text' ? 'New Label' : type === 'caption' ? 'Note here' : '',
      color: effectiveMode === 'dark' ? '#3b82f6' : '#2563eb',
      width: type === 'rect' || type === 'circle' ? 100 : undefined,
      height: type === 'rect' ? 60 : undefined,
      targetX: type === 'arrow' ? 500 : undefined,
      targetY: type === 'arrow' ? 400 : undefined,
    };
    setAnnotations((prev) => [...prev, newAnn]);
    setSelectedId(id);
    triggerLayoutSave();
  };

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
      // Optimistically update nodeStates — runtimeStates derived automatically
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
  }, [activeLab, nodes, pendingNodeOps, studioRequest, addTaskLogEntry, loadNodeStates, loadJobs]);

  const handleOpenConsole = (nodeId: string) => {
    const node = nodes.find((n) => n.id === nodeId);
    if (!node) return;

    const openInPanel = preferences?.canvas_settings?.consoleInBottomPanel ?? false;

    if (openInPanel) {
      // Check if already docked
      if (dockedConsoles.some((c) => c.nodeId === nodeId)) {
        setActiveBottomTabId(nodeId);
        setIsTaskLogVisible(true);
        return;
      }
      // Add to docked consoles
      setDockedConsoles((prev) => [...prev, { nodeId, nodeName: node.name }]);
      setActiveBottomTabId(nodeId);
      setIsTaskLogVisible(true);
    } else {
      // Existing floating window logic
      setConsoleWindows((prev) => {
        const existingWin = prev.find((win) => win.deviceIds.includes(nodeId));
        if (existingWin) {
          return prev.map((win) => (win.id === existingWin.id ? { ...win, activeDeviceId: nodeId } : win));
        }
        const newWin: ConsoleWindow = {
          id: Math.random().toString(36).slice(2, 9),
          deviceIds: [nodeId],
          activeDeviceId: nodeId,
          x: 100,
          y: 100,
          isExpanded: true,
        };
        return [...prev, newWin];
      });
    }
  };

  // Merge all tabs from source window into target window
  const handleMergeWindows = useCallback((sourceWindowId: string, targetWindowId: string) => {
    setConsoleWindows((prev) => {
      const sourceWin = prev.find((w) => w.id === sourceWindowId);
      const targetWin = prev.find((w) => w.id === targetWindowId);
      if (!sourceWin || !targetWin) return prev;

      // Merge deviceIds, using Set to deduplicate
      const mergedDeviceIds = [...new Set([...targetWin.deviceIds, ...sourceWin.deviceIds])];

      return prev
        .filter((w) => w.id !== sourceWindowId) // Remove source window
        .map((w) =>
          w.id === targetWindowId
            ? { ...w, deviceIds: mergedDeviceIds }
            : w
        );
    });
  }, []);

  // Split a tab out of a window into a new separate window
  const handleSplitTab = useCallback((windowId: string, deviceId: string, x: number, y: number) => {
    setConsoleWindows((prev) => {
      const sourceWin = prev.find((w) => w.id === windowId);
      if (!sourceWin || sourceWin.deviceIds.length <= 1) return prev;

      // Create new window for the split tab
      const newWin: ConsoleWindow = {
        id: Math.random().toString(36).slice(2, 9),
        deviceIds: [deviceId],
        activeDeviceId: deviceId,
        x: Math.max(0, x),
        y: Math.max(0, y),
        isExpanded: true,
      };

      // Remove tab from source window
      const updatedWindows = prev.map((w) => {
        if (w.id !== windowId) return w;
        const newDeviceIds = w.deviceIds.filter((id) => id !== deviceId);
        return {
          ...w,
          deviceIds: newDeviceIds,
          activeDeviceId: w.activeDeviceId === deviceId ? newDeviceIds[0] : w.activeDeviceId,
        };
      });

      return [...updatedWindows, newWin];
    });
  }, []);

  // Reorder tabs within a console window
  const handleReorderTab = useCallback((windowId: string, fromIndex: number, toIndex: number) => {
    setConsoleWindows((prev) =>
      prev.map((win) => {
        if (win.id !== windowId) return win;
        const newDeviceIds = [...win.deviceIds];
        const [movedId] = newDeviceIds.splice(fromIndex, 1);
        // Adjust toIndex if moving right (since we removed an element)
        const adjustedTo = toIndex > fromIndex ? toIndex - 1 : toIndex;
        newDeviceIds.splice(adjustedTo, 0, movedId);
        return { ...win, deviceIds: newDeviceIds };
      })
    );
  }, []);

  // Toggle minimize state of a console window
  const handleToggleMinimize = useCallback((windowId: string) => {
    setConsoleWindows((prev) =>
      prev.map((win) =>
        win.id === windowId ? { ...win, isExpanded: !win.isExpanded } : win
      )
    );
  }, []);

  // Undock a console from the bottom panel to a floating window
  const handleUndockConsole = useCallback((nodeId: string, x: number, y: number) => {
    // Remove from docked
    setDockedConsoles((prev) => prev.filter((c) => c.nodeId !== nodeId));
    if (activeBottomTabId === nodeId) {
      setActiveBottomTabId('log');
    }

    // Add to floating windows
    const newWin: ConsoleWindow = {
      id: Math.random().toString(36).slice(2, 9),
      deviceIds: [nodeId],
      activeDeviceId: nodeId,
      x: Math.max(0, x),
      y: Math.max(0, y),
      isExpanded: true,
    };
    setConsoleWindows((prev) => [...prev, newWin]);
  }, [activeBottomTabId]);

  // Dock a floating window to the bottom panel
  const handleDockWindow = useCallback((windowId: string) => {
    const win = consoleWindows.find((w) => w.id === windowId);
    if (!win) return;

    // Add all tabs to docked
    win.deviceIds.forEach((nodeId) => {
      const node = nodes.find((n) => n.id === nodeId);
      if (!node) return;
      if (dockedConsoles.some((c) => c.nodeId === nodeId)) return;
      setDockedConsoles((prev) => [...prev, { nodeId, nodeName: node.name }]);
    });

    setActiveBottomTabId(win.activeDeviceId);
    setIsTaskLogVisible(true);

    // Remove floating window
    setConsoleWindows((prev) => prev.filter((w) => w.id !== windowId));
  }, [consoleWindows, nodes, dockedConsoles]);

  // Close a floating console window
  const handleCloseConsoleWindow = useCallback((id: string) => {
    setConsoleWindows((prev) => prev.filter((win) => win.id !== id));
  }, []);

  // Close a tab within a console window
  const handleCloseConsoleTab = useCallback((winId: string, nodeId: string) => {
    setConsoleWindows((prev) =>
      prev
        .map((win) => {
          if (win.id !== winId) return win;
          const nextIds = win.deviceIds.filter((did) => did !== nodeId);
          const nextActive = win.activeDeviceId === nodeId ? nextIds[0] || '' : win.activeDeviceId;
          return { ...win, deviceIds: nextIds, activeDeviceId: nextActive };
        })
        .filter((win) => win.deviceIds.length > 0)
    );
  }, []);

  // Set active tab in a console window
  const handleSetActiveConsoleTab = useCallback((winId: string, nodeId: string) => {
    setConsoleWindows((prev) => prev.map((win) => (win.id === winId ? { ...win, activeDeviceId: nodeId } : win)));
  }, []);

  // Update console window position
  const handleUpdateConsoleWindowPos = useCallback((id: string, x: number, y: number) => {
    setConsoleWindows((prev) => prev.map((win) => (win.id === id ? { ...win, x, y } : win)));
  }, []);

  // Close a docked console tab
  const handleCloseDockedConsole = useCallback((nodeId: string) => {
    setDockedConsoles((prev) => prev.filter((c) => c.nodeId !== nodeId));
    if (activeBottomTabId === nodeId) {
      setActiveBottomTabId('log');
    }
  }, [activeBottomTabId]);

  // Reorder docked console tabs
  const handleReorderDockedTab = useCallback((fromIndex: number, toIndex: number) => {
    setDockedConsoles((prev) => {
      const newTabs = [...prev];
      const [movedTab] = newTabs.splice(fromIndex, 1);
      // Adjust toIndex if moving right (since we removed an element)
      const adjustedTo = toIndex > fromIndex ? toIndex - 1 : toIndex;
      newTabs.splice(adjustedTo, 0, movedTab);
      return newTabs;
    });
  }, []);

  const handleOpenConfigViewer = useCallback((nodeId?: string, nodeName?: string) => {
    if (nodeId && nodeName) {
      setConfigViewerNode({ id: nodeId, name: nodeName });
    } else {
      setConfigViewerNode(null);
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

  const handleCloseConfigViewer = useCallback(() => {
    setConfigViewerOpen(false);
    setConfigViewerNode(null);
  }, []);

  const handleTaskLogEntryClick = useCallback((entry: TaskLogEntry) => {
    if (entry.jobId) {
      setJobLogModalJobId(entry.jobId);
      setJobLogModalOpen(true);
    }
  }, []);

  const handleCloseJobLogModal = useCallback(() => {
    setJobLogModalOpen(false);
    setJobLogModalJobId(null);
  }, []);

  const handleNodeMove = useCallback((id: string, x: number, y: number) => {
    setNodes((prev) => prev.map((node) => (node.id === id ? { ...node, x, y } : node)));
    triggerLayoutSave();
  }, [triggerLayoutSave]);

  const handleAnnotationMove = useCallback((id: string, x: number, y: number) => {
    setAnnotations((prev) => prev.map((ann) => (ann.id === id ? { ...ann, x, y } : ann)));
    triggerLayoutSave();
  }, [triggerLayoutSave]);

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
    setNodes((prev) => prev.map((node) => (node.id === id ? { ...node, ...updates } as Node : node)));
    // Auto-save topology if name, model, version, or host changed (device nodes only)
    const deviceUpdates = updates as Partial<DeviceNode>;
    if (updates.name || deviceUpdates.model || deviceUpdates.version || deviceUpdates.host) {
      triggerTopologySave();
    }
    // Also save if external network fields change
    const extUpdates = updates as Partial<ExternalNetworkNode>;
    if (extUpdates.managedInterfaceId !== undefined || extUpdates.connectionType || extUpdates.parentInterface || extUpdates.vlanId || extUpdates.bridgeName || extUpdates.host) {
      triggerTopologySave();
    }
  };

  const handleUpdateLink = (id: string, updates: Partial<Link>) => {
    setLinks((prev) => prev.map((link) => (link.id === id ? { ...link, ...updates } : link)));
    // Auto-save topology if interface assignments changed
    if (updates.sourceInterface || updates.targetInterface) {
      triggerTopologySave();
    }
  };

  const handleUpdateAnnotation = (id: string, updates: Partial<Annotation>) => {
    setAnnotations((prev) => prev.map((ann) => (ann.id === id ? { ...ann, ...updates } : ann)));
    triggerLayoutSave();
  };

  const handleDelete = (id: string) => {
    const isAnnotation = annotations.some((ann) => ann.id === id);
    const isNode = nodes.some((node) => node.id === id);
    const isLink = links.some((link) => link.id === id);
    setNodes((prev) => prev.filter((node) => node.id !== id));
    setLinks((prev) => prev.filter((link) => link.id !== id && link.source !== id && link.target !== id));
    setAnnotations((prev) => prev.filter((ann) => ann.id !== id));
    setSelectedId(null);
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

  const handleExportFull = async () => {
    if (!activeLab) return;
    // Save layout first to ensure we have the latest
    await saveLayout(activeLab.id, nodes, annotations);
    // Get both YAML and layout
    const [yamlData, layoutData] = await Promise.all([
      studioRequest<{ content: string }>(`/labs/${activeLab.id}/export-yaml`),
      studioRequest<LabLayout>(`/labs/${activeLab.id}/layout`).catch(() => null),
    ]);
    // Create a combined export object
    const exportData = {
      topology: yamlData.content,
      layout: layoutData,
    };
    // Download as JSON file
    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${activeLab.name.replace(/\s+/g, '_')}_full_export.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const handleDeploy = async () => {
    if (!activeLab) return;
    addTaskLogEntry('info', 'Saving topology...');
    const graph: TopologyGraph = {
      nodes: nodes.map((node) => {
        // Handle external network nodes
        if (isExternalNetworkNode(node)) {
          return {
            id: node.id,
            name: node.name,
            node_type: 'external',
            managed_interface_id: node.managedInterfaceId,
            connection_type: node.connectionType,
            parent_interface: node.parentInterface,
            vlan_id: node.vlanId,
            bridge_name: node.bridgeName,
            host: node.host,
          };
        }
        // Handle device nodes
        const deviceNode = node as DeviceNode;
        return {
          id: node.id,
          name: node.name,
          node_type: 'device',
          container_name: deviceNode.container_name,
          device: deviceNode.model,
          version: deviceNode.version,
          host: deviceNode.host,
        };
      }),
      links: links.map((link) => ({
        endpoints: [
          { node: link.source, ifname: link.sourceInterface },
          { node: link.target, ifname: link.targetInterface },
        ],
      })),
    };
    try {
      await studioRequest(`/labs/${activeLab.id}/update-topology`, {
        method: 'POST',
        body: JSON.stringify(graph),
      });

      // Pre-deploy resource capacity check
      try {
        const resourceCheck = await studioRequest<{
          sufficient: boolean;
          warnings: string[];
          errors: string[];
        }>(`/labs/${activeLab.id}/check-resources`, { method: 'POST', body: '{}' });

        if (!resourceCheck.sufficient) {
          const errorList = resourceCheck.errors.join('\n  - ');
          addTaskLogEntry('error', `Insufficient resources:\n  - ${errorList}`);
          addTaskLogEntry('info', 'Deploy cancelled. Assign nodes to different agents or free resources.');
          return;
        }

        if (resourceCheck.warnings.length > 0) {
          const warningList = resourceCheck.warnings.join('\n');
          const proceed = window.confirm(
            `Resource warnings:\n\n${warningList}\n\nProceed with deployment?`
          );
          if (!proceed) {
            addTaskLogEntry('info', 'Deploy cancelled by user due to resource warnings');
            return;
          }
          for (const w of resourceCheck.warnings) {
            addTaskLogEntry('warning', w);
          }
        }
      } catch {
        // Resource check is best-effort - don't block deploy if the endpoint fails
        addTaskLogEntry('info', 'Resource check skipped');
      }

      addTaskLogEntry('info', 'Deploying lab...');
      const jobResponse = await studioRequest<{ id: string; image_sync_events?: string[] }>(`/labs/${activeLab.id}/up`, {
        method: 'POST',
      });
      // Log image sync events if any
      if (jobResponse.image_sync_events && jobResponse.image_sync_events.length > 0) {
        for (const event of jobResponse.image_sync_events) {
          // Determine log level based on event content
          const level = event.toLowerCase().includes('failed') || event.toLowerCase().includes('error')
            ? 'warning'
            : event.toLowerCase().includes('success') || event.toLowerCase().includes('synced')
            ? 'success'
            : 'info';
          addTaskLogEntry(level, event);
        }
      }
      addTaskLogEntry('success', 'Lab deployment queued');
      loadGraph(activeLab.id);
      loadJobs(activeLab.id, nodes);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Deploy failed';
      addTaskLogEntry('error', `Deploy failed: ${message}`);
    }
  };

  const handleLogin = async (event: React.FormEvent) => {
    event.preventDefault();
    setAuthError(null);
    setAuthLoading(true);
    try {
      const body = new URLSearchParams();
      body.set('username', authEmail);
      body.set('password', authPassword);
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
      setAuthPassword('');
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
      default:
        return (
          <>
            <Sidebar categories={deviceCategories} onAddDevice={handleAddDevice} onAddAnnotation={handleAddAnnotation} onAddExternalNetwork={handleAddExternalNetwork} imageLibrary={imageLibrary} />
            <Canvas
              nodes={nodes}
              links={links}
              annotations={annotations}
              runtimeStates={runtimeStates}
              nodeStates={nodeStates}
              deviceModels={deviceModels}
              agents={agents}
              showAgentIndicators={showAgentIndicators}
              onToggleAgentIndicators={handleToggleAgentIndicators}
              onNodeMove={handleNodeMove}
              onAnnotationMove={handleAnnotationMove}
              onConnect={handleConnect}
              selectedId={selectedId}
              onSelect={setSelectedId}
              onOpenConsole={handleOpenConsole}
              onUpdateStatus={handleUpdateStatus}
              onDelete={handleDelete}
              onDropDevice={handleAddDevice}
              onDropExternalNetwork={handleAddExternalNetwork}
              onUpdateAnnotation={handleUpdateAnnotation}
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
                  agents={agents}
                  nodeStates={nodeStates}
                />
              </div>
            </div>
          </>
        );
    }
  };

  const backgroundGradient =
    effectiveMode === 'dark'
      ? 'bg-gradient-to-br from-stone-950/90 via-stone-900/82 to-stone-950/90 bg-gradient-animate backdrop-blur-[1px]'
      : 'bg-gradient-to-br from-stone-50/70 via-white/64 to-stone-100/70 bg-gradient-animate backdrop-blur-[1px]';

  if (authRequired) {
    return (
      <div className={`min-h-screen flex items-center justify-center ${backgroundGradient}`}>
        <div className="w-[420px] bg-white/90 dark:bg-stone-950/90 border border-stone-200 dark:border-stone-800 rounded-2xl shadow-2xl p-8">
          <div className="flex items-center gap-4 mb-6">
            <ArchetypeIcon size={40} className="text-sage-600 dark:text-sage-400" />
            <div>
              <h1 className="text-lg font-black text-stone-900 dark:text-white tracking-tight">Archetype Studio</h1>
              <p className="text-[10px] text-sage-600 dark:text-sage-500 font-bold uppercase tracking-widest">Sign in</p>
            </div>
          </div>
          <form onSubmit={handleLogin} className="space-y-4">
            <div className="space-y-1">
              <label className="text-[10px] font-bold text-stone-500 uppercase tracking-widest">Username</label>
              <input
                value={authEmail}
                onChange={(event) => setAuthEmail(event.target.value)}
                type="text"
                className="w-full bg-stone-100 dark:bg-stone-900 border border-stone-300 dark:border-stone-700 rounded-lg px-3 py-2 text-sm text-stone-900 dark:text-stone-100 focus:outline-none focus:border-sage-500"
              />
            </div>
            <div className="space-y-1">
              <label className="text-[10px] font-bold text-stone-500 uppercase tracking-widest">Password</label>
              <input
                value={authPassword}
                onChange={(event) => setAuthPassword(event.target.value)}
                type="password"
                className="w-full bg-stone-100 dark:bg-stone-900 border border-stone-300 dark:border-stone-700 rounded-lg px-3 py-2 text-sm text-stone-900 dark:text-stone-100 focus:outline-none focus:border-sage-500"
              />
            </div>
            {authError && <div className="text-xs text-red-500 dark:text-red-400">{authError}</div>}
            <button
              type="submit"
              disabled={authLoading}
              className="w-full bg-sage-600 hover:bg-sage-500 disabled:opacity-60 text-white px-4 py-2 rounded-lg text-xs font-bold transition-all"
            >
              {authLoading ? 'Signing in...' : 'Sign in'}
            </button>
          </form>
        </div>
      </div>
    );
  }

  if (!activeLab) {
    return (
      <Dashboard
        labs={labs}
        labStatuses={labStatuses}
        systemMetrics={systemMetrics}
        onSelect={handleSelectLab}
        onCreate={handleCreateLab}
        onDelete={handleDeleteLab}
        onRename={handleRenameLab}
      />
    );
  }

  return (
    <div className={`flex flex-col h-screen overflow-hidden select-none transition-colors duration-500 ${view === 'designer' ? '' : backgroundGradient}`}>
      <TopBar labName={activeLab.name} onExport={handleExport} onExportFull={handleExportFull} onExit={() => setActiveLab(null)} onRename={(newName) => handleRenameLab(activeLab.id, newName)} />
      <div className="h-10 bg-white/35 dark:bg-black/35 backdrop-blur-md border-b border-stone-200/70 dark:border-black/70 flex px-6 items-center gap-1 shrink-0">
        <button
          onClick={() => setView('designer')}
          className={`h-full px-4 text-[10px] font-black uppercase border-b-2 transition-all ${
            view === 'designer' ? 'text-sage-600 dark:text-sage-500 border-sage-600 dark:border-sage-500' : 'text-stone-400 dark:text-stone-500 border-transparent'
          }`}
        >
          Designer
        </button>
        <button
          onClick={() => setView('runtime')}
          className={`h-full px-4 text-[10px] font-black uppercase border-b-2 transition-all ${
            view === 'runtime' ? 'text-sage-600 dark:text-sage-500 border-sage-600 dark:border-sage-500' : 'text-stone-400 dark:text-stone-500 border-transparent'
          }`}
        >
          Runtime
        </button>
        <button
          onClick={() => setView('configs')}
          className={`h-full px-4 text-[10px] font-black uppercase border-b-2 transition-all ${
            view === 'configs' ? 'text-sage-600 dark:text-sage-500 border-sage-600 dark:border-sage-500' : 'text-stone-400 dark:text-stone-500 border-transparent'
          }`}
        >
          Configs
        </button>
        <button
          onClick={() => setView('logs')}
          className={`h-full px-4 text-[10px] font-black uppercase border-b-2 transition-all ${
            view === 'logs' ? 'text-sage-600 dark:text-sage-500 border-sage-600 dark:border-sage-500' : 'text-stone-400 dark:text-stone-500 border-transparent'
          }`}
        >
          Logs
        </button>
      </div>
      {showAdminStrip && <SystemStatusStrip metrics={systemMetrics} />}
      <AgentAlertBanner />
      <div className="flex flex-1 overflow-hidden relative">
        {renderView()}
        <ConsoleManager
          labId={activeLab.id}
          windows={consoleWindows}
          nodes={nodes}
          nodeStates={nodeStates}
          onCloseWindow={handleCloseConsoleWindow}
          onCloseTab={handleCloseConsoleTab}
          onSetActiveTab={handleSetActiveConsoleTab}
          onUpdateWindowPos={handleUpdateConsoleWindowPos}
          onMergeWindows={handleMergeWindows}
          onSplitTab={handleSplitTab}
          onReorderTab={handleReorderTab}
          onToggleMinimize={handleToggleMinimize}
          onDockWindow={handleDockWindow}
        />
      </div>
      <StatusBar nodeStates={nodeStates} wsConnected={wsConnected} reconnectAttempts={wsReconnectAttempts} />
      <TaskLogPanel
        entries={filteredTaskLog}
        isVisible={isTaskLogVisible}
        onToggle={() => setIsTaskLogVisible(!isTaskLogVisible)}
        onClear={clearTaskLog}
        onEntryClick={handleTaskLogEntryClick}
        consoleTabs={dockedConsoles}
        activeTabId={activeBottomTabId}
        onSelectTab={setActiveBottomTabId}
        onCloseConsoleTab={handleCloseDockedConsole}
        onUndockConsole={handleUndockConsole}
        onReorderTab={handleReorderDockedTab}
        labId={activeLab?.id}
        nodeStates={nodeStates}
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
      />
      <JobLogModal
        isOpen={jobLogModalOpen}
        onClose={handleCloseJobLogModal}
        labId={activeLab?.id || ''}
        jobId={jobLogModalJobId || ''}
        studioRequest={studioRequest}
      />
    </div>
  );
};

export default StudioPage;
