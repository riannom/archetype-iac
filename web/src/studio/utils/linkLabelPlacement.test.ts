import { describe, expect, it } from 'vitest';
import { DeviceType, Link, Node } from '../types';
import { computeLinkLabelPlacements } from './linkLabelPlacement';

function createDeviceNode(id: string, x: number, y: number): Node {
  return {
    id,
    name: id,
    nodeType: 'device',
    type: DeviceType.ROUTER,
    model: 'ceos',
    version: 'latest',
    x,
    y,
  };
}

function estimateRect(x: number, y: number, text: string) {
  const width = Math.max(24, text.length * 7 + 12);
  const halfWidth = width / 2;
  const halfHeight = 8;
  return {
    left: x - halfWidth,
    top: y - halfHeight,
    right: x + halfWidth,
    bottom: y + halfHeight,
  };
}

function overlaps(
  a: { left: number; right: number; top: number; bottom: number },
  b: { left: number; right: number; top: number; bottom: number }
) {
  return !(a.right <= b.left || a.left >= b.right || a.bottom <= b.top || a.top >= b.bottom);
}

describe('computeLinkLabelPlacements', () => {
  it('places labels near the link they describe', () => {
    const nodes = [
      createDeviceNode('a', 100, 100),
      createDeviceNode('b', 300, 100),
    ];
    const links: Link[] = [
      {
        id: 'l1',
        source: 'a',
        target: 'b',
        type: 'p2p',
        sourceInterface: 'eth0',
        targetInterface: 'eth1',
      },
    ];

    const placements = computeLinkLabelPlacements(nodes, links);
    const placement = placements.get('l1');

    expect(placement?.source).toBeDefined();
    expect(placement?.target).toBeDefined();
    expect(Math.abs((placement?.source?.y ?? 0) - 100)).toBeLessThan(35);
    expect(Math.abs((placement?.target?.y ?? 0) - 100)).toBeLessThan(35);
    expect((placement?.source?.x ?? 0)).toBeGreaterThan(100);
    expect((placement?.target?.x ?? 9999)).toBeLessThan(300);
  });

  it('spreads labels for high-degree endpoints to avoid overlap', () => {
    const nodes = [
      createDeviceNode('hub', 300, 300),
      createDeviceNode('n1', 500, 300),
      createDeviceNode('n2', 500, 360),
      createDeviceNode('n3', 500, 240),
      createDeviceNode('n4', 440, 420),
    ];
    const links: Link[] = [
      { id: 'l1', source: 'hub', target: 'n1', type: 'p2p', sourceInterface: 'eth0' },
      { id: 'l2', source: 'hub', target: 'n2', type: 'p2p', sourceInterface: 'eth1' },
      { id: 'l3', source: 'hub', target: 'n3', type: 'p2p', sourceInterface: 'eth2' },
      { id: 'l4', source: 'hub', target: 'n4', type: 'p2p', sourceInterface: 'eth3' },
    ];

    const placements = computeLinkLabelPlacements(nodes, links);
    const rects = links
      .map((link) => {
        const p = placements.get(link.id)?.source;
        if (!p || !link.sourceInterface) return null;
        return estimateRect(p.x, p.y, link.sourceInterface);
      })
      .filter(Boolean) as Array<{ left: number; right: number; top: number; bottom: number }>;

    for (let i = 0; i < rects.length; i += 1) {
      for (let j = i + 1; j < rects.length; j += 1) {
        expect(overlaps(rects[i], rects[j])).toBe(false);
      }
    }
  });

  it('avoids placing labels on top of nodes when possible', () => {
    const nodes = [
      createDeviceNode('a', 100, 100),
      createDeviceNode('b', 220, 100),
      createDeviceNode('blocker', 160, 100),
    ];
    const links: Link[] = [
      {
        id: 'l1',
        source: 'a',
        target: 'b',
        type: 'p2p',
        sourceInterface: 'very-long-interface-name',
      },
    ];

    const placements = computeLinkLabelPlacements(nodes, links);
    const source = placements.get('l1')?.source;
    expect(source).toBeDefined();

    const blockerRect = {
      left: 160 - 24,
      top: 100 - 24,
      right: 160 + 24,
      bottom: 100 + 24,
    };
    const labelRect = estimateRect(source!.x, source!.y, 'very-long-interface-name');

    expect(overlaps(blockerRect, labelRect)).toBe(false);
  });

  it('is deterministic for the same input', () => {
    const nodes = [
      createDeviceNode('a', 100, 100),
      createDeviceNode('b', 300, 140),
      createDeviceNode('c', 220, 280),
    ];
    const links: Link[] = [
      { id: 'l1', source: 'a', target: 'b', type: 'p2p', sourceInterface: 'eth0', targetInterface: 'eth1' },
      { id: 'l2', source: 'b', target: 'c', type: 'p2p', sourceInterface: 'eth2', targetInterface: 'eth3' },
      { id: 'l3', source: 'a', target: 'c', type: 'p2p', sourceInterface: 'eth4', targetInterface: 'eth5' },
    ];

    const first = computeLinkLabelPlacements(nodes, links);
    const second = computeLinkLabelPlacements(nodes, links);

    const toSerializable = (map: Map<string, { source?: { x: number; y: number }; target?: { x: number; y: number } }>) =>
      Array.from(map.entries()).sort(([a], [b]) => a.localeCompare(b));

    expect(toSerializable(first)).toEqual(toSerializable(second));
  });
});
