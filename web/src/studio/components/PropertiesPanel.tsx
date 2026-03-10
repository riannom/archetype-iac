
import React from 'react';
import { Node, Link, Annotation, DeviceModel, isExternalNetworkNode, ExternalNetworkNode, DeviceNode } from '../types';
import { RuntimeStatus } from './RuntimeControl';
import { PortManager } from '../hooks/usePortManager';
import ExternalNetworkConfig from './ExternalNetworkConfig';
import { NodeStateEntry } from '../../types/nodeState';
import AnnotationProperties from './properties/AnnotationProperties';
import LinkProperties from './properties/LinkProperties';
import DeviceNodeProperties from './properties/DeviceNodeProperties';

interface PropertiesPanelProps {
  selectedItem: Node | Link | Annotation | null;
  onUpdateNode: (id: string, updates: Partial<Node>) => void;
  onUpdateLink: (id: string, updates: Partial<Link>) => void;
  onUpdateAnnotation: (id: string, updates: Partial<Annotation>) => void;
  onDelete: (id: string) => void;
  nodes: Node[];
  links: Link[];
  annotations?: Annotation[];
  onOpenConsole: (nodeId: string) => void;
  runtimeStates: Record<string, RuntimeStatus>;
  onUpdateStatus: (nodeId: string, status: RuntimeStatus) => void;
  deviceModels: DeviceModel[];
  portManager: PortManager;
  onOpenConfigViewer?: (nodeId: string, nodeName: string, snapshotContent?: string, snapshotLabel?: string) => void;
  labId?: string;
  studioRequest?: <T>(path: string, options?: RequestInit) => Promise<T>;
  agents?: { id: string; name: string }[];
  nodeStates?: Record<string, NodeStateEntry>;
  nodeReadinessHints?: Record<
    string,
    {
      is_ready: boolean;
      actual_state: string;
      progress_percent?: number | null;
      message?: string | null;
    }
  >;
}

const PropertiesPanel: React.FC<PropertiesPanelProps> = ({
  selectedItem, onUpdateNode, onUpdateLink, onUpdateAnnotation, onDelete, nodes, links, annotations = [], onOpenConsole, runtimeStates, onUpdateStatus, deviceModels, portManager, onOpenConfigViewer, labId, studioRequest, agents = [], nodeStates = {}
}) => {
  if (!selectedItem) {
    return null;
  }

  const isLink = 'source' in selectedItem && 'target' in selectedItem;
  const isNodeItem = 'x' in selectedItem && 'y' in selectedItem && !isLink;
  const isAnnotation = isNodeItem && 'type' in selectedItem && typeof (selectedItem as Annotation).type === 'string' && ['text', 'rect', 'circle', 'arrow'].includes((selectedItem as Annotation).type as string);

  // External network node
  if (isNodeItem && !isAnnotation && isExternalNetworkNode(selectedItem as Node)) {
    const extNode = selectedItem as ExternalNetworkNode;
    return (
      <ExternalNetworkConfig
        node={extNode}
        onUpdate={(id, updates) => onUpdateNode(id, updates as Partial<Node>)}
        onDelete={onDelete}
        agents={agents}
      />
    );
  }

  // Annotation
  if (isAnnotation) {
    return (
      <AnnotationProperties
        annotation={selectedItem as Annotation}
        annotations={annotations}
        onUpdateAnnotation={onUpdateAnnotation}
        onDelete={onDelete}
      />
    );
  }

  // Link
  if (isLink) {
    return (
      <LinkProperties
        link={selectedItem as Link}
        nodes={nodes}
        portManager={portManager}
        onUpdateLink={onUpdateLink}
        onDelete={onDelete}
      />
    );
  }

  // Device node (default)
  const node = selectedItem as DeviceNode;
  return (
    <DeviceNodeProperties
      node={node}
      nodes={nodes}
      links={links}
      onUpdateNode={onUpdateNode}
      onUpdateLink={onUpdateLink}
      onDelete={onDelete}
      onOpenConsole={onOpenConsole}
      runtimeStates={runtimeStates}
      onUpdateStatus={onUpdateStatus}
      deviceModels={deviceModels}
      portManager={portManager}
      onOpenConfigViewer={onOpenConfigViewer}
      labId={labId}
      studioRequest={studioRequest}
      agents={agents}
      nodeStates={nodeStates}
    />
  );
};

export default PropertiesPanel;
