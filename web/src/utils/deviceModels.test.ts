import {
  buildImageCompatibilityAliasMap,
  buildResolvedImageDeviceIdsIndex,
  buildDeviceModels,
  enrichDeviceCategories,
  flattenVendorCategories,
  getAllowedInstantiableImageKinds,
  imageMatchesDeviceId,
  isInstantiableImageKind,
  resolveImageDeviceIdsForCatalog,
  requiresRunnableImage,
} from './deviceModels';
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

  it('ignores non-instantiable image kinds when enriching device models', () => {
    const images = [
      { id: 'img1', kind: 'iol', reference: 'iol-l3.bin', device_id: 'linux', version: '15.9' },
    ];

    const result = buildDeviceModels(vendorCategories, images);
    const linux = result.find((m) => m.id === 'linux');
    expect(linux?.versions).toEqual(['1.0']);
  });

  it('recognizes only docker and qcow2 as instantiable kinds', () => {
    expect(isInstantiableImageKind('docker')).toBe(true);
    expect(isInstantiableImageKind('qcow2')).toBe(true);
    expect(isInstantiableImageKind('iol')).toBe(false);
  });

  it('does not infer docker/qcow2 fallback when supportedImageKinds is explicitly non-instantiable', () => {
    const allowed = getAllowedInstantiableImageKinds({ supportedImageKinds: ['iol'] });
    expect(Array.from(allowed)).toEqual([]);
  });

  it('treats iol-tagged devices as requiring runnable images even if requiresImage is missing', () => {
    expect(
      requiresRunnableImage({
        id: 'iol-xe',
        kind: 'iol-xe',
        tags: ['router', 'iol'],
        requiresImage: undefined,
        supportedImageKinds: ['iol'],
      })
    ).toBe(true);
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

  it('matches compatibility via shared alias tokens', () => {
    const image = {
      id: 'img',
      kind: 'qcow2',
      reference: 'shared-cat9k.qcow2',
      compatible_devices: ['cisco_cat9kv'],
    };
    const compatAliases = buildImageCompatibilityAliasMap([
      { id: 'cat9000v-uadp', compatibilityAliases: ['cisco_cat9kv'] },
      { id: 'cat9000v-q200', compatibilityAliases: ['cisco_cat9kv'] },
    ]);

    expect(imageMatchesDeviceId(image, 'cat9000v-uadp', compatAliases)).toBe(true);
    expect(imageMatchesDeviceId(image, 'cat9000v-q200', compatAliases)).toBe(true);
  });

  it('resolves image device IDs against a known catalog with alias expansion', () => {
    const image = {
      id: 'img',
      kind: 'qcow2',
      reference: 'c8000v.qcow2',
      compatible_devices: ['cisco_c8000v'],
    };
    const compatAliases = buildImageCompatibilityAliasMap([
      { id: 'c8000v', compatibilityAliases: ['cisco_c8000v'] },
    ]);

    const resolved = resolveImageDeviceIdsForCatalog(image, ['ceos', 'c8000v'], compatAliases);
    expect(resolved).toEqual(expect.arrayContaining(['cisco_c8000v', 'c8000v']));
  });

  it('builds indexed image compatibility for large-catalog lookups', () => {
    const compatAliases = buildImageCompatibilityAliasMap([
      { id: 'cat9000v-uadp', compatibilityAliases: ['cisco_cat9kv'] },
      { id: 'cat9000v-q200', compatibilityAliases: ['cisco_cat9kv'] },
    ]);
    const images = [
      {
        id: 'img-cat9k',
        kind: 'qcow2',
        reference: 'shared-cat9k.qcow2',
        compatible_devices: ['cisco_cat9kv'],
      },
    ];

    const resolved = buildResolvedImageDeviceIdsIndex(
      images,
      ['cat9000v-uadp', 'cat9000v-q200'],
      compatAliases
    );
    expect(resolved.get('img-cat9k')).toEqual(
      expect.arrayContaining(['cisco_cat9kv', 'cat9000v-uadp', 'cat9000v-q200'])
    );
  });
});
