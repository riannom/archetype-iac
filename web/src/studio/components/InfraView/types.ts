import { Node } from '../../types';
import { NodeStateEntry } from '../../../types/nodeState';
import { LinkStateData } from '../../hooks/useLabStateWS';
import { DeviceModel } from '../../types';

export interface HostGroup {
  hostId: string;
  hostName: string;
  agentId: string;
  nodes: NodeWithState[];
  localLinks: LinkStateData[];
  stats: HostStats;
}

export interface NodeWithState {
  node: Node;
  state: NodeStateEntry | null;
  containerName: string;
}

export interface HostStats {
  nodeCount: number;
  runningCount: number;
  linkCount: number;
  vlanTags: Set<number>;
}

export interface InfraViewProps {
  labId: string;
  nodes: Node[];
  nodeStates: Record<string, NodeStateEntry>;
  linkStates: Map<string, LinkStateData>;
  agents: { id: string; name: string }[];
  deviceModels: DeviceModel[];
}

export interface AgentGraphNode {
  agentId: string;
  agentName: string;
  color: string;
  nodes: NodeWithState[];
  localLinks: LinkStateData[];
  stats: HostStats;
}

export interface CrossHostBundle {
  agentA: string;
  agentB: string;
  links: LinkStateData[];
  hasError: boolean;
  allUp: boolean;
}

