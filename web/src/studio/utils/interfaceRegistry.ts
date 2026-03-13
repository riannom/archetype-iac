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

interface InterfacePattern {
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
 * Runtime pattern registry populated from device models.
 * This is updated when device models are loaded from the API.
 */
let _runtimePatterns: Record<string, InterfacePattern> = {};
let _runtimeAliases: Record<string, string> = {};

function normalizeToken(value: string | null | undefined): string {
  return (value || '').trim().toLowerCase();
}

function registerAlias(
  bucket: Map<string, Set<string>>,
  alias: string | null | undefined,
  canonical: string | null | undefined
): void {
  const normalizedAlias = normalizeToken(alias);
  const normalizedCanonical = normalizeToken(canonical);
  if (!normalizedAlias || !normalizedCanonical) return;
  const current = bucket.get(normalizedAlias) || new Set<string>();
  current.add(normalizedCanonical);
  bucket.set(normalizedAlias, current);
}

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

  // Determine management interface — prefer API-provided field, fall back for compat
  let managementInterface = model.managementInterface || 'eth0';
  if (!model.managementInterface) {
    if (model.kind === 'ceos' || model.id === 'ceos') {
      managementInterface = 'Management0';
    } else if (model.kind === 'nokia_srlinux' || model.id === 'nokia_srlinux' || model.id === 'srl') {
      managementInterface = 'mgmt0';
    } else if (model.kind === 'cisco_xrd' || model.id === 'cisco_xrd' || model.id === 'xrd') {
      managementInterface = 'MgmtEth0/RP0/CPU0/0';
    }
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
  const aliasBucket = new Map<string, Set<string>>();

  for (const model of models) {
    const pattern = buildPatternFromModel(model);
    const canonicalId = normalizeToken(model.id);
    if (!canonicalId) continue;

    // Register by model ID
    patterns[canonicalId] = pattern;
    registerAlias(aliasBucket, canonicalId, canonicalId);

    // Also register by kind if different from id
    if (model.kind && model.kind !== model.id) {
      patterns[normalizeToken(model.kind)] = pattern;
      registerAlias(aliasBucket, model.kind, canonicalId);
    }
    (model.compatibilityAliases || []).forEach((alias) => registerAlias(aliasBucket, alias, canonicalId));
  }

  _runtimePatterns = patterns;

  const aliases: Record<string, string> = {};
  aliasBucket.forEach((canonicals, alias) => {
    if (canonicals.size === 1) {
      aliases[alias] = Array.from(canonicals)[0];
    }
  });
  _runtimeAliases = aliases;
}

export function setRuntimeAliases(aliasMap: Record<string, string>): void {
  const aliases: Record<string, string> = {};
  Object.entries(aliasMap || {}).forEach(([alias, canonical]) => {
    const normalizedAlias = normalizeToken(alias);
    const normalizedCanonical = normalizeToken(canonical);
    if (!normalizedAlias || !normalizedCanonical) return;
    aliases[normalizedAlias] = normalizedCanonical;
  });
  _runtimeAliases = {
    ..._runtimeAliases,
    ...aliases,
  };
}

/**
 * Resolve a device ID to its canonical form using aliases.
 */
function resolveAlias(modelId: string): string {
  const normalized = normalizeToken(modelId);
  return _runtimeAliases[normalized] || normalized;
}

/**
 * Get the pattern for a device model.
 * First checks runtime patterns (from API), then falls back to defaults.
 * Also resolves server-provided alias mappings (e.g., "eos" -> "ceos").
 */
function getPattern(modelId: string): InterfacePattern {
  const normalizedId = normalizeToken(modelId);

  // Check runtime patterns first (populated from /vendors API)
  if (_runtimePatterns[modelId]) {
    return _runtimePatterns[modelId];
  }
  if (_runtimePatterns[normalizedId]) {
    return _runtimePatterns[normalizedId];
  }

  // Try resolving alias and check again
  const canonicalId = resolveAlias(normalizedId);
  if (canonicalId !== modelId && _runtimePatterns[canonicalId]) {
    return _runtimePatterns[canonicalId];
  }

  // Check fallback patterns
  if (FALLBACK_PATTERNS[modelId]) {
    return FALLBACK_PATTERNS[modelId];
  }
  if (FALLBACK_PATTERNS[normalizedId]) {
    return FALLBACK_PATTERNS[normalizedId];
  }

  // Return generic fallback
  return FALLBACK_PATTERNS.generic;
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

  // Data interfaces first
  for (let i = pattern.startIndex; i < maxIndex && available.length < count; i++) {
    const ifName = pattern.pattern.replace('{index}', String(i));
    if (!usedInterfaces.has(ifName)) {
      available.push(ifName);
    }
  }

  // Management interface last (opt-in wiring)
  if (
    pattern.managementInterface &&
    !usedInterfaces.has(pattern.managementInterface) &&
    !available.includes(pattern.managementInterface) &&
    available.length < count
  ) {
    available.push(pattern.managementInterface);
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

