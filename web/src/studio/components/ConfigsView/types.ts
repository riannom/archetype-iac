import { Node, DeviceNode } from '../../types';
import { RuntimeStatus } from '../RuntimeControl';

export interface ConfigSnapshot {
  id: string;
  lab_id: string;
  node_name: string;
  content: string;
  content_hash: string;
  snapshot_type: string;
  device_kind: string | null;
  mapped_to_node_id: string | null;
  created_at: string;
  is_active: boolean;
  is_orphaned: boolean;
}

export interface ConfigsViewProps {
  labId: string;
  nodes: Node[];
  runtimeStates: Record<string, RuntimeStatus>;
  studioRequest: <T>(path: string, options?: RequestInit) => Promise<T>;
  onExtractConfigs: () => Promise<void>;
}

export interface OrphanedConfigGroup {
  device_kind: string;
  snapshots: ConfigSnapshot[];
}
