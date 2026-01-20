import React, { useCallback, useEffect, useMemo, useState, createContext, useContext } from 'react';
import Sidebar from './components/Sidebar';
import Canvas from './components/Canvas';
import TopBar from './components/TopBar';
import PropertiesPanel from './components/PropertiesPanel';
import ConsoleManager from './components/ConsoleManager';
import DeviceManager from './components/DeviceManager';
import RuntimeControl, { RuntimeStatus } from './components/RuntimeControl';
import StatusBar from './components/StatusBar';
import Dashboard from './components/Dashboard';
import { Annotation, AnnotationType, ConsoleWindow, DeviceModel, DeviceType, Link, Node } from './types';
import { apiRequest } from '../api';
import { TopologyGraph } from '../types';
import './studio.css';
import 'xterm/css/xterm.css';

type Theme = 'light' | 'dark';

interface ThemeContextType {
  theme: Theme;
  toggleTheme: () => void;
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

export const useTheme = () => {
  const context = useContext(ThemeContext);
  if (!context) throw new Error('useTheme must be used within a ThemeProvider');
  return context;
};

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

interface DeviceCategory {
  name: string;
  models: DeviceModel[];
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

const buildDeviceModels = (devices: DeviceCatalogEntry[], images: ImageLibraryEntry[]): DeviceModel[] => {
  const versionsByDevice = new Map<string, Set<string>>();
  images.forEach((image) => {
    if (!image.device_id) return;
    const versions = versionsByDevice.get(image.device_id) || new Set<string>();
    if (image.version) {
      versions.add(image.version);
    }
    versionsByDevice.set(image.device_id, versions);
  });

  return devices.map((device) => {
    const versions = Array.from(versionsByDevice.get(device.id) || []);
    return {
      id: device.id,
      type: guessDeviceType(device.id, device.label),
      name: device.label,
      icon: DEFAULT_ICON,
      versions: versions.length > 0 ? versions : ['default'],
      isActive: true,
      vendor: device.support || 'unknown',
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

const buildStatusMap = (jobs: any[], nodes: Node[]) => {
  const map = new Map<string, RuntimeStatus>();
  let globalStatus: RuntimeStatus | undefined;

  for (const job of jobs) {
    if (typeof job.action !== 'string') continue;
    if (job.action.startsWith('node:')) {
      const [, nodeAction, nodeName] = job.action.split(':', 3);
      if (map.has(nodeName)) continue;
      const status = resolveNodeStatus(nodeAction, job.status);
      if (status) {
        map.set(nodeName, status);
      }
      continue;
    }
    if (!globalStatus && ['up', 'down', 'restart'].includes(job.action)) {
      globalStatus = resolveNodeStatus(job.action, job.status);
    }
  }

  if (globalStatus) {
    for (const node of nodes) {
      if (!map.has(node.id)) {
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
  const [theme, setTheme] = useState<Theme>(() => (localStorage.getItem('aura_theme') as Theme) || 'dark');
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
  const [imageLibrary, setImageLibrary] = useState<ImageLibraryEntry[]>([]);
  const [imageCatalog, setImageCatalog] = useState<Record<string, { clab?: string; libvirt?: string; virtualbox?: string; caveats?: string[] }>>({});

  useEffect(() => {
    const root = window.document.documentElement;
    if (theme === 'dark') {
      root.classList.add('dark');
    } else {
      root.classList.remove('dark');
    }
    localStorage.setItem('aura_theme', theme);
  }, [theme]);

  const toggleTheme = () => setTheme((prev) => (prev === 'dark' ? 'light' : 'dark'));

  const deviceModels = useMemo(() => buildDeviceModels(deviceCatalog, imageLibrary), [deviceCatalog, imageLibrary]);
  const deviceCategories = useMemo<DeviceCategory[]>(() => [{ name: 'Devices', models: deviceModels }], [deviceModels]);

  const loadLabs = useCallback(async () => {
    const data = await apiRequest<{ labs: LabSummary[] }>('/labs');
    setLabs(data.labs || []);
  }, []);

  const loadDevices = useCallback(async () => {
    const data = await apiRequest<{ devices?: DeviceCatalogEntry[] }>('/devices');
    setDeviceCatalog(data.devices || []);
    const imageData = await apiRequest<{ images?: Record<string, { clab?: string; libvirt?: string; virtualbox?: string; caveats?: string[] }> }>('/images');
    setImageCatalog(imageData.images || {});
    const libraryData = await apiRequest<{ images?: ImageLibraryEntry[] }>('/images/library');
    setImageLibrary(libraryData.images || []);
  }, []);

  const loadGraph = useCallback(async (labId: string) => {
    const graph = await apiRequest<TopologyGraph>(`/labs/${labId}/export-graph`);
    setNodes(buildGraphNodes(graph, deviceModels));
    setLinks(buildGraphLinks(graph));
  }, [deviceModels]);

  const loadJobs = useCallback(async (labId: string, currentNodes: Node[]) => {
    const data = await apiRequest<{ jobs: any[] }>(`/labs/${labId}/jobs`);
    const statusMap = buildStatusMap(data.jobs || [], currentNodes);
    const next: Record<string, RuntimeStatus> = {};
    currentNodes.forEach((node) => {
      next[node.id] = statusMap.get(node.id) || 'stopped';
    });
    setRuntimeStates(next);
  }, []);

  useEffect(() => {
    loadLabs();
    loadDevices();
  }, [loadLabs, loadDevices]);

  useEffect(() => {
    if (!activeLab) return;
    loadGraph(activeLab.id);
  }, [activeLab, loadGraph]);

  useEffect(() => {
    if (!activeLab || nodes.length === 0) return;
    loadJobs(activeLab.id, nodes);
  }, [activeLab, nodes, loadJobs]);

  const handleCreateLab = async () => {
    const name = `Project_${labs.length + 1}`;
    await apiRequest('/labs', { method: 'POST', body: JSON.stringify({ name }) });
    loadLabs();
  };

  const handleSelectLab = (lab: LabSummary) => {
    setActiveLab(lab);
    setAnnotations([]);
    setConsoleWindows([]);
    setSelectedId(null);
    setView('designer');
  };

  const handleDeleteLab = async (labId: string) => {
    await apiRequest(`/labs/${labId}`, { method: 'DELETE' });
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
    setRuntimeStates((prev) => ({ ...prev, [id]: 'stopped' }));
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
      color: theme === 'dark' ? '#3b82f6' : '#2563eb',
      width: type === 'rect' || type === 'circle' ? 100 : undefined,
      height: type === 'rect' ? 60 : undefined,
      targetX: type === 'arrow' ? 500 : undefined,
      targetY: type === 'arrow' ? 400 : undefined,
    };
    setAnnotations((prev) => [...prev, newAnn]);
    setSelectedId(id);
  };

  const handleUpdateStatus = async (nodeId: string, status: RuntimeStatus) => {
    if (!activeLab) return;
    const action = status === 'booting' ? 'start' : status === 'stopped' ? 'stop' : 'restart';
    try {
      await apiRequest(`/labs/${activeLab.id}/nodes/${encodeURIComponent(nodeId)}/${action}`, { method: 'POST' });
      setRuntimeStates((prev) => ({ ...prev, [nodeId]: status }));
      loadJobs(activeLab.id, nodes);
    } catch {
      setRuntimeStates((prev) => ({ ...prev, [nodeId]: 'error' }));
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
  }, []);

  const handleAnnotationMove = useCallback((id: string, x: number, y: number) => {
    setAnnotations((prev) => prev.map((ann) => (ann.id === id ? { ...ann, x, y } : ann)));
  }, []);

  const handleConnect = (sourceId: string, targetId: string) => {
    const exists = links.find(
      (link) => (link.source === sourceId && link.target === targetId) || (link.source === targetId && link.target === sourceId)
    );
    if (exists) return;
    const newLink: Link = {
      id: Math.random().toString(36).slice(2, 9),
      source: sourceId,
      target: targetId,
      type: 'p2p',
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
  };

  const handleDelete = (id: string) => {
    setNodes((prev) => prev.filter((node) => node.id !== id));
    setLinks((prev) => prev.filter((link) => link.id !== id && link.source !== id && link.target !== id));
    setAnnotations((prev) => prev.filter((ann) => ann.id !== id));
    setSelectedId(null);
  };

  const handleExport = async () => {
    if (!activeLab) return;
    const data = await apiRequest<{ content: string }>(`/labs/${activeLab.id}/export-yaml`);
    setYamlContent(data.content || '');
    setShowYamlModal(true);
  };

  const handleDeploy = async () => {
    if (!activeLab) return;
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
    await apiRequest(`/labs/${activeLab.id}/import-graph`, {
      method: 'POST',
      body: JSON.stringify(graph),
    });
    loadGraph(activeLab.id);
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
            />
          </>
        );
    }
  };

  const backgroundGradient =
    theme === 'dark'
      ? 'bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 bg-gradient-animate'
      : 'bg-gradient-to-br from-slate-50 via-white to-slate-100 bg-gradient-animate';

  if (!activeLab) {
    return (
      <ThemeContext.Provider value={{ theme, toggleTheme }}>
        <Dashboard
          labs={labs}
          onSelect={handleSelectLab}
          onCreate={handleCreateLab}
          onDelete={handleDeleteLab}
          onRefresh={loadLabs}
        />
      </ThemeContext.Provider>
    );
  }

  return (
    <ThemeContext.Provider value={{ theme, toggleTheme }}>
      <div className={`flex flex-col h-screen overflow-hidden select-none transition-colors duration-500 ${backgroundGradient}`}>
        <TopBar labName={activeLab.name} onExport={handleExport} onDeploy={handleDeploy} onExit={() => setActiveLab(null)} />
        <div className="h-10 bg-white/60 dark:bg-slate-900/60 backdrop-blur-md border-b border-slate-200 dark:border-slate-800 flex px-6 items-center gap-1 shrink-0">
          <button
            onClick={() => setView('designer')}
            className={`h-full px-4 text-[10px] font-black uppercase border-b-2 transition-all ${
              view === 'designer' ? 'text-blue-600 dark:text-blue-500 border-blue-600 dark:border-blue-500' : 'text-slate-400 dark:text-slate-500 border-transparent'
            }`}
          >
            Designer
          </button>
          <button
            onClick={() => setView('runtime')}
            className={`h-full px-4 text-[10px] font-black uppercase border-b-2 transition-all ${
              view === 'runtime' ? 'text-blue-600 dark:text-blue-500 border-blue-600 dark:border-blue-500' : 'text-slate-400 dark:text-slate-500 border-transparent'
            }`}
          >
            Runtime
          </button>
          <button
            onClick={() => setView('images')}
            className={`h-full px-4 text-[10px] font-black uppercase border-b-2 transition-all ${
              view === 'images' ? 'text-blue-600 dark:text-blue-500 border-blue-600 dark:border-blue-500' : 'text-slate-400 dark:text-slate-500 border-transparent'
            }`}
          >
            Images
          </button>
        </div>
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
        {showYamlModal && (
          <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/80 backdrop-blur-md">
            <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-2xl w-[700px] max-h-[85vh] flex flex-col overflow-hidden shadow-2xl">
              <div className="p-5 border-b border-slate-100 dark:border-slate-800 flex justify-between items-center">
                <h3 className="text-slate-900 dark:text-slate-100 font-bold text-sm uppercase">YAML Preview</h3>
                <button onClick={() => setShowYamlModal(false)} className="text-slate-500 hover:text-slate-900 dark:hover:text-white">
                  <i className="fa-solid fa-times"></i>
                </button>
              </div>
              <div className="flex-1 p-6 overflow-y-auto bg-slate-50 dark:bg-slate-950/50 font-mono text-[11px] text-blue-700 dark:text-blue-300 whitespace-pre">
                {yamlContent}
              </div>
              <div className="p-5 border-t border-slate-100 dark:border-slate-800 flex justify-end gap-3">
                <button onClick={() => setShowYamlModal(false)} className="px-6 py-2 bg-blue-600 text-white font-black rounded-lg">
                  DONE
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </ThemeContext.Provider>
  );
};

export default StudioPage;
