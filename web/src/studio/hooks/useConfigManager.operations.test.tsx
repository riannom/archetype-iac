import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useConfigManager } from './useConfigManager';
import { DeviceType, type Node } from '../types';

// Mock the download utility so downloadConfigs doesn't try to create DOM elements
vi.mock('../../utils/download', () => ({
  downloadBlob: vi.fn(),
}));

// Mock rawApiRequest used by downloadConfigs
vi.mock('../../api', () => ({
  rawApiRequest: vi.fn(),
}));

import { rawApiRequest } from '../../api';
import { downloadBlob } from '../../utils/download';

// ---- Fixtures ----

const deviceNode: Node = {
  id: '1',
  name: 'R1',
  x: 0,
  y: 0,
  nodeType: 'device',
  type: DeviceType.ROUTER,
  model: 'ceos',
  version: '1',
  container_name: 'r1',
};

const deviceNode2: Node = {
  id: '2',
  name: 'SW1',
  x: 10,
  y: 10,
  nodeType: 'device',
  type: DeviceType.SWITCH,
  model: 'ceos',
  version: '1',
  container_name: 'sw1',
};

const externalNode: Node = {
  id: '3',
  name: 'Ext',
  x: 1,
  y: 1,
  nodeType: 'external',
};

const nodeWithoutContainerName: Node = {
  id: '4',
  name: 'R2',
  x: 5,
  y: 5,
  nodeType: 'device',
  type: DeviceType.ROUTER,
  model: 'srlinux',
  version: '1',
};

const mkSnapshot = (overrides: Record<string, unknown> = {}) => ({
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
  ...overrides,
});

const snapshots = [
  mkSnapshot({ id: 's1', created_at: '2024-01-01T00:00:00Z' }),
  mkSnapshot({ id: 's2', content: 'cfg2', content_hash: 'h2', created_at: '2024-01-02T00:00:00Z', is_active: true }),
  mkSnapshot({
    id: 's3',
    node_name: 'orphan',
    content: 'cfg3',
    content_hash: 'h3',
    device_kind: 'iosv',
    created_at: '2024-01-03T00:00:00Z',
    is_orphaned: true,
  }),
];

// ---- Helpers ----

