import { describe, it, expect } from 'vitest';
import { generateContainerName, buildGraphNodes, buildGraphLinks } from './studioUtils';
import { DeviceType, DeviceModel } from './types';
import { TopologyGraph } from '../types';

// ── Test device models ──

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
    versions: ['4.30', '4.29'],
    isActive: true,
    vendor: 'Arista',
    cpu: 2,
    memory: 2048,
  },
  {
    id: 'srl',
    type: DeviceType.SWITCH,
    name: 'Nokia SR Linux',
    icon: 'fa-arrows-left-right-to-line',
    versions: ['23.10.1'],
    isActive: true,
    vendor: 'Nokia',
    cpu: 2,
    memory: 4096,
  },
];

// ── generateContainerName ──

describe('generateContainerName', () => {
  it('converts display name to lowercase', () => {
    expect(generateContainerName('Router1')).toBe('router1');
  });

  it('replaces spaces with underscores', () => {
    expect(generateContainerName('my router')).toBe('my_router');
  });

  it('replaces special characters with underscores', () => {
    expect(generateContainerName('router-1.test')).toBe('router_1_test');
  });

  it('collapses multiple underscores into one', () => {
    expect(generateContainerName('a---b...c')).toBe('a_b_c');
  });

  it('ensures name starts with a letter or underscore', () => {
    const result = generateContainerName('1router');
    expect(result).toBe('nrouter');
  });

  it('truncates name to 20 characters', () => {
    const longName = 'this_is_a_very_long_container_name';
    const result = generateContainerName(longName);
    expect(result.length).toBeLessThanOrEqual(20);
  });

  it('handles empty string', () => {
    const result = generateContainerName('');
    expect(result).toBe('');
  });

  it('handles already valid name', () => {
    expect(generateContainerName('router_1')).toBe('router_1');
  });

  it('handles name with only special characters', () => {
    const result = generateContainerName('---');
    // All replaced with underscore, collapsed to single underscore
    // Starts with underscore which is valid
    expect(result).toBe('_');
  });
});

// ── buildGraphNodes ──

