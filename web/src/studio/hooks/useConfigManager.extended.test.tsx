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
];

describe('useConfigManager extended', () => {
  let studioRequest: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();
    studioRequest = vi.fn().mockResolvedValue({ snapshots });
  });

  function renderManager(nodes: Node[] = [deviceNode]) {
    return renderHook(() =>
      useConfigManager({
        labId: 'lab1',
        nodes,
        studioRequest,
      })
    );
  }

  // ---- setActiveConfig ----

  describe('setActiveConfig', () => {
    it('calls PUT on active-config endpoint and refreshes snapshots', async () => {
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      const callsBefore = studioRequest.mock.calls.length;
      studioRequest
        .mockResolvedValueOnce({ success: true, message: 'ok' }) // setActiveConfig
        .mockResolvedValueOnce({ snapshots }); // refresh after set

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
      // 2 new calls: setActiveConfig + refresh
      expect(studioRequest.mock.calls.length - callsBefore).toBe(2);
    });

    it('propagates errors from setActiveConfig', async () => {
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      studioRequest.mockRejectedValueOnce(new Error('Config conflict'));

      await expect(result.current.setActiveConfig('r1', 's1')).rejects.toThrow(
        'Config conflict'
      );
    });
  });

  // ---- deleteSnapshot ----

  describe('deleteSnapshot', () => {
    it('calls DELETE and removes from selectedSnapshotIds', async () => {
      studioRequest
        .mockResolvedValueOnce({ snapshots }) // initial load
        .mockResolvedValueOnce({}) // delete
        .mockResolvedValueOnce({ snapshots: [snapshots[1]] }); // refresh

      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      // Select s1
      act(() => {
        result.current.handleSnapshotClick('s1');
      });
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

    it('refreshes snapshots after deletion', async () => {
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      const callsBefore = studioRequest.mock.calls.length;
      studioRequest
        .mockResolvedValueOnce({}) // delete
        .mockResolvedValueOnce({ snapshots: [] }); // refresh

      await act(async () => {
        await result.current.deleteSnapshot('s1');
      });

      // 2 new calls: delete + refresh
      expect(studioRequest.mock.calls.length - callsBefore).toBe(2);
    });
  });

  // ---- bulkDeleteSnapshots ----

  describe('bulkDeleteSnapshots', () => {
    it('includes node_name in query params', async () => {
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      studioRequest
        .mockResolvedValueOnce({ deleted_count: 2 }) // bulk delete
        .mockResolvedValueOnce({ snapshots: [] }); // refresh

      await act(async () => {
        await result.current.bulkDeleteSnapshots({ nodeName: 'r1' });
      });

      // Find the bulk delete call (has method: DELETE and node_name in URL)
      const deleteCall = studioRequest.mock.calls.find(
        (c: any[]) => c[1]?.method === 'DELETE' && c[0].includes('node_name')
      );
      expect(deleteCall).toBeDefined();
      expect(deleteCall![0]).toContain('node_name=r1');
    });

    it('includes orphaned_only flag', async () => {
      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      studioRequest
        .mockResolvedValueOnce({ deleted_count: 1 }) // bulk delete
        .mockResolvedValueOnce({ snapshots: [] }); // refresh

      await act(async () => {
        await result.current.bulkDeleteSnapshots({ orphanedOnly: true });
      });

      const deleteCall = studioRequest.mock.calls.find(
        (c: any[]) => c[1]?.method === 'DELETE' && c[0].includes('orphaned_only')
      );
      expect(deleteCall).toBeDefined();
      expect(deleteCall![0]).toContain('orphaned_only=true');
    });
  });

  // ---- downloadConfigs ----

  describe('downloadConfigs', () => {
    it('uses rawApiRequest and calls downloadBlob on success', async () => {
      studioRequest.mockResolvedValueOnce({ snapshots }); // initial load

      const mockBlob = new Blob(['zip-data']);
      const mockedRawApi = vi.mocked(rawApiRequest);
      mockedRawApi.mockResolvedValueOnce({
        ok: true,
        blob: () => Promise.resolve(mockBlob),
        headers: new Headers({ 'Content-Disposition': 'attachment; filename=configs.zip' }),
      } as any);

      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      await act(async () => {
        await result.current.downloadConfigs({ all: true });
      });

      expect(mockedRawApi).toHaveBeenCalledWith(
        expect.stringContaining('/v1/labs/lab1/config-snapshots/download')
      );
      expect(downloadBlob).toHaveBeenCalledWith(mockBlob, 'configs.zip');
    });

    it('throws on non-ok response', async () => {
      studioRequest.mockResolvedValueOnce({ snapshots }); // initial load

      const mockedRawApi = vi.mocked(rawApiRequest);
      mockedRawApi.mockResolvedValueOnce({
        ok: false,
        json: () => Promise.resolve({ detail: 'No configs found' }),
      } as any);

      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      await expect(
        act(async () => {
          await result.current.downloadConfigs();
        })
      ).rejects.toThrow('No configs found');
    });
  });

  // ---- selectNode ----

  describe('selectNode', () => {
    it('resets selectedSnapshotIds and viewMode', async () => {
      studioRequest.mockResolvedValueOnce({ snapshots }); // initial load

      const { result } = renderManager();
      await waitFor(() => expect(result.current.loading).toBe(false));

      // Set up some state first
      act(() => {
        result.current.setViewMode('compare');
        result.current.handleSnapshotClick('s1');
      });
      expect(result.current.selectedSnapshotIds.size).toBeGreaterThan(0);
      expect(result.current.viewMode).toBe('compare');

      // Now select a node
      act(() => {
        result.current.selectNode('r1');
      });

      expect(result.current.selectedNodeName).toBe('r1');
      expect(result.current.selectedSnapshotIds.size).toBe(0);
      expect(result.current.viewMode).toBe('view');
    });
  });
});
