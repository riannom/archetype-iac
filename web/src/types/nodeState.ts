/**
 * Canonical type definitions for node state management.
 *
 * This is the single source of truth for all node state types used across
 * the frontend. All components should import from here instead of defining
 * local duplicate interfaces.
 */

/** The 8 possible actual states a node can be in. */
export type NodeActualState =
  | 'undeployed'
  | 'pending'
  | 'starting'
  | 'running'
  | 'stopped'
  | 'stopping'
  | 'error'
  | 'exited';

/** The desired state for a node. */
export type NodeDesiredState = 'stopped' | 'running';

/** Display-level runtime status shown in the UI. */
export type NodeRuntimeStatus = 'stopped' | 'booting' | 'running' | 'stopping' | 'error';

/** Full node state entry from the REST API (includes all fields). */
export interface NodeStateEntry {
  id: string;
  lab_id: string;
  node_id: string;
  node_name: string;
  desired_state: NodeDesiredState;
  actual_state: NodeActualState | string;
  error_message?: string | null;
  is_ready?: boolean;
  boot_started_at?: string | null;
  image_sync_status?: string | null;
  image_sync_message?: string | null;
  host_id?: string | null;
  host_name?: string | null;
  management_ip?: string | null;
  all_ips?: string[];
  will_retry?: boolean;
  /** Server-computed display state: running, starting, stopping, stopped, error */
  display_state?: string;
  created_at: string;
  updated_at: string;
}

/** WebSocket message shape for node state updates (subset of NodeStateEntry). */
export interface NodeStateData {
  node_id: string;
  node_name: string;
  desired_state: NodeDesiredState;
  actual_state: NodeActualState | string;
  is_ready: boolean;
  error_message?: string | null;
  host_id?: string | null;
  host_name?: string | null;
  image_sync_status?: string | null;
  image_sync_message?: string | null;
  will_retry?: boolean;
  /** Server-computed display state: running, starting, stopping, stopped, error */
  display_state?: string;
}

/** Map server display_state to frontend NodeRuntimeStatus. */
const DISPLAY_STATE_MAP: Record<string, NodeRuntimeStatus | null> = {
  running: 'running',
  starting: 'booting',
  stopping: 'stopping',
  stopped: 'stopped',
  error: 'error',
};

/**
 * Map an actual_state + desired_state to a display-level runtime status.
 *
 * Prefers server-computed display_state when available. Falls back to
 * client-side mapping for backward compatibility.
 *
 * Returns null for states that should show no indicator (e.g. 'undeployed').
 */
export function mapActualToRuntime(
  actualState: string,
  desiredState?: string,
  willRetry?: boolean,
  displayState?: string,
): NodeRuntimeStatus | null {
  // Prefer server-computed display_state
  if (displayState) {
    if (displayState === 'error' && willRetry) return 'booting';
    return DISPLAY_STATE_MAP[displayState] ?? null;
  }

  // Fallback to client-side mapping
  switch (actualState) {
    case 'running':
      return 'running';
    case 'stopping':
      return 'stopping';
    case 'starting':
      return 'booting';
    case 'pending':
      return desiredState === 'running' ? 'booting' : 'stopped';
    case 'error':
      return willRetry ? 'booting' : 'error';
    case 'stopped':
    case 'exited':
      return 'stopped';
    case 'undeployed':
      return null;
    default:
      return null;
  }
}
