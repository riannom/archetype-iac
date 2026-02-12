import { describe, it, expect, vi } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useConfigManager } from './useConfigManager';
import { DeviceType, type Node } from '../types';

const deviceNode: Node = {
  id: '1',
  name: 'R1',
  x: 0,
  y: 0,
  nodeType: 'device',
  type: DeviceType.ROUTER,
  model: 'x',
  version: '1',
  container_name: 'r1',
};

const externalNode: Node = {
  id: '2',
  name: 'Ext',
  x: 1,
  y: 1,
  nodeType: 'external',
};

const snapshots = [
  {
    id: 's1',
    lab_id: 'lab1',
    node_name: 'r1',
    content: 'cfg1',
    content_hash: 'h1',
    snapshot_type: 'manual',
    device_kind: 'ceos',
    mapped_to_node_id: null,
    created_at: '2024-01-01T00:00:00Z',
    is_active: false,
    is_orphaned: false,
  },
  {
    id: 's2',
    lab_id: 'lab1',
    node_name: 'r1',
    content: 'cfg2',
    content_hash: 'h2',
    snapshot_type: 'manual',
    device_kind: 'ceos',
    mapped_to_node_id: null,
    created_at: '2024-01-02T00:00:00Z',
    is_active: true,
    is_orphaned: false,
  },
  {
    id: 's3',
    lab_id: 'lab1',
    node_name: 'orphan',
    content: 'cfg3',
    content_hash: 'h3',
    snapshot_type: 'manual',
    device_kind: 'iosv',
    mapped_to_node_id: null,
    created_at: '2024-01-03T00:00:00Z',
    is_active: false,
    is_orphaned: true,
  },
];

describe('useConfigManager', () => {
  it('loads snapshots and computes node names', async () => {
    const studioRequest = vi.fn().mockResolvedValue({ snapshots });

    const { result } = renderHook(() =>
      useConfigManager({
        labId: 'lab1',
        nodes: [deviceNode, externalNode],
        studioRequest,
      })
    );

    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.snapshots).toHaveLength(3);
    expect(result.current.deviceNodes).toHaveLength(1);
    expect(result.current.selectedNodeName).toBe('r1');
    expect(result.current.nodeNames).toEqual(['orphan', 'r1']);
    expect(result.current.orphanedNodeNames).toEqual(['orphan']);
    expect(result.current.orphanedByDeviceKind.get('iosv')?.length).toBe(1);

    const nodeSnapshots = result.current.nodeSnapshots;
    expect(nodeSnapshots[0].id).toBe('s2');
    expect(nodeSnapshots[1].id).toBe('s1');
  });

  it('handles compare selection ordering', async () => {
    const studioRequest = vi.fn().mockResolvedValue({ snapshots });

    const { result } = renderHook(() =>
      useConfigManager({
        labId: 'lab1',
        nodes: [deviceNode],
        studioRequest,
      })
    );

    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => {
      result.current.setViewMode('compare');
    });
    act(() => {
      result.current.handleSnapshotClick('s2');
      result.current.handleSnapshotClick('s1');
    });

    expect(result.current.selectedSnapshotIds.size).toBe(2);
    expect(result.current.comparisonSnapshots?.[0].id).toBe('s1');
    expect(result.current.comparisonSnapshots?.[1].id).toBe('s2');
  });
});