describe('buildGraphNodes', () => {
  it('converts API nodes to device nodes with correct fields', () => {
    const graph: TopologyGraph = {
      nodes: [
        { id: 'r1', name: 'Router1', device: 'ceos', version: '4.30' },
      ],
      links: [],
    };

    const result = buildGraphNodes(graph, testModels);

    expect(result).toHaveLength(1);
    const node = result[0];
    expect(node.id).toBe('r1');
    expect(node.name).toBe('Router1');
    expect(node.nodeType).toBe('device');
    if (node.nodeType === 'device') {
      expect(node.model).toBe('ceos');
      expect(node.version).toBe('4.30');
      expect(node.type).toBe(DeviceType.SWITCH);
      expect(node.cpu).toBe(2);
      expect(node.memory).toBe(2048);
    }
  });

  it('returns empty array for empty graph', () => {
    const graph: TopologyGraph = { nodes: [], links: [] };
    const result = buildGraphNodes(graph, testModels);
    expect(result).toEqual([]);
  });

  it('uses node id as name when name is missing', () => {
    const graph: TopologyGraph = {
      nodes: [{ id: 'r1', name: '', device: 'linux' }],
      links: [],
    };

    const result = buildGraphNodes(graph, testModels);
    // Name fallback: node.name || node.id
    expect(result[0].name).toBe('r1');
  });

  it('handles nodes with unknown device model gracefully', () => {
    const graph: TopologyGraph = {
      nodes: [
        { id: 'r1', name: 'Unknown', device: 'nonexistent_device' },
      ],
      links: [],
    };

    const result = buildGraphNodes(graph, testModels);
    expect(result).toHaveLength(1);
    if (result[0].nodeType === 'device') {
      // Falls back to DeviceType.CONTAINER and uses modelId as model
      expect(result[0].type).toBe(DeviceType.CONTAINER);
      expect(result[0].model).toBe('nonexistent_device');
    }
  });

  it('assigns grid positions based on index', () => {
    const graph: TopologyGraph = {
      nodes: [
        { id: 'r1', name: 'R1', device: 'linux' },
        { id: 'r2', name: 'R2', device: 'linux' },
        { id: 'r3', name: 'R3', device: 'linux' },
        { id: 'r4', name: 'R4', device: 'linux' },
        { id: 'r5', name: 'R5', device: 'linux' },
        { id: 'r6', name: 'R6', device: 'linux' }, // Second row
      ],
      links: [],
    };

    const result = buildGraphNodes(graph, testModels);

    // First node: column 0, row 0
    expect(result[0].x).toBe(220 + 0 * 160);
    expect(result[0].y).toBe(180 + 0 * 140);

    // Sixth node: column 0, row 1 (wraps after 5)
    expect(result[5].x).toBe(220 + 0 * 160);
    expect(result[5].y).toBe(180 + 1 * 140);
  });

  it('preserves container_name from backend', () => {
    const graph: TopologyGraph = {
      nodes: [
        { id: 'r1', name: 'R1', device: 'linux', container_name: 'r1_custom' },
      ],
      links: [],
    };

    const result = buildGraphNodes(graph, testModels);
    if (result[0].nodeType === 'device') {
      expect(result[0].container_name).toBe('r1_custom');
    }
  });

  it('uses first model version when node version is missing', () => {
    const graph: TopologyGraph = {
      nodes: [
        { id: 'r1', name: 'R1', device: 'ceos' }, // no version
      ],
      links: [],
    };

    const result = buildGraphNodes(graph, testModels);
    if (result[0].nodeType === 'device') {
      expect(result[0].version).toBe('4.30'); // first version in ceos model
    }
  });

  it('handles external network nodes', () => {
    const graph: TopologyGraph = {
      nodes: [
        {
          id: 'ext1',
          name: 'External1',
          node_type: 'external',
          managed_interface_id: 'iface-1',
          connection_type: 'vlan',
          vlan_id: 100,
          host: 'agent-1',
        } as any,
      ],
      links: [],
    };

    const result = buildGraphNodes(graph, testModels);
    expect(result).toHaveLength(1);
    expect(result[0].nodeType).toBe('external');
    if (result[0].nodeType === 'external') {
      expect(result[0].managedInterfaceId).toBe('iface-1');
      expect(result[0].connectionType).toBe('vlan');
      expect(result[0].vlanId).toBe(100);
      expect(result[0].host).toBe('agent-1');
    }
  });

  it('falls back node.device to node.id when device is missing', () => {
    const graph: TopologyGraph = {
      nodes: [{ id: 'ceos', name: 'R1' }], // device omitted; modelMap lookup uses id
      links: [],
    };
    const result = buildGraphNodes(graph, testModels);
    if (result[0].nodeType === 'device') {
      // 'ceos' resolves through the model map even though `device` was omitted
      expect(result[0].model).toBe('ceos');
      expect(result[0].type).toBe(DeviceType.SWITCH);
    }
  });

  it('falls back external node name to id when name is empty', () => {
    const graph: TopologyGraph = {
      nodes: [{ id: 'ext-fallback', name: '', node_type: 'external' } as any],
      links: [],
    };
    const result = buildGraphNodes(graph, testModels);
    expect(result[0].name).toBe('ext-fallback');
    expect(result[0].nodeType).toBe('external');
  });

  it('handles external network nodes with omitted connection_type', () => {
    const graph: TopologyGraph = {
      nodes: [
        {
          id: 'ext1',
          name: 'External1',
          node_type: 'external',
          // connection_type intentionally missing → connectionType should be undefined
        } as any,
      ],
      links: [],
    };
    const result = buildGraphNodes(graph, testModels);
    expect(result).toHaveLength(1);
    if (result[0].nodeType === 'external') {
      expect(result[0].connectionType).toBeUndefined();
    }
  });

  it('preserves hardware spec overrides on device nodes', () => {
    const graph: TopologyGraph = {
      nodes: [
        {
          id: 'r1',
          name: 'R1',
          device: 'linux',
          cpu: 4,
          memory: 8192,
          disk_driver: 'virtio',
          nic_driver: 'e1000',
          machine_type: 'q35',
        },
      ],
      links: [],
    };

    const result = buildGraphNodes(graph, testModels);
    if (result[0].nodeType === 'device') {
      expect(result[0].cpu).toBe(4);
      expect(result[0].memory).toBe(8192);
      expect(result[0].disk_driver).toBe('virtio');
      expect(result[0].nic_driver).toBe('e1000');
      expect(result[0].machine_type).toBe('q35');
    }
  });
});

