// ============================================================================
// Types
// ============================================================================

interface InfraSettings {
  overlay_mtu: number;
  mtu_verification_enabled: boolean;
  overlay_preserve_container_mtu: boolean;
  overlay_clamp_host_mtu: boolean;
  updated_at: string | null;
  updated_by_id: string | null;
}

interface AgentMeshNode {
  id: string;
  name: string;
  address: string;
  status: string;
}

interface AgentLinkOut {
  id: string;
  source_agent_id: string;
  source_agent_name: string | null;
  target_agent_id: string;
  target_agent_name: string | null;
  link_type: string;
  configured_mtu: number;
  tested_mtu: number | null;
  last_test_at: string | null;
  test_status: string;
  test_error: string | null;
  latency_ms: number | null;
  test_path: string;
}

export interface AgentMeshResponse {
  agents: AgentMeshNode[];
  links: AgentLinkOut[];
  settings: InfraSettings;
}

export interface MtuTestResponse {
  success: boolean;
  source_agent_id: string;
  target_agent_id: string;
  configured_mtu: number;
  tested_mtu: number | null;
  link_type: string | null;
  latency_ms: number | null;
  test_path: string | null;
  error: string | null;
}

export interface MtuTestAllResponse {
  total_pairs: number;
  successful: number;
  failed: number;
  results: MtuTestResponse[];
}

export interface InterfaceDetail {
  name: string;
  mtu: number;
  is_physical: boolean;
  is_default_route: boolean;
  mac: string | null;
  ipv4_addresses: string[];
  state: string;
}

export interface InterfaceDetailsResponse {
  interfaces: InterfaceDetail[];
  default_route_interface: string | null;
  network_manager: string | null;
}

export interface AgentNetworkConfig {
  id: string;
  host_id: string;
  host_name: string | null;
  data_plane_interface: string | null;
  desired_mtu: number;
  current_mtu: number | null;
  last_sync_at: string | null;
  sync_status: string;
  sync_error: string | null;
  // Transport configuration
  transport_mode: string;  // "management" | "subinterface" | "dedicated"
  parent_interface: string | null;
  vlan_id: number | null;
  transport_ip: string | null;
  transport_subnet: string | null;
}

export interface ManagedInterface {
  id: string;
  host_id: string;
  host_name: string | null;
  name: string;
  interface_type: string;
  parent_interface: string | null;
  vlan_id: number | null;
  ip_address: string | null;
  desired_mtu: number;
  current_mtu: number | null;
  is_up: boolean;
  sync_status: string;
  sync_error: string | null;
  last_sync_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ManagedInterfacesResponse {
  interfaces: ManagedInterface[];
  total: number;
}

export interface NicGroupMember {
  id: string;
  nic_group_id: string;
  managed_interface_id: string;
  interface_name: string | null;
  interface_type: string | null;
  role: string | null;
  created_at: string;
}

export interface NicGroup {
  id: string;
  host_id: string;
  host_name: string | null;
  name: string;
  description: string | null;
  created_at: string;
  updated_at: string;
  members: NicGroupMember[];
}

export interface NicGroupsResponse {
  groups: NicGroup[];
  total: number;
}

interface LabInfo {
  id: string;
  name: string;
  state: string;
}

export interface ContainerDetail {
  name: string;
  status: string;
  node_name?: string;
  node_kind?: string;
  lab_prefix?: string;
  lab_id?: string | null;
  lab_name?: string | null;
  is_system?: boolean;
}

export interface VmDetail {
  name: string;
  status: string;
  node_name?: string;
  node_kind?: string;
  lab_prefix?: string;
  lab_id?: string | null;
  lab_name?: string | null;
}

export interface ImageDetail {
  image_id: string;
  reference: string;
  status: string;
  size_bytes: number | null;
  synced_at: string | null;
  error_message: string | null;
}

interface AgentInventoryImage {
  reference: string;
  display_reference: string;
  kind: string;
  size_bytes: number | null;
  created: string | null;
  device_id: string | null;
  tracked_image_id: string | null;
  tracked_status: string | null;
  is_needed: boolean;
  is_stale: boolean;
  reason: string | null;
}

export interface AgentImagesDetailResponse {
  agent_id: string;
  agent_name: string;
  images: ImageDetail[];
  inventory: AgentInventoryImage[];
  stale_images: AgentInventoryImage[];
  inventory_refreshed_at: string | null;
}

export interface AgentStaleCleanupResponse {
  agent_id: string;
  agent_name: string;
  status?: string;
  requested: number;
  deleted: string[];
  failed: Array<{
    reference: string;
    error: string;
  }>;
  stale_images_remaining: number;
  inventory_refreshed_at: string | null;
}

export interface BulkAgentStaleCleanupResponse {
  hosts: AgentStaleCleanupResponse[];
  total_hosts: number;
  processed_hosts: number;
  skipped_offline_hosts: number;
  total_requested: number;
  total_deleted: number;
  total_failed: number;
}

export interface HostDetailed {
  id: string;
  name: string;
  address: string;
  status: string;
  version: string;
  role: 'agent' | 'controller' | 'agent+controller';
  image_sync_strategy?: string;
  deployment_mode?: 'systemd' | 'docker' | 'unknown';
  capabilities: {
    providers?: string[];
    features?: string[];
    max_concurrent_jobs?: number;
  };
  resource_usage: {
    cpu_percent: number;
    memory_percent: number;
    memory_used_gb: number;
    memory_total_gb: number;
    storage_percent: number;
    storage_used_gb: number;
    storage_total_gb: number;
    containers_running: number;
    containers_total: number;
    vms_running: number;
    vms_total: number;
    container_details: ContainerDetail[];
    vm_details: VmDetail[];
  };
  images: ImageDetail[];
  labs: LabInfo[];
  lab_count: number;
  started_at: string | null;
  last_heartbeat: string | null;
  git_sha: string | null;
  last_error: string | null;
  error_since: string | null;
  data_plane_address: string | null;
}

export interface UpdateStatus {
  job_id: string;
  agent_id: string;
  from_version: string;
  to_version: string;
  status: string;
  progress_percent: number;
  error_message: string | null;
}

export type SyncStrategy = 'push' | 'pull' | 'on_demand' | 'disabled';
export type TabType = 'hosts' | 'settings' | 'network';

export const SYNC_STRATEGY_OPTIONS: { value: SyncStrategy; label: string; description: string }[] = [
  { value: 'on_demand', label: 'On Demand', description: 'Sync when deployment needs image' },
  { value: 'push', label: 'Push', description: 'Receive images on upload' },
  { value: 'pull', label: 'Pull', description: 'Pull images when online' },
  { value: 'disabled', label: 'Disabled', description: 'Manual sync only' },
];
