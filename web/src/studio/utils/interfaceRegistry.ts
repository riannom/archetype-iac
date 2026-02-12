/**
 * Interface Registry - Device-specific interface naming patterns
 *
 * This utility provides interface name generation and management for different
 * network device types used in containerlab topologies.
 *
 * IMPORTANT: Interface patterns are now sourced from the /vendors API endpoint,
 * which is the single source of truth (agent/vendors.py). The DEFAULT_PATTERNS
 * below serve only as fallbacks when the API data is not yet loaded.
 */

import { DeviceModel } from '../types';

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
 * Fallback interface patterns for when API data is not available.
 * The primary source of truth is agent/vendors.py via the /vendors API.
 */
const FALLBACK_PATTERNS: Record<string, InterfacePattern> = {
  // Generic fallback for unknown devices
  generic: {
    pattern: 'eth{index}',
    startIndex: 1,
    managementInterface: 'eth0',
    maxInterfaces: 32,
  },
  // External network nodes
  external: {
    pattern: 'eth{index}',
    startIndex: 0,
    managementInterface: '',
    maxInterfaces: 1,
  },
  // Cisco Catalyst 9000v variants (Q200/UADP from RefPlat metadata)
  'cat9000v-q200': {
    pattern: 'GigabitEthernet1/0/{index}',
    startIndex: 1,
    managementInterface: 'GigabitEthernet0/0',
    maxInterfaces: 24,
  },
  'cat9000v-uadp': {
    pattern: 'GigabitEthernet1/0/{index}',
    startIndex: 1,
    managementInterface: 'GigabitEthernet0/0',
    maxInterfaces: 24,
  },
  cat9000v_q200: {
    pattern: 'GigabitEthernet1/0/{index}',
    startIndex: 1,
    managementInterface: 'GigabitEthernet0/0',
    maxInterfaces: 24,
  },
  cat9000v_uadp: {
    pattern: 'GigabitEthernet1/0/{index}',
    startIndex: 1,
    managementInterface: 'GigabitEthernet0/0',
    maxInterfaces: 24,
  },
};

/**
 * Common device ID aliases that map to canonical IDs.
 * This handles cases where devices are stored with alias names
 * (e.g., from YAML imports using "eos" instead of "ceos").
 */
const DEVICE_ALIASES: Record<string, string> = {
  // Arista EOS aliases
  eos: 'ceos',
  arista_eos: 'ceos',
  arista_ceos: 'ceos',
  // Nokia SR Linux aliases
  srl: 'nokia_srlinux',
  srlinux: 'nokia_srlinux',
  // Cisco legacy/custom IDs from older RefPlat imports
  cat8000v: 'c8000v',
  nxosv9000: 'cisco_n9kv',
  iosxrv9000: 'cisco_iosxr',
  ioll2_xe: 'iol-l2',
  'ioll2-xe': 'iol-l2',
  iol_xe: 'iol-xe',
  'iol-xe-serial-4eth': 'iol-xe',
};

/**
 * Runtime pattern registry populated from device models.
 * This is updated when device models are loaded from the API.
 */
let _runtimePatterns: Record<string, InterfacePattern> = {};

/**
 * Build an interface pattern from a DeviceModel's port configuration.
 */
