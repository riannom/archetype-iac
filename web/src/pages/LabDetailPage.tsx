import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import ReactFlow, {
  Background,
  Connection,
  Controls,
  Edge,
  Handle,
  MiniMap,
  Node,
  Position,
  ReactFlowInstance,
  addEdge,
  useEdgesState,
  useNodesState,
} from "reactflow";
import "reactflow/dist/style.css";
import "xterm/css/xterm.css";
import { Terminal } from "xterm";
import { FitAddon } from "xterm-addon-fit";
import { API_BASE_URL, apiRequest } from "../api";
import { GraphLink, GraphNode, TopologyGraph } from "../types";

interface Lab {
  id: string;
  name: string;
  created_at: string;
}

interface DeviceCatalogEntry {
  id: string;
  label: string;
  support?: string;
}

interface ImageCatalogEntry {
  clab?: string;
  libvirt?: string;
  virtualbox?: string;
  caveats?: string[];
}

interface ImageLibraryEntry {
  id: string;
  kind: string;
  reference: string;
  device_id?: string | null;
  filename?: string;
  version?: string | null;
}

interface NodeData {
  label: string;
  device?: string;
  version?: string;
  image?: string;
  netlabName?: string;
  status?: string;
}

interface LinkData {
  name?: string;
  type?: string;
  pool?: string;
  prefix?: string;
}

const fallbackPalette: DeviceCatalogEntry[] = [
  { id: "iosv", label: "Cisco IOSv" },
  { id: "csr", label: "Cisco CSR" },
  { id: "eos", label: "Arista cEOS" },
  { id: "frr", label: "FRR" },
];

const DEVICE_LABEL_OVERRIDES: Record<string, string> = {
  eos: "Arista cEOS",
};

const NODE_NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_]{0,15}$/;

function buildNetlabName(sourceId: string, hint: string | undefined, used: Set<string>) {
  if (NODE_NAME_PATTERN.test(sourceId) && !used.has(sourceId)) {
    used.add(sourceId);
    return sourceId;
  }
  const base = (hint || sourceId || "node")
    .toLowerCase()
    .replace(/[^a-z0-9_]/g, "_")
    .replace(/^_+|_+$/g, "");
  const prefix = base && /^[a-z_]/.test(base) ? base : `n_${base || "node"}`;
  const hash = [...sourceId].reduce((acc, char) => (acc * 31 + char.charCodeAt(0)) % 46656, 0);
  const suffix = hash.toString(36).padStart(3, "0");
  const baseMax = Math.max(1, 16 - suffix.length - 1);
  let candidate = `${prefix.slice(0, baseMax)}_${suffix}`;
  let counter = 0;
  while (used.has(candidate)) {
    counter += 1;
    const counterSuffix = `${suffix}${counter.toString(36)}`.slice(0, 4);
    const counterBaseMax = Math.max(1, 16 - counterSuffix.length - 1);
    candidate = `${prefix.slice(0, counterBaseMax)}_${counterSuffix}`;
  }
  used.add(candidate);
  return candidate;
}

function isSwitchDevice(deviceId: string | undefined) {
  if (!deviceId) return false;
  return deviceId.includes("l2") || deviceId.includes("switch");
}

