
export enum DeviceType {
  ROUTER = 'router',
  SWITCH = 'switch',
  FIREWALL = 'firewall',
  HOST = 'host',
  EXTERNAL = 'external',
  CONTAINER = 'container'
}

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
  requiresImage?: boolean;
  supportedImageKinds?: string[];
  documentationUrl?: string;
  licenseRequired?: boolean;
  tags?: string[];
  // Additional config fields
  memory?: number;
  cpu?: number;
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
}

export interface Node {
  id: string;
  name: string;
  container_name?: string; // Immutable container identifier (set on first save, never changes)
  type: DeviceType;
  model: string;
  version: string;
  x: number;
  y: number;
  label?: string;
  cpu?: number;
  memory?: number;
  config?: string;
}

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
