import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useAgentUpdates } from './useAgentUpdates';
import type { HostDetailed, UpdateStatus } from './infrastructureTypes';

// Mock api module
vi.mock('../../api', () => ({
  apiRequest: vi.fn(),
}));

// Mock NotificationContext
const mockAddNotification = vi.fn();
vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => ({
    addNotification: mockAddNotification,
  }),
}));

// Mock window.confirm
const mockConfirm = vi.fn().mockReturnValue(true);
vi.stubGlobal('confirm', mockConfirm);

import { apiRequest } from '../../api';

const mockApiRequest = apiRequest as ReturnType<typeof vi.fn>;

// ============================================================================
// Helpers
// ============================================================================

function makeHost(overrides: Partial<HostDetailed> = {}): HostDetailed {
  return {
    id: 'agent-1',
    name: 'Agent One',
    address: '10.0.0.1:8001',
    status: 'online',
    version: 'abc1234',
    role: 'agent',
    capabilities: {},
    resource_usage: {
      cpu_percent: 10,
      memory_percent: 20,
      memory_used_gb: 4,
      memory_total_gb: 16,
      storage_percent: 30,
      storage_used_gb: 100,
      storage_total_gb: 500,
      containers_running: 5,
      containers_total: 10,
      vms_running: 0,
      vms_total: 0,
      container_details: [],
      vm_details: [],
    },
    images: [],
    labs: [],
    lab_count: 0,
    started_at: null,
    last_heartbeat: null,
    git_sha: null,
    last_error: null,
    error_since: null,
    data_plane_address: null,
    ...overrides,
  };
}

// ============================================================================
// Tests
// ============================================================================

