import { describe, it, expect, beforeAll } from 'vitest';
import { DeviceModel, DeviceType } from '../types';
import {
  initializePatterns,
  setRuntimeAliases,
  getAvailableInterfaces,
} from './interfaceRegistry';

// Test device models matching vendor catalog patterns
const testDeviceModels: DeviceModel[] = [
  {
    id: 'ceos',
    type: DeviceType.SWITCH,
    name: 'Arista cEOS',
    icon: 'fa-arrows-left-right-to-line',
    versions: ['latest'],
    isActive: true,
    vendor: 'Arista',
    kind: 'ceos',
    portNaming: 'Ethernet',
    portStartIndex: 1,
    maxPorts: 64,
    managementInterface: 'Management0',
  },
  {
    id: 'nokia_srlinux',
    type: DeviceType.SWITCH,
    name: 'Nokia SR Linux',
    icon: 'fa-arrows-left-right-to-line',
    versions: ['latest'],
    isActive: true,
    vendor: 'Nokia',
    kind: 'nokia_srlinux',
    portNaming: 'e1-',
    portStartIndex: 1,
    maxPorts: 34,
    managementInterface: 'mgmt0',
  },
  {
    id: 'linux',
    type: DeviceType.HOST,
    name: 'Linux',
    icon: 'fa-terminal',
    versions: ['latest'],
    isActive: true,
    vendor: 'Open Source',
    portNaming: 'eth',
    portStartIndex: 1,
    maxPorts: 32,
    managementInterface: 'eth0',
  },
  {
    id: 'cisco_iosxr',
    type: DeviceType.ROUTER,
    name: 'Cisco IOS-XR',
    icon: 'fa-arrows-to-dot',
    versions: [],
    isActive: true,
    vendor: 'Cisco',
    kind: 'cisco_iosxr',
    portNaming: 'GigabitEthernet0/0/0/{index}',
    portStartIndex: 0,
    maxPorts: 8,
    managementInterface: 'MgmtEth0/RP0/CPU0/0',
  },
];

beforeAll(() => {
  initializePatterns(testDeviceModels);
});

describe('getAvailableInterfaces', () => {
  it('data ports fill count first, management appended if room remains', () => {
    // count=5 with 64 maxPorts: all 5 slots filled by data, no room for management
    const interfaces = getAvailableInterfaces('ceos', new Set(), 5);
    expect(interfaces).toEqual([
      'Ethernet1',
      'Ethernet2',
      'Ethernet3',
      'Ethernet4',
      'Ethernet5',
    ]);
  });

  it('management appended when most data ports are used', () => {
    // Use all but 2 data ports from a small pool — management fills last slot
    const used = new Set<string>();
    for (let i = 1; i <= 62; i++) used.add(`Ethernet${i}`);
    const interfaces = getAvailableInterfaces('ceos', used, 5);
    // Only Ethernet63, Ethernet64 remain, plus Management0
    expect(interfaces).toEqual(['Ethernet63', 'Ethernet64', 'Management0']);
  });

  it('excludes management when already used', () => {
    const used = new Set<string>();
    for (let i = 1; i <= 64; i++) used.add(`Ethernet${i}`);
    used.add('Management0');
    const interfaces = getAvailableInterfaces('ceos', used, 3);
    expect(interfaces).toEqual([]);
  });

  it('excludes used data ports', () => {
    const used = new Set(['Ethernet1', 'Ethernet3']);
    const interfaces = getAvailableInterfaces('ceos', used, 3);
    expect(interfaces[0]).toBe('Ethernet2');
    expect(interfaces[1]).toBe('Ethernet4');
    expect(interfaces[2]).toBe('Ethernet5');
  });

  it('management only when data ports exhausted for srl', () => {
    const used = new Set<string>();
    for (let i = 1; i <= 34; i++) used.add(`e1-${i}`);
    const interfaces = getAvailableInterfaces('nokia_srlinux', used, 5);
    expect(interfaces).toEqual(['mgmt0']);
  });

  it('respects count limit — data ports have priority', () => {
    const interfaces = getAvailableInterfaces('ceos', new Set(), 2);
    expect(interfaces).toHaveLength(2);
    expect(interfaces).toEqual(['Ethernet1', 'Ethernet2']);
  });
});

describe('setRuntimeAliases', () => {
  it('resolves alias to original device pattern', () => {
    setRuntimeAliases({ eos: 'ceos' });
    const interfaces = getAvailableInterfaces('eos', new Set(), 2);
    expect(interfaces).toEqual(['Ethernet1', 'Ethernet2']);
  });
});