function DeviceNode({
  data,
  selected,
}: {
  data: NodeData;
  selected: boolean;
}) {
  const statusIcon =
    data.status === "running"
      ? "▶️"
      : data.status === "stopped"
      ? "⏹️"
      : data.status === "restarting"
      ? "🔄"
      : data.status === "starting"
      ? "▶️"
      : data.status === "stopping"
      ? "⏹️"
      : data.status === "error"
      ? "⚠️"
      : null;

  const icon = (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="device-icon">
      <rect x="3.5" y="6" width="17" height="6.5" rx="2.2" />
      <rect x="3.5" y="11.5" width="17" height="6.5" rx="2.2" />
      <path d="M7 10h10M7 14.5h10" strokeWidth="1.4" />
      <circle cx="8" cy="9" r="0.9" />
      <circle cx="16" cy="9" r="0.9" />
    </svg>
  );

  return (
    <div className={`device-node${selected ? " selected" : ""}`}>
      <Handle type="target" position={Position.Top} />
      <div className="device-node-body" title={data.label || "Device"}>
        {icon}
      </div>
      {statusIcon && (
        <div className={`device-status device-status-${data.status}`} title={data.status}>
          {statusIcon}
        </div>
      )}
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}

function flowFromGraph(graph: TopologyGraph): { nodes: Node<NodeData>[]; edges: Edge<LinkData>[] } {
  const nodes = graph.nodes.map((node, index) => ({
    id: node.id || node.name,
    type: "device",
    position: { x: 80 + (index % 4) * 180, y: 80 + Math.floor(index / 4) * 140 },
    data: {
      label: (node.vars as any)?.name || (node.vars as any)?.label || node.name,
      device: node.device || "",
      version: node.version || "",
      image: node.image || "",
      netlabName: node.name,
    },
  }));

  const edges: Edge<LinkData>[] = [];
  graph.links.forEach((link, linkIndex) => {
    if (link.endpoints.length < 2) {
      return;
    }
    const [first, ...rest] = link.endpoints;
    rest.forEach((endpoint) => {
      edges.push({
        id: `link-${linkIndex}-${first.node}-${endpoint.node}`,
        source: first.node,
        target: endpoint.node,
        label: link.name || "",
        data: {
          name: link.name || "",
          type: link.type || "",
          pool: link.pool || "",
          prefix: link.prefix || "",
        },
      });
    });
  });

  return { nodes, edges };
}

function graphFromFlow(nodes: Node<NodeData>[], edges: Edge<LinkData>[]): TopologyGraph {
  const nameMap = new Map<string, string>();
  const usedNames = new Set<string>();
  const graphNodes: GraphNode[] = nodes.map((node) => ({
    id: node.id,
    name:
      node.data.netlabName && !usedNames.has(node.data.netlabName)
        ? (() => {
            usedNames.add(node.data.netlabName as string);
            nameMap.set(node.id, node.data.netlabName as string);
            return node.data.netlabName as string;
          })()
        : (() => {
            const generated = buildNetlabName(node.id, node.data.label || node.data.device, usedNames);
            nameMap.set(node.id, generated);
            return generated;
          })(),
    device: node.data.device || null,
    version: node.data.version || null,
    image: node.data.image || null,
    vars:
      node.data.label
        ? { name: node.data.label }
        : null,
  }));

  const graphLinks: GraphLink[] = edges.map((edge) => ({
    endpoints: [{ node: edge.source }, { node: edge.target }],
    name: edge.data?.name || edge.label?.toString() || null,
    type: edge.data?.type || null,
    pool: edge.data?.pool || null,
    prefix: edge.data?.prefix || null,
  }));

  graphLinks.forEach((link) => {
    link.endpoints = link.endpoints.map((endpoint) => ({
      ...endpoint,
      node: nameMap.get(endpoint.node) || endpoint.node,
    }));
  });

  return { nodes: graphNodes, links: graphLinks, defaults: { device: "iosv" } };
}

export function LabDetailPage() {
  const { labId } = useParams();
  const [lab, setLab] = useState<Lab | null>(null);
  const [yaml, setYaml] = useState<string>("");
  const [status, setStatus] = useState<string | null>(null);
  const [runtimeStatus, setRuntimeStatus] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [runtimeLog, setRuntimeLog] = useState<string>("");
  const [jobs, setJobs] = useState<any[]>([]);
  const [consoleOutput, setConsoleOutput] = useState<string>("");
  const [terminalHostEl, setTerminalHostEl] = useState<HTMLDivElement | null>(null);
  const [deviceLog, setDeviceLog] = useState<string>("");
  const consoleSocket = useRef<WebSocket | null>(null);
  const consolePopoutRef = useRef<Window | null>(null);
  const [isConsolePoppedOut, setIsConsolePoppedOut] = useState(false);
  const imageInputRef = useRef<HTMLInputElement | null>(null);
  const [imageUploadStatus, setImageUploadStatus] = useState<string | null>(null);
  const [imageUploadProgress, setImageUploadProgress] = useState<number | null>(null);
  const terminalHostRef = useRef<HTMLDivElement | null>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const [permissions, setPermissions] = useState<any[]>([]);
  const [shareEmail, setShareEmail] = useState<string>("");
  const [shareRole, setShareRole] = useState<string>("viewer");
  const [isShareOpen, setIsShareOpen] = useState(false);
  const [deviceCatalog, setDeviceCatalog] = useState<DeviceCatalogEntry[]>([]);
  const [imageCatalog, setImageCatalog] = useState<Record<string, ImageCatalogEntry>>({});
  const [qcow2Images, setQcow2Images] = useState<{ filename: string; path: string }[]>([]);
  const [imageLibrary, setImageLibrary] = useState<ImageLibraryEntry[]>([]);
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    nodeId: string;
  } | null>(null);
  const [pendingDeviceAdd, setPendingDeviceAdd] = useState<{
    device: string;
    label: string;
    position?: { x: number; y: number };
  } | null>(null);

  const reactFlowWrapper = useRef<HTMLDivElement | null>(null);
  const reactFlowInstance = useRef<ReactFlowInstance | null>(null);

  const [nodes, setNodes, onNodesChange] = useNodesState<NodeData>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<LinkData>([]);

  const nodeTypes = useMemo(() => ({ device: DeviceNode }), []);
  const libraryByDevice = useMemo(() => {
    const map = new Map<string, ImageLibraryEntry[]>();
    imageLibrary.forEach((entry) => {
      if (!entry.device_id) return;
      const list = map.get(entry.device_id) || [];
      list.push(entry);
      map.set(entry.device_id, list);
    });
    return map;
  }, [imageLibrary]);

  const selectedNode = useMemo(
    () => nodes.find((node) => node.id === selectedNodeId) || null,
    [nodes, selectedNodeId]
  );

  const selectedEdge = useMemo(
    () => edges.find((edge) => edge.id === selectedEdgeId) || null,
    [edges, selectedEdgeId]
  );

  function normalizeNodeNames(currentNodes: Node<NodeData>[]) {
    const usedNames = new Set<string>();
    let changed = false;
    const normalized = currentNodes.map((node) => {
      let netlabName = node.data.netlabName;
      if (!netlabName || usedNames.has(netlabName)) {
        netlabName = buildNetlabName(node.id, node.data.label || node.data.device, usedNames);
        changed = true;
      } else {
        usedNames.add(netlabName);
      }
      if (node.data.netlabName !== netlabName) {
        changed = true;
        return { ...node, data: { ...node.data, netlabName } };
      }
      return node;
    });
    return { nodes: normalized, changed };
  }

  function resolveNodeStatus(action: string, status: string) {
    if (status === "failed") {
      return "error";
    }
    const isDone = status === "completed";
    if (action === "start" || action === "up") {
      return isDone ? "running" : "starting";
    }
    if (action === "stop" || action === "down") {
      return isDone ? "stopped" : "stopping";
    }
    if (action === "restart") {
      return isDone ? "running" : "restarting";
    }
    return undefined;
  }

  function buildStatusMap(currentJobs: any[], currentNodes: Node<NodeData>[]) {
    const map = new Map<string, string>();
    let globalStatus: string | undefined;

    for (const job of currentJobs) {
      if (typeof job.action !== "string") continue;
      if (job.action.startsWith("node:")) {
        const [, nodeAction, nodeName] = job.action.split(":", 3);
        if (map.has(nodeName)) continue;
        const status = resolveNodeStatus(nodeAction, job.status);
        if (status) {
          map.set(nodeName, status);
        }
        continue;
      }
      if (!globalStatus && ["up", "down", "restart"].includes(job.action)) {
        globalStatus = resolveNodeStatus(job.action, job.status);
      }
    }

    if (globalStatus) {
      for (const node of currentNodes) {
        const name = node.data.netlabName || node.id;
        if (!map.has(name)) {
          map.set(name, globalStatus);
        }
      }
    }

    return map;
  }

  function renderDeviceIcon(deviceId: string) {
    if (isSwitchDevice(deviceId)) {
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true" className="device-icon">
          <rect x="3" y="6" width="18" height="12" rx="2" />
          <circle cx="7.5" cy="12" r="1.2" />
          <circle cx="12" cy="12" r="1.2" />
          <circle cx="16.5" cy="12" r="1.2" />
        </svg>
      );
    }
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true" className="device-icon">
        <rect x="4" y="5" width="16" height="6" rx="2" />
        <rect x="4" y="13" width="16" height="6" rx="2" />
        <path d="M8 11.5h8M8 13.5h8" strokeWidth="1.2" />
      </svg>
    );
  }

  async function loadLab() {
    if (!labId) return;
    const data = await apiRequest<Lab>(`/labs/${labId}`);
    setLab(data);
    const yamlData = await apiRequest<{ content: string }>(`/labs/${labId}/export-yaml`);
    setYaml(yamlData.content);
    await loadGraph();
    await loadDevices();
    await loadImages();
    loadJobs();
    loadPermissions();
  }

  async function loadDevices() {
    try {
      const data = await apiRequest<{ devices?: DeviceCatalogEntry[] }>("/devices");
      const devices = (data.devices || []).map((device) => ({
        ...device,
        label: DEVICE_LABEL_OVERRIDES[device.id] || device.label,
      }));
      setDeviceCatalog(devices);
    } catch {
      setDeviceCatalog([]);
    }
  }

  async function loadImages() {
    try {
      const data = await apiRequest<{ images?: Record<string, ImageCatalogEntry> }>("/images");
      setImageCatalog(data.images || {});
      const qcow2Data = await apiRequest<{ files?: { filename: string; path: string }[] }>("/images/qcow2");
      setQcow2Images(qcow2Data.files || []);
    const libraryData = await apiRequest<{ images?: ImageLibraryEntry[] }>("/images/library");
      setImageLibrary(libraryData.images || []);
    } catch {
      setImageCatalog({});
      setQcow2Images([]);
      setImageLibrary([]);
    }
  }

  async function loadGraph() {
    if (!labId) return;
    const graph = await apiRequest<TopologyGraph>(`/labs/${labId}/export-graph`);
    const flow = flowFromGraph(graph);
    setNodes(flow.nodes);
    setEdges(flow.edges);
  }

  async function exportYaml() {
    if (!labId) return;
    const data = await apiRequest<{ content: string }>(`/labs/${labId}/export-yaml`);
    const blob = new Blob([data.content], { type: "text/yaml" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${lab?.name || "topology"}.yaml`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }

  async function saveGraph() {
    if (!labId) return;
    const nameInfo = normalizeNodeNames(nodes);
    if (nameInfo.changed) {
      setNodes(nameInfo.nodes);
    }
    const graph = graphFromFlow(nameInfo.nodes, edges);
    await apiRequest(`/labs/${labId}/import-graph`, {
      method: "POST",
      body: JSON.stringify(graph),
    });
    await loadGraph();
    const yamlData = await apiRequest<{ content: string }>(`/labs/${labId}/export-yaml`);
    setYaml(yamlData.content);
    setStatus("Canvas saved to YAML");
  }

  async function loadJobs() {
    if (!labId) return;
    const data = await apiRequest<{ jobs: any[] }>(`/labs/${labId}/jobs`);
    setJobs(data.jobs);
  }

  async function runAction(action: "up" | "down" | "restart") {
    if (!labId) return;
    try {
      setRuntimeStatus(null);
      await apiRequest(`/labs/${labId}/${action}`, { method: "POST" });
      loadJobs();
    } catch (error) {
      setRuntimeStatus(error instanceof Error ? error.message : "Action failed");
    }
  }

  async function fetchStatus() {
    if (!labId) return;
    const data = await apiRequest<{ raw: string }>(`/labs/${labId}/status`);
    setRuntimeLog(data.raw);
  }

  async function loadLatestLog() {
    if (!labId || jobs.length === 0) return;
    const jobId = jobs[0].id;
    const data = await apiRequest<{ log: string }>(`/labs/${labId}/jobs/${jobId}/log?tail=200`);
    setRuntimeLog(data.log);
  }

  function findLatestNodeJob(nodeName: string) {
    return jobs.find(
      (item) =>
        typeof item.action === "string" &&
        item.action.startsWith("node:") &&
        item.action.endsWith(`:${nodeName}`)
    );
  }

  async function loadNodeActionLog() {
    if (!labId || !selectedNode) return;
    const nodeName = selectedNode.id;
    const job = findLatestNodeJob(nodeName);
    if (!job) {
      setDeviceLog("No node action logs found yet.");
      return;
    }
    try {
      const data = await apiRequest<{ log: string }>(`/labs/${labId}/jobs/${job.id}/log?tail=200`);
      setDeviceLog(data.log);
    } catch (error) {
      setDeviceLog("Startup output is not available yet.");
    }
  }

  async function uploadImage(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      setImageUploadStatus(`Uploading ${file.name}...`);
      setImageUploadProgress(0);
      const data = await new Promise<{ output?: string }>((resolve, reject) => {
        const formData = new FormData();
        formData.append("file", file);
        const token = localStorage.getItem("token");
        const request = new XMLHttpRequest();
        request.open("POST", `${API_BASE_URL}/images/load`);
        if (token) {
          request.setRequestHeader("Authorization", `Bearer ${token}`);
        }
        request.upload.onprogress = (eventProgress) => {
          if (eventProgress.lengthComputable) {
            setImageUploadProgress(Math.round((eventProgress.loaded / eventProgress.total) * 100));
          }
        };
        request.onerror = () => reject(new Error("Upload failed"));
        request.onload = () => {
          if (request.status >= 200 && request.status < 300) {
            try {
              resolve(JSON.parse(request.responseText));
            } catch {
              resolve({});
            }
          } else {
            reject(new Error(request.responseText || "Upload failed"));
          }
        };
        request.send(formData);
      });
      setImageUploadStatus(data.output || "Image loaded");
      await loadImages();
    } catch (error) {
      setImageUploadStatus(error instanceof Error ? error.message : "Upload failed");
    } finally {
      event.target.value = "";
      setImageUploadProgress(null);
    }
  }

  function openImagePicker() {
    imageInputRef.current?.click();
  }

  async function loadPermissions() {
    if (!labId) return;
    const data = await apiRequest<{ permissions: any[] }>(`/labs/${labId}/permissions`);
    setPermissions(data.permissions);
  }

  async function shareLab() {
    if (!labId || !shareEmail) return;
    await apiRequest(`/labs/${labId}/permissions`, {
      method: "POST",
      body: JSON.stringify({ user_email: shareEmail, role: shareRole }),
    });
    setShareEmail("");
    loadPermissions();
  }

  async function removePermission(permissionId: string) {
    if (!labId) return;
    await apiRequest(`/labs/${labId}/permissions/${permissionId}`, { method: "DELETE" });
    loadPermissions();
  }

  async function runNodeAction(nodeId: string, action: "start" | "stop") {
    if (!labId) return;
    const node = nodes.find((item) => item.id === nodeId);
    const nodeName = node?.id || nodeId;
    const safeNodes = normalizeNodeNames(nodes);
    if (safeNodes.changed) {
      setNodes(safeNodes.nodes);
    }
    const safeNode = safeNodes.nodes.find((item) => item.id === nodeId) || node;
    const netlabName = safeNode?.data.netlabName || nodeName;
    try {
      await apiRequest(`/labs/${labId}/nodes/${encodeURIComponent(netlabName)}/${action}`, {
        method: "POST",
      });
      setSelectedNodeId(nodeId);
      setSelectedEdgeId(null);
      setDeviceLog(`Queued ${action} for ${nodeName}...\n`);
      setConsoleOutput((prev) => `${prev}\n[queued ${action} for ${nodeName}]\n`);
      loadJobs();
      setContextMenu(null);
    } catch (error) {
      setDeviceLog(error instanceof Error ? error.message : "Action failed");
    }
  }

  function connectConsoleForNode(nodeId: string) {
    const node = nodes.find((item) => item.id === nodeId);
    if (!node || !labId) return;
    setSelectedNodeId(nodeId);
    setSelectedEdgeId(null);
    const nodeName = node.data.netlabName || node.id;
    if (consoleSocket.current) {
      consoleSocket.current.close();
    }
    terminalRef.current?.clear();
    const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    let wsUrl = `${wsProtocol}//${window.location.host}${API_BASE_URL}`;
    if (API_BASE_URL.startsWith("http")) {
      const apiUrl = new URL(API_BASE_URL);
      wsUrl = `${apiUrl.protocol === "https:" ? "wss:" : "ws:"}//${apiUrl.host}`;
    }
    wsUrl = `${wsUrl.replace(/\/$/, "")}/labs/${labId}/nodes/${encodeURIComponent(nodeName)}/console`;
    const socket = new WebSocket(wsUrl);
    socket.binaryType = "arraybuffer";
  
    socket.onmessage = (event) => {
      handleConsoleMessage(event.data);
    };
    socket.onclose = () => {
      terminalRef.current?.writeln("\n[console disconnected]\n");
      setConsoleOutput((prev) => `${prev}\n[console disconnected]\n`);
    };
    socket.onopen = () => {
      terminalRef.current?.focus();
    };
    consoleSocket.current = socket;
    setContextMenu(null);
  }

  function handleConsoleMessage(data: unknown) {
    if (typeof data === "string") {
      terminalRef.current?.write(data);
      setConsoleOutput((prev) => prev + data);
      return;
    }

    if (data instanceof ArrayBuffer) {
      const bytes = new Uint8Array(data);
      terminalRef.current?.write(bytes);
      const text = new TextDecoder().decode(bytes);
      setConsoleOutput((prev) => prev + text);
      return;
    }

    if (data instanceof Blob) {
      data.arrayBuffer().then((buffer) => {
        const bytes = new Uint8Array(buffer);
        terminalRef.current?.write(bytes);
        const text = new TextDecoder().decode(bytes);
        setConsoleOutput((prev) => prev + text);
        });
    }
  }

  function connectConsole() {
    if (!labId || !selectedNode) return;
    const nodeName = selectedNode.data.netlabName || (selectedNode.id as string);
    if (consoleSocket.current) {
      consoleSocket.current.close();
    }
    terminalRef.current?.clear();
    const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    let wsUrl = `${wsProtocol}//${window.location.host}${API_BASE_URL}`;
    if (API_BASE_URL.startsWith("http")) {
      const apiUrl = new URL(API_BASE_URL);
      wsUrl = `${apiUrl.protocol === "https:" ? "wss:" : "ws:"}//${apiUrl.host}`;
    }
    wsUrl = `${wsUrl.replace(/\/$/, "")}/labs/${labId}/nodes/${encodeURIComponent(nodeName)}/console`;
    const socket = new WebSocket(wsUrl);
    socket.binaryType = "arraybuffer";
  
    socket.onmessage = (event) => {
      handleConsoleMessage(event.data);
    };
    socket.onclose = () => {
      terminalRef.current?.writeln("\n[console disconnected]\n");
      setConsoleOutput((prev) => `${prev}\n[console disconnected]\n`);
    };
    socket.onopen = () => {
      terminalRef.current?.focus();
    };
    consoleSocket.current = socket;
  }

  function openConsolePopout() {
    const existing = consolePopoutRef.current;
    if (existing && !existing.closed) {
      existing.focus();
      return;
    }
    const popup = window.open("", "aura-console", "width=920,height=620");
    if (!popup) return;
    popup.document.title = "Aura Console";
    popup.document.body.style.margin = "0";
    popup.document.body.style.background = "#0b0f16";
    popup.document.body.style.color = "#dbe7ff";
    popup.document.body.innerHTML = `
      <div style="font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace; padding: 16px;">
        <div id="console-meta" style="margin-bottom: 12px; color: #7aa2f7; font-weight: 600;"></div>
        <pre id="console-output" style="white-space: pre-wrap; background: #0f1726; border: 1px solid #1f2a44; border-radius: 12px; padding: 14px; min-height: 60vh; overflow: auto;"></pre>
      </div>
    `;
    popup.onbeforeunload = () => {
      consolePopoutRef.current = null;
      setIsConsolePoppedOut(false);
    };
    consolePopoutRef.current = popup;
    setIsConsolePoppedOut(true);
  }

  const onConnect = useCallback(
    (params: Connection) => setEdges((eds) => addEdge({ ...params, label: "" }, eds)),
    [setEdges]
  );

  function addNode(device: string, label: string, image?: string, positionOverride?: { x: number; y: number }) {
    const id = `${device}-${Date.now()}`;
    const netlabName = buildNetlabName(id, label || device, new Set(nodes.map((node) => node.data.netlabName).filter(Boolean) as string[]));
    setNodes((prev) => [
      ...prev,
      {
        id,
        type: "device",
        position: positionOverride || { x: 100 + prev.length * 40, y: 100 + prev.length * 40 },
        data: { label, device, version: "", image: image || "", netlabName },
      },
    ]);
  }

  function requestAddNode(device: string, label: string, position?: { x: number; y: number }) {
    const options = libraryByDevice.get(device) || [];
    if (options.length > 0) {
      setPendingDeviceAdd({ device, label, position });
      return;
    }
    addNode(device, label, "", position);
  }

  function onDragOver(event: React.DragEvent) {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  }

  function onDrop(event: React.DragEvent) {
    event.preventDefault();
    if (!reactFlowWrapper.current || !reactFlowInstance.current) return;
    const payload = event.dataTransfer.getData("application/netlab-device");
    if (!payload) return;
    const { device, label } = JSON.parse(payload) as { device: string; label: string };
    const bounds = reactFlowWrapper.current.getBoundingClientRect();
    const position = reactFlowInstance.current.project({
      x: event.clientX - bounds.left,
      y: event.clientY - bounds.top,
    });
    requestAddNode(device, label, position);
  }

  function updateSelectedNode(field: keyof NodeData, value: string) {
    if (!selectedNodeId) return;
    setNodes((prev) =>
      prev.map((node) =>
        node.id === selectedNodeId
          ? { ...node, data: { ...node.data, [field]: value } }
          : node
      )
    );
  }

  function getImageOptions(deviceId: string | undefined): { label: string; value: string }[] {
    if (!deviceId) return [];
    const entry = imageCatalog[deviceId];
    if (!entry) return [];
    const options = [];
    if (entry.clab) options.push({ label: `clab: ${entry.clab}`, value: entry.clab });
    if (entry.libvirt) options.push({ label: `libvirt: ${entry.libvirt}`, value: entry.libvirt });
    if (entry.virtualbox) options.push({ label: `virtualbox: ${entry.virtualbox}`, value: entry.virtualbox });
    const assigned = libraryByDevice.get(deviceId) || [];
    assigned.forEach((item) => {
      const label = item.kind === "qcow2" ? item.filename || item.reference : item.reference;
      options.push({ label: `${item.kind}: ${label}`, value: item.reference });
    });
    return options;
  }

  function updateSelectedEdge(field: keyof LinkData, value: string) {
    if (!selectedEdgeId) return;
    setEdges((prev) =>
      prev.map((edge) =>
        edge.id === selectedEdgeId
          ? {
              ...edge,
              label: field === "name" ? value : edge.label,
              data: { ...edge.data, [field]: value },
            }
          : edge
      )
    );
  }

  useEffect(() => {
    loadLab();
  }, [labId]);

  useEffect(() => {
    if (!labId) return;
    const timer = window.setInterval(() => {
      loadJobs();
    }, 4000);
    return () => window.clearInterval(timer);
  }, [labId]);

  useEffect(() => {
    if (nodes.length === 0) return;
    const statusMap = buildStatusMap(jobs, nodes);
    if (statusMap.size === 0) return;
    setNodes((prev) => {
      let changed = false;
      const next = prev.map((node) => {
        const name = node.data.netlabName || node.id;
        const status = statusMap.get(name);
        if (!status || node.data.status === status) {
          return node;
        }
        changed = true;
        return { ...node, data: { ...node.data, status } };
      });
      return changed ? next : prev;
    });
  }, [jobs]);

  useEffect(() => {
    if (!selectedNode) return;
    const job = findLatestNodeJob(selectedNode.id);
    if (!job) return;
    loadNodeActionLog();
  }, [jobs, selectedNodeId]);

  useEffect(() => {
    const popup = consolePopoutRef.current;
    if (!popup || popup.closed) return;
    const output = popup.document.getElementById("console-output");
    if (output) {
      output.textContent = consoleOutput || "";
    }
    const meta = popup.document.getElementById("console-meta");
    if (meta) {
      meta.textContent = selectedNode ? `Node: ${selectedNode.data.label}` : "No node selected";
    }
  }, [consoleOutput, selectedNode]);

  useEffect(() => {
    if (!terminalHostEl || terminalRef.current) return;
    const terminal = new Terminal({
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace",
      fontSize: 15,
      lineHeight: 1.2,
      cursorBlink: true,
      convertEol: true,
      theme: {
        background: "#0b0f16",
        foreground: "#e3edf8",
        cursor: "#e3edf8",
      },
    });
    const fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.open(terminalHostEl);
    fitAddon.fit();
    terminal.onData((data) => {
      if (consoleSocket.current && consoleSocket.current.readyState === WebSocket.OPEN) {
        consoleSocket.current.send(data);
      }
    });
    terminalRef.current = terminal;
    fitAddonRef.current = fitAddon;
    const handleResize = () => fitAddon.fit();
    window.addEventListener("resize", handleResize);
    return () => {
      window.removeEventListener("resize", handleResize);
      terminal.dispose();
      terminalRef.current = null;
      fitAddonRef.current = null;
    };
  }, [terminalHostEl]);

  function handleNodeContextMenu(event: React.MouseEvent, nodeId: string) {
    event.preventDefault();
    setContextMenu({ x: event.clientX, y: event.clientY, nodeId });
  }

  function deleteNode(nodeId: string) {
    setNodes((prev) => prev.filter((node) => node.id !== nodeId));
    setEdges((prev) => prev.filter((edge) => edge.source !== nodeId && edge.target !== nodeId));
    setContextMenu(null);
  }

  if (!localStorage.getItem("token")) {
    return <div className="panel">Please sign in to view this lab.</div>;
  }

  if (!lab) {
    return <div className="panel">Loading lab...</div>;
  }

  return (
    <div className="page">
      <header className="page-header">
        <div className="eyebrow">Lab</div>
        <h1>{lab.name}</h1>
        <p>Created {new Date(lab.created_at).toLocaleString()}</p>
        <div className="badge-row">
          <span className="badge">Topology canvas</span>
          <span className="badge">YAML sync</span>
          <span className="badge">containerlab runtime</span>
        </div>
      </header>

      <section className="panel">
        <div className="panel-header">
          <div>
            <h3>Topology actions</h3>
            <p className="panel-subtitle">Keep YAML and canvas in lockstep.</p>
          </div>
          <div className="page-actions">
            <button
              className="icon-button"
              type="button"
              onClick={() => setIsShareOpen(true)}
              aria-label="Share lab"
              title="Share lab"
            >
              🤝
            </button>
            <button
              className="icon-button"
              type="button"
              onClick={exportYaml}
              aria-label="Export YAML"
              title="Export YAML"
            >
              ⬇️
            </button>
            <button
              className="icon-button"
              type="button"
              onClick={saveGraph}
              aria-label="Save canvas"
              title="Save canvas"
            >
              💾
            </button>
            <button
              className="icon-button"
              type="button"
              onClick={loadGraph}
              aria-label="Reload canvas"
              title="Reload canvas"
            >
              🔄
            </button>
          </div>
        </div>
        {status && <p className="status">{status}</p>}
      </section>

      <div className="lab-workspace">
        <aside className="panel">
          <div className="panel-header">
            <h3>Device palette</h3>
          </div>
          <p className="panel-subtitle">Drag in common models to build the topology.</p>
          <div className="palette">
            {deviceCatalog.length > 0
              ? deviceCatalog.map((item) => (
                  <button
                    key={item.id}
                    onClick={() => requestAddNode(item.id, item.label)}
                    draggable
                    onDragStart={(event) => {
                      event.dataTransfer.setData(
                        "application/netlab-device",
                        JSON.stringify({ device: item.id, label: item.label })
                      );
                      event.dataTransfer.effectAllowed = "move";
                    }}
                  >
                    {renderDeviceIcon(item.id)}
                    {libraryByDevice.get(item.id)?.length ? (
                      <span className="palette-badge" title="Images available">
                        ●
                      </span>
                    ) : null}
                    <span>{item.label}</span>
                  </button>
                ))
              : fallbackPalette.map((item) => (
                  <button
                    key={item.id}
                    onClick={() => requestAddNode(item.id, item.label)}
                    draggable
                    onDragStart={(event) => {
                      event.dataTransfer.setData(
                        "application/netlab-device",
                        JSON.stringify({ device: item.id, label: item.label })
                      );
                      event.dataTransfer.effectAllowed = "move";
                    }}
                  >
                    {renderDeviceIcon(item.id)}
                    {libraryByDevice.get(item.id)?.length ? (
                      <span className="palette-badge" title="Images available">
                        ●
                      </span>
                    ) : null}
                    <span>{item.label}</span>
                  </button>
                ))}
          </div>
        </aside>

        <section className="panel canvas-panel" ref={reactFlowWrapper} onDrop={onDrop} onDragOver={onDragOver}>
          <div className="canvas-inner">
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              nodeTypes={nodeTypes}
              onInit={(instance) => {
                reactFlowInstance.current = instance;
              }}
              onNodeClick={(_, node) => {
                setSelectedNodeId(node.id);
                setSelectedEdgeId(null);
              }}
              onNodeContextMenu={(event, node) => handleNodeContextMenu(event, node.id)}
              onEdgeClick={(_, edge) => {
                setSelectedEdgeId(edge.id);
                setSelectedNodeId(null);
              }}
              fitView
            >
              <MiniMap />
              <Controls />
              <Background />
            </ReactFlow>
            {contextMenu && (
            <div
              className="context-menu"
              style={{ top: contextMenu.y, left: contextMenu.x }}
              onClick={() => setContextMenu(null)}
            >
              <button type="button" onClick={() => runNodeAction(contextMenu.nodeId, "start")}>
                <span className="menu-icon">▶️</span>
                Start device
              </button>
              <button type="button" onClick={() => runNodeAction(contextMenu.nodeId, "stop")}>
                <span className="menu-icon">⏹️</span>
                Stop device
              </button>
              <button type="button" onClick={() => connectConsoleForNode(contextMenu.nodeId)}>
                <span className="menu-icon">🖥️</span>
                Open console
              </button>
              <button type="button" onClick={() => deleteNode(contextMenu.nodeId)}>
                <span className="menu-icon">🗑️</span>
                Delete device
              </button>
            </div>
          )}
          </div>
        </section>

        <aside className="panel">
          <div className="panel-header">
            <h3>Inspector</h3>
          </div>
          <p className="panel-subtitle">Select a node or link to edit properties.</p>
          {selectedNode ? (
            <div className="form">
              <label>
                Label
                <input
                  value={selectedNode.data.label}
                  onChange={(e) => updateSelectedNode("label", e.target.value)}
                />
              </label>
              <label>
                Device
                <select
                  value={selectedNode.data.device || ""}
                  onChange={(e) => updateSelectedNode("device", e.target.value)}
                >
                  <option value="">Select device</option>
                  {deviceCatalog.map((device) => (
                    <option key={device.id} value={device.id}>
                      {device.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Version
                <input
                  value={selectedNode.data.version || ""}
                  onChange={(e) => updateSelectedNode("version", e.target.value)}
                />
              </label>
              <label>
                Image
                {getImageOptions(selectedNode.data.device).length > 0 ? (
                  <select
                    value={selectedNode.data.image || ""}
                    onChange={(e) => updateSelectedNode("image", e.target.value)}
                  >
                    <option value="">Select image</option>
                    {getImageOptions(selectedNode.data.device).map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    value={selectedNode.data.image || ""}
                    onChange={(e) => updateSelectedNode("image", e.target.value)}
                  />
                )}
              </label>
            </div>
          ) : selectedEdge ? (
            <div className="form">
              <label>
                Link name
                <input
                  value={selectedEdge.data?.name || ""}
                  onChange={(e) => updateSelectedEdge("name", e.target.value)}
                />
              </label>
              <label>
                Type
                <input
                  value={selectedEdge.data?.type || ""}
                  onChange={(e) => updateSelectedEdge("type", e.target.value)}
                />
              </label>
              <label>
                Pool
                <input
                  value={selectedEdge.data?.pool || ""}
                  onChange={(e) => updateSelectedEdge("pool", e.target.value)}
                />
              </label>
              <label>
                Prefix
                <input
                  value={selectedEdge.data?.prefix || ""}
                  onChange={(e) => updateSelectedEdge("prefix", e.target.value)}
                />
              </label>
            </div>
          ) : null}
        </aside>
      </div>

      <div className="lab-sections">
        <section className="panel">
          <div className="panel-header">
            <h3>Topology YAML</h3>
          </div>
          <textarea value={yaml} onChange={(e) => setYaml(e.target.value)} rows={16} />
        </section>
        <section className="panel">
          <div className="panel-header">
            <h3>Runtime control</h3>
          </div>
          {runtimeStatus && <p className="status">{runtimeStatus}</p>}
          {imageUploadProgress !== null && (
            <div className="upload-progress">
              <div className="upload-progress-label">Image upload {imageUploadProgress}%</div>
              <div className="upload-progress-track">
                <div className="upload-progress-bar" style={{ width: `${imageUploadProgress}%` }} />
              </div>
            </div>
          )}
          <div className="inline-form">
            <button
              className="icon-button"
              type="button"
              onClick={() => runAction("up")}
              aria-label="Start all nodes"
              title="Start all nodes"
            >
              ▶️
            </button>
            <button
              className="icon-button"
              type="button"
              onClick={() => runAction("down")}
              aria-label="Stop all nodes"
              title="Stop all nodes"
            >
              ⏹️
            </button>
            <button
              className="icon-button"
              type="button"
              onClick={() => runAction("restart")}
              aria-label="Restart all nodes"
              title="Restart all nodes"
            >
              🔄
            </button>
            <button className="button-secondary" onClick={fetchStatus}>
              Status
            </button>
            <button className="button-secondary" onClick={loadLatestLog}>
              Tail log
            </button>
            <button className="button-secondary" type="button" onClick={openImagePicker}>
              Upload image
            </button>
            <input
              ref={imageInputRef}
              className="file-input"
              type="file"
              accept=".tar,.tgz,.tar.gz"
              onChange={uploadImage}
            />
          </div>
          {imageUploadStatus && <p className="status">{imageUploadStatus}</p>}
          <textarea value={runtimeLog} readOnly rows={8} />
          <div className="list">
            {jobs.map((job) => (
              <div key={job.id} className="lab-item">
                <span>{job.action}</span>
                <span className="lab-meta">
                  {job.status} · {new Date(job.created_at).toLocaleString()}
                </span>
              </div>
            ))}
          </div>
        </section>
      </div>

      <div className="lab-sections">
        <section className="panel">
          <div className="panel-header">
            <h3>Console</h3>
          </div>
          <p className="panel-subtitle">
            Selected node: {selectedNode ? selectedNode.data.label : "None"}
          </p>
          <div className="inline-form">
            <button onClick={connectConsole} disabled={!selectedNode}>
              Connect
            </button>
            <button className="button-secondary" onClick={openConsolePopout}>
              {isConsolePoppedOut ? "Focus popout" : "Pop out"}
            </button>
            <button className="button-secondary" onClick={loadNodeActionLog} disabled={!selectedNode}>
              Load node action log
            </button>
          </div>
          <div className="terminal">
            <div
              ref={(el) => {
                terminalHostRef.current = el;
                setTerminalHostEl(el);
              }}
              className="xterm-host"
            />
          </div>
          <div className="terminal terminal-compact">
            <div className="terminal-title">Startup output</div>
            <pre className="terminal-output terminal-output-muted">
              {deviceLog || "[no startup output yet]"}
            </pre>
          </div>
          <div className="panel-subtitle">Type directly into the console.</div>
        </section>
      </div>
      {pendingDeviceAdd && (
        <div className="modal-backdrop" onClick={() => setPendingDeviceAdd(null)}>
          <div className="modal" onClick={(event) => event.stopPropagation()}>
            <div className="panel-header">
              <h3>Select image</h3>
              <button
                className="icon-button"
                type="button"
                onClick={() => setPendingDeviceAdd(null)}
                aria-label="Close image picker"
              >
                ✕
              </button>
            </div>
            <p className="panel-subtitle">
              Choose an image for {pendingDeviceAdd.label}. You can change it later in the inspector.
            </p>
            <div className="list">
              {(libraryByDevice.get(pendingDeviceAdd.device) || []).map((item) => (
                <button
                  key={item.id}
                  className="catalog-image"
                  type="button"
                  onClick={() => {
                    addNode(
                      pendingDeviceAdd.device,
                      pendingDeviceAdd.label,
                      item.reference,
                      pendingDeviceAdd.position
                    );
                    setPendingDeviceAdd(null);
                  }}
                >
                  <div className="catalog-image-title">
                    {item.kind.toUpperCase()} · {item.filename || item.reference}
                    {item.version ? ` (${item.version})` : ""}
                  </div>
                </button>
              ))}
              <button
                className="button-secondary"
                type="button"
                onClick={() => {
                  addNode(
                    pendingDeviceAdd.device,
                    pendingDeviceAdd.label,
                    "",
                    pendingDeviceAdd.position
                  );
                  setPendingDeviceAdd(null);
                }}
              >
                Skip image
              </button>
            </div>
          </div>
        </div>
      )}
      {isShareOpen && (
        <div className="modal-backdrop" onClick={() => setIsShareOpen(false)}>
          <div className="modal" onClick={(event) => event.stopPropagation()}>
            <div className="panel-header">
              <h3>Share this lab</h3>
              <button
                className="icon-button"
                type="button"
                onClick={() => setIsShareOpen(false)}
                aria-label="Close share panel"
              >
                ✕
              </button>
            </div>
            <p className="panel-subtitle">Grant view or edit access to another user.</p>
            <div className="inline-form">
              <input
                value={shareEmail}
                onChange={(e) => setShareEmail(e.target.value)}
                placeholder="User email"
              />
              <select value={shareRole} onChange={(e) => setShareRole(e.target.value)}>
                <option value="viewer">Viewer</option>
                <option value="editor">Editor</option>
              </select>
              <button onClick={shareLab}>Share</button>
            </div>
            <div className="list">
              {permissions.map((perm) => (
                <div key={perm.id} className="lab-item">
                  <span>{perm.user_email || perm.user_id}</span>
                  <div className="page-actions">
                    <span className="lab-meta">{perm.role}</span>
                    <button className="button-secondary" onClick={() => removePermission(perm.id)}>
                      Remove
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
