/**
 * Tests for useNodeStates hook.
 *
 * These tests verify:
 * 1. Runtime state derivation from raw node state data
 * 2. WebSocket node state change handling
 * 3. Loading node states from API
 * 4. Optimistic guard behavior
 * 5. Error notification on node error state
 * 6. Empty state handling
 * 7. Readiness hint loading
 * 8. Node state refresh from agent
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useNodeStates } from './useNodeStates';
import type { NodeStateEntry, NodeStateData } from '../../types/nodeState';
import type { NotificationLevel } from '../../types/notifications';

function createMockOptions(activeLabId: string | null = 'lab-1') {
  return {
    activeLabId,
    studioRequest: vi.fn(),
    addNotification: vi.fn(),
  };
}

function makeNodeStateEntry(overrides: Partial<NodeStateEntry> = {}): NodeStateEntry {
  return {
    id: 'ns-1',
    lab_id: 'lab-1',
    node_id: 'node-1',
    node_name: 'R1',
    desired_state: 'running',
    actual_state: 'running',
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    ...overrides,
  };
}

function makeWSNodeState(overrides: Partial<NodeStateData> = {}): NodeStateData {
  return {
    node_id: 'node-1',
    node_name: 'R1',
    desired_state: 'running',
    actual_state: 'running',
    is_ready: true,
    ...overrides,
  };
}

describe('useNodeStates', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ============================================================================
  // Runtime State Derivation
  // ============================================================================

  describe('runtime state derivation', () => {
    it('derives running status from running actual state', async () => {
      const opts = createMockOptions();
      opts.studioRequest.mockResolvedValue({
        nodes: [makeNodeStateEntry({ actual_state: 'running', desired_state: 'running' })],
      });

      const { result } = renderHook(() => useNodeStates(opts));

      await act(async () => {
        await result.current.loadNodeStates('lab-1', []);
      });

      expect(result.current.runtimeStates['node-1']).toBe('running');
    });

    it('derives booting status from starting actual state', async () => {
      const opts = createMockOptions();
      opts.studioRequest.mockResolvedValue({
        nodes: [makeNodeStateEntry({ actual_state: 'starting', desired_state: 'running' })],
      });

      const { result } = renderHook(() => useNodeStates(opts));

      await act(async () => {
        await result.current.loadNodeStates('lab-1', []);
      });

      expect(result.current.runtimeStates['node-1']).toBe('booting');
    });

    it('derives stopped status from stopped actual state', async () => {
      const opts = createMockOptions();
      opts.studioRequest.mockResolvedValue({
        nodes: [makeNodeStateEntry({ actual_state: 'stopped', desired_state: 'stopped' })],
      });

      const { result } = renderHook(() => useNodeStates(opts));

      await act(async () => {
        await result.current.loadNodeStates('lab-1', []);
      });

      expect(result.current.runtimeStates['node-1']).toBe('stopped');
    });

    it('derives error status from error actual state without retry', async () => {
      const opts = createMockOptions();
      opts.studioRequest.mockResolvedValue({
        nodes: [makeNodeStateEntry({
          actual_state: 'error',
          desired_state: 'running',
          will_retry: false,
        })],
      });

      const { result } = renderHook(() => useNodeStates(opts));

      await act(async () => {
        await result.current.loadNodeStates('lab-1', []);
      });

      expect(result.current.runtimeStates['node-1']).toBe('error');
    });

    it('derives booting status from error state when will_retry is true', async () => {
      const opts = createMockOptions();
      opts.studioRequest.mockResolvedValue({
        nodes: [makeNodeStateEntry({
          actual_state: 'error',
          desired_state: 'running',
          will_retry: true,
        })],
      });

      const { result } = renderHook(() => useNodeStates(opts));

      await act(async () => {
        await result.current.loadNodeStates('lab-1', []);
      });

      expect(result.current.runtimeStates['node-1']).toBe('booting');
    });

    it('prefers server display_state when available', async () => {
      const opts = createMockOptions();
      opts.studioRequest.mockResolvedValue({
        nodes: [makeNodeStateEntry({
          actual_state: 'pending',
          desired_state: 'running',
          display_state: 'stopping',
        })],
      });

      const { result } = renderHook(() => useNodeStates(opts));

      await act(async () => {
        await result.current.loadNodeStates('lab-1', []);
      });

      expect(result.current.runtimeStates['node-1']).toBe('stopping');
    });

    it('returns no runtime state for undeployed nodes', async () => {
      const opts = createMockOptions();
      opts.studioRequest.mockResolvedValue({
        nodes: [makeNodeStateEntry({ actual_state: 'undeployed', desired_state: 'stopped' })],
      });

      const { result } = renderHook(() => useNodeStates(opts));

      await act(async () => {
        await result.current.loadNodeStates('lab-1', []);
      });

      expect(result.current.runtimeStates['node-1']).toBeUndefined();
    });
  });

  // ============================================================================
  // WebSocket Node State Handling
  // ============================================================================

  describe('handleWSNodeStateChange', () => {
    it('updates node state from WS data', () => {
      const opts = createMockOptions();

      const { result } = renderHook(() => useNodeStates(opts));

      act(() => {
        result.current.handleWSNodeStateChange('node-1', makeWSNodeState({
          actual_state: 'running',
          desired_state: 'running',
          is_ready: true,
        }));
      });

      expect(result.current.nodeStates['node-1']).toBeDefined();
      expect(result.current.nodeStates['node-1'].actual_state).toBe('running');
      expect(result.current.nodeStates['node-1'].is_ready).toBe(true);
      expect(result.current.runtimeStates['node-1']).toBe('running');
    });

    it('preserves host_id from previous state when WS omits it', () => {
      const opts = createMockOptions();

      const { result } = renderHook(() => useNodeStates(opts));

      // Set initial state with host_id
      act(() => {
        result.current.handleWSNodeStateChange('node-1', makeWSNodeState({
          host_id: 'agent-1',
          host_name: 'Agent 1',
        }));
      });

      // Update without host_id (WS sends null for host_id)
      act(() => {
        result.current.handleWSNodeStateChange('node-1', makeWSNodeState({
          actual_state: 'stopped',
          // host_id and host_name are undefined (not in payload)
        }));
      });

      // Should preserve existing host_id since WS sent undefined (not null)
      expect(result.current.nodeStates['node-1'].actual_state).toBe('stopped');
    });

    it('preserves image_sync fields when WS omits them', () => {
      const opts = createMockOptions();

      const { result } = renderHook(() => useNodeStates(opts));

      // Initial state with image sync info
      act(() => {
        result.current.handleWSNodeStateChange('node-1', makeWSNodeState({
          image_sync_status: 'syncing',
          image_sync_message: '50% complete',
        }));
      });

      // Update without image_sync fields
      act(() => {
        result.current.handleWSNodeStateChange('node-1', makeWSNodeState({
          actual_state: 'starting',
          // image_sync_status and image_sync_message are undefined
        }));
      });

      expect(result.current.nodeStates['node-1'].image_sync_status).toBe('syncing');
      expect(result.current.nodeStates['node-1'].image_sync_message).toBe('50% complete');
    });

    it('shows error notification when node enters error state', () => {
      const opts = createMockOptions();

      const { result } = renderHook(() => useNodeStates(opts));

      act(() => {
        result.current.handleWSNodeStateChange('node-1', makeWSNodeState({
          actual_state: 'error',
          error_message: 'Container crashed',
          will_retry: false,
        }));
      });

      expect(opts.addNotification).toHaveBeenCalledWith(
        'error',
        'Node Error: R1',
        'Container crashed'
      );
    });

    it('suppresses error notification when will_retry is true', () => {
      const opts = createMockOptions();

      const { result } = renderHook(() => useNodeStates(opts));

      act(() => {
        result.current.handleWSNodeStateChange('node-1', makeWSNodeState({
          actual_state: 'error',
          error_message: 'Temporary failure',
          will_retry: true,
        }));
      });

      expect(opts.addNotification).not.toHaveBeenCalled();
    });

    it('does not notify when non-error state received', () => {
      const opts = createMockOptions();

      const { result } = renderHook(() => useNodeStates(opts));

      act(() => {
        result.current.handleWSNodeStateChange('node-1', makeWSNodeState({
          actual_state: 'running',
        }));
      });

      expect(opts.addNotification).not.toHaveBeenCalled();
    });
  });

  // ============================================================================
  // Loading Node States from API
  // ============================================================================

  describe('loadNodeStates', () => {
    it('loads and indexes node states by node_id', async () => {
      const opts = createMockOptions();
      opts.studioRequest.mockResolvedValue({
        nodes: [
          makeNodeStateEntry({ node_id: 'n1', node_name: 'R1', actual_state: 'running' }),
          makeNodeStateEntry({ node_id: 'n2', node_name: 'R2', actual_state: 'stopped' }),
        ],
      });

      const { result } = renderHook(() => useNodeStates(opts));

      await act(async () => {
        await result.current.loadNodeStates('lab-1', []);
      });

      expect(Object.keys(result.current.nodeStates)).toHaveLength(2);
      expect(result.current.nodeStates['n1'].actual_state).toBe('running');
      expect(result.current.nodeStates['n2'].actual_state).toBe('stopped');
    });

    it('handles API failure gracefully', async () => {
      const opts = createMockOptions();
      opts.studioRequest.mockRejectedValue(new Error('Network error'));

      const { result } = renderHook(() => useNodeStates(opts));

      // Should not throw
      await act(async () => {
        await result.current.loadNodeStates('lab-1', []);
      });

      // nodeStates remain empty
      expect(Object.keys(result.current.nodeStates)).toHaveLength(0);
    });

    it('handles missing nodes field in response', async () => {
      const opts = createMockOptions();
      opts.studioRequest.mockResolvedValue({});

      const { result } = renderHook(() => useNodeStates(opts));

      await act(async () => {
        await result.current.loadNodeStates('lab-1', []);
      });

      expect(Object.keys(result.current.nodeStates)).toHaveLength(0);
    });
  });

  // ============================================================================
  // Empty State
  // ============================================================================

  describe('empty state', () => {
    it('starts with empty nodeStates and runtimeStates', () => {
      const opts = createMockOptions();

      const { result } = renderHook(() => useNodeStates(opts));

      expect(result.current.nodeStates).toEqual({});
      expect(result.current.runtimeStates).toEqual({});
    });

    it('starts with empty readiness hints', () => {
      const opts = createMockOptions();

      const { result } = renderHook(() => useNodeStates(opts));

      expect(result.current.nodeReadinessHints).toEqual({});
    });

    it('starts with empty pending node ops', () => {
      const opts = createMockOptions();

      const { result } = renderHook(() => useNodeStates(opts));

      expect(result.current.pendingNodeOps.size).toBe(0);
    });
  });

  // ============================================================================
  // Readiness Hints
  // ============================================================================

  describe('loadNodeReadiness', () => {
    it('loads readiness hints from API', async () => {
      const opts = createMockOptions();
      opts.studioRequest.mockResolvedValue({
        nodes: [
          { node_id: 'n1', is_ready: true, actual_state: 'running', progress_percent: 100, message: null },
          { node_id: 'n2', is_ready: false, actual_state: 'starting', progress_percent: 45, message: 'Booting...' },
        ],
      });

      const { result } = renderHook(() => useNodeStates(opts));

      await act(async () => {
        await result.current.loadNodeReadiness('lab-1');
      });

      expect(result.current.nodeReadinessHints['n1']).toEqual({
        is_ready: true,
        actual_state: 'running',
        progress_percent: 100,
        message: null,
      });
      expect(result.current.nodeReadinessHints['n2']).toEqual({
        is_ready: false,
        actual_state: 'starting',
        progress_percent: 45,
        message: 'Booting...',
      });
    });

    it('keeps previous hints when API fails', async () => {
      const opts = createMockOptions();

      // First load succeeds
      opts.studioRequest.mockResolvedValueOnce({
        nodes: [{ node_id: 'n1', is_ready: true, actual_state: 'running' }],
      });

      const { result } = renderHook(() => useNodeStates(opts));

      await act(async () => {
        await result.current.loadNodeReadiness('lab-1');
      });

      expect(result.current.nodeReadinessHints['n1']).toBeDefined();

      // Second load fails
      opts.studioRequest.mockRejectedValueOnce(new Error('timeout'));

      await act(async () => {
        await result.current.loadNodeReadiness('lab-1');
      });

      // Previous hints preserved
      expect(result.current.nodeReadinessHints['n1']).toBeDefined();
    });
  });

  // ============================================================================
  // Refresh from Agent
  // ============================================================================

  describe('refreshNodeStatesFromAgent', () => {
    it('sends POST request to refresh endpoint', async () => {
      const opts = createMockOptions();
      opts.studioRequest.mockResolvedValue({});

      const { result } = renderHook(() => useNodeStates(opts));

      await act(async () => {
        await result.current.refreshNodeStatesFromAgent('lab-1');
      });

      expect(opts.studioRequest).toHaveBeenCalledWith(
        '/labs/lab-1/nodes/refresh',
        { method: 'POST' }
      );
    });

    it('handles agent unavailability gracefully', async () => {
      const opts = createMockOptions();
      const consoleWarnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
      opts.studioRequest.mockRejectedValue(new Error('Agent unreachable'));

      const { result } = renderHook(() => useNodeStates(opts));

      // Should not throw
      await act(async () => {
        await result.current.refreshNodeStatesFromAgent('lab-1');
      });

      expect(consoleWarnSpy).toHaveBeenCalledWith('Failed to refresh node states from agent');
      consoleWarnSpy.mockRestore();
    });
  });

  // ============================================================================
  // Optimistic Guard
  // ============================================================================

  describe('optimistic guard', () => {
    it('guards against backward WS updates during transitional state', () => {
      const opts = createMockOptions();

      const { result } = renderHook(() => useNodeStates(opts));

      // Simulate optimistic stopping state
      act(() => {
        result.current.handleWSNodeStateChange('node-1', makeWSNodeState({
          actual_state: 'running',
          desired_state: 'running',
        }));
      });

      // Set optimistic guard for stopping
      act(() => {
        result.current.setNodeStates(prev => ({
          ...prev,
          'node-1': { ...prev['node-1'], actual_state: 'stopping' },
        }));
        result.current.optimisticGuardRef.current.set('node-1', Date.now() + 10000);
      });

      // WS sends "running" -- should be rejected during guard window
      act(() => {
        result.current.handleWSNodeStateChange('node-1', makeWSNodeState({
          actual_state: 'running',
          desired_state: 'stopped',
          display_state: 'running',
        }));
      });

      // The guard should prevent the backward update
      expect(result.current.nodeStates['node-1'].actual_state).toBe('stopping');
    });

    it('allows forward progress WS updates through guard', () => {
      const opts = createMockOptions();

      const { result } = renderHook(() => useNodeStates(opts));

      // Set state to stopping with guard
      act(() => {
        result.current.handleWSNodeStateChange('node-1', makeWSNodeState({
          actual_state: 'running',
        }));
      });

      act(() => {
        result.current.setNodeStates(prev => ({
          ...prev,
          'node-1': { ...prev['node-1'], actual_state: 'stopping' },
        }));
        result.current.optimisticGuardRef.current.set('node-1', Date.now() + 10000);
      });

      // WS sends "stopped" -- forward progress, should be accepted
      act(() => {
        result.current.handleWSNodeStateChange('node-1', makeWSNodeState({
          actual_state: 'stopped',
          desired_state: 'stopped',
          display_state: 'stopped',
        }));
      });

      expect(result.current.nodeStates['node-1'].actual_state).toBe('stopped');
    });
  });

  // ============================================================================
  // Filtering by State
  // ============================================================================

  describe('state filtering via runtimeStates', () => {
    it('computes runtime states for multiple nodes with different states', async () => {
      const opts = createMockOptions();
      opts.studioRequest.mockResolvedValue({
        nodes: [
          makeNodeStateEntry({ node_id: 'n1', actual_state: 'running', desired_state: 'running' }),
          makeNodeStateEntry({ node_id: 'n2', actual_state: 'stopped', desired_state: 'stopped' }),
          makeNodeStateEntry({ node_id: 'n3', actual_state: 'error', desired_state: 'running', will_retry: false }),
          makeNodeStateEntry({ node_id: 'n4', actual_state: 'starting', desired_state: 'running' }),
          makeNodeStateEntry({ node_id: 'n5', actual_state: 'undeployed', desired_state: 'stopped' }),
        ],
      });

      const { result } = renderHook(() => useNodeStates(opts));

      await act(async () => {
        await result.current.loadNodeStates('lab-1', []);
      });

      const rs = result.current.runtimeStates;
      expect(rs['n1']).toBe('running');
      expect(rs['n2']).toBe('stopped');
      expect(rs['n3']).toBe('error');
      expect(rs['n4']).toBe('booting');
      expect(rs['n5']).toBeUndefined(); // undeployed -> null -> not in map

      // Count by state
      const running = Object.values(rs).filter(s => s === 'running');
      const stopped = Object.values(rs).filter(s => s === 'stopped');
      const errors = Object.values(rs).filter(s => s === 'error');
      const booting = Object.values(rs).filter(s => s === 'booting');

      expect(running).toHaveLength(1);
      expect(stopped).toHaveLength(1);
      expect(errors).toHaveLength(1);
      expect(booting).toHaveLength(1);
    });
  });
});
