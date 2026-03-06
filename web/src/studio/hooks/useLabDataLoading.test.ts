/**
 * Tests for useLabDataLoading hook.
 *
 * These tests verify:
 * 1. Initial load: fetches labs, agents, system metrics
 * 2. Polling behavior (10-second interval)
 * 3. Agent filtering (online vs offline)
 * 4. Error handling when API calls fail
 * 5. Refetch triggers
 * 6. Empty state (no labs, no agents)
 * 7. Loading state transitions
 * 8. Lab status loading
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useLabDataLoading } from './useLabDataLoading';
import type { LabSummary, SystemMetrics } from './useLabDataLoading';

// ============================================================================
// Helpers
// ============================================================================

function makeLab(overrides: Partial<LabSummary> = {}): LabSummary {
  return {
    id: 'lab-1',
    name: 'Test Lab',
    created_at: '2026-01-01T00:00:00Z',
    node_count: 3,
    running_count: 2,
    container_count: 2,
    vm_count: 1,
    ...overrides,
  };
}

function makeMetrics(overrides: Partial<SystemMetrics> = {}): SystemMetrics {
  return {
    agents: { online: 1, total: 1 },
    containers: { running: 2, total: 3 },
    cpu_percent: 25.0,
    memory_percent: 40.0,
    labs_running: 1,
    labs_total: 2,
    ...overrides,
  };
}

function createMockStudioRequest() {
  return vi.fn();
}

/** Flush all pending microtasks so async effects resolve under fake timers. */
async function flushEffects() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

// ============================================================================
// Tests
// ============================================================================