describe('useAgentUpdates', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // ── Initial State ──

  it('returns initial state', () => {
    const loadHosts = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useAgentUpdates([makeHost()], loadHosts));

    expect(result.current.updatingAgents.size).toBe(0);
    expect(result.current.updateStatuses.size).toBe(0);
    expect(result.current.customUpdateTarget).toBeNull();
    expect(result.current.customVersion).toBe('');
  });

  // ── triggerUpdate ──

  it('triggers agent update successfully', async () => {
    const loadHosts = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useAgentUpdates([makeHost()], loadHosts));

    mockApiRequest.mockResolvedValueOnce({
      job_id: 'update-job-1',
      status: 'started',
      message: 'Update started',
    });

    await act(async () => {
      await result.current.triggerUpdate('agent-1', 'def5678');
    });

    expect(mockApiRequest).toHaveBeenCalledWith('/agents/agent-1/update', {
      method: 'POST',
      body: JSON.stringify({ target_version: 'def5678' }),
    });
    expect(result.current.updatingAgents.has('agent-1')).toBe(true);
  });

  it('triggers update without target version', async () => {
    const loadHosts = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useAgentUpdates([makeHost()], loadHosts));

    mockApiRequest.mockResolvedValueOnce({
      job_id: 'update-job-2',
      status: 'started',
      message: 'Update started',
    });

    await act(async () => {
      await result.current.triggerUpdate('agent-1');
    });

    expect(mockApiRequest).toHaveBeenCalledWith('/agents/agent-1/update', {
      method: 'POST',
    });
  });

  it('removes agent from updating set on failed status', async () => {
    const loadHosts = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useAgentUpdates([makeHost()], loadHosts));

    mockApiRequest.mockResolvedValueOnce({
      job_id: 'update-job-3',
      status: 'failed',
      message: 'Agent unreachable',
    });

    await act(async () => {
      await result.current.triggerUpdate('agent-1');
    });

    expect(mockAddNotification).toHaveBeenCalledWith('error', 'Update failed to start', 'Agent unreachable');
    expect(result.current.updatingAgents.has('agent-1')).toBe(false);
  });

  it('handles trigger error', async () => {
    const loadHosts = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useAgentUpdates([makeHost()], loadHosts));

    mockApiRequest.mockRejectedValueOnce(new Error('Network error'));

    await act(async () => {
      await result.current.triggerUpdate('agent-1');
    });

    expect(mockAddNotification).toHaveBeenCalledWith('error', 'Failed to trigger update', 'Network error');
    expect(result.current.updatingAgents.has('agent-1')).toBe(false);
  });

  // ── triggerRebuild ──

  it('triggers rebuild with confirmation', async () => {
    const loadHosts = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useAgentUpdates([makeHost()], loadHosts));

    mockConfirm.mockReturnValueOnce(true);
    mockApiRequest.mockResolvedValueOnce({ success: true, message: 'Rebuilding' });

    await act(async () => {
      await result.current.triggerRebuild('agent-1');
    });

    expect(mockConfirm).toHaveBeenCalled();
    expect(mockApiRequest).toHaveBeenCalledWith('/agents/agent-1/rebuild', { method: 'POST' });
    expect(result.current.updatingAgents.has('agent-1')).toBe(true);

    // After 5s timeout, agent removed and hosts reloaded
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5100);
    });

    expect(result.current.updatingAgents.has('agent-1')).toBe(false);
    expect(loadHosts).toHaveBeenCalled();
  });

  it('does nothing when rebuild confirm is cancelled', async () => {
    const loadHosts = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useAgentUpdates([makeHost()], loadHosts));

    mockConfirm.mockReturnValueOnce(false);

    await act(async () => {
      await result.current.triggerRebuild('agent-1');
    });

    expect(mockApiRequest).not.toHaveBeenCalled();
  });

  it('handles rebuild failure response', async () => {
    const loadHosts = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useAgentUpdates([makeHost()], loadHosts));

    mockConfirm.mockReturnValueOnce(true);
    mockApiRequest.mockResolvedValueOnce({ success: false, message: 'Container not found' });

    await act(async () => {
      await result.current.triggerRebuild('agent-1');
    });

    expect(mockAddNotification).toHaveBeenCalledWith('error', 'Rebuild failed', 'Container not found');
    expect(result.current.updatingAgents.has('agent-1')).toBe(false);
  });

  it('handles rebuild API error', async () => {
    const loadHosts = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useAgentUpdates([makeHost()], loadHosts));

    mockConfirm.mockReturnValueOnce(true);
    mockApiRequest.mockRejectedValueOnce(new Error('Connection refused'));

    await act(async () => {
      await result.current.triggerRebuild('agent-1');
    });

    expect(mockAddNotification).toHaveBeenCalledWith('error', 'Failed to trigger rebuild', 'Connection refused');
  });

  // ── triggerBulkUpdate ──

  it('updates all outdated agents', async () => {
    const hosts = [
      makeHost({ id: 'agent-1', version: 'old-sha', status: 'online' }),
      makeHost({ id: 'agent-2', version: 'latest-sha', status: 'online' }),
      makeHost({ id: 'agent-3', version: 'old-sha', status: 'online' }),
    ];
    const loadHosts = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useAgentUpdates(hosts, loadHosts));

    mockConfirm.mockReturnValueOnce(true);
    mockApiRequest.mockResolvedValueOnce({
      success_count: 2,
      failure_count: 0,
      results: [
        { agent_id: 'agent-1', success: true },
        { agent_id: 'agent-3', success: true },
      ],
    });

    await act(async () => {
      await result.current.triggerBulkUpdate('latest-sha');
    });

    expect(mockApiRequest).toHaveBeenCalledWith('/agents/updates/bulk', {
      method: 'POST',
      body: JSON.stringify({ agent_ids: ['agent-1', 'agent-3'] }),
    });
    expect(result.current.updatingAgents.has('agent-1')).toBe(true);
    expect(result.current.updatingAgents.has('agent-3')).toBe(true);
    expect(result.current.updatingAgents.has('agent-2')).toBe(false);
  });

  it('notifies when all agents are already up to date', async () => {
    const hosts = [makeHost({ version: 'latest-sha' })];
    const loadHosts = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useAgentUpdates(hosts, loadHosts));

    await act(async () => {
      await result.current.triggerBulkUpdate('latest-sha');
    });

    expect(mockAddNotification).toHaveBeenCalledWith('info', 'All agents are already up to date');
    expect(mockApiRequest).not.toHaveBeenCalled();
  });

  it('does nothing when bulk update confirm is cancelled', async () => {
    const hosts = [makeHost({ version: 'old-sha' })];
    const loadHosts = vi.fn().mockResolvedValue(undefined);
    renderHook(() => useAgentUpdates(hosts, loadHosts));

    mockConfirm.mockReturnValueOnce(false);

    // The hook needs to be called via the result
    const { result } = renderHook(() => useAgentUpdates(hosts, loadHosts));

    await act(async () => {
      await result.current.triggerBulkUpdate('latest-sha');
    });

    expect(mockApiRequest).not.toHaveBeenCalled();
  });

  it('handles partial bulk update failure', async () => {
    const hosts = [
      makeHost({ id: 'agent-1', version: 'old-sha', status: 'online' }),
      makeHost({ id: 'agent-2', version: 'old-sha', status: 'online' }),
    ];
    const loadHosts = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useAgentUpdates(hosts, loadHosts));

    mockConfirm.mockReturnValueOnce(true);
    mockApiRequest.mockResolvedValueOnce({
      success_count: 1,
      failure_count: 1,
      results: [
        { agent_id: 'agent-1', success: true },
        { agent_id: 'agent-2', success: false, error: 'Agent unreachable' },
      ],
    });

    await act(async () => {
      await result.current.triggerBulkUpdate('latest-sha');
    });

    expect(mockAddNotification).toHaveBeenCalledWith(
      'warning',
      'Bulk update partially failed',
      expect.stringContaining('1 updates started')
    );
    // Failed agent removed from updating set
    expect(result.current.updatingAgents.has('agent-2')).toBe(false);
    expect(result.current.updatingAgents.has('agent-1')).toBe(true);
  });

  it('handles bulk update API error', async () => {
    const hosts = [makeHost({ version: 'old-sha', status: 'online' })];
    const loadHosts = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useAgentUpdates(hosts, loadHosts));

    mockConfirm.mockReturnValueOnce(true);
    mockApiRequest.mockRejectedValueOnce(new Error('Server down'));

    await act(async () => {
      await result.current.triggerBulkUpdate('latest-sha');
    });

    expect(mockAddNotification).toHaveBeenCalledWith('error', 'Failed to trigger bulk update', 'Server down');
    expect(result.current.updatingAgents.size).toBe(0);
  });

  // ── isUpdateAvailable ──

  it('returns true when versions differ', () => {
    const loadHosts = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useAgentUpdates([makeHost()], loadHosts));

    const host = makeHost({ version: 'old-sha' });
    expect(result.current.isUpdateAvailable(host, 'new-sha')).toBe(true);
  });

  it('returns false when versions match', () => {
    const loadHosts = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useAgentUpdates([makeHost()], loadHosts));

    const host = makeHost({ version: 'same-sha' });
    expect(result.current.isUpdateAvailable(host, 'same-sha')).toBe(false);
  });

  it('returns false when host has no version', () => {
    const loadHosts = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useAgentUpdates([makeHost()], loadHosts));

    const host = makeHost({ version: '' });
    expect(result.current.isUpdateAvailable(host, 'latest')).toBe(false);
  });

  it('returns false when latestVersion is empty', () => {
    const loadHosts = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useAgentUpdates([makeHost()], loadHosts));

    const host = makeHost({ version: 'abc' });
    expect(result.current.isUpdateAvailable(host, '')).toBe(false);
  });

  // ── Update Status Polling ──

  it('polls update status for updating agents', async () => {
    const loadHosts = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useAgentUpdates([makeHost()], loadHosts));

    // Start an update
    mockApiRequest.mockResolvedValueOnce({
      job_id: 'poll-test',
      status: 'started',
      message: 'Update started',
    });

    await act(async () => {
      await result.current.triggerUpdate('agent-1');
    });

    // Mock poll response: in progress
    mockApiRequest.mockResolvedValueOnce({
      job_id: 'poll-test',
      agent_id: 'agent-1',
      from_version: 'old',
      to_version: 'new',
      status: 'updating',
      progress_percent: 50,
      error_message: null,
    } satisfies UpdateStatus);

    // Advance timer to trigger poll
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100);
    });

    expect(result.current.updateStatuses.get('agent-1')?.status).toBe('updating');

    // Mock poll response: completed
    mockApiRequest.mockResolvedValueOnce({
      job_id: 'poll-test',
      agent_id: 'agent-1',
      from_version: 'old',
      to_version: 'new',
      status: 'completed',
      progress_percent: 100,
      error_message: null,
    } satisfies UpdateStatus);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100);
    });

    expect(result.current.updatingAgents.has('agent-1')).toBe(false);
    expect(loadHosts).toHaveBeenCalled();
  });

  // ── Custom Update Target ──

  it('can set and clear custom update target', () => {
    const loadHosts = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useAgentUpdates([makeHost()], loadHosts));

    act(() => {
      result.current.setCustomUpdateTarget({ hostId: 'agent-1', hostName: 'Agent One' });
    });

    expect(result.current.customUpdateTarget).toEqual({ hostId: 'agent-1', hostName: 'Agent One' });

    act(() => {
      result.current.setCustomUpdateTarget(null);
    });

    expect(result.current.customUpdateTarget).toBeNull();
  });

  it('can set custom version', () => {
    const loadHosts = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useAgentUpdates([makeHost()], loadHosts));

    act(() => {
      result.current.setCustomVersion('abc123');
    });

    expect(result.current.customVersion).toBe('abc123');
  });

  // ── removeUpdatingAgent ──

  it('manually removes agent from updating set', async () => {
    const loadHosts = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useAgentUpdates([makeHost()], loadHosts));

    mockApiRequest.mockResolvedValueOnce({
      job_id: 'test',
      status: 'started',
      message: 'ok',
    });

    await act(async () => {
      await result.current.triggerUpdate('agent-1');
    });

    expect(result.current.updatingAgents.has('agent-1')).toBe(true);

    act(() => {
      result.current.removeUpdatingAgent('agent-1');
    });

    expect(result.current.updatingAgents.has('agent-1')).toBe(false);
  });
});
