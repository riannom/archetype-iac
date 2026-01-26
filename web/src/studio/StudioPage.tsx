import React, { useCallback, useEffect, useMemo, useState, useRef } from 'react';
import Sidebar from './components/Sidebar';
import Canvas from './components/Canvas';
import TopBar from './components/TopBar';
import PropertiesPanel from './components/PropertiesPanel';
import ConsoleManager from './components/ConsoleManager';
import DeviceManager from './components/DeviceManager';
import RuntimeControl, { RuntimeStatus } from './components/RuntimeControl';
import StatusBar from './components/StatusBar';
import TaskLogPanel, { TaskLogEntry } from './components/TaskLogPanel';
import Dashboard from './components/Dashboard';
import SystemStatusStrip from './components/SystemStatusStrip';
import { Annotation, AnnotationType, ConsoleWindow, DeviceModel, DeviceType, LabLayout, Link, Node } from './types';
import { API_BASE_URL, apiRequest } from '../api';
import { TopologyGraph } from '../types';
import { usePortManager } from './hooks/usePortManager';
import { useTheme } from '../theme/index';
import './studio.css';
import 'xterm/css/xterm.css';

interface LabSummary {
  id: string;
  name: string;
  created_at?: string;
}

interface DeviceCatalogEntry {
  id: string;
  label: string;
  support?: string;
}

interface ImageLibraryEntry {
  id: string;
  kind: string;
  reference: string;
  device_id?: string | null;
  filename?: string;
  version?: string | null;
}

interface DeviceSubCategory {
  name: string;
  models: DeviceModel[];
}

interface DeviceCategory {
  name: string;
  models?: DeviceModel[];
  subCategories?: DeviceSubCategory[];
}

interface CustomDevice {
  id: string;
  label: string;
}

const DEFAULT_ICON = 'fa-microchip';

const guessDeviceType = (id: string, label: string): DeviceType => {
  const token = `${id} ${label}`.toLowerCase();
  if (token.includes('switch')) return DeviceType.SWITCH;
  if (token.includes('router')) return DeviceType.ROUTER;
  if (token.includes('firewall')) return DeviceType.FIREWALL;
  if (token.includes('linux') || token.includes('server') || token.includes('host')) return DeviceType.HOST;
  return DeviceType.CONTAINER;
};

const buildDeviceModels = (devices: DeviceCatalogEntry[], images: ImageLibraryEntry[], customDevices: CustomDevice[]): DeviceModel[] => {
  const versionsByDevice = new Map<string, Set<string>>();
  const imageDeviceIds = new Set<string>();
  images.forEach((image) => {
    if (!image.device_id) return;
    imageDeviceIds.add(image.device_id);
    const versions = versionsByDevice.get(image.device_id) || new Set<string>();
    if (image.version) {
      versions.add(image.version);
    }
    versionsByDevice.set(image.device_id, versions);
  });

  const catalogMap = new Map(devices.map((device) => [device.id, device]));
  const customMap = new Map(customDevices.map((device) => [device.id, device]));
  const deviceIds = new Set<string>(devices.map((device) => device.id));
  imageDeviceIds.forEach((deviceId) => deviceIds.add(deviceId));
  customDevices.forEach((device) => deviceIds.add(device.id));

  return Array.from(deviceIds).map((deviceId) => {
    const device = catalogMap.get(deviceId);
    const custom = customMap.get(deviceId);
    const label = device?.label || custom?.label || deviceId;
    const versions = Array.from(versionsByDevice.get(deviceId) || []);
    return {
      id: deviceId,
      type: guessDeviceType(deviceId, label),
      name: label,
      icon: DEFAULT_ICON,
      versions: versions.length > 0 ? versions : ['default'],
      isActive: true,
      vendor: device?.support || custom?.label ? 'custom' : 'custom',
    };
  });
};

