import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useUptime } from './useUptime';

vi.mock('../../utils/format', () => ({
  formatUptime: vi.fn((ms: number) => {
    const totalSeconds = Math.floor(ms / 1000);
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;
    return `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
  }),
}));

interface NodeStateEntry {
  id: string;
  lab_id: string;
  node_id: string;
  node_name: string;
  desired_state: 'stopped' | 'running';
  actual_state: 'undeployed' | 'pending' | 'starting' | 'running' | 'stopped' | 'stopping' | 'error';
  error_message?: string | null;
  is_ready?: boolean;
  boot_started_at?: string | null;
  created_at: string;
  updated_at: string;
}

const createNodeState = (overrides: Partial<NodeStateEntry> = {}): NodeStateEntry => ({
  id: 'state-1',
  lab_id: 'lab-1',
  node_id: 'node-1',
  node_name: 'router1',
  desired_state: 'running',
  actual_state: 'running',
  is_ready: true,
  boot_started_at: null,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  ...overrides,
});

describe('useUptime', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('returns default uptime when no nodes', () => {
    const { result } = renderHook(() => useUptime({}));
    expect(result.current).toBe('--:--:--');
  });

  it('returns default uptime when no nodes have boot_started_at', () => {
    const nodeStates = {
      'node-1': createNodeState({ actual_state: 'running', boot_started_at: null }),
    };
    const { result } = renderHook(() => useUptime(nodeStates as any));
    expect(result.current).toBe('--:--:--');
  });

  it('returns default uptime when no running nodes', () => {
    const nodeStates = {
      'node-1': createNodeState({
        actual_state: 'stopped',
        boot_started_at: new Date(Date.now() - 3600000).toISOString(),
      }),
    };
    const { result } = renderHook(() => useUptime(nodeStates as any));
    expect(result.current).toBe('--:--:--');
  });

  it('calculates uptime from running node with boot_started_at', () => {
    const now = new Date('2024-01-15T12:00:00Z');
    vi.setSystemTime(now);

    const bootTime = new Date('2024-01-15T11:00:00Z'); // 1 hour ago
    const nodeStates = {
      'node-1': createNodeState({
        actual_state: 'running',
        boot_started_at: bootTime.toISOString(),
      }),
    };

    const { result } = renderHook(() => useUptime(nodeStates as any));
    expect(result.current).toBe('01:00:00');
  });

  it('uses earliest boot time when multiple nodes are running', () => {
    const now = new Date('2024-01-15T12:00:00Z');
    vi.setSystemTime(now);

    const nodeStates = {
      'node-1': createNodeState({
        id: 'state-1',
        node_id: 'node-1',
        actual_state: 'running',
        boot_started_at: new Date('2024-01-15T10:00:00Z').toISOString(), // 2 hours ago (earliest)
      }),
      'node-2': createNodeState({
        id: 'state-2',
        node_id: 'node-2',
        actual_state: 'running',
        boot_started_at: new Date('2024-01-15T11:00:00Z').toISOString(), // 1 hour ago
      }),
      'node-3': createNodeState({
        id: 'state-3',
        node_id: 'node-3',
        actual_state: 'running',
        boot_started_at: new Date('2024-01-15T11:30:00Z').toISOString(), // 30 min ago
      }),
    };

    const { result } = renderHook(() => useUptime(nodeStates as any));
    expect(result.current).toBe('02:00:00');
  });

  it('ignores stopped nodes when calculating uptime', () => {
    const now = new Date('2024-01-15T12:00:00Z');
    vi.setSystemTime(now);

    const nodeStates = {
      'node-1': createNodeState({
        id: 'state-1',
        node_id: 'node-1',
        actual_state: 'stopped',
        boot_started_at: new Date('2024-01-15T08:00:00Z').toISOString(), // 4 hours ago
      }),
      'node-2': createNodeState({
        id: 'state-2',
        node_id: 'node-2',
        actual_state: 'running',
        boot_started_at: new Date('2024-01-15T11:00:00Z').toISOString(), // 1 hour ago
      }),
    };

    const { result } = renderHook(() => useUptime(nodeStates as any));
    expect(result.current).toBe('01:00:00');
  });

  it('sets up interval for uptime updates', () => {
    const now = new Date('2024-01-15T12:00:00Z');
    vi.setSystemTime(now);

    const bootTime = new Date('2024-01-15T11:59:00Z');
    const nodeStates = {
      'node-1': createNodeState({
        actual_state: 'running',
        boot_started_at: bootTime.toISOString(),
      }),
    };

    const setIntervalSpy = vi.spyOn(global, 'setInterval');
    renderHook(() => useUptime(nodeStates as any));
    expect(setIntervalSpy).toHaveBeenCalledWith(expect.any(Function), 1000);
    setIntervalSpy.mockRestore();
  });
});
