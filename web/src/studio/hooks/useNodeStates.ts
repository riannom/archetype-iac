import { useCallback, useMemo, useRef, useState } from 'react';
import { NodeStateEntry, NodeStateData, NodeRuntimeStatus, mapActualToRuntime } from '../../types/nodeState';
import type { Notification, NotificationLevel } from '../../types/notifications';

// RuntimeStatus is an alias for NodeRuntimeStatus for backward compatibility
export type RuntimeStatus = NodeRuntimeStatus;

interface NodeReadinessHint {
  is_ready: boolean;
  actual_state: string;
  progress_percent?: number | null;
  message?: string | null;
}

interface UseNodeStatesOptions {
  activeLabId: string | null;
  studioRequest: <T>(path: string, options?: RequestInit) => Promise<T>;
  addNotification: (level: NotificationLevel, title: string, message?: string, options?: Partial<Notification>) => void;
}

export function useNodeStates({
  activeLabId,
  studioRequest,
  addNotification,
}: UseNodeStatesOptions) {
  const [nodeStates, setNodeStates] = useState<Record<string, NodeStateEntry>>({});
  const [nodeReadinessHints, setNodeReadinessHints] = useState<Record<string, NodeReadinessHint>>({});
  // Track pending node operations to prevent race conditions from rapid clicks
  const [pendingNodeOps, setPendingNodeOps] = useState<Set<string>>(new Set());
  // Tracks optimistic updates: nodeId -> expiry timestamp. Prevents polling/WS from
  // overwriting transitional states (stopping/starting) before the server catches up.
  const optimisticGuardRef = useRef<Map<string, number>>(new Map());

  // Derive runtimeStates from nodeStates -- single source of truth
  const runtimeStates = useMemo(() => {
    const result: Record<string, RuntimeStatus> = {};
    for (const [nodeId, state] of Object.entries(nodeStates)) {
      const status = mapActualToRuntime(state.actual_state, state.desired_state, state.will_retry, state.display_state);
      if (status !== null) result[nodeId] = status;
    }
    return result;
  }, [nodeStates]);

  // WebSocket handler for node state changes
  // Updates nodeStates; runtimeStates are derived automatically via useMemo
  const handleWSNodeStateChange = useCallback((nodeId: string, wsState: NodeStateData) => {
    // Update nodeStates record -- runtimeStates derived automatically
    setNodeStates((prev) => {
      const guardExpiry = optimisticGuardRef.current.get(nodeId);
      const isGuarded = guardExpiry && Date.now() < guardExpiry;
      const localState = prev[nodeId];

      // During optimistic guard window: only accept WS updates that show
      // equal or forward progress (e.g., stopping->stopped is OK, stopping->running is not)
      if (isGuarded && localState) {
        const localActual = localState.actual_state;
        const wsDisplay = wsState.display_state;
        // If local state is transitional and WS shows a non-matching transitional or
        // the wrong terminal state, preserve the local optimistic state
        if (localActual === 'stopping' && wsDisplay && wsDisplay !== 'stopping' && wsDisplay !== 'stopped' && wsDisplay !== 'error') {
          return prev;
        }
        if (localActual === 'starting' && wsDisplay && wsDisplay !== 'starting' && wsDisplay !== 'running' && wsDisplay !== 'error') {
          return prev;
        }
        // Forward progress -- clear the guard since server has caught up
        optimisticGuardRef.current.delete(nodeId);
      }

      return {
        ...prev,
        [nodeId]: {
          ...prev[nodeId],
          id: wsState.node_id,
          lab_id: activeLabId || '',
          node_id: wsState.node_id,
          node_name: wsState.node_name,
          desired_state: wsState.desired_state,
          actual_state: wsState.actual_state,
          error_message: wsState.error_message,
          is_ready: wsState.is_ready,
          host_id: wsState.host_id ?? prev[nodeId]?.host_id,
          host_name: wsState.host_name ?? prev[nodeId]?.host_name,
          // Preserve image-sync fields when WS payload omits them.
          // Clear only when server explicitly sends null.
          image_sync_status:
            wsState.image_sync_status !== undefined
              ? wsState.image_sync_status
              : prev[nodeId]?.image_sync_status,
          image_sync_message:
            wsState.image_sync_message !== undefined
              ? wsState.image_sync_message
              : prev[nodeId]?.image_sync_message,
          will_retry: wsState.will_retry,
          enforcement_attempts: wsState.enforcement_attempts,
          max_enforcement_attempts: wsState.max_enforcement_attempts,
          display_state: wsState.display_state,
          starting_started_at: wsState.starting_started_at,
          created_at: prev[nodeId]?.created_at || new Date().toISOString(),
          updated_at: new Date().toISOString(),
        } as NodeStateEntry,
      };
    });

    // Show toast for nodes entering error state (suppress if will_retry)
    if (wsState.actual_state === 'error' && wsState.error_message && !wsState.will_retry) {
      addNotification(
        'error',
        `Node Error: ${wsState.node_name}`,
        wsState.error_message
      );
    }
  }, [activeLabId, addNotification]);

  // Load node states from the backend (per-node desired/actual state)
  // runtimeStates are derived automatically via useMemo
  const loadNodeStates = useCallback(async (labId: string, _currentNodes: unknown[]) => {
    try {
      const data = await studioRequest<{ nodes: NodeStateEntry[] }>(`/labs/${labId}/nodes/states`);
      const statesByNodeId: Record<string, NodeStateEntry> = {};

      (data.nodes || []).forEach((state) => {
        statesByNodeId[state.node_id] = state;
      });

      // Merge with existing state, preserving optimistic transitional states
      // that the server hasn't caught up with yet
      setNodeStates(prev => {
        const now = Date.now();
        const merged = { ...statesByNodeId };
        for (const [id, guardExpiry] of optimisticGuardRef.current.entries()) {
          if (now < guardExpiry && prev[id] && merged[id]) {
            const localActual = prev[id].actual_state;
            const serverDisplay = merged[id].display_state;
            // Keep local optimistic state if server hasn't reflected the transition yet
            if (localActual === 'stopping' && serverDisplay !== 'stopping' && serverDisplay !== 'stopped' && serverDisplay !== 'error') {
              merged[id] = { ...merged[id], actual_state: 'stopping', display_state: 'stopping' };
            } else if (localActual === 'starting' && serverDisplay !== 'starting' && serverDisplay !== 'running' && serverDisplay !== 'error') {
              merged[id] = { ...merged[id], actual_state: 'starting', display_state: 'starting' };
            } else {
              // Server caught up -- clear the guard
              optimisticGuardRef.current.delete(id);
            }
          }
        }
        return merged;
      });
    } catch {
      // Node states endpoint may fail for new labs - use job-based fallback
    }
  }, [studioRequest]);

  const loadNodeReadiness = useCallback(async (labId: string) => {
    try {
      const data = await studioRequest<{ nodes: Array<NodeReadinessHint & { node_id: string }> }>(
        `/labs/${labId}/nodes/ready`
      );
      const hints: Record<string, NodeReadinessHint> = {};
      for (const node of data.nodes || []) {
        hints[node.node_id] = {
          is_ready: !!node.is_ready,
          actual_state: node.actual_state,
          progress_percent: node.progress_percent,
          message: node.message,
        };
      }
      setNodeReadinessHints(hints);
    } catch {
      // Keep previous hints when readiness endpoint is unavailable
    }
  }, [studioRequest]);

  // Refresh node states from the agent (queries actual container status)
  // This is called once when entering a lab to ensure states are fresh
  const refreshNodeStatesFromAgent = useCallback(async (labId: string) => {
    try {
      await studioRequest(`/labs/${labId}/nodes/refresh`, { method: 'POST' });
    } catch {
      // Agent may be unavailable - states will still load from DB
      console.warn('Failed to refresh node states from agent');
    }
  }, [studioRequest]);

  return {
    nodeStates,
    setNodeStates,
    runtimeStates,
    nodeReadinessHints,
    pendingNodeOps,
    setPendingNodeOps,
    optimisticGuardRef,
    handleWSNodeStateChange,
    loadNodeStates,
    loadNodeReadiness,
    refreshNodeStatesFromAgent,
  };
}
