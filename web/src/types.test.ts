import type { TopologyGraph } from './types';

const graph: TopologyGraph = {
  nodes: [{ id: 'n1', name: 'Node 1' }],
  links: [{ endpoints: [{ node: 'n1' }, { node: 'n2' }] }],
};

describe('types', () => {
  it('accepts a topology graph shape', () => {
    expect(graph.nodes[0].id).toBe('n1');
  });
});
