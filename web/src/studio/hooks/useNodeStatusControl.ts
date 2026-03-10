import { useCallback } from 'react';
import { RuntimeStatus } from './useNodeStates';
import { Node } from '../types';
import { NodeStateEntry } from '../../types/nodeState';

interface UseNodeStatusControlOptions {
  activeLab: { id: string } | null;
  nodes: Node[];
  pendingNodeOps: Set<string>;
  setPendingNodeOps: React.Dispatch<React.SetStateAction<Set<string>>>;
  setNodeStates: React.Dispatch<React.SetStateAction<Record<string, NodeStateEntry>>>;
  optimisticGuardRef: React.MutableRefObject<Map<string, number>>;
  studioRequest: <T>(path: string, options?: RequestInit) => Promise<T>;
  addTaskLogEntry: (level: 'info' | 'success' | 'warning' | 'error', message: string) => void;
  loadJobs: (labId: string, nodes: Node[]) => void;
  flushTopologySave: () => Promise<void>;
}

export function useNodeStatusControl({
  activeLab,
  nodes,
  pendingNodeOps,
  setPendingNodeOps,
  setNodeStates,
  optimisticGuardRef,
  studioRequest,
  addTaskLogEntry,
  loadJobs,
  flushTopologySave,
}: UseNodeStatusControlOptions) {
  const handleUpdateStatus = useCallback(async (nodeId: string, status: RuntimeStatus): Promise<void> => {
    if (!activeLab) return;

    // Block if operation already pending for this node
    if (pendingNodeOps.has(nodeId)) {
      addTaskLogEntry('info', 'Operation already in progress for this node');
      return;
    }

    const node = nodes.find((n) => n.id === nodeId);
    if (!node) return;
    const nodeName = node.name;

    // Map RuntimeStatus to desired state
    const desiredState = status === 'stopped' ? 'stopped' : 'running';
    const action = desiredState === 'running' ? 'start' : 'stop';

    // Mark operation as pending
    setPendingNodeOps((prev) => new Set(prev).add(nodeId));
    addTaskLogEntry('info', `Setting "${nodeName}" to ${desiredState}...`);

    try {
      if (desiredState === 'running') {
        await flushTopologySave();
      }

      // Optimistically update nodeStates -- runtimeStates derived automatically
      const transitionalState = status === 'stopped' ? 'stopping' : 'starting';
      optimisticGuardRef.current.set(nodeId, Date.now() + 5000);
      setNodeStates((prev) => ({
        ...prev,
        [nodeId]: {
          ...prev[nodeId],
          actual_state: transitionalState,
          desired_state: desiredState as 'stopped' | 'running',
          display_state: transitionalState,
        },
      }));

      // Set desired state - this now auto-triggers sync
      await studioRequest(`/labs/${activeLab.id}/nodes/${encodeURIComponent(nodeId)}/desired-state`, {
        method: 'PUT',
        body: JSON.stringify({ state: desiredState }),
      });

      addTaskLogEntry('success', `${action === 'start' ? 'Starting' : 'Stopping'} "${nodeName}"...`);
      loadJobs(activeLab.id, nodes);
    } catch (error) {
      let message = error instanceof Error ? error.message : 'Action failed';

      // Handle specific HTTP error codes with user-friendly messages
      if (error instanceof Error) {
        // Check for 409 Conflict (operation already in progress)
        if (message.includes('409') || message.toLowerCase().includes('already in progress') || message.toLowerCase().includes('conflict')) {
          message = 'Another operation is already in progress for this lab';
          addTaskLogEntry('warning', `Cannot ${action} "${nodeName}": ${message}`);
          return;
        }
        // Check for 503 Service Unavailable (agent busy / lock timeout)
        if (message.includes('503') || message.toLowerCase().includes('try again later')) {
          message = 'Service temporarily unavailable, please try again';
          addTaskLogEntry('warning', `Cannot ${action} "${nodeName}": ${message}`);
          return;
        }
      }

      console.error('Node action failed:', error);
      setNodeStates((prev) => ({
        ...prev,
        [nodeId]: { ...prev[nodeId], actual_state: 'error', error_message: message },
      }));
      addTaskLogEntry('error', `Node ${action} failed for "${nodeName}": ${message}`);
    } finally {
      // Clear pending operation
      setPendingNodeOps((prev) => {
        const next = new Set(prev);
        next.delete(nodeId);
        return next;
      });
    }
  }, [activeLab, nodes, pendingNodeOps, studioRequest, addTaskLogEntry, loadJobs, flushTopologySave, setPendingNodeOps, setNodeStates, optimisticGuardRef]);

  return { handleUpdateStatus };
}
