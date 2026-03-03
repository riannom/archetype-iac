import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useLabTopology } from './useLabTopology';
import { DeviceType, DeviceModel, Node, Link } from '../types';

// ── Test data factories ──

const testModels: DeviceModel[] = [
  {
    id: 'linux',
    type: DeviceType.HOST,
    name: 'Linux',
    icon: 'fa-terminal',
    versions: ['latest'],
    isActive: true,
    vendor: 'Open Source',
    cpu: 1,
    memory: 512,
  },
  {
    id: 'ceos',
    type: DeviceType.SWITCH,
    name: 'Arista EOS',
    icon: 'fa-arrows-left-right-to-line',
    versions: ['4.30'],
    isActive: true,
    vendor: 'Arista',
    cpu: 2,
    memory: 2048,
  },
];

const createActiveLab = (id = 'lab-1', name = 'Test Lab') => ({
  id,
  name,
  created_at: '2024-01-01T00:00:00Z',
});

const sampleGraph = {
  nodes: [
    { id: 'r1', name: 'Router1', device: 'ceos', version: '4.30' },
    { id: 'r2', name: 'Router2', device: 'linux' },
  ],
  links: [
    {
      endpoints: [
        { node: 'r1', ifname: 'eth1' },
        { node: 'r2', ifname: 'eth1' },
      ],
    },
  ],
};

const sampleLayout = {
  version: 1,
  nodes: {
    r1: { x: 300, y: 400 },
    r2: { x: 500, y: 400, label: 'Custom Label' },
  },
  annotations: [],
};

// ── Test setup ──