const buildGraphNodes = (graph: TopologyGraph, models: DeviceModel[]): Node[] => {
  const modelMap = new Map(models.map((model) => [model.id, model]));
  return graph.nodes.map((node, index) => {
    const modelId = node.device || node.id;
    const model = modelMap.get(modelId);
    const column = index % 5;
    const row = Math.floor(index / 5);
    return {
      id: node.id,
      name: node.name || node.id,
      type: model?.type || DeviceType.CONTAINER,
      model: model?.id || modelId,
      version: node.version || model?.versions?.[0] || 'default',
      x: 220 + column * 160,
      y: 180 + row * 140,
      cpu: 1,
      memory: 1024,
    };
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
  const [labs, setLabs] = useState<LabSummary[]>([]);
  const [activeLab, setActiveLab] = useState<LabSummary | null>(null);
  const [view, setView] = useState<'designer' | 'images' | 'runtime'>('designer');
  const [nodes, setNodes] = useState<Node[]>([]);
  const [links, setLinks] = useState<Link[]>([]);
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [runtimeStates, setRuntimeStates] = useState<Record<string, RuntimeStatus>>({});
  const [consoleWindows, setConsoleWindows] = useState<ConsoleWindow[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showYamlModal, setShowYamlModal] = useState(false);
  const [yamlContent, setYamlContent] = useState('');
  const [deviceCatalog, setDeviceCatalog] = useState<DeviceCatalogEntry[]>([]);
  const [vendorCategories, setVendorCategories] = useState<DeviceCategory[]>([]);
  const [imageLibrary, setImageLibrary] = useState<ImageLibraryEntry[]>([]);
  const [imageCatalog, setImageCatalog] = useState<Record<string, { clab?: string; libvirt?: string; virtualbox?: string; caveats?: string[] }>>({});
  const [customDevices, setCustomDevices] = useState<CustomDevice[]>(() => {
    const stored = localStorage.getItem('aura_custom_devices');
    if (!stored) return [];
    try {
      const parsed = JSON.parse(stored) as CustomDevice[];
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  });
  const [authRequired, setAuthRequired] = useState(false);
  const [authEmail, setAuthEmail] = useState('');
  const [authPassword, setAuthPassword] = useState('');
  const [authError, setAuthError] = useState<string | null>(null);
  const [authLoading, setAuthLoading] = useState(false);
  const [taskLog, setTaskLog] = useState<TaskLogEntry[]>([]);
  const [isTaskLogVisible, setIsTaskLogVisible] = useState(true);
  const [jobs, setJobs] = useState<any[]>([]);
  const prevJobsRef = useRef<Map<string, string>>(new Map());
  const [labStatuses, setLabStatuses] = useState<Record<string, { running: number; total: number }>>({});
  const layoutDirtyRef = useRef(false);
  const saveLayoutTimeoutRef = useRef<number | null>(null);
  const [systemMetrics, setSystemMetrics] = useState<{
    agents: { online: number; total: number };
    containers: { running: number; total: number };
    cpu_percent: number;
    memory_percent: number;
    labs_running: number;
    labs_total: number;
  } | null>(null);

  // Port manager for interface auto-assignment
  const portManager = usePortManager(nodes, links);

  const addTaskLogEntry = useCallback((level: TaskLogEntry['level'], message: string, jobId?: string) => {
    const id = `log-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    setTaskLog((prev) => [...prev.slice(-99), { id, timestamp: new Date(), level, message, jobId }]);
  }, []);

  const clearTaskLog = useCallback(() => setTaskLog([]), []);

  const deviceModels = useMemo(
    () => buildDeviceModels(deviceCatalog, imageLibrary, customDevices),
    [deviceCatalog, imageLibrary, customDevices]
  );
  const deviceCategories = useMemo<DeviceCategory[]>(() => {
    // Use vendor categories from API if available, otherwise fall back to flat structure
    if (vendorCategories.length > 0) {
      return vendorCategories;
    }
    return [{ name: 'Devices', models: deviceModels }];
  }, [vendorCategories, deviceModels]);
  
  const updateCustomDevices = (next: CustomDevice[]) => {
    setCustomDevices(next);
    localStorage.setItem('aura_custom_devices', JSON.stringify(next));
  };

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
  const triggerLayoutSave = useCallback(() => {
    if (!activeLab) return;
    layoutDirtyRef.current = true;
    if (saveLayoutTimeoutRef.current) {
      window.clearTimeout(saveLayoutTimeoutRef.current);
    }
    saveLayoutTimeoutRef.current = window.setTimeout(() => {
      if (activeLab && layoutDirtyRef.current) {
        saveLayout(activeLab.id, nodes, annotations);
      }
    }, 500);
  }, [activeLab, nodes, annotations, saveLayout]);

  const loadLabs = useCallback(async () => {
    const data = await studioRequest<{ labs: LabSummary[] }>('/labs');
    setLabs(data.labs || []);
  }, [studioRequest]);

  const loadDevices = useCallback(async () => {
    const data = await studioRequest<{ devices?: DeviceCatalogEntry[] }>('/devices');
    setDeviceCatalog(data.devices || []);
    const imageData = await studioRequest<{ images?: Record<string, { clab?: string; libvirt?: string; virtualbox?: string; caveats?: string[] }> }>('/images');
    setImageCatalog(imageData.images || {});
    const libraryData = await studioRequest<{ images?: ImageLibraryEntry[] }>('/images/library');
    setImageLibrary(libraryData.images || []);
    // Load vendor categories from unified registry
    const vendorData = await studioRequest<DeviceCategory[]>('/vendors');
    setVendorCategories(vendorData || []);
  }, [studioRequest]);

  const loadSystemMetrics = useCallback(async () => {
    try {
      const data = await studioRequest<{
        agents: { online: number; total: number };
        containers: { running: number; total: number };
        cpu_percent: number;
        memory_percent: number;
        labs_running: number;
        labs_total: number;
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
        }))
      );
    }

    layoutDirtyRef.current = false;
  }, [deviceModels, studioRequest, loadLayout]);

  const loadJobs = useCallback(async (labId: string, currentNodes: Node[]) => {
    // Fetch actual container status from agent
    let deployedNodeNames = new Set<string>();
    let deployedStatus = new Map<string, RuntimeStatus>();
    try {
      const statusData = await studioRequest<{ nodes?: { name: string; status: string }[] }>(`/labs/${labId}/status`);
      if (statusData.nodes) {
        statusData.nodes.forEach((node) => {
          deployedNodeNames.add(node.name);
          // Map container status to RuntimeStatus
          const status = node.status === 'running' ? 'running' : node.status === 'exited' ? 'stopped' : 'stopped';
          deployedStatus.set(node.name, status as RuntimeStatus);
        });
      }
    } catch {
      // Status endpoint may fail if lab not deployed - that's ok
    }

    const data = await studioRequest<{ jobs: any[] }>(`/labs/${labId}/jobs`);
    setJobs(data.jobs || []);
    const statusMap = buildStatusMap(data.jobs || [], currentNodes, deployedNodeNames);

    const next: Record<string, RuntimeStatus> = {};
    currentNodes.forEach((node) => {
      // Priority: 1) In-progress job status, 2) Real container status, 3) undefined (no status shown)
      const jobStatus = statusMap.get(node.id);
      const realStatus = deployedStatus.get(node.name);
      if (jobStatus) {
        next[node.id] = jobStatus;
      } else if (realStatus) {
        next[node.id] = realStatus;
      }
      // Don't set any status for nodes that aren't deployed and have no active jobs
    });
    setRuntimeStates(next);
  }, [studioRequest]);

  useEffect(() => {
    loadLabs();
    loadDevices();
    loadSystemMetrics();
  }, [loadLabs, loadDevices, loadSystemMetrics]);

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

  useEffect(() => {
    if (!activeLab || nodes.length === 0) return;
    loadJobs(activeLab.id, nodes);
  }, [activeLab, nodes, loadJobs]);

  // Poll for job updates
  useEffect(() => {
    if (!activeLab || nodes.length === 0) return;
    const timer = setInterval(() => {
      loadJobs(activeLab.id, nodes);
    }, 4000);
    return () => clearInterval(timer);
  }, [activeLab, nodes, loadJobs]);

  // Cleanup: save layout and clear timeout on unmount
  useEffect(() => {
    return () => {
      if (saveLayoutTimeoutRef.current) {
        window.clearTimeout(saveLayoutTimeoutRef.current);
      }
    };
  }, []);

  // Track job status changes and log them
  useEffect(() => {
    const prevStatuses = prevJobsRef.current;
    const newStatuses = new Map<string, string>();

    for (const job of jobs) {
      const jobKey = job.id;
      const prevStatus = prevStatuses.get(jobKey);
      newStatuses.set(jobKey, job.status);

      if (prevStatus && prevStatus !== job.status) {
        const actionLabel = job.action.startsWith('node:')
          ? `Node ${job.action.split(':')[1]} (${job.action.split(':')[2]})`
          : job.action.toUpperCase();

        if (job.status === 'running') {
          addTaskLogEntry('info', `Job running: ${actionLabel}`, job.id);
        } else if (job.status === 'completed') {
          addTaskLogEntry('success', `Job completed: ${actionLabel}`, job.id);
        } else if (job.status === 'failed') {
          addTaskLogEntry('error', `Job failed: ${actionLabel}`, job.id);
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
        } else if (job.status === 'completed') {
          addTaskLogEntry('success', `Job completed: ${actionLabel}`, job.id);
        } else if (job.status === 'failed') {
          addTaskLogEntry('error', `Job failed: ${actionLabel}`, job.id);
        }
      }
    }

    prevJobsRef.current = newStatuses;
  }, [jobs, addTaskLogEntry]);

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
    setActiveLab(lab);
    setAnnotations([]);
    setConsoleWindows([]);
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

  const handleAddDevice = (model: DeviceModel) => {
    const id = Math.random().toString(36).slice(2, 9);
    const newNode: Node = {
      id,
      name: `${model.id.toUpperCase()}-${nodes.length + 1}`,
      type: model.type,
      model: model.id,
      version: model.versions[0],
      x: 300 + Math.random() * 50,
      y: 200 + Math.random() * 50,
      cpu: 1,
      memory: 1024,
    };
    setNodes((prev) => [...prev, newNode]);
    // Don't set any status for new nodes - they should show no status icon until deployed
    setSelectedId(id);
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

  const handleUpdateStatus = async (nodeId: string, status: RuntimeStatus) => {
    if (!activeLab) return;
    const node = nodes.find((n) => n.id === nodeId);
    if (!node) return;
    const nodeName = node.name;
    const action = status === 'booting' ? 'start' : status === 'stopped' ? 'stop' : 'restart';
    addTaskLogEntry('info', `Queuing ${action} for node "${nodeName}"...`);
    try {
      await studioRequest(`/labs/${activeLab.id}/nodes/${encodeURIComponent(nodeName)}/${action}`, { method: 'POST' });
      setRuntimeStates((prev) => ({ ...prev, [nodeId]: status }));
      addTaskLogEntry('success', `Node ${action} queued for "${nodeName}"`);
      loadJobs(activeLab.id, nodes);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Action failed';
      console.error('Node action failed:', error);
      setRuntimeStates((prev) => ({ ...prev, [nodeId]: 'error' }));
      addTaskLogEntry('error', `Node ${action} failed for "${nodeName}": ${message}`);
    }
  };

  const handleOpenConsole = (nodeId: string) => {
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
  };

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
  };

  const handleUpdateNode = (id: string, updates: Partial<Node>) => {
    setNodes((prev) => prev.map((node) => (node.id === id ? { ...node, ...updates } : node)));
  };

  const handleUpdateLink = (id: string, updates: Partial<Link>) => {
    setLinks((prev) => prev.map((link) => (link.id === id ? { ...link, ...updates } : link)));
  };

  const handleUpdateAnnotation = (id: string, updates: Partial<Annotation>) => {
    setAnnotations((prev) => prev.map((ann) => (ann.id === id ? { ...ann, ...updates } : ann)));
    triggerLayoutSave();
  };

  const handleDelete = (id: string) => {
    const isAnnotation = annotations.some((ann) => ann.id === id);
    setNodes((prev) => prev.filter((node) => node.id !== id));
    setLinks((prev) => prev.filter((link) => link.id !== id && link.source !== id && link.target !== id));
    setAnnotations((prev) => prev.filter((ann) => ann.id !== id));
    setSelectedId(null);
    // Only trigger layout save if an annotation was deleted (node positions remain the same)
    if (isAnnotation) {
      triggerLayoutSave();
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
      nodes: nodes.map((node) => ({
        id: node.id,
        name: node.name,
        device: node.model,
        version: node.version,
      })),
      links: links.map((link) => ({
        endpoints: [
          { node: link.source, ifname: link.sourceInterface },
          { node: link.target, ifname: link.targetInterface },
        ],
      })),
    };
    try {
      await studioRequest(`/labs/${activeLab.id}/import-graph`, {
        method: 'POST',
        body: JSON.stringify(graph),
      });
      addTaskLogEntry('info', 'Deploying lab...');
      await studioRequest(`/labs/${activeLab.id}/up`, {
        method: 'POST',
      });
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
      await loadLabs();
      await loadDevices();
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : 'Login failed');
    } finally {
      setAuthLoading(false);
    }
  };

  const selectedItem = nodes.find((node) => node.id === selectedId) || links.find((link) => link.id === selectedId) || annotations.find((ann) => ann.id === selectedId) || null;

  const renderView = () => {
    switch (view) {
      case 'images':
        return (
          <DeviceManager
            deviceModels={deviceModels}
            imageCatalog={imageCatalog}
            imageLibrary={imageLibrary}
            customDevices={customDevices}
            onAddCustomDevice={(device) => updateCustomDevices([...customDevices, device])}
            onRemoveCustomDevice={(deviceId) => updateCustomDevices(customDevices.filter((item) => item.id !== deviceId))}
            onUploadImage={loadDevices}
            onUploadQcow2={loadDevices}
            onRefresh={loadDevices}
          />
        );
      case 'runtime':
        return (
          <RuntimeControl
            nodes={nodes}
            runtimeStates={runtimeStates}
            deviceModels={deviceModels}
            onUpdateStatus={handleUpdateStatus}
          />
        );
      default:
        return (
          <>
            <Sidebar categories={deviceCategories} onAddDevice={handleAddDevice} onAddAnnotation={handleAddAnnotation} />
            <Canvas
              nodes={nodes}
              links={links}
              annotations={annotations}
              runtimeStates={runtimeStates}
              deviceModels={deviceModels}
              onNodeMove={handleNodeMove}
              onAnnotationMove={handleAnnotationMove}
              onConnect={handleConnect}
              selectedId={selectedId}
              onSelect={setSelectedId}
              onOpenConsole={handleOpenConsole}
              onUpdateStatus={handleUpdateStatus}
              onDelete={handleDelete}
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
                  onOpenConsole={handleOpenConsole}
                  runtimeStates={runtimeStates}
                  deviceModels={deviceModels}
                  onUpdateStatus={handleUpdateStatus}
                  portManager={portManager}
                />
              </div>
            </div>
          </>
        );
    }
  };

  const backgroundGradient =
    effectiveMode === 'dark'
      ? 'bg-gradient-to-br from-stone-950 via-stone-900 to-stone-950 bg-gradient-animate'
      : 'bg-gradient-to-br from-stone-50 via-white to-stone-100 bg-gradient-animate';

  if (authRequired) {
    return (
      <div className={`min-h-screen flex items-center justify-center ${backgroundGradient}`}>
        <div className="w-[420px] bg-white/90 dark:bg-stone-950/90 border border-stone-200 dark:border-stone-800 rounded-2xl shadow-2xl p-8">
          <div className="flex items-center gap-3 mb-6">
            <div className="w-10 h-10 bg-sage-600 rounded-xl flex items-center justify-center shadow-lg shadow-sage-900/20 border border-sage-400/30">
              <i className="fa-solid fa-bolt-lightning text-white"></i>
            </div>
            <div>
              <h1 className="text-lg font-black text-stone-900 dark:text-white tracking-tight">Aura Studio</h1>
              <p className="text-[10px] text-sage-600 dark:text-sage-500 font-bold uppercase tracking-widest">Sign in</p>
            </div>
          </div>
          <form onSubmit={handleLogin} className="space-y-4">
            <div className="space-y-1">
              <label className="text-[10px] font-bold text-stone-500 uppercase tracking-widest">Email</label>
              <input
                value={authEmail}
                onChange={(event) => setAuthEmail(event.target.value)}
                type="email"
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
        onRefresh={() => {
          loadLabs();
          loadSystemMetrics();
        }}
        onNavigateToImages={() => {
          // Set a placeholder lab to enter the studio, then switch to images view
          if (labs.length > 0) {
            setActiveLab(labs[0]);
            setView('images');
          }
        }}
      />
    );
  }

  return (
    <div className={`flex flex-col h-screen overflow-hidden select-none transition-colors duration-500 ${backgroundGradient}`}>
      <TopBar labName={activeLab.name} onExport={handleExport} onExportFull={handleExportFull} onDeploy={handleDeploy} onExit={() => setActiveLab(null)} />
      <div className="h-10 bg-white/60 dark:bg-stone-900/60 backdrop-blur-md border-b border-stone-200 dark:border-stone-800 flex px-6 items-center gap-1 shrink-0">
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
          onClick={() => setView('images')}
          className={`h-full px-4 text-[10px] font-black uppercase border-b-2 transition-all ${
            view === 'images' ? 'text-sage-600 dark:text-sage-500 border-sage-600 dark:border-sage-500' : 'text-stone-400 dark:text-stone-500 border-transparent'
          }`}
        >
          Images
        </button>
      </div>
      <SystemStatusStrip metrics={systemMetrics} />
      <div className="flex flex-1 overflow-hidden relative">
        {renderView()}
        <ConsoleManager
          labId={activeLab.id}
          windows={consoleWindows}
          nodes={nodes}
          onCloseWindow={(id) => setConsoleWindows((prev) => prev.filter((win) => win.id !== id))}
          onCloseTab={(winId, nodeId) =>
            setConsoleWindows((prev) =>
              prev
                .map((win) => {
                  if (win.id !== winId) return win;
                  const nextIds = win.deviceIds.filter((did) => did !== nodeId);
                  const nextActive = win.activeDeviceId === nodeId ? nextIds[0] || '' : win.activeDeviceId;
                  return { ...win, deviceIds: nextIds, activeDeviceId: nextActive };
                })
                .filter((win) => win.deviceIds.length > 0)
            )
          }
          onSetActiveTab={(winId, nodeId) => setConsoleWindows((prev) => prev.map((win) => (win.id === winId ? { ...win, activeDeviceId: nodeId } : win)))}
          onUpdateWindowPos={(id, x, y) => setConsoleWindows((prev) => prev.map((win) => (win.id === id ? { ...win, x, y } : win)))}
        />
      </div>
      <StatusBar />
      <TaskLogPanel
        entries={taskLog}
        isVisible={isTaskLogVisible}
        onToggle={() => setIsTaskLogVisible(!isTaskLogVisible)}
        onClear={clearTaskLog}
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
    </div>
  );
};

export default StudioPage;