describe('useConfigManager extended', () => {
  let studioRequest: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();
    studioRequest = vi.fn().mockResolvedValue({ snapshots });
  });

  function renderManager(opts: { nodes?: Node[]; labId?: string } = {}) {
    return renderHook(() =>
      useConfigManager({
        labId: opts.labId ?? 'lab1',
        nodes: opts.nodes ?? [deviceNode],
        studioRequest,
      })
    );
  }

  // ---- loadSnapshots ----

  describe('loadSnapshots', () => {
    it('auto-selects first node_name when selectedNodeName is null', async () => {
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(result.current.selectedNodeName).toBe('r1');
    });

    it('does not auto-select if a node is already selected', async () => {
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      // Select a different node, then reload
      act(() => {
        result.current.selectNode('orphan');
      });

      studioRequest.mockResolvedValueOnce({ snapshots });
      await act(async () => {
        await result.current.loadSnapshots();
      });

      // Should keep 'orphan', not revert to 'r1'
      expect(result.current.selectedNodeName).toBe('orphan');
    });

    it('handles empty snapshots response', async () => {
      studioRequest.mockResolvedValue({ snapshots: [] });
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      expect(result.current.snapshots).toHaveLength(0);
      expect(result.current.selectedNodeName).toBeNull();
    });

    it('handles undefined snapshots in response', async () => {
      studioRequest.mockResolvedValue({});
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      expect(result.current.snapshots).toHaveLength(0);
    });

    it('sets error on API failure', async () => {
      studioRequest.mockRejectedValue(new Error('Network timeout'));
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      expect(result.current.error).toBe('Network timeout');
    });

    it('sets generic error for non-Error thrown values', async () => {
      studioRequest.mockRejectedValue('string error');
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      expect(result.current.error).toBe('Failed to load snapshots');
    });

    it('does not load when labId is empty', async () => {
      const { result } = renderManager({ labId: '' });
      // Give it a tick to not load
      await new Promise((r) => setTimeout(r, 50));

      expect(studioRequest).not.toHaveBeenCalled();
      expect(result.current.loading).toBe(false);
    });
  });

  // ---- nodeSnapshots (sorted newest first) ----

  describe('nodeSnapshots', () => {
    it('returns snapshots for selected node sorted newest first', async () => {
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      const ns = result.current.nodeSnapshots;
      expect(ns).toHaveLength(2);
      expect(ns[0].id).toBe('s2'); // 2024-01-02
      expect(ns[1].id).toBe('s1'); // 2024-01-01
    });

    it('returns empty array when no node is selected', async () => {
      studioRequest.mockResolvedValue({ snapshots: [] });
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      expect(result.current.selectedNodeName).toBeNull();
      expect(result.current.nodeSnapshots).toHaveLength(0);
    });
  });

  // ---- orphanedNodeNames ----

  describe('orphanedNodeNames', () => {
    it('identifies nodes with snapshots but not in topology', async () => {
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      expect(result.current.orphanedNodeNames).toEqual(['orphan']);
    });

    it('returns empty when all snapshot nodes exist in topology', async () => {
      studioRequest.mockResolvedValue({ snapshots: snapshots.filter((s) => s.node_name === 'r1') });
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      expect(result.current.orphanedNodeNames).toHaveLength(0);
    });
  });

  // ---- orphanedByDeviceKind ----

  describe('orphanedByDeviceKind', () => {
    it('groups orphaned snapshots by device_kind', async () => {
      const orphanSnaps = [
        mkSnapshot({ id: 'o1', node_name: 'dead1', device_kind: 'iosv', is_orphaned: true }),
        mkSnapshot({ id: 'o2', node_name: 'dead2', device_kind: 'iosv', is_orphaned: true }),
        mkSnapshot({ id: 'o3', node_name: 'dead3', device_kind: 'ceos', is_orphaned: true }),
      ];
      studioRequest.mockResolvedValue({ snapshots: orphanSnaps });
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      expect(result.current.orphanedByDeviceKind.get('iosv')).toHaveLength(2);
      expect(result.current.orphanedByDeviceKind.get('ceos')).toHaveLength(1);
    });

    it('uses "unknown" for null device_kind', async () => {
      const orphanSnaps = [
        mkSnapshot({ id: 'o1', node_name: 'dead1', device_kind: null, is_orphaned: true }),
      ];
      studioRequest.mockResolvedValue({ snapshots: orphanSnaps });
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      expect(result.current.orphanedByDeviceKind.get('unknown')).toHaveLength(1);
    });
  });

  // ---- getDisplayName ----

  describe('getDisplayName', () => {
    it('maps container_name to node display name', async () => {
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      expect(result.current.getDisplayName('r1')).toBe('R1');
    });

    it('falls back to container name when not found', async () => {
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      expect(result.current.getDisplayName('unknown-container')).toBe('unknown-container');
    });

    it('uses node.name as container_name fallback', async () => {
      const { result } = renderManager({ nodes: [nodeWithoutContainerName] });
      await waitFor(() => expect(result.current.loading).toBe(false));

      // nodeWithoutContainerName has name "R2" and no container_name,
      // so container_name defaults to "R2", and display name maps "R2" -> "R2"
      expect(result.current.getDisplayName('R2')).toBe('R2');
    });
  });

  // ---- viewMode and comparison logic ----

  describe('viewMode and comparison', () => {
    it('in view mode, clicking replaces selection', async () => {
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      act(() => result.current.handleSnapshotClick('s1'));
      expect(result.current.selectedSnapshotIds.has('s1')).toBe(true);

      act(() => result.current.handleSnapshotClick('s2'));
      expect(result.current.selectedSnapshotIds.has('s1')).toBe(false);
      expect(result.current.selectedSnapshotIds.has('s2')).toBe(true);
      expect(result.current.selectedSnapshotIds.size).toBe(1);
    });

    it('in compare mode, toggling adds up to 2 snapshots', async () => {
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      act(() => result.current.setViewMode('compare'));
      act(() => result.current.handleSnapshotClick('s1'));
      act(() => result.current.handleSnapshotClick('s2'));

      expect(result.current.selectedSnapshotIds.size).toBe(2);
    });

    it('in compare mode, third click replaces oldest selection', async () => {
      const threeSnaps = [
        mkSnapshot({ id: 'a', node_name: 'r1', created_at: '2024-01-01T00:00:00Z' }),
        mkSnapshot({ id: 'b', node_name: 'r1', created_at: '2024-01-02T00:00:00Z' }),
        mkSnapshot({ id: 'c', node_name: 'r1', created_at: '2024-01-03T00:00:00Z' }),
      ];
      studioRequest.mockResolvedValue({ snapshots: threeSnaps });
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      act(() => result.current.setViewMode('compare'));
      act(() => result.current.handleSnapshotClick('a'));
      act(() => result.current.handleSnapshotClick('b'));
      act(() => result.current.handleSnapshotClick('c'));

      expect(result.current.selectedSnapshotIds.size).toBe(2);
      expect(result.current.selectedSnapshotIds.has('a')).toBe(false);
      expect(result.current.selectedSnapshotIds.has('b')).toBe(true);
      expect(result.current.selectedSnapshotIds.has('c')).toBe(true);
    });

    it('in compare mode, clicking selected snapshot deselects it', async () => {
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      act(() => result.current.setViewMode('compare'));
      act(() => result.current.handleSnapshotClick('s1'));
      expect(result.current.selectedSnapshotIds.has('s1')).toBe(true);

      act(() => result.current.handleSnapshotClick('s1'));
      expect(result.current.selectedSnapshotIds.has('s1')).toBe(false);
    });

    it('comparisonSnapshots returns older first', async () => {
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      act(() => result.current.setViewMode('compare'));
      // Click newer first, then older
      act(() => result.current.handleSnapshotClick('s2'));
      act(() => result.current.handleSnapshotClick('s1'));

      const pair = result.current.comparisonSnapshots;
      expect(pair).not.toBeNull();
      expect(pair![0].id).toBe('s1'); // older
      expect(pair![1].id).toBe('s2'); // newer
    });

    it('comparisonSnapshots returns null with fewer than 2 selected', async () => {
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      act(() => result.current.setViewMode('compare'));
      act(() => result.current.handleSnapshotClick('s1'));

      expect(result.current.comparisonSnapshots).toBeNull();
    });

    it('selectedSnapshot returns single snapshot', async () => {
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      act(() => result.current.handleSnapshotClick('s1'));

      expect(result.current.selectedSnapshot).not.toBeNull();
      expect(result.current.selectedSnapshot!.id).toBe('s1');
    });

    it('selectedSnapshot returns null when multiple are selected', async () => {
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      act(() => result.current.setViewMode('compare'));
      act(() => result.current.handleSnapshotClick('s1'));
      act(() => result.current.handleSnapshotClick('s2'));

      expect(result.current.selectedSnapshot).toBeNull();
    });
  });

  // ---- setActiveConfig ----

  describe('setActiveConfig', () => {
    it('calls PUT on active-config endpoint and refreshes', async () => {
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      studioRequest
        .mockResolvedValueOnce({ success: true, message: 'ok' })
        .mockResolvedValueOnce({ snapshots });

      await act(async () => {
        await result.current.setActiveConfig('r1', 's1');
      });

      expect(studioRequest).toHaveBeenCalledWith(
        '/labs/lab1/nodes/r1/active-config',
        expect.objectContaining({
          method: 'PUT',
          body: JSON.stringify({ snapshot_id: 's1' }),
        })
      );
    });

    it('propagates errors from setActiveConfig', async () => {
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      studioRequest.mockRejectedValueOnce(new Error('Config conflict'));

      await expect(result.current.setActiveConfig('r1', 's1')).rejects.toThrow('Config conflict');
    });
  });

  // ---- deleteSnapshot ----

  describe('deleteSnapshot', () => {
    it('calls DELETE and removes from selectedSnapshotIds', async () => {
      studioRequest
        .mockResolvedValueOnce({ snapshots })
        .mockResolvedValueOnce({})
        .mockResolvedValueOnce({ snapshots: [snapshots[1]] });

      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      act(() => result.current.handleSnapshotClick('s1'));
      expect(result.current.selectedSnapshotIds.has('s1')).toBe(true);

      await act(async () => {
        await result.current.deleteSnapshot('s1');
      });

      expect(studioRequest).toHaveBeenCalledWith(
        '/labs/lab1/config-snapshots/s1',
        expect.objectContaining({ method: 'DELETE' })
      );
      expect(result.current.selectedSnapshotIds.has('s1')).toBe(false);
    });

    it('wraps non-Error thrown values in Error', async () => {
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      studioRequest.mockRejectedValueOnce('plain string');

      await expect(result.current.deleteSnapshot('s1')).rejects.toThrow('Failed to delete snapshot');
    });
  });

  // ---- bulkDeleteSnapshots ----

  describe('bulkDeleteSnapshots', () => {
    it('includes force flag in query params', async () => {
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      studioRequest
        .mockResolvedValueOnce({ deleted_count: 5 })
        .mockResolvedValueOnce({ snapshots: [] });

      await act(async () => {
        await result.current.bulkDeleteSnapshots({ force: true });
      });

      const deleteCall = studioRequest.mock.calls.find(
        (c: any[]) => c[1]?.method === 'DELETE' && c[0].includes('force')
      );
      expect(deleteCall).toBeDefined();
      expect(deleteCall![0]).toContain('force=true');
    });

    it('returns deleted_count from API', async () => {
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      studioRequest
        .mockResolvedValueOnce({ deleted_count: 3 })
        .mockResolvedValueOnce({ snapshots: [] });

      let apiResult: { deleted_count: number } | undefined;
      await act(async () => {
        apiResult = await result.current.bulkDeleteSnapshots({ nodeName: 'r1' });
      });

      expect(apiResult!.deleted_count).toBe(3);
    });
  });

  // ---- mapConfig ----

  describe('mapConfig', () => {
    it('calls POST on map endpoint with target_node_id', async () => {
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      const mappedSnapshot = mkSnapshot({ id: 's1-mapped', mapped_to_node_id: 'node-2' });
      studioRequest
        .mockResolvedValueOnce(mappedSnapshot)
        .mockResolvedValueOnce({ snapshots });

      await act(async () => {
        await result.current.mapConfig('s1', 'node-2');
      });

      expect(studioRequest).toHaveBeenCalledWith(
        '/labs/lab1/config-snapshots/s1/map',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ target_node_id: 'node-2' }),
        })
      );
    });
  });

  // ---- downloadConfigs ----

  describe('downloadConfigs', () => {
    it('passes nodeNames as repeated query params', async () => {
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      const mockedRawApi = vi.mocked(rawApiRequest);
      const mockBlob = new Blob(['data']);
      mockedRawApi.mockResolvedValueOnce({
        ok: true,
        blob: () => Promise.resolve(mockBlob),
        headers: new Headers({ 'Content-Disposition': 'attachment; filename=test.zip' }),
      } as any);

      await act(async () => {
        await result.current.downloadConfigs({ nodeNames: ['r1', 'sw1'] });
      });

      const calledUrl = mockedRawApi.mock.calls[0][0] as string;
      expect(calledUrl).toContain('node_name=r1');
      expect(calledUrl).toContain('node_name=sw1');
      expect(downloadBlob).toHaveBeenCalledWith(mockBlob, 'test.zip');
    });

    it('falls back to "configs.zip" when no Content-Disposition header', async () => {
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      const mockedRawApi = vi.mocked(rawApiRequest);
      const mockBlob = new Blob(['data']);
      mockedRawApi.mockResolvedValueOnce({
        ok: true,
        blob: () => Promise.resolve(mockBlob),
        headers: new Headers(),
      } as any);

      await act(async () => {
        await result.current.downloadConfigs();
      });

      expect(downloadBlob).toHaveBeenCalledWith(mockBlob, 'configs.zip');
    });

    it('throws on non-ok response with detail', async () => {
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      const mockedRawApi = vi.mocked(rawApiRequest);
      mockedRawApi.mockResolvedValueOnce({
        ok: false,
        json: () => Promise.resolve({ detail: 'No configs found' }),
      } as any);

      await expect(
        act(async () => {
          await result.current.downloadConfigs();
        })
      ).rejects.toThrow('No configs found');
    });

    it('throws generic error when response json parsing fails', async () => {
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      const mockedRawApi = vi.mocked(rawApiRequest);
      mockedRawApi.mockResolvedValueOnce({
        ok: false,
        json: () => Promise.reject(new Error('parse error')),
      } as any);

      await expect(
        act(async () => {
          await result.current.downloadConfigs();
        })
      ).rejects.toThrow('Download failed');
    });
  });

  // ---- selectNode ----

  describe('selectNode', () => {
    it('resets selectedSnapshotIds and viewMode to view', async () => {
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      act(() => {
        result.current.setViewMode('compare');
        result.current.handleSnapshotClick('s1');
      });

      act(() => result.current.selectNode('sw1'));

      expect(result.current.selectedNodeName).toBe('sw1');
      expect(result.current.selectedSnapshotIds.size).toBe(0);
      expect(result.current.viewMode).toBe('view');
    });
  });

  // ---- nodeNames merging ----

  describe('nodeNames merging', () => {
    it('merges topology nodes and snapshot-only nodes sorted', async () => {
      const { result } = renderManager({ nodes: [deviceNode, deviceNode2] });
      await waitFor(() => expect(result.current.loading).toBe(false));

      // Topology: r1, sw1. Snapshots add: orphan
      expect(result.current.nodeNames).toEqual(['orphan', 'r1', 'sw1']);
    });
  });

  // ---- deviceNodes filtering ----

  describe('deviceNodes filtering', () => {
    it('excludes external nodes from deviceNodes', async () => {
      const { result } = renderManager({ nodes: [deviceNode, externalNode] });
      await waitFor(() => expect(result.current.loading).toBe(false));

      expect(result.current.deviceNodes).toHaveLength(1);
      expect(result.current.deviceNodes[0].name).toBe('R1');
    });
  });
});
