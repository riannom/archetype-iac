
import { DeviceType, DeviceModel } from './types';

export interface DeviceSubCategory {
  name: string;
  models: DeviceModel[];
}

export interface DeviceCategory {
  name: string;
  subCategories?: DeviceSubCategory[];
  models?: DeviceModel[];
}

export const DEVICE_CATEGORIES: DeviceCategory[] = [
  {
    name: 'Network',
    subCategories: [
      {
        name: 'Routers',
        models: [
          { id: 'iosv', type: DeviceType.ROUTER, vendor: 'Cisco', name: 'Cisco IOSv', icon: 'fa-arrows-to-dot', versions: ['15.9(3)M4', '15.8'], isActive: true },
          { id: 'csr1000v', type: DeviceType.ROUTER, vendor: 'Cisco', name: 'Cisco CSR1000v', icon: 'fa-arrows-to-dot', versions: ['17.3.2'], isActive: true },
          { id: 'vyos', type: DeviceType.ROUTER, vendor: 'VyOS', name: 'VyOS', icon: 'fa-arrows-to-dot', versions: ['1.4-rolling'], isActive: true },
        ]
      },
      {
        name: 'Switches',
        models: [
          { id: 'eos', type: DeviceType.SWITCH, vendor: 'Arista', name: 'Arista EOS', icon: 'fa-arrows-left-right-to-line', versions: ['4.28.0F', '4.27.1F'], isActive: true },
          { id: 'cumulus', type: DeviceType.SWITCH, vendor: 'Nvidia', name: 'Nvidia Cumulus', icon: 'fa-arrows-left-right-to-line', versions: ['4.4.0', '5.0.1'], isActive: true },
          { id: 'nxos', type: DeviceType.SWITCH, vendor: 'Cisco', name: 'Cisco NX-OSv', icon: 'fa-arrows-left-right-to-line', versions: ['9.3.9'], isActive: false },
        ]
      },
      {
        name: 'Load Balancers',
        models: [
          { id: 'f5', type: DeviceType.SWITCH, vendor: 'F5', name: 'F5 BIG-IP VE', icon: 'fa-server', versions: ['16.1.0', '17.0.0'], isActive: true },
          { id: 'haproxy', type: DeviceType.CONTAINER, vendor: 'Open Source', name: 'HAProxy', icon: 'fa-box', versions: ['2.6', 'latest'], isActive: true },
          { id: 'citrix', type: DeviceType.SWITCH, vendor: 'Citrix', name: 'Citrix ADC', icon: 'fa-server', versions: ['13.1'], isActive: false },
        ]
      }
    ]
  },
  {
    name: 'Security',
    models: [
      { id: 'asa', type: DeviceType.FIREWALL, vendor: 'Cisco', name: 'Cisco ASAv', icon: 'fa-shield-halved', versions: ['9.16.1'], isActive: true },
      { id: 'fortigate', type: DeviceType.FIREWALL, vendor: 'Fortinet', name: 'FortiGate VM', icon: 'fa-user-shield', versions: ['7.2.0'], isActive: false },
      { id: 'paloalto', type: DeviceType.FIREWALL, vendor: 'Palo Alto', name: 'Palo Alto VM-Series', icon: 'fa-lock', versions: ['10.1.0'], isActive: false },
    ]
  },
  {
    name: 'Compute',
    models: [
      { id: 'linux', type: DeviceType.HOST, vendor: 'Open Source', name: 'Linux Server', icon: 'fa-terminal', versions: ['Ubuntu 22.04', 'Alpine', 'Debian 12'], isActive: true },
      { id: 'frr', type: DeviceType.CONTAINER, vendor: 'Open Source', name: 'FRR Container', icon: 'fa-box-open', versions: ['latest', '8.4.1'], isActive: true },
      { id: 'windows', type: DeviceType.HOST, vendor: 'Microsoft', name: 'Windows Server', icon: 'fa-window-maximize', versions: ['2022', '2019'], isActive: false },
    ]
  },
  {
    name: 'Cloud & External',
    models: [
      { id: 'internet', type: DeviceType.EXTERNAL, vendor: 'System', name: 'Public Internet', icon: 'fa-cloud', versions: ['Default'], isActive: true },
      { id: 'mgmt', type: DeviceType.EXTERNAL, vendor: 'System', name: 'Management Bridge', icon: 'fa-plug-circle-bolt', versions: ['br0'], isActive: true }
    ]
  }
];

export const DEVICE_MODELS: DeviceModel[] = DEVICE_CATEGORIES.flatMap(cat => {
  if (cat.subCategories) {
    return cat.subCategories.flatMap(sub => sub.models);
  }
  return cat.models || [];
});
