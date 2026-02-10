/**
 * Test data factories for creating mock data in tests.
 *
 * These factories provide consistent test data with sensible defaults
 * that can be overridden as needed.
 */

/**
 * Host data for InfrastructurePage tests
 */
export interface MockHostDetailed {
  id: string;
  name: string;
  address: string;
  status: string;
  version: string;
  role: "agent" | "controller" | "agent+controller";
  image_sync_strategy?: string;
  deployment_mode?: "systemd" | "docker" | "unknown";
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
  };
  labs: Array<{
    id: string;
    name: string;
    state: string;
  }>;
  lab_count: number;
  last_heartbeat: string | null;
}

let hostIdCounter = 1;

/**
 * Create a mock host with sensible defaults
 */
export function createMockHost(
  overrides: Partial<MockHostDetailed> = {}
): MockHostDetailed {
  const id = overrides.id || `host-${hostIdCounter++}`;
  return {
    id,
    name: overrides.name || `Agent ${id}`,
    address: overrides.address || `${id}.local:8080`,
    status: overrides.status || "online",
    version: overrides.version || "1.0.0",
    role: overrides.role || "agent",
    image_sync_strategy: overrides.image_sync_strategy || "on_demand",
    deployment_mode: overrides.deployment_mode || "systemd",
    capabilities: {
      providers: ["docker"],
      ...overrides.capabilities,
    },
    resource_usage: {
      cpu_percent: 25,
      memory_percent: 45,
      memory_used_gb: 8,
      memory_total_gb: 16,
      storage_percent: 60,
      storage_used_gb: 120,
      storage_total_gb: 200,
      containers_running: 5,
      containers_total: 10,
      ...overrides.resource_usage,
    },
    labs: overrides.labs || [],
    lab_count: overrides.lab_count || overrides.labs?.length || 0,
    last_heartbeat: overrides.last_heartbeat || new Date().toISOString(),
  };
}

/**
 * Create multiple mock hosts
 */
export function createMockHosts(
  count: number,
  overridesPerHost?: Partial<MockHostDetailed>[]
): MockHostDetailed[] {
  return Array.from({ length: count }, (_, i) =>
    createMockHost(overridesPerHost?.[i])
  );
}

/**
 * Create an online host
 */
export function createOnlineHost(
  overrides: Partial<MockHostDetailed> = {}
): MockHostDetailed {
  return createMockHost({ status: "online", ...overrides });
}

/**
 * Create an offline host
 */
export function createOfflineHost(
  overrides: Partial<MockHostDetailed> = {}
): MockHostDetailed {
  return createMockHost({
    status: "offline",
    resource_usage: {
      cpu_percent: 0,
      memory_percent: 0,
      memory_used_gb: 0,
      memory_total_gb: 0,
      storage_percent: 0,
      storage_used_gb: 0,
      storage_total_gb: 0,
      containers_running: 0,
      containers_total: 0,
    },
    last_heartbeat: new Date(Date.now() - 600000).toISOString(), // 10 minutes ago
    ...overrides,
  });
}

/**
 * Lab data
 */
export interface MockLab {
  id: string;
  name: string;
  state: string;
  owner_id?: string;
  provider?: string;
}

let labIdCounter = 1;

/**
 * Create a mock lab
 */
export function createMockLab(overrides: Partial<MockLab> = {}): MockLab {
  const id = overrides.id || `lab-${labIdCounter++}`;
  return {
    id,
    name: overrides.name || `Test Lab ${id}`,
    state: overrides.state || "stopped",
    owner_id: overrides.owner_id || "user-1",
    provider: overrides.provider || "docker",
    ...overrides,
  };
}

/**
 * Device model data for NodesPage tests
 */
export interface MockDeviceModel {
  id: string;
  type: "container" | "vm";
  name: string;
  icon: string;
  versions: string[];
  isActive: boolean;
  vendor?: string;
  isCustom?: boolean;
}

/**
 * Create a mock device model
 */
export function createMockDeviceModel(
  overrides: Partial<MockDeviceModel> = {}
): MockDeviceModel {
  return {
    id: overrides.id || "ceos",
    type: overrides.type || "container",
    name: overrides.name || "Arista cEOS",
    icon: overrides.icon || "fa-network-wired",
    versions: overrides.versions || ["4.28.0F", "4.29.0F"],
    isActive: overrides.isActive ?? true,
    vendor: overrides.vendor || "arista",
    isCustom: overrides.isCustom ?? false,
    ...overrides,
  };
}

/**
 * Image library entry
 */
export interface MockImageLibraryEntry {
  id: string;
  kind: "docker" | "qcow2";
  device_id?: string;
  version?: string;
  reference?: string;
  filename?: string;
  is_default?: boolean;
}

/**
 * Create a mock image library entry
 */
export function createMockImageEntry(
  overrides: Partial<MockImageLibraryEntry> = {}
): MockImageLibraryEntry {
  return {
    id: overrides.id || "docker:ceos:4.28.0F",
    kind: overrides.kind || "docker",
    device_id: overrides.device_id || "ceos",
    version: overrides.version || "4.28.0F",
    reference: overrides.reference || "ceos:4.28.0F",
    is_default: overrides.is_default ?? false,
    ...overrides,
  };
}

/**
 * User data
 */
export interface MockUser {
  id: string;
  username: string;
  email: string;
  is_active: boolean;
  global_role: string;
  created_at: string;
}

/**
 * Create a mock user
 */
export function createMockUser(overrides: Partial<MockUser> = {}): MockUser {
  return {
    id: overrides.id || "user-1",
    username: overrides.username || "testuser",
    email: overrides.email || "user@example.com",
    is_active: overrides.is_active ?? true,
    global_role: overrides.global_role || "operator",
    created_at: overrides.created_at || "2024-01-01T00:00:00Z",
    ...overrides,
  };
}

/**
 * Create an admin user
 */
export function createMockAdminUser(
  overrides: Partial<MockUser> = {}
): MockUser {
  return createMockUser({
    username: "admin",
    email: "admin@example.com",
    global_role: "super_admin",
    ...overrides,
  });
}

/**
 * Reset all ID counters (call in beforeEach)
 */
export function resetFactories(): void {
  hostIdCounter = 1;
  labIdCounter = 1;
}
