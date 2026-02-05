import { buildDeviceModels, enrichDeviceCategories, flattenVendorCategories } from './deviceModels';
import { DeviceType } from '../studio/types';

const vendorCategories = [
  {
    name: 'Compute',
    models: [
      {
        id: 'linux',
        type: DeviceType.HOST,
        name: 'Linux',
        icon: 'linux',
        versions: ['1.0'],
        isActive: true,
        vendor: 'Linux',
      },
    ],
  },
];

describe('deviceModels', () => {
  it('flattens vendor categories', () => {
    const flat = flattenVendorCategories(vendorCategories);
    expect(flat).toHaveLength(1);
    expect(flat[0].id).toBe('linux');
  });

  it('builds device models with image versions and unknown devices', () => {
    const images = [
      { id: 'img1', kind: 'docker', reference: 'linux:1.0', device_id: 'linux', version: '2.0' },
      { id: 'img2', kind: 'docker', reference: 'unknown:1.0', device_id: 'mystery', version: '1.2' },
    ];

    const result = buildDeviceModels(vendorCategories, images);

    const linux = result.find((m) => m.id === 'linux');
    expect(linux?.versions).toEqual(expect.arrayContaining(['1.0', '2.0']));

    const mystery = result.find((m) => m.id === 'mystery');
    expect(mystery?.vendor).toBe('unknown');
    expect(mystery?.versions).toEqual(['1.2']);
  });

  it('enriches categories and adds Other', () => {
    const devices = [
      {
        id: 'linux',
        type: DeviceType.HOST,
        name: 'Linux',
        icon: 'linux',
        versions: ['1.0'],
        isActive: true,
        vendor: 'Linux',
      },
      {
        id: 'mystery',
        type: DeviceType.CONTAINER,
        name: 'Mystery',
        icon: 'fa-microchip',
        versions: ['default'],
        isActive: true,
        vendor: 'unknown',
      },
    ];

    const enriched = enrichDeviceCategories(vendorCategories, devices);
    expect(enriched).toHaveLength(2);
    expect(enriched[1].name).toBe('Other');
  });
});
