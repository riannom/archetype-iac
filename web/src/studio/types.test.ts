import { describe, it, expect } from 'vitest';
import { isDeviceNode, isExternalNetworkNode, DeviceType } from './types';
import type { DeviceNode, ExternalNetworkNode, Node } from './types';

describe('isDeviceNode', () => {
  it('returns true for a DeviceNode with nodeType "device"', () => {
    const node: DeviceNode = {
      id: 'r1',
      name: 'Router 1',
      nodeType: 'device',
      type: DeviceType.ROUTER,
      model: 'ceos',
      version: '4.28.0F',
      x: 0,
      y: 0,
    };
    expect(isDeviceNode(node)).toBe(true);
  });

  it('returns false for an ExternalNetworkNode with nodeType "external"', () => {
    const node: ExternalNetworkNode = {
      id: 'ext1',
      name: 'External 1',
      nodeType: 'external',
      x: 0,
      y: 0,
    };
    expect(isDeviceNode(node)).toBe(false);
  });

  it('returns true for a legacy node without nodeType field', () => {
    // Legacy nodes don't have nodeType at all — backward compat branch
    const node = {
      id: 'legacy1',
      name: 'Legacy Router',
      type: DeviceType.ROUTER,
      model: 'ceos',
      version: '4.28.0F',
      x: 0,
      y: 0,
    } as unknown as Node;
    expect(isDeviceNode(node)).toBe(true);
  });

  it('returns true for a node with nodeType undefined', () => {
    const node = {
      id: 'undef1',
      name: 'Undef Node',
      nodeType: undefined,
      type: DeviceType.SWITCH,
      model: 'ceos',
      version: '4.28.0F',
      x: 0,
      y: 0,
    } as unknown as Node;
    expect(isDeviceNode(node)).toBe(true);
  });

  it('returns true for all DeviceType variants', () => {
    for (const dt of Object.values(DeviceType)) {
      if (dt === DeviceType.EXTERNAL) continue;
      const node: DeviceNode = {
        id: `${dt}-1`,
        name: `${dt} Node`,
        nodeType: 'device',
        type: dt,
        model: 'test',
        version: '1.0',
        x: 0,
        y: 0,
      };
      expect(isDeviceNode(node)).toBe(true);
    }
  });
});

describe('isExternalNetworkNode', () => {
  it('returns true for a node with nodeType "external"', () => {
    const node: ExternalNetworkNode = {
      id: 'ext1',
      name: 'External 1',
      nodeType: 'external',
      x: 0,
      y: 0,
    };
    expect(isExternalNetworkNode(node)).toBe(true);
  });

  it('returns false for a DeviceNode with nodeType "device"', () => {
    const node: DeviceNode = {
      id: 'r1',
      name: 'Router 1',
      nodeType: 'device',
      type: DeviceType.ROUTER,
      model: 'ceos',
      version: '4.28.0F',
      x: 0,
      y: 0,
    };
    expect(isExternalNetworkNode(node)).toBe(false);
  });

  it('returns false for a legacy node without nodeType field', () => {
    const node = {
      id: 'legacy1',
      name: 'Legacy Router',
      type: DeviceType.ROUTER,
      model: 'ceos',
      version: '4.28.0F',
      x: 0,
      y: 0,
    } as unknown as Node;
    expect(isExternalNetworkNode(node)).toBe(false);
  });

  it('returns false for a node with nodeType undefined', () => {
    const node = {
      id: 'undef1',
      name: 'Undef Node',
      nodeType: undefined,
      type: DeviceType.SWITCH,
      model: 'ceos',
      version: '4.28.0F',
      x: 0,
      y: 0,
    } as unknown as Node;
    expect(isExternalNetworkNode(node)).toBe(false);
  });

  it('returns true for external node with optional connection fields', () => {
    const node: ExternalNetworkNode = {
      id: 'ext2',
      name: 'VLAN Network',
      nodeType: 'external',
      x: 100,
      y: 200,
      connectionType: 'vlan',
      parentInterface: 'ens192',
      vlanId: 100,
      host: 'agent-1',
    };
    expect(isExternalNetworkNode(node)).toBe(true);
  });

  it('returns true for external node with managed interface fields', () => {
    const node: ExternalNetworkNode = {
      id: 'ext3',
      name: 'Managed Ext',
      nodeType: 'external',
      x: 0,
      y: 0,
      managedInterfaceId: 'iface-1',
      managedInterfaceName: 'eth0',
      managedInterfaceHostId: 'host-1',
      managedInterfaceHostName: 'agent-1',
    };
    expect(isExternalNetworkNode(node)).toBe(true);
  });
});