// ── buildGraphLinks ──

describe('buildGraphLinks', () => {
  it('converts API links to Link objects', () => {
    const graph: TopologyGraph = {
      nodes: [],
      links: [
        {
          endpoints: [
            { node: 'r1', ifname: 'eth1' },
            { node: 'r2', ifname: 'eth1' },
          ],
        },
      ],
    };

    const result = buildGraphLinks(graph);
    expect(result).toHaveLength(1);
    expect(result[0].source).toBe('r1');
    expect(result[0].target).toBe('r2');
    expect(result[0].sourceInterface).toBe('eth1');
    expect(result[0].targetInterface).toBe('eth1');
    expect(result[0].type).toBe('p2p');
  });

  it('returns empty array for empty links', () => {
    const graph: TopologyGraph = { nodes: [], links: [] };
    const result = buildGraphLinks(graph);
    expect(result).toEqual([]);
  });

  it('filters out links with fewer than 2 endpoints', () => {
    const graph: TopologyGraph = {
      nodes: [],
      links: [
        { endpoints: [{ node: 'r1', ifname: 'eth1' }] }, // Only 1 endpoint
        { endpoints: [] }, // Empty endpoints
      ],
    };

    const result = buildGraphLinks(graph);
    expect(result).toEqual([]);
  });

  it('generates unique link IDs based on index and node names', () => {
    const graph: TopologyGraph = {
      nodes: [],
      links: [
        {
          endpoints: [
            { node: 'r1', ifname: 'eth1' },
            { node: 'r2', ifname: 'eth1' },
          ],
        },
        {
          endpoints: [
            { node: 'r2', ifname: 'eth2' },
            { node: 'r3', ifname: 'eth1' },
          ],
        },
      ],
    };

    const result = buildGraphLinks(graph);
    expect(result[0].id).toBe('link-0-r1-r2');
    expect(result[1].id).toBe('link-1-r2-r3');
  });

  it('handles links with missing ifname (undefined interface)', () => {
    const graph: TopologyGraph = {
      nodes: [],
      links: [
        {
          endpoints: [
            { node: 'r1' }, // No ifname
            { node: 'r2' }, // No ifname
          ],
        },
      ],
    };

    const result = buildGraphLinks(graph);
    expect(result).toHaveLength(1);
    expect(result[0].sourceInterface).toBeUndefined();
    expect(result[0].targetInterface).toBeUndefined();
  });

  it('handles links with null ifname', () => {
    const graph: TopologyGraph = {
      nodes: [],
      links: [
        {
          endpoints: [
            { node: 'r1', ifname: null },
            { node: 'r2', ifname: null },
          ],
        },
      ],
    };

    const result = buildGraphLinks(graph);
    expect(result).toHaveLength(1);
    // null ifname should become undefined (via || undefined would keep null, but ifname is optional)
    // The code uses: source.ifname || undefined
    expect(result[0].sourceInterface).toBeUndefined();
    expect(result[0].targetInterface).toBeUndefined();
  });

  it('handles multiple links between the same nodes', () => {
    const graph: TopologyGraph = {
      nodes: [],
      links: [
        {
          endpoints: [
            { node: 'r1', ifname: 'eth1' },
            { node: 'r2', ifname: 'eth1' },
          ],
        },
        {
          endpoints: [
            { node: 'r1', ifname: 'eth2' },
            { node: 'r2', ifname: 'eth2' },
          ],
        },
      ],
    };

    const result = buildGraphLinks(graph);
    expect(result).toHaveLength(2);
    expect(result[0].id).not.toBe(result[1].id);
  });
});
