import { afterEach, describe, expect, it } from 'vitest';

import { useCanvasStore } from './canvasStore';
import { DeviceType } from '../types';

const nodeA = {
  id: 'n1',
  name: 'Node 1',
  x: 0,
  y: 0,
  nodeType: 'device' as const,
  type: DeviceType.ROUTER,
  model: 'iosxr',
  version: '1.0',
};

const nodeB = {
  id: 'n2',
  name: 'Node 2',
  x: 10,
  y: 10,
  nodeType: 'device' as const,
  type: DeviceType.ROUTER,
  model: 'iosxr',
  version: '1.0',
};

const link = {
  id: 'l1',
  source: 'n1',
  target: 'n2',
  type: 'p2p' as const,
};

afterEach(() => {
  useCanvasStore.getState().reset();
});

describe('canvasStore', () => {
  it('adds nodes and marks dirty', () => {
    const store = useCanvasStore.getState();
    store.addNode(nodeA);

    expect(store.nodes.size).toBe(1);
    expect(store.isDirty).toBe(true);
  });

  it('removes node and connected links', () => {
    const store = useCanvasStore.getState();
    store.addNode(nodeA);
    store.addNode(nodeB);
    store.addLink(link);

    store.removeNode('n1');

    expect(store.nodes.has('n1')).toBe(false);
    expect(store.links.size).toBe(0);
  });

  it('resets state when lab changes', () => {
    const store = useCanvasStore.getState();
    store.addNode(nodeA);
    store.selectNode('n1');

    store.setLabId('lab-1');

    expect(store.nodes.size).toBe(0);
    expect(store.selectedNodeIds.size).toBe(0);
  });
});
