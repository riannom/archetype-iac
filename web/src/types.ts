export interface GraphEndpoint {
  node: string;
  ifname?: string | null;
}

export interface GraphLink {
  endpoints: GraphEndpoint[];
  type?: string | null;
  name?: string | null;
  pool?: string | null;
  prefix?: string | null;
  bridge?: string | null;
  mtu?: number | null;
  bandwidth?: number | null;
}

export interface GraphNode {
  id: string;
  name: string;
  container_name?: string | null; // Immutable container identifier for backend operations
  device?: string | null;
  image?: string | null;
  version?: string | null;
  role?: string | null;
  mgmt?: Record<string, unknown> | null;
  vars?: Record<string, unknown> | null;
}

export interface TopologyGraph {
  nodes: GraphNode[];
  links: GraphLink[];
  defaults?: Record<string, unknown> | null;
}
