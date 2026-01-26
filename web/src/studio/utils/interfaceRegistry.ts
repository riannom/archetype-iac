/**
 * Interface Registry - Device-specific interface naming patterns
 *
 * This utility provides interface name generation and management for different
 * network device types used in containerlab topologies.
 */

export interface InterfacePattern {
  /** Pattern template with {index} placeholder, e.g., "eth{index}" */
  pattern: string;
  /** Starting index for data interfaces (management uses index 0 typically) */
  startIndex: number;
  /** Management interface name (excluded from data interface pool) */
  managementInterface: string;
  /** Maximum number of interfaces supported */
  maxInterfaces?: number;
}

/**
 * Default interface patterns for common network device types.
 * The key is the device model ID (as used in containerlab).
 */
export const DEFAULT_PATTERNS: Record<string, InterfacePattern> = {
  // Linux-based devices
  linux: {
    pattern: 'eth{index}',
    startIndex: 1,
    managementInterface: 'eth0',
    maxInterfaces: 32,
  },
  alpine: {
    pattern: 'eth{index}',
    startIndex: 1,
    managementInterface: 'eth0',
    maxInterfaces: 32,
  },

  // VyOS
  vyos: {
    pattern: 'eth{index}',
    startIndex: 1,
    managementInterface: 'eth0',
    maxInterfaces: 16,
  },

  // Arista cEOS
  ceos: {
    pattern: 'Ethernet{index}',
    startIndex: 1,
    managementInterface: 'Management0',
    maxInterfaces: 64,
  },
  'arista_ceos': {
    pattern: 'Ethernet{index}',
    startIndex: 1,
    managementInterface: 'Management0',
    maxInterfaces: 64,
  },

  // Nokia SR Linux
  srl: {
    pattern: 'e1-{index}',
    startIndex: 1,
    managementInterface: 'mgmt0',
    maxInterfaces: 34,
  },
  'nokia_srlinux': {
    pattern: 'e1-{index}',
    startIndex: 1,
    managementInterface: 'mgmt0',
    maxInterfaces: 34,
  },

  // Cisco XRd
  xrd: {
    pattern: 'Gi0-0-0-{index}',
    startIndex: 0,
    managementInterface: 'MgmtEth0/RP0/CPU0/0',
    maxInterfaces: 32,
  },
  'cisco_xrd': {
    pattern: 'Gi0-0-0-{index}',
    startIndex: 0,
    managementInterface: 'MgmtEth0/RP0/CPU0/0',
    maxInterfaces: 32,
  },

  // Juniper cRPD
  crpd: {
    pattern: 'eth{index}',
    startIndex: 1,
    managementInterface: 'eth0',
    maxInterfaces: 16,
  },
  'juniper_crpd': {
    pattern: 'eth{index}',
    startIndex: 1,
    managementInterface: 'eth0',
    maxInterfaces: 16,
  },

  // FRRouting
  frr: {
    pattern: 'eth{index}',
    startIndex: 1,
    managementInterface: 'eth0',
    maxInterfaces: 16,
  },

  // Generic/default pattern
  generic: {
    pattern: 'eth{index}',
    startIndex: 1,
    managementInterface: 'eth0',
    maxInterfaces: 32,
  },
};

/**
 * Generate an interface name for a given device model and index.
 */
export function generateInterfaceName(modelId: string, index: number): string {
  const pattern = DEFAULT_PATTERNS[modelId] || DEFAULT_PATTERNS.generic;
  return pattern.pattern.replace('{index}', String(index));
}

/**
 * Get the pattern for a device model.
 */
export function getPattern(modelId: string): InterfacePattern {
  return DEFAULT_PATTERNS[modelId] || DEFAULT_PATTERNS.generic;
}

/**
 * Parse an interface name to extract its index.
 * Returns null if the interface doesn't match the expected pattern.
 */
export function parseInterfaceIndex(modelId: string, interfaceName: string): number | null {
  const pattern = DEFAULT_PATTERNS[modelId] || DEFAULT_PATTERNS.generic;

  // Build a regex from the pattern
  const escaped = pattern.pattern.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const regexStr = escaped.replace('\\{index\\}', '(\\d+)');
  const regex = new RegExp(`^${regexStr}$`);

  const match = interfaceName.match(regex);
  if (match && match[1]) {
    return parseInt(match[1], 10);
  }
  return null;
}

/**
 * Generate a list of available interfaces for a device, excluding used ones.
 */
export function getAvailableInterfaces(
  modelId: string,
  usedInterfaces: Set<string>,
  count: number = 10
): string[] {
  const pattern = DEFAULT_PATTERNS[modelId] || DEFAULT_PATTERNS.generic;
  const available: string[] = [];
  const maxIndex = pattern.startIndex + (pattern.maxInterfaces || 32);

  for (let i = pattern.startIndex; i < maxIndex && available.length < count; i++) {
    const ifName = pattern.pattern.replace('{index}', String(i));
    if (!usedInterfaces.has(ifName)) {
      available.push(ifName);
    }
  }

  return available;
}

/**
 * Get the next available interface for a device.
 */
export function getNextAvailableInterface(
  modelId: string,
  usedInterfaces: Set<string>
): string {
  const available = getAvailableInterfaces(modelId, usedInterfaces, 1);
  if (available.length > 0) {
    return available[0];
  }
  // Fallback: generate based on count of used interfaces
  const pattern = DEFAULT_PATTERNS[modelId] || DEFAULT_PATTERNS.generic;
  return pattern.pattern.replace('{index}', String(pattern.startIndex + usedInterfaces.size));
}

/**
 * Check if an interface name matches the expected pattern for a device.
 */
export function isValidInterface(modelId: string, interfaceName: string): boolean {
  return parseInterfaceIndex(modelId, interfaceName) !== null;
}

/**
 * Get the management interface for a device model.
 */
export function getManagementInterface(modelId: string): string {
  const pattern = DEFAULT_PATTERNS[modelId] || DEFAULT_PATTERNS.generic;
  return pattern.managementInterface;
}
