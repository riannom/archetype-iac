
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
}

export interface Node {
  id: string;
  name: string;
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
