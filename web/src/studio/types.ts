
export enum DeviceType {
  ROUTER = 'router',
  SWITCH = 'switch',
  FIREWALL = 'firewall',
  HOST = 'host',
  EXTERNAL = 'external',
  CONTAINER = 'container'
}

// Node type discriminator for canvas nodes
export type NodeType = 'device' | 'external';

// Connection type for external networks
export type ExternalConnectionType = 'vlan' | 'bridge';

export type AnnotationType = 'text' | 'rect' | 'circle' | 'arrow' | 'caption';

export interface Annotation {
  id: string;
  type: AnnotationType;
  x: number;
  y: number;
  width?: number;
  height?: number;
  text?: string;
  color?: string;
  fontSize?: number;
  targetX?: number; // For arrows
  targetY?: number; // For arrows
  zIndex?: number; // Layer ordering (lower = behind, higher = front)
}

export interface DeviceImage {
  id: string;
  version: string;
  filename: string;
  filesize: string;
  isActive: boolean;
  uploadDate: string;
}

export interface DeviceModel {
  id: string;
  type: DeviceType;
  name: string;
  icon: string;
  versions: string[];
  images?: DeviceImage[];
  isActive: boolean;
  vendor: string;
  // Extended metadata fields
  portNaming?: string;
  portStartIndex?: number;
  maxPorts?: number;
  managementInterface?: string;
  requiresImage?: boolean;
  supportedImageKinds?: string[];
  documentationUrl?: string;
  licenseRequired?: boolean;
  tags?: string[];
  // Additional config fields
  memory?: number;
  cpu?: number;
  diskDriver?: string;
  nicDriver?: string;
  machineType?: string;
  kind?: string;
  consoleShell?: string;
  notes?: string;
  readinessProbe?: string;
  readinessPattern?: string;
  readinessTimeout?: number;
  vendorOptions?: Record<string, unknown>;
  isCustom?: boolean;
}

export interface DeviceConfig {
  base: Record<string, unknown>;
  overrides: Record<string, unknown>;
  effective: Record<string, unknown>;
}

export interface ImageHostStatus {
  host_id: string;
  host_name: string;
  status: 'synced' | 'syncing' | 'failed' | 'missing' | 'unknown';
  size_bytes?: number | null;
  synced_at?: string | null;
  error_message?: string | null;
}

export interface ImageLibraryEntry {
  id: string;
  kind: string;
  reference: string;
  filename?: string;
  device_id?: string | null;
  version?: string | null;
  // Extended metadata fields (optional for backward compatibility)
  vendor?: string | null;
  uploaded_at?: string | null;
  size_bytes?: number | null;
  is_default?: boolean;
  notes?: string;
  compatible_devices?: string[];
  source?: string | null; // Import source (e.g., ISO filename)
  sha256?: string | null; // SHA256 checksum for integrity verification
  build_status?: string | null; // queued, building, complete, failed, ignored (IOL source images)
  build_error?: string | null; // Build error for IOL source images
  build_job_id?: string | null; // Background job ID for image build
  built_from?: string | null; // Source image ID for derived Docker images
  build_requested_at?: string | null;
  build_ignored_at?: string | null;
  build_ignored_by?: string | null;
  // Sync status across agents (populated by API)
  host_status?: ImageHostStatus[];
}

// Base node interface with common properties
interface BaseNode {
  id: string;
  name: string;
  x: number;
  y: number;
  label?: string;
}

// Device node - represents a lab device (router, switch, etc.)
export interface DeviceNode extends BaseNode {
  nodeType: 'device';
  container_name?: string; // Immutable container identifier (set on first save, never changes)
  type: DeviceType;
  model: string;
  version: string;
  cpu?: number;
  memory?: number;
  disk_driver?: string;
  nic_driver?: string;
  machine_type?: string;
  config?: string;
  host?: string; // Agent ID for multi-host placement
}

// External network node - represents an external network connection
export interface ExternalNetworkNode extends BaseNode {
  nodeType: 'external';
  // New model: reference to infrastructure managed interface
  managedInterfaceId?: string;
  // Derived display fields (populated by API)
  managedInterfaceName?: string;
  managedInterfaceHostId?: string;
  managedInterfaceHostName?: string;
  // Legacy fields (kept for backward compat display)
  connectionType?: ExternalConnectionType; // 'vlan' or 'bridge'
  parentInterface?: string; // e.g., 'ens192', 'eth0' - for VLAN mode
  vlanId?: number; // VLAN ID (1-4094) - for VLAN mode
  bridgeName?: string; // e.g., 'br-prod' - for bridge mode
  host?: string; // Agent/host ID where this external network is located
}

// Union type for all node types
export type Node = DeviceNode | ExternalNetworkNode;

// Type guard for device nodes
export function isDeviceNode(node: Node): node is DeviceNode {
  return node.nodeType === 'device' || !('nodeType' in node) || node.nodeType === undefined;
}

// Type guard for external network nodes
export function isExternalNetworkNode(node: Node): node is ExternalNetworkNode {
  return 'nodeType' in node && node.nodeType === 'external';
}

// Legacy Node type for backward compatibility (alias to DeviceNode)
export type LegacyNode = {
  id: string;
  name: string;
  container_name?: string;
  type: DeviceType;
  model: string;
  version: string;
  x: number;
  y: number;
  label?: string;
  cpu?: number;
  memory?: number;
  config?: string;
};

export interface Link {
  id: string;
  source: string;
  target: string;
  type: 'p2p' | 'lan';
  sourceInterface?: string;
  targetInterface?: string;
  bandwidth?: string;
}

export interface Topology {
  name: string;
  nodes: Node[];
  links: Link[];
  annotations?: Annotation[];
}

export interface ConsoleWindow {
  id: string;
  deviceIds: string[];
  activeDeviceId: string;
  x: number;
  y: number;
  isExpanded: boolean;
}

// Layout persistence types
export interface NodeLayout {
  x: number;
  y: number;
  label?: string;
  color?: string;
  metadata?: Record<string, unknown>;
}

export interface AnnotationLayout {
  id: string;
  type: string; // text, rect, circle, arrow, caption
  x: number;
  y: number;
  width?: number;
  height?: number;
  text?: string;
  color?: string;
  fontSize?: number;
  targetX?: number; // For arrows
  targetY?: number; // For arrows
  zIndex?: number; // Layer ordering (lower = behind, higher = front)
  metadata?: Record<string, unknown>;
}

export interface LinkLayout {
  color?: string;
  strokeWidth?: number;
  style?: string; // solid, dashed, dotted
  metadata?: Record<string, unknown>;
}

export interface CanvasState {
  zoom?: number;
  offsetX?: number;
  offsetY?: number;
}

export interface LabLayout {
  version: number;
  canvas?: CanvasState;
  nodes: Record<string, NodeLayout>; // node_id -> position
  annotations: AnnotationLayout[];
  links?: Record<string, LinkLayout>; // link_id -> styling
  custom?: Record<string, unknown>;
}

// Per-host resource metrics returned by the dashboard API
export interface PerHostMetrics {
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
  vms_running?: number;
  started_at: string | null;
}

// Aggregated system metrics for the dashboard status strip
export interface SystemMetrics {
  agents: { online: number; total: number };
  containers: { running: number; total: number };
  vms?: { running: number; total: number };
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
  per_host?: PerHostMetrics[];
  is_multi_host?: boolean;
}