describe('useLabDataLoading', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // ── Initial State ──

  it('starts with empty labs, agents, labStatuses, and null metrics', () => {
    const studioRequest = createMockStudioRequest();
    // Never resolve to avoid effects firing
    studioRequest.mockReturnValue(new Promise(() => {}));

    const { result } = renderHook(() =>
      useLabDataLoading({ studioRequest, activeLab: null })
    );

    expect(result.current.labs).toEqual([]);
    expect(result.current.agents).toEqual([]);
    expect(result.current.labStatuses).toEqual({});
    expect(result.current.systemMetrics).toBeNull();
  });

  // ── Initial Load ──

  describe('initial load', () => {
    it('fetches labs, agents, and system metrics on mount', async () => {
      const studioRequest = createMockStudioRequest();
      const labs = [makeLab({ id: 'lab-1' }), makeLab({ id: 'lab-2', name: 'Lab Two' })];
      studioRequest.mockImplementation((path: string) => {
        if (path === '/labs') return Promise.resolve({ labs });
        if (path === '/dashboard/metrics') return Promise.resolve(makeMetrics());
        if (path === '/agents') return Promise.resolve([
          { id: 'a1', name: 'Agent 1', address: '10.0.0.1:8001', status: 'online' },
        ]);
        return Promise.resolve({ nodes: [] });
      });

      const { result } = renderHook(() =>
        useLabDataLoading({ studioRequest, activeLab: null })
      );

      // Flush initial mount effects and lab status cascade
      await flushEffects();
      await flushEffects();

      expect(result.current.labs).toHaveLength(2);
      expect(studioRequest).toHaveBeenCalledWith('/labs');
      expect(studioRequest).toHaveBeenCalledWith('/dashboard/metrics');
      expect(studioRequest).toHaveBeenCalledWith('/agents');
    });

    it('sets labs from API response', async () => {
      const studioRequest = createMockStudioRequest();
      studioRequest.mockImplementation((path: string) => {
        if (path === '/labs') return Promise.resolve({ labs: [makeLab({ id: 'lab-a', name: 'Alpha' })] });
        return Promise.resolve({});
      });

      const { result } = renderHook(() =>
        useLabDataLoading({ studioRequest, activeLab: null })
      );

      await flushEffects();

      expect(result.current.labs).toHaveLength(1);
      expect(result.current.labs[0].name).toBe('Alpha');
    });

    it('handles empty labs array from API', async () => {
      const studioRequest = createMockStudioRequest();
      studioRequest.mockImplementation((path: string) => {
        if (path === '/labs') return Promise.resolve({ labs: [] });
        return Promise.resolve({});
      });

      const { result } = renderHook(() =>
        useLabDataLoading({ studioRequest, activeLab: null })
      );

      await flushEffects();

      expect(studioRequest).toHaveBeenCalledWith('/labs');
      expect(result.current.labs).toEqual([]);
    });

    it('handles missing labs field in API response', async () => {
      const studioRequest = createMockStudioRequest();
      studioRequest.mockImplementation((path: string) => {
        if (path === '/labs') return Promise.resolve({});
        return Promise.resolve({});
      });

      const { result } = renderHook(() =>
        useLabDataLoading({ studioRequest, activeLab: null })
      );

      await flushEffects();

      expect(studioRequest).toHaveBeenCalledWith('/labs');
      expect(result.current.labs).toEqual([]);
    });
  });

  // ── Agent Filtering ──

  describe('agent filtering', () => {
    it('filters agents to only online ones', async () => {
      const studioRequest = createMockStudioRequest();
      studioRequest.mockImplementation((path: string) => {
        if (path === '/labs') return Promise.resolve({ labs: [] });
        if (path === '/dashboard/metrics') return Promise.resolve(makeMetrics());
        if (path === '/agents') return Promise.resolve([
          { id: 'a1', name: 'Agent 1', address: '10.0.0.1:8001', status: 'online' },
          { id: 'a2', name: 'Agent 2', address: '10.0.0.2:8001', status: 'offline' },
          { id: 'a3', name: 'Agent 3', address: '10.0.0.3:8001', status: 'online' },
        ]);
        return Promise.resolve({});
      });

      const { result } = renderHook(() =>
        useLabDataLoading({ studioRequest, activeLab: null })
      );

      await flushEffects();

      expect(result.current.agents).toHaveLength(2);
      expect(result.current.agents.map(a => a.id)).toEqual(['a1', 'a3']);
    });

    it('maps agents to id and name only', async () => {
      const studioRequest = createMockStudioRequest();
      studioRequest.mockImplementation((path: string) => {
        if (path === '/labs') return Promise.resolve({ labs: [] });
        if (path === '/dashboard/metrics') return Promise.resolve(makeMetrics());
        if (path === '/agents') return Promise.resolve([
          { id: 'a1', name: 'Agent 1', address: '10.0.0.1:8001', status: 'online' },
        ]);
        return Promise.resolve({});
      });

      const { result } = renderHook(() =>
        useLabDataLoading({ studioRequest, activeLab: null })
      );

      await flushEffects();

      expect(result.current.agents).toHaveLength(1);
      expect(result.current.agents[0]).toEqual({ id: 'a1', name: 'Agent 1' });
      expect(result.current.agents[0]).not.toHaveProperty('address');
      expect(result.current.agents[0]).not.toHaveProperty('status');
    });

    it('handles empty agent list', async () => {
      const studioRequest = createMockStudioRequest();
      studioRequest.mockImplementation((path: string) => {
        if (path === '/labs') return Promise.resolve({ labs: [] });
        if (path === '/dashboard/metrics') return Promise.resolve(makeMetrics());
        if (path === '/agents') return Promise.resolve([]);
        return Promise.resolve({});
      });

      const { result } = renderHook(() =>
        useLabDataLoading({ studioRequest, activeLab: null })
      );

      await flushEffects();

      expect(result.current.agents).toEqual([]);
    });

    it('handles null agent response gracefully', async () => {
      const studioRequest = createMockStudioRequest();
      studioRequest.mockImplementation((path: string) => {
        if (path === '/labs') return Promise.resolve({ labs: [] });
        if (path === '/dashboard/metrics') return Promise.resolve(makeMetrics());
        if (path === '/agents') return Promise.resolve(null);
        return Promise.resolve({});
      });

      const { result } = renderHook(() =>
        useLabDataLoading({ studioRequest, activeLab: null })
      );

      await flushEffects();

      expect(result.current.agents).toEqual([]);
    });
  });

  // ── Error Handling ──

  describe('error handling', () => {
    it('silently handles agent API failure', async () => {
      const studioRequest = createMockStudioRequest();
      studioRequest.mockImplementation((path: string) => {
        if (path === '/labs') return Promise.resolve({ labs: [] });
        if (path === '/dashboard/metrics') return Promise.resolve(makeMetrics());
        if (path === '/agents') return Promise.reject(new Error('Agent API down'));
        return Promise.resolve({});
      });

      const { result } = renderHook(() =>
        useLabDataLoading({ studioRequest, activeLab: null })
      );

      await flushEffects();

      expect(result.current.agents).toEqual([]);
    });

    it('silently handles metrics API failure', async () => {
      const studioRequest = createMockStudioRequest();
      studioRequest.mockImplementation((path: string) => {
        if (path === '/labs') return Promise.resolve({ labs: [] });
        if (path === '/dashboard/metrics') return Promise.reject(new Error('Metrics unavailable'));
        if (path === '/agents') return Promise.resolve([]);
        return Promise.resolve({});
      });

      const { result } = renderHook(() =>
        useLabDataLoading({ studioRequest, activeLab: null })
      );

      await flushEffects();

      expect(result.current.systemMetrics).toBeNull();
    });
  });

  // ── System Metrics ──

  describe('system metrics', () => {
    it('sets system metrics from API response', async () => {
      const studioRequest = createMockStudioRequest();
      const metrics = makeMetrics({ cpu_percent: 75.0, labs_running: 3 });
      studioRequest.mockImplementation((path: string) => {
        if (path === '/labs') return Promise.resolve({ labs: [] });
        if (path === '/dashboard/metrics') return Promise.resolve(metrics);
        if (path === '/agents') return Promise.resolve([]);
        return Promise.resolve({});
      });

      const { result } = renderHook(() =>
        useLabDataLoading({ studioRequest, activeLab: null })
      );

      await flushEffects();

      expect(result.current.systemMetrics).not.toBeNull();
      expect(result.current.systemMetrics!.cpu_percent).toBe(75.0);
      expect(result.current.systemMetrics!.labs_running).toBe(3);
    });
  });

  // ── Lab Statuses ──

  describe('lab statuses', () => {
    it('loads lab statuses when labs are available and no active lab', async () => {
      const studioRequest = createMockStudioRequest();
      studioRequest.mockImplementation((path: string) => {
        if (path === '/labs') return Promise.resolve({ labs: [makeLab({ id: 'lab-1' }), makeLab({ id: 'lab-2' })] });
        if (path === '/dashboard/metrics') return Promise.resolve(makeMetrics());
        if (path === '/agents') return Promise.resolve([]);
        if (path === '/labs/lab-1/status') return Promise.resolve({
          nodes: [
            { name: 'R1', status: 'running' },
            { name: 'R2', status: 'stopped' },
          ],
        });
        if (path === '/labs/lab-2/status') return Promise.resolve({
          nodes: [{ name: 'SW1', status: 'running' }],
        });
        return Promise.resolve({});
      });

      const { result } = renderHook(() =>
        useLabDataLoading({ studioRequest, activeLab: null })
      );

      // Flush mount effects and the lab status cascade
      await flushEffects();
      await flushEffects();

      expect(result.current.labStatuses['lab-1']).toEqual({ running: 1, total: 2 });
      expect(result.current.labStatuses['lab-2']).toEqual({ running: 1, total: 1 });
    });

    it('does not load lab statuses when activeLab is set', async () => {
      const studioRequest = createMockStudioRequest();
      const activeLab = makeLab({ id: 'lab-1' });
      studioRequest.mockImplementation((path: string) => {
        if (path === '/labs') return Promise.resolve({ labs: [makeLab({ id: 'lab-1' })] });
        if (path === '/dashboard/metrics') return Promise.resolve(makeMetrics());
        if (path === '/agents') return Promise.resolve([]);
        return Promise.resolve({});
      });

      renderHook(() =>
        useLabDataLoading({ studioRequest, activeLab })
      );

      await flushEffects();
      await flushEffects();

      const statusCalls = studioRequest.mock.calls.filter(
        (call: unknown[]) => typeof call[0] === 'string' && (call[0] as string).includes('/status')
      );
      expect(statusCalls).toHaveLength(0);
    });

    it('handles lab status API failure gracefully', async () => {
      const studioRequest = createMockStudioRequest();
      studioRequest.mockImplementation((path: string) => {
        if (path === '/labs') return Promise.resolve({ labs: [makeLab({ id: 'lab-1' })] });
        if (path === '/dashboard/metrics') return Promise.resolve(makeMetrics());
        if (path === '/agents') return Promise.resolve([]);
        if (path === '/labs/lab-1/status') return Promise.reject(new Error('Lab not deployed'));
        return Promise.resolve({});
      });

      const { result } = renderHook(() =>
        useLabDataLoading({ studioRequest, activeLab: null })
      );

      await flushEffects();
      await flushEffects();

      expect(result.current.labStatuses).toEqual({});
    });
  });

  // ── Polling ──

  describe('polling', () => {
    it('polls system metrics every 10 seconds', async () => {
      const studioRequest = createMockStudioRequest();
      studioRequest.mockResolvedValue({ labs: [] });

      renderHook(() =>
        useLabDataLoading({ studioRequest, activeLab: null })
      );

      await flushEffects();

      const callCountAfterInit = studioRequest.mock.calls.length;

      await act(async () => {
        await vi.advanceTimersByTimeAsync(10000);
      });

      expect(studioRequest.mock.calls.length).toBeGreaterThan(callCountAfterInit);
    });

    it('polls lab statuses on dashboard (no activeLab) with labs present', async () => {
      const studioRequest = createMockStudioRequest();
      studioRequest.mockImplementation((path: string) => {
        if (path === '/labs') return Promise.resolve({ labs: [makeLab({ id: 'lab-1' })] });
        return Promise.resolve(makeMetrics());
      });

      renderHook(() =>
        useLabDataLoading({ studioRequest, activeLab: null })
      );

      await flushEffects();
      await flushEffects();

      // Clear call history to isolate poll calls
      studioRequest.mockClear();
      studioRequest.mockImplementation(() => Promise.resolve(makeMetrics()));

      await act(async () => {
        await vi.advanceTimersByTimeAsync(10000);
      });

      const statusCalls = studioRequest.mock.calls.filter(
        (call: unknown[]) => typeof call[0] === 'string' && (call[0] as string).includes('/labs/lab-1/status')
      );
      expect(statusCalls.length).toBeGreaterThan(0);
    });

    it('polls agents on interval to recover from initial auth failures', async () => {
      const studioRequest = createMockStudioRequest();
      // Simulate initial 401 failure on /agents, then success on retry
      let agentCallCount = 0;
      studioRequest.mockImplementation((path: string) => {
        if (path === '/agents') {
          agentCallCount++;
          if (agentCallCount === 1) return Promise.reject(new Error('401 Unauthorized'));
          return Promise.resolve([{ id: 'a1', name: 'Agent 1', address: '10.0.0.1:8001', status: 'online' }]);
        }
        if (path === '/labs') return Promise.resolve({ labs: [] });
        return Promise.resolve(makeMetrics());
      });

      const { result } = renderHook(() =>
        useLabDataLoading({ studioRequest, activeLab: null })
      );

      await flushEffects();

      // Initial load failed — agents should be empty
      expect(result.current.agents).toHaveLength(0);

      // Advance past one polling interval — agents should recover
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10000);
      });

      expect(agentCallCount).toBeGreaterThanOrEqual(2);
      expect(result.current.agents).toHaveLength(1);
      expect(result.current.agents[0].name).toBe('Agent 1');
    });

    it('does not poll lab statuses when activeLab is set', async () => {
      const studioRequest = createMockStudioRequest();
      const activeLab = makeLab({ id: 'lab-1' });
      studioRequest.mockImplementation((path: string) => {
        if (path === '/labs') return Promise.resolve({ labs: [makeLab({ id: 'lab-1' })] });
        return Promise.resolve(makeMetrics());
      });

      renderHook(() =>
        useLabDataLoading({ studioRequest, activeLab })
      );

      await flushEffects();

      studioRequest.mockClear();
      studioRequest.mockImplementation(() => Promise.resolve(makeMetrics()));

      await act(async () => {
        await vi.advanceTimersByTimeAsync(30000);
      });

      const statusCalls = studioRequest.mock.calls.filter(
        (call: unknown[]) => typeof call[0] === 'string' && (call[0] as string).includes('/status')
      );
      expect(statusCalls).toHaveLength(0);
    });

    it('cleans up poll interval on unmount', async () => {
      const studioRequest = createMockStudioRequest();
      studioRequest.mockResolvedValue({ labs: [] });

      const { unmount } = renderHook(() =>
        useLabDataLoading({ studioRequest, activeLab: null })
      );

      await flushEffects();

      const callCountAtUnmount = studioRequest.mock.calls.length;
      unmount();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(30000);
      });

      expect(studioRequest.mock.calls.length).toBe(callCountAtUnmount);
    });
  });

  // ── Refetch Triggers ──

  describe('refetch triggers', () => {
    it('exposes loadLabs for manual refetch', async () => {
      const studioRequest = createMockStudioRequest();
      studioRequest.mockResolvedValue({ labs: [] });

      const { result } = renderHook(() =>
        useLabDataLoading({ studioRequest, activeLab: null })
      );

      await flushEffects();

      const callsBefore = studioRequest.mock.calls.filter(
        (call: unknown[]) => call[0] === '/labs'
      ).length;

      await act(async () => {
        await result.current.loadLabs();
      });

      const callsAfter = studioRequest.mock.calls.filter(
        (call: unknown[]) => call[0] === '/labs'
      ).length;

      expect(callsAfter).toBe(callsBefore + 1);
    });

    it('exposes loadAgents for manual refetch', async () => {
      const studioRequest = createMockStudioRequest();
      studioRequest.mockResolvedValue([]);

      const { result } = renderHook(() =>
        useLabDataLoading({ studioRequest, activeLab: null })
      );

      await flushEffects();

      await act(async () => {
        await result.current.loadAgents();
      });

      const agentCalls = studioRequest.mock.calls.filter(
        (call: unknown[]) => call[0] === '/agents'
      );
      expect(agentCalls.length).toBeGreaterThanOrEqual(2);
    });

    it('exposes loadSystemMetrics for manual refetch', async () => {
      const studioRequest = createMockStudioRequest();
      studioRequest.mockResolvedValue(makeMetrics());

      const { result } = renderHook(() =>
        useLabDataLoading({ studioRequest, activeLab: null })
      );

      await flushEffects();

      await act(async () => {
        await result.current.loadSystemMetrics();
      });

      const metricsCalls = studioRequest.mock.calls.filter(
        (call: unknown[]) => call[0] === '/dashboard/metrics'
      );
      expect(metricsCalls.length).toBeGreaterThanOrEqual(2);
    });

    it('exposes setLabs to directly set labs state', async () => {
      const studioRequest = createMockStudioRequest();
      studioRequest.mockResolvedValue({ labs: [] });

      const { result } = renderHook(() =>
        useLabDataLoading({ studioRequest, activeLab: null })
      );

      await flushEffects();

      act(() => {
        result.current.setLabs([makeLab({ id: 'injected', name: 'Injected Lab' })]);
      });

      expect(result.current.labs).toHaveLength(1);
      expect(result.current.labs[0].id).toBe('injected');
    });
  });
});
