/**
 * Unified status and resource color utilities for the Archetype frontend.
 * Consolidates duplicated status color logic across components.
 */

export type ResourceLevel = 'normal' | 'warning' | 'danger';

export interface ResourceThresholds {
  warning: number;
  danger: number;
}

/**
 * Default thresholds for different resource types.
 */
export const defaultThresholds = {
  cpu: { warning: 60, danger: 80 },
  memory: { warning: 70, danger: 85 },
  storage: { warning: 75, danger: 90 },
} as const;

/**
 * Determine resource level based on percentage and thresholds.
 * @param percent - Current usage percentage (0-100)
 * @param thresholds - Warning and danger thresholds
 * @returns Resource level: 'normal', 'warning', or 'danger'
 */
export function getResourceLevel(
  percent: number,
  thresholds: ResourceThresholds = defaultThresholds.cpu
): ResourceLevel {
  if (percent >= thresholds.danger) return 'danger';
  if (percent >= thresholds.warning) return 'warning';
  return 'normal';
}

/**
 * Get Tailwind background color class for a resource level.
 * @param level - Resource level
 * @returns Tailwind class like 'bg-green-500', 'bg-amber-500', or 'bg-red-500'
 */
export function getResourceBgColor(level: ResourceLevel): string {
  switch (level) {
    case 'danger': return 'bg-red-500';
    case 'warning': return 'bg-amber-500';
    case 'normal': return 'bg-green-500';
  }
}

/**
 * Get CPU-specific color based on percentage.
 * Uses sage-500 for normal (brand color), amber for warning, red for danger.
 * @param percent - CPU usage percentage
 * @returns Tailwind background class
 */
export function getCpuColor(percent: number): string {
  if (percent >= defaultThresholds.cpu.danger) return 'bg-red-500';
  if (percent >= defaultThresholds.cpu.warning) return 'bg-amber-500';
  return 'bg-sage-500';
}

/**
 * Get memory-specific color based on percentage.
 * Uses blue-500 for normal, amber for warning, red for danger.
 * @param percent - Memory usage percentage
 * @returns Tailwind background class
 */
export function getMemoryColor(percent: number): string {
  if (percent >= defaultThresholds.memory.danger) return 'bg-red-500';
  if (percent >= defaultThresholds.memory.warning) return 'bg-amber-500';
  return 'bg-blue-500';
}

/**
 * Get storage-specific color based on percentage.
 * Uses violet-500 for normal, amber for warning, red for danger.
 * @param percent - Storage usage percentage
 * @returns Tailwind background class
 */
export function getStorageColor(percent: number): string {
  if (percent >= defaultThresholds.storage.danger) return 'bg-red-500';
  if (percent >= defaultThresholds.storage.warning) return 'bg-amber-500';
  return 'bg-violet-500';
}

export type RuntimeStatus = 'running' | 'stopped' | 'pending' | 'error' | 'partial' | 'unknown';

/**
 * Get color classes for runtime status display.
 * Returns a combination of background, text, and border colors for badges.
 * @param status - Runtime status
 * @returns Tailwind class string for status badge
 */
export function getRuntimeStatusColor(status: RuntimeStatus): string {
  switch (status) {
    case 'running':
      return 'bg-green-100 dark:bg-green-900/30 text-green-600 dark:text-green-400 border-green-200 dark:border-green-700';
    case 'stopped':
      return 'bg-stone-100 dark:bg-stone-800 text-stone-500 dark:text-stone-400 border-stone-200 dark:border-stone-700';
    case 'pending':
      return 'bg-amber-100 dark:bg-amber-900/30 text-amber-600 dark:text-amber-400 border-amber-200 dark:border-amber-700';
    case 'error':
      return 'bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400 border-red-200 dark:border-red-700';
    case 'partial':
      return 'bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-700';
    default:
      return 'bg-stone-100 dark:bg-stone-800 text-stone-500 dark:text-stone-400 border-stone-200 dark:border-stone-700';
  }
}

export type ConnectionStatus = 'online' | 'offline' | 'degraded' | 'connecting';

/**
 * Get background color for connection status indicators.
 * @param status - Connection status
 * @returns Tailwind background class
 */
export function getConnectionStatusColor(status: ConnectionStatus): string {
  switch (status) {
    case 'online': return 'bg-green-500';
    case 'degraded': return 'bg-amber-500';
    case 'connecting': return 'bg-blue-500';
    case 'offline': return 'bg-red-500';
    default: return 'bg-stone-500';
  }
}

/**
 * Get text label for connection status.
 * @param status - Connection status
 * @returns Human-readable status label
 */
export function getConnectionStatusText(status: ConnectionStatus): string {
  switch (status) {
    case 'online': return 'Online';
    case 'degraded': return 'Degraded';
    case 'connecting': return 'Connecting';
    case 'offline': return 'Offline';
    default: return 'Unknown';
  }
}

export type RoleBadgeType = 'agent' | 'controller' | 'agent+controller';

/**
 * Get color classes for role badges.
 * @param role - Role type
 * @returns Tailwind class string for role badge styling
 */
export function getRoleBadgeColor(role: RoleBadgeType): string {
  switch (role) {
    case 'controller':
      return 'bg-purple-100 dark:bg-purple-900/30 text-purple-600 dark:text-purple-400 border-purple-200 dark:border-purple-700';
    case 'agent+controller':
      return 'bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-700';
    case 'agent':
    default:
      return 'bg-sage-100 dark:bg-sage-800 text-sage-600 dark:text-sage-400 border-sage-200 dark:border-sage-700';
  }
}

/**
 * Get human-readable label for role.
 * @param role - Role type
 * @returns Human-readable role label
 */
export function getRoleLabel(role: RoleBadgeType): string {
  switch (role) {
    case 'controller': return 'Controller';
    case 'agent+controller': return 'Agent + Controller';
    case 'agent':
    default: return 'Agent';
  }
}
