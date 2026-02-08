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
    useCanvasStore.getState().addNode(nodeA);

    const store = useCanvasStore.getState();
    expect(store.nodes.size).toBe(1);
    expect(store.isDirty).toBe(true);
  });

  it('removes node and connected links', () => {
    const { addNode, addLink, removeNode } = useCanvasStore.getState();
    addNode(nodeA);
    addNode(nodeB);
    addLink(link);

    removeNode('n1');

    const store = useCanvasStore.getState();
    expect(store.nodes.has('n1')).toBe(false);
    expect(store.links.size).toBe(0);
  });

  it('resets state when lab changes', () => {
    const { addNode, selectNode, setLabId } = useCanvasStore.getState();
    addNode(nodeA);
    selectNode('n1');

    setLabId('lab-1');

    const store = useCanvasStore.getState();
    expect(store.nodes.size).toBe(0);
    expect(store.selectedNodeIds.size).toBe(0);
  });
});
