import { Node, Link, Annotation, AnnotationType, CanvasTool, DeviceModel } from '../../types';
import { RuntimeStatus } from '../RuntimeControl';
import { NodeStateEntry } from '../../../types/nodeState';
import { LinkStateData } from '../../hooks/useLabStateWS';

export interface CanvasProps {
  nodes: Node[];
  links: Link[];
  annotations: Annotation[];
  runtimeStates: Record<string, RuntimeStatus>;
  nodeStates?: Record<string, NodeStateEntry>;
  linkStates?: Map<string, LinkStateData>;
  scenarioHighlights?: { activeNodeNames: Set<string>; activeLinkName: string | null; stepName: string };
  deviceModels: DeviceModel[];
  labId?: string;
  agents?: { id: string; name: string }[];
  showAgentIndicators?: boolean;
  onToggleAgentIndicators?: () => void;
  activeTool?: CanvasTool;
  onToolCreate?: (type: AnnotationType, x: number, y: number, opts?: { width?: number; height?: number; targetX?: number; targetY?: number }) => void;
  onNodeMove: (id: string, x: number, y: number) => void;
  onAnnotationMove: (id: string, x: number, y: number) => void;
  onConnect: (sourceId: string, targetId: string) => void;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onOpenConsole: (nodeId: string) => void;
  onExtractConfig?: (nodeId: string) => void;
  onUpdateStatus: (nodeId: string, status: RuntimeStatus) => void;
  onDelete: (id: string) => void;
  onDropDevice?: (model: DeviceModel, x: number, y: number) => void;
  onDropExternalNetwork?: (x: number, y: number) => void;
  onUpdateAnnotation?: (id: string, updates: Partial<Annotation>) => void;
  selectedIds?: Set<string>;
  onSelectMultiple?: (ids: Set<string>) => void;
  focusNodeId?: string | null;
  onFocusHandled?: () => void;
}

export type ResizeHandle = 'nw' | 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w';

export interface ResizeState {
  id: string;
  handle: ResizeHandle;
  startX: number;
  startY: number;
  startWidth: number;
  startHeight: number;
  startAnnX: number;
  startAnnY: number;
}

export interface ContextMenu {
  x: number;
  y: number;
  id: string;
  type: 'node' | 'link';
}