function buildPatternFromModel(model: DeviceModel): InterfacePattern {
  // Cat9000v variants from RefPlat use explicit data-port naming:
  // GigabitEthernet0/0 (mgmt) + GigabitEthernet1/0/1..24 (data).
  const modelId = (model.id || '').toLowerCase();
  if (
    modelId === 'cat9000v-q200' ||
    modelId === 'cat9000v-uadp' ||
    modelId === 'cat9000v_q200' ||
    modelId === 'cat9000v_uadp'
  ) {
    return {
      pattern: 'GigabitEthernet1/0/{index}',
      startIndex: 1,
      managementInterface: 'GigabitEthernet0/0',
      maxInterfaces: 24,
    };
  }

  const portNaming = model.portNaming || 'eth';
  const startIndex = model.portStartIndex ?? 1;
  const maxPorts = model.maxPorts ?? 32;

  // Build pattern string - if portNaming already contains {index}, use as-is
  // Otherwise append {index} to the naming pattern
  const pattern = portNaming.includes('{index}')
    ? portNaming
    : `${portNaming}{index}`;

  // Determine management interface based on device type
  let managementInterface = 'eth0';
  if (model.kind === 'ceos' || model.id === 'ceos') {
    managementInterface = 'Management0';
  } else if (model.kind === 'nokia_srlinux' || model.id === 'nokia_srlinux' || model.id === 'srl') {
    managementInterface = 'mgmt0';
  } else if (model.kind === 'cisco_xrd' || model.id === 'cisco_xrd' || model.id === 'xrd') {
    managementInterface = 'MgmtEth0/RP0/CPU0/0';
  }

  return {
    pattern,
    startIndex,
    managementInterface,
    maxInterfaces: maxPorts,
  };
}

/**
 * Initialize the interface registry from device models.
 * This should be called when device models are loaded from the API.
 *
 * @param models - Device models from the DeviceCatalogContext
 */
export function initializePatterns(models: DeviceModel[]): void {
  const patterns: Record<string, InterfacePattern> = {};

  for (const model of models) {
    const pattern = buildPatternFromModel(model);

    // Register by model ID
    patterns[model.id] = pattern;

    // Also register by kind if different from id
    if (model.kind && model.kind !== model.id) {
      patterns[model.kind] = pattern;
    }
  }

  _runtimePatterns = patterns;
}

/**
 * Resolve a device ID to its canonical form using aliases.
 */
function resolveAlias(modelId: string): string {
  return DEVICE_ALIASES[modelId] || modelId;
}

/**
 * Get the pattern for a device model.
 * First checks runtime patterns (from API), then falls back to defaults.
 * Also resolves common device aliases (e.g., "eos" -> "ceos").
 */
export function getPattern(modelId: string): InterfacePattern {
  // Check runtime patterns first (populated from /vendors API)
  if (_runtimePatterns[modelId]) {
    return _runtimePatterns[modelId];
  }

  // Try resolving alias and check again
  const canonicalId = resolveAlias(modelId);
  if (canonicalId !== modelId && _runtimePatterns[canonicalId]) {
    return _runtimePatterns[canonicalId];
  }

  // Check fallback patterns
  if (FALLBACK_PATTERNS[modelId]) {
    return FALLBACK_PATTERNS[modelId];
  }

  // Return generic fallback
  return FALLBACK_PATTERNS.generic;
}

/**
 * Check if patterns have been initialized from API data.
 */
export function isInitialized(): boolean {
  return Object.keys(_runtimePatterns).length > 0;
}

/**
 * Get all registered patterns (for debugging/testing).
 */
export function getAllPatterns(): Record<string, InterfacePattern> {
  return { ...FALLBACK_PATTERNS, ..._runtimePatterns };
}

/**
 * Generate an interface name for a given device model and index.
 */
export function generateInterfaceName(modelId: string, index: number): string {
  const pattern = getPattern(modelId);
  return pattern.pattern.replace('{index}', String(index));
}

/**
 * Parse an interface name to extract its index.
 * Returns null if the interface doesn't match the expected pattern.
 */
export function parseInterfaceIndex(modelId: string, interfaceName: string): number | null {
  const pattern = getPattern(modelId);

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
  const pattern = getPattern(modelId);
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
  const pattern = getPattern(modelId);
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
  const pattern = getPattern(modelId);
  return pattern.managementInterface;
}

/**
 * @deprecated Use getPattern() instead. Maintained for backward compatibility.
 */
export const DEFAULT_PATTERNS = FALLBACK_PATTERNS;