describe('useLabTopology', () => {
  let mockStudioRequest: ReturnType<typeof vi.fn>;
  let mockAddTaskLogEntry: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    mockStudioRequest = vi.fn();
    mockAddTaskLogEntry = vi.fn();
  });

  const renderTopologyHook = (
    activeLab: { id: string; name: string } | null = createActiveLab(),
    deviceModels: DeviceModel[] = testModels,
  ) => {
    return renderHook(
      ({ activeLab: currentLab, deviceModels: models }) =>
        useLabTopology({
          activeLab: currentLab,
          deviceModels: models,
          studioRequest: mockStudioRequest,
          addTaskLogEntry: mockAddTaskLogEntry,
        }),
      {
        initialProps: { activeLab, deviceModels },
      },
    );
  };

  // ── Loading graph ──

  describe('loadGraph', () => {
    it('loads graph and layout when activeLab is set', async () => {
      mockStudioRequest
        .mockResolvedValueOnce(sampleGraph)
        .mockResolvedValueOnce(sampleLayout);

      const { result } = renderTopologyHook();

      await waitFor(() => {
        expect(result.current.nodes).toHaveLength(2);
      });

      expect(result.current.links).toHaveLength(1);
      expect(mockStudioRequest).toHaveBeenCalledWith('/labs/lab-1/export-graph');
      expect(mockStudioRequest).toHaveBeenCalledWith('/labs/lab-1/layout');
    });

    it('applies layout positions to nodes', async () => {
      mockStudioRequest
        .mockResolvedValueOnce(sampleGraph)
        .mockResolvedValueOnce(sampleLayout);

      const { result } = renderTopologyHook();

      await waitFor(() => {
        expect(result.current.nodes).toHaveLength(2);
      });

      const r1 = result.current.nodes.find((n) => n.id === 'r1');
      expect(r1?.x).toBe(300);
      expect(r1?.y).toBe(400);

      const r2 = result.current.nodes.find((n) => n.id === 'r2');
      expect(r2?.x).toBe(500);
      expect(r2?.y).toBe(400);
      expect(r2?.label).toBe('Custom Label');
    });

    it('uses default grid positions when no layout exists', async () => {
      mockStudioRequest
        .mockResolvedValueOnce(sampleGraph)
        .mockRejectedValueOnce(new Error('Not found'));

      const { result } = renderTopologyHook();

      await waitFor(() => {
        expect(result.current.nodes).toHaveLength(2);
      });

      // Default grid positions: 220 + column * 160
      expect(result.current.nodes[0].x).toBe(220);
      expect(result.current.nodes[0].y).toBe(180);
    });

    it('clears state when graph load fails', async () => {
      mockStudioRequest.mockRejectedValue(new Error('Network error'));

      const { result } = renderTopologyHook();

      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalled();
      });

      expect(result.current.nodes).toEqual([]);
      expect(result.current.links).toEqual([]);
      expect(result.current.annotations).toEqual([]);
    });

    it('does not load graph when activeLab is null', () => {
      renderTopologyHook(null);

      expect(mockStudioRequest).not.toHaveBeenCalled();
    });
  });

  // ── Node management ──

  describe('node management', () => {
    it('exposes setNodes for adding a node', async () => {
      mockStudioRequest
        .mockResolvedValueOnce({ nodes: [], links: [] })
        .mockResolvedValueOnce(null);

      const { result } = renderTopologyHook();

      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalled();
      });

      const newNode: Node = {
        id: 'new-1',
        name: 'NewRouter',
        nodeType: 'device',
        type: DeviceType.ROUTER,
        model: 'ceos',
        version: '4.30',
        x: 100,
        y: 200,
      };

      act(() => {
        result.current.setNodes((prev) => [...prev, newNode]);
      });

      expect(result.current.nodes).toHaveLength(1);
      expect(result.current.nodes[0].id).toBe('new-1');
    });

    it('exposes setNodes for deleting a node', async () => {
      mockStudioRequest
        .mockResolvedValueOnce(sampleGraph)
        .mockResolvedValueOnce(null);

      const { result } = renderTopologyHook();

      await waitFor(() => {
        expect(result.current.nodes).toHaveLength(2);
      });

      act(() => {
        result.current.setNodes((prev) => prev.filter((n) => n.id !== 'r1'));
      });

      expect(result.current.nodes).toHaveLength(1);
      expect(result.current.nodes[0].id).toBe('r2');
    });

    it('exposes setNodes for updating a node', async () => {
      mockStudioRequest
        .mockResolvedValueOnce(sampleGraph)
        .mockResolvedValueOnce(null);

      const { result } = renderTopologyHook();

      await waitFor(() => {
        expect(result.current.nodes).toHaveLength(2);
      });

      act(() => {
        result.current.setNodes((prev) =>
          prev.map((n) => (n.id === 'r1' ? { ...n, name: 'UpdatedRouter' } : n)),
        );
      });

      expect(result.current.nodes.find((n) => n.id === 'r1')?.name).toBe('UpdatedRouter');
    });
  });

  // ── Link management ──

  describe('link management', () => {
    it('exposes setLinks for adding a link', async () => {
      mockStudioRequest
        .mockResolvedValueOnce({ nodes: [], links: [] })
        .mockResolvedValueOnce(null);

      const { result } = renderTopologyHook();

      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalled();
      });

      const newLink: Link = {
        id: 'link-new',
        source: 'r1',
        target: 'r2',
        type: 'p2p',
        sourceInterface: 'eth1',
        targetInterface: 'eth1',
      };

      act(() => {
        result.current.setLinks((prev) => [...prev, newLink]);
      });

      expect(result.current.links).toHaveLength(1);
      expect(result.current.links[0].source).toBe('r1');
    });

    it('exposes setLinks for deleting a link', async () => {
      mockStudioRequest
        .mockResolvedValueOnce(sampleGraph)
        .mockResolvedValueOnce(null);

      const { result } = renderTopologyHook();

      await waitFor(() => {
        expect(result.current.links).toHaveLength(1);
      });

      act(() => {
        result.current.setLinks([]);
      });

      expect(result.current.links).toHaveLength(0);
    });
  });

  // ── Topology save ──

  describe('saveTopology', () => {
    it('posts graph data to update-topology endpoint', async () => {
      mockStudioRequest
        .mockResolvedValueOnce(sampleGraph)
        .mockResolvedValueOnce(null)
        .mockResolvedValueOnce(undefined);

      const { result } = renderTopologyHook();

      await waitFor(() => {
        expect(result.current.nodes).toHaveLength(2);
      });

      await act(async () => {
        await result.current.saveTopology('lab-1', result.current.nodes, result.current.links);
      });

      const call = mockStudioRequest.mock.calls.find(
        (c) => c[0] === '/labs/lab-1/update-topology',
      );
      expect(call).toBeDefined();
      expect(call?.[1]?.method).toBe('POST');

      const body = JSON.parse(call?.[1]?.body as string);
      expect(body.nodes).toHaveLength(2);
      expect(body.links).toHaveLength(1);
    });

    it('serializes device nodes with model and version', async () => {
      mockStudioRequest
        .mockResolvedValueOnce(sampleGraph)
        .mockResolvedValueOnce(null)
        .mockResolvedValueOnce(undefined);

      const { result } = renderTopologyHook();

      await waitFor(() => {
        expect(result.current.nodes).toHaveLength(2);
      });

      await act(async () => {
        await result.current.saveTopology('lab-1', result.current.nodes, result.current.links);
      });

      const call = mockStudioRequest.mock.calls.find(
        (c) => c[0] === '/labs/lab-1/update-topology',
      );
      const body = JSON.parse(call?.[1]?.body as string);
      const ceosNode = body.nodes.find((n: any) => n.id === 'r1');
      expect(ceosNode.device).toBe('ceos');
      expect(ceosNode.version).toBe('4.30');
      expect(ceosNode.node_type).toBe('device');
    });

    it('serializes link endpoints correctly', async () => {
      mockStudioRequest
        .mockResolvedValueOnce(sampleGraph)
        .mockResolvedValueOnce(null)
        .mockResolvedValueOnce(undefined);

      const { result } = renderTopologyHook();

      await waitFor(() => {
        expect(result.current.nodes).toHaveLength(2);
      });

      await act(async () => {
        await result.current.saveTopology('lab-1', result.current.nodes, result.current.links);
      });

      const call = mockStudioRequest.mock.calls.find(
        (c) => c[0] === '/labs/lab-1/update-topology',
      );
      const body = JSON.parse(call?.[1]?.body as string);
      expect(body.links[0].endpoints).toHaveLength(2);
      expect(body.links[0].endpoints[0].node).toBe('r1');
      expect(body.links[0].endpoints[1].node).toBe('r2');
    });

    it('logs success message on save', async () => {
      mockStudioRequest
        .mockResolvedValueOnce(sampleGraph)
        .mockResolvedValueOnce(null)
        .mockResolvedValueOnce(undefined);

      const { result } = renderTopologyHook();

      await waitFor(() => {
        expect(result.current.nodes).toHaveLength(2);
      });

      await act(async () => {
        await result.current.saveTopology('lab-1', result.current.nodes, result.current.links);
      });

      expect(mockAddTaskLogEntry).toHaveBeenCalledWith('info', 'Topology auto-saved');
    });

    it('does not save when nodes array is empty', async () => {
      mockStudioRequest.mockResolvedValue(undefined);

      const { result } = renderTopologyHook();

      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalled();
      });

      const callCountBefore = mockStudioRequest.mock.calls.length;

      await act(async () => {
        await result.current.saveTopology('lab-1', [], []);
      });

      expect(mockStudioRequest.mock.calls.length).toBe(callCountBefore);
    });

    it('handles save error without throwing by default', async () => {
      mockStudioRequest
        .mockResolvedValueOnce({ nodes: [{ id: 'r1', name: 'R1', device: 'linux' }], links: [] })
        .mockResolvedValueOnce(null)
        .mockRejectedValueOnce(new Error('Server error'));

      const { result } = renderTopologyHook();

      await waitFor(() => {
        expect(result.current.nodes).toHaveLength(1);
      });

      // Should not throw
      await act(async () => {
        await result.current.saveTopology('lab-1', result.current.nodes, result.current.links);
      });
    });

    it('rethrows error when rethrowOnError is true', async () => {
      mockStudioRequest
        .mockResolvedValueOnce({ nodes: [{ id: 'r1', name: 'R1', device: 'linux' }], links: [] })
        .mockResolvedValueOnce(null)
        .mockRejectedValueOnce(new Error('Server error'));

      const { result } = renderTopologyHook();

      await waitFor(() => {
        expect(result.current.nodes).toHaveLength(1);
      });

      await expect(
        act(async () => {
          await result.current.saveTopology('lab-1', result.current.nodes, result.current.links, true);
        }),
      ).rejects.toThrow('Server error');
    });
  });

  // ── Debounced topology save ──

  describe('triggerTopologySave', () => {
    it('does nothing when activeLab is null', () => {
      vi.useFakeTimers();
      try {
        const { result } = renderTopologyHook(null);

        act(() => {
          result.current.triggerTopologySave();
        });

        act(() => {
          vi.advanceTimersByTime(3000);
        });

        expect(mockStudioRequest).not.toHaveBeenCalled();
      } finally {
        vi.useRealTimers();
      }
    });

    it('marks topology as dirty when triggered', async () => {
      mockStudioRequest
        .mockResolvedValueOnce(sampleGraph)
        .mockResolvedValueOnce(null);

      const { result } = renderTopologyHook();

      await waitFor(() => {
        expect(result.current.nodes).toHaveLength(2);
      });

      // After triggerTopologySave, flushing should save (proving dirty was set)
      mockStudioRequest.mockResolvedValue(undefined);

      act(() => {
        result.current.triggerTopologySave();
      });

      await act(async () => {
        await result.current.flushTopologySave();
      });

      const saveCalls = mockStudioRequest.mock.calls.filter(
        (c) => c[0]?.includes('update-topology'),
      );
      expect(saveCalls.length).toBe(1);
    });
  });

  // ── Flush topology save ──

  describe('flushTopologySave', () => {
    it('immediately saves when dirty', async () => {
      mockStudioRequest
        .mockResolvedValueOnce(sampleGraph)
        .mockResolvedValueOnce(null);

      const { result } = renderTopologyHook();

      await waitFor(() => {
        expect(result.current.nodes).toHaveLength(2);
      });

      mockStudioRequest.mockResolvedValue(undefined);

      act(() => {
        result.current.triggerTopologySave();
      });

      await act(async () => {
        await result.current.flushTopologySave();
      });

      const saveCalls = mockStudioRequest.mock.calls.filter(
        (c) => c[0]?.includes('update-topology'),
      );
      expect(saveCalls.length).toBe(1);
    });

    it('does nothing when not dirty', async () => {
      mockStudioRequest
        .mockResolvedValueOnce(sampleGraph)
        .mockResolvedValueOnce(null);

      const { result } = renderTopologyHook();

      await waitFor(() => {
        expect(result.current.nodes).toHaveLength(2);
      });

      const callCountBefore = mockStudioRequest.mock.calls.length;

      await act(async () => {
        await result.current.flushTopologySave();
      });

      expect(mockStudioRequest.mock.calls.length).toBe(callCountBefore);
    });

    it('does nothing when activeLab is null', async () => {
      const { result } = renderTopologyHook(null);

      await act(async () => {
        await result.current.flushTopologySave();
      });

      expect(mockStudioRequest).not.toHaveBeenCalled();
    });
  });

  // ── Layout save ──

  describe('saveLayout', () => {
    it('sends layout data to layout endpoint', async () => {
      mockStudioRequest
        .mockResolvedValueOnce(sampleGraph)
        .mockResolvedValueOnce(null)
        .mockResolvedValueOnce(undefined);

      const { result } = renderTopologyHook();

      await waitFor(() => {
        expect(result.current.nodes).toHaveLength(2);
      });

      await act(async () => {
        await result.current.saveLayout('lab-1', result.current.nodes, result.current.annotations);
      });

      const putCall = mockStudioRequest.mock.calls.find(
        (c) => c[0] === '/labs/lab-1/layout' && c[1]?.method === 'PUT',
      );
      expect(putCall).toBeDefined();

      const body = JSON.parse(putCall?.[1]?.body as string);
      expect(body.version).toBe(1);
      expect(body.nodes).toBeDefined();
    });

    it('includes node positions in layout payload', async () => {
      mockStudioRequest
        .mockResolvedValueOnce(sampleGraph)
        .mockResolvedValueOnce(sampleLayout)
        .mockResolvedValueOnce(undefined);

      const { result } = renderTopologyHook();

      await waitFor(() => {
        expect(result.current.nodes).toHaveLength(2);
      });

      await act(async () => {
        await result.current.saveLayout('lab-1', result.current.nodes, result.current.annotations);
      });

      const putCall = mockStudioRequest.mock.calls.find(
        (c) => c[0] === '/labs/lab-1/layout' && c[1]?.method === 'PUT',
      );
      const body = JSON.parse(putCall?.[1]?.body as string);
      expect(body.nodes['r1']).toBeDefined();
      expect(body.nodes['r1'].x).toBe(300);
      expect(body.nodes['r1'].y).toBe(400);
    });

    it('does not save layout when nodes array is empty', async () => {
      mockStudioRequest.mockResolvedValue(undefined);

      const { result } = renderTopologyHook();

      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalled();
      });

      const callCountBefore = mockStudioRequest.mock.calls.length;

      await act(async () => {
        await result.current.saveLayout('lab-1', [], []);
      });

      expect(mockStudioRequest.mock.calls.length).toBe(callCountBefore);
    });
  });

  // ── Annotation management ──

  describe('annotations', () => {
    it('restores annotations from layout', async () => {
      const layoutWithAnnotations = {
        ...sampleLayout,
        annotations: [
          {
            id: 'ann-1',
            type: 'text',
            x: 100,
            y: 100,
            text: 'Hello',
            fontSize: 14,
          },
          {
            id: 'ann-2',
            type: 'rect',
            x: 200,
            y: 200,
            width: 100,
            height: 50,
            color: '#ff0000',
          },
        ],
      };

      mockStudioRequest
        .mockResolvedValueOnce(sampleGraph)
        .mockResolvedValueOnce(layoutWithAnnotations);

      const { result } = renderTopologyHook();

      await waitFor(() => {
        expect(result.current.annotations).toHaveLength(2);
      });

      expect(result.current.annotations[0].id).toBe('ann-1');
      expect(result.current.annotations[0].text).toBe('Hello');
      expect(result.current.annotations[1].type).toBe('rect');
    });

    it('converts caption annotation type to text', async () => {
      const layoutWithCaption = {
        ...sampleLayout,
        annotations: [
          {
            id: 'ann-1',
            type: 'caption',
            x: 100,
            y: 100,
            text: 'Caption text',
          },
        ],
      };

      mockStudioRequest
        .mockResolvedValueOnce(sampleGraph)
        .mockResolvedValueOnce(layoutWithCaption);

      const { result } = renderTopologyHook();

      await waitFor(() => {
        expect(result.current.annotations).toHaveLength(1);
      });

      expect(result.current.annotations[0].type).toBe('text');
    });

    it('clears annotations when layout has none', async () => {
      mockStudioRequest
        .mockResolvedValueOnce(sampleGraph)
        .mockResolvedValueOnce({ ...sampleLayout, annotations: [] });

      const { result } = renderTopologyHook();

      await waitFor(() => {
        expect(result.current.nodes).toHaveLength(2);
      });

      expect(result.current.annotations).toEqual([]);
    });

    it('exposes setAnnotations for direct manipulation', async () => {
      mockStudioRequest
        .mockResolvedValueOnce({ nodes: [], links: [] })
        .mockResolvedValueOnce(null);

      const { result } = renderTopologyHook();

      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalled();
      });

      act(() => {
        result.current.setAnnotations([
          { id: 'a1', type: 'text', x: 50, y: 50, text: 'Test' },
        ]);
      });

      expect(result.current.annotations).toHaveLength(1);
      expect(result.current.annotations[0].text).toBe('Test');
    });
  });

  // ── Cleanup ──

  describe('cleanup', () => {
    it('clears timeouts on unmount', async () => {
      vi.useFakeTimers();
      try {
        // With fake timers, mock promises resolve when we advance timers
        mockStudioRequest.mockImplementation(() => Promise.resolve({ nodes: [], links: [] }));

        const { result, unmount } = renderTopologyHook();

        // Advance to let the initial load fire
        await act(async () => {
          await vi.advanceTimersByTimeAsync(10);
        });

        mockStudioRequest.mockResolvedValue(undefined);

        // Trigger debounced saves
        act(() => {
          result.current.triggerLayoutSave();
          result.current.triggerTopologySave();
        });

        // Unmount before timeouts fire
        unmount();

        const callCountBefore = mockStudioRequest.mock.calls.length;

        // Advance time past debounce delays
        act(() => {
          vi.advanceTimersByTime(5000);
        });

        // Unmount should have cleared timeouts, so no extra calls
        expect(mockStudioRequest.mock.calls.length).toBe(callCountBefore);
      } finally {
        vi.useRealTimers();
      }
    });
  });

  // ── Lab switching ──

  describe('lab switching', () => {
    it('reloads graph when activeLab changes', async () => {
      mockStudioRequest
        .mockResolvedValueOnce(sampleGraph)
        .mockResolvedValueOnce(null);

      const { result, rerender } = renderTopologyHook(createActiveLab('lab-1'));

      await waitFor(() => {
        expect(result.current.nodes).toHaveLength(2);
      });

      const newGraph = {
        nodes: [{ id: 's1', name: 'Switch1', device: 'ceos' }],
        links: [],
      };

      mockStudioRequest
        .mockResolvedValueOnce(newGraph)
        .mockResolvedValueOnce(null);

      rerender({
        activeLab: createActiveLab('lab-2', 'Lab 2'),
        deviceModels: testModels,
      });

      await waitFor(() => {
        expect(result.current.nodes).toHaveLength(1);
      });

      expect(result.current.nodes[0].id).toBe('s1');
    });
  });

  // ── Refs ──

  describe('refs', () => {
    it('exposes nodesRef and linksRef for current state access', async () => {
      mockStudioRequest
        .mockResolvedValueOnce(sampleGraph)
        .mockResolvedValueOnce(null);

      const { result } = renderTopologyHook();

      await waitFor(() => {
        expect(result.current.nodes).toHaveLength(2);
      });

      // Refs should mirror state
      expect(result.current.nodesRef.current).toHaveLength(2);
      expect(result.current.linksRef.current).toHaveLength(1);
    });

    it('refs update when state changes', async () => {
      mockStudioRequest
        .mockResolvedValueOnce(sampleGraph)
        .mockResolvedValueOnce(null);

      const { result } = renderTopologyHook();

      await waitFor(() => {
        expect(result.current.nodes).toHaveLength(2);
      });

      act(() => {
        result.current.setNodes((prev) => prev.filter((n) => n.id !== 'r1'));
      });

      expect(result.current.nodesRef.current).toHaveLength(1);
    });
  });
});
