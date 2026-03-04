import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useDeviceFilters } from './useDeviceFilters';
import type { DeviceModel, ImageLibraryEntry } from '../../types';
import { DeviceType } from '../../types';

// Mock usePersistedState to use plain useState (avoids localStorage side effects)
vi.mock('../../hooks/usePersistedState', async () => {
  const react = await import('react');
  return {
    usePersistedState: <T,>(_key: string, defaultValue: T): [T, (v: T | ((p: T) => T)) => void] => {
      return react.useState<T>(defaultValue);
    },
    usePersistedSet: (_key: string): [Set<string>, (value: string) => void, () => void] => {
      const [set, setSet] = react.useState<Set<string>>(new Set());
      const toggle = react.useCallback((value: string) => {
        setSet((prev: Set<string>) => {
          const next = new Set(prev);
          if (next.has(value)) {
            next.delete(value);
          } else {
            next.add(value);
          }
          return next;
        });
      }, []);
      const clear = react.useCallback(() => {
        setSet(new Set());
      }, []);
      return [set, toggle, clear];
    },
  };
});

// ============================================================================
// Helpers
// ============================================================================

function makeDevice(overrides: Partial<DeviceModel> = {}): DeviceModel {
  return {
    id: 'ceos',
    type: DeviceType.ROUTER,
    name: 'Arista cEOS',
    icon: 'fa-network-wired',
    versions: ['4.28.0F'],
    isActive: true,
    vendor: 'Arista',
    ...overrides,
  };
}

function makeImage(overrides: Partial<ImageLibraryEntry> = {}): ImageLibraryEntry {
  return {
    id: 'docker:ceos:4.28.0F',
    kind: 'docker',
    reference: 'ceos:4.28.0F',
    device_id: 'ceos',
    ...overrides,
  };
}

function defaultArgs(overrides: {
  deviceModels?: DeviceModel[];
  imagesByDevice?: Map<string, ImageLibraryEntry[]>;
} = {}) {
  return {
    deviceModels: overrides.deviceModels || [
      makeDevice({ id: 'ceos', name: 'Arista cEOS', vendor: 'Arista' }),
      makeDevice({ id: 'srlinux', name: 'Nokia SR Linux', vendor: 'Nokia' }),
      makeDevice({ id: 'iosv', name: 'Cisco IOSv', vendor: 'Cisco', type: DeviceType.ROUTER }),
    ],
    imagesByDevice: overrides.imagesByDevice || new Map<string, ImageLibraryEntry[]>([
      ['ceos', [makeImage({ id: 'img-ceos', device_id: 'ceos' })]],
    ]),
  };
}

// ============================================================================
// Tests
// ============================================================================

describe('useDeviceFilters', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Initial State ──

  it('returns all devices unfiltered by default', () => {
    const args = defaultArgs();
    const { result } = renderHook(() => useDeviceFilters(args));

    expect(result.current.filteredDevices).toHaveLength(3);
    expect(result.current.deviceSearch).toBe('');
    expect(result.current.deviceImageStatus).toBe('all');
    expect(result.current.selectedDeviceVendors.size).toBe(0);
    expect(result.current.hasDeviceFilters).toBe(false);
  });

  it('defaults sort to vendor', () => {
    const args = defaultArgs();
    const { result } = renderHook(() => useDeviceFilters(args));

    expect(result.current.deviceSort).toBe('vendor');
  });

  // ── Device Vendors ──

  it('extracts unique vendor names sorted alphabetically', () => {
    const args = defaultArgs();
    const { result } = renderHook(() => useDeviceFilters(args));

    expect(result.current.deviceVendors).toEqual(['Arista', 'Cisco', 'Nokia']);
  });

  it('excludes devices with no vendor from vendors list', () => {
    const devices = [
      makeDevice({ id: 'd1', vendor: 'Arista' }),
      makeDevice({ id: 'd2', vendor: undefined as unknown as string }),
    ];
    const args = defaultArgs({ deviceModels: devices });
    const { result } = renderHook(() => useDeviceFilters(args));

    expect(result.current.deviceVendors).toEqual(['Arista']);
  });

  // ── Search Filter ──

  it('filters devices by name search', () => {
    const args = defaultArgs();
    const { result } = renderHook(() => useDeviceFilters(args));

    act(() => {
      result.current.setDeviceSearch('ceos');
    });

    expect(result.current.filteredDevices).toHaveLength(1);
    expect(result.current.filteredDevices[0].id).toBe('ceos');
  });

  it('search is case-insensitive', () => {
    const args = defaultArgs();
    const { result } = renderHook(() => useDeviceFilters(args));

    act(() => {
      result.current.setDeviceSearch('NOKIA');
    });

    expect(result.current.filteredDevices).toHaveLength(1);
    expect(result.current.filteredDevices[0].id).toBe('srlinux');
  });

  it('filters by vendor name in search', () => {
    const args = defaultArgs();
    const { result } = renderHook(() => useDeviceFilters(args));

    act(() => {
      result.current.setDeviceSearch('cisco');
    });

    expect(result.current.filteredDevices).toHaveLength(1);
    expect(result.current.filteredDevices[0].id).toBe('iosv');
  });

  it('filters by device ID in search', () => {
    const args = defaultArgs();
    const { result } = renderHook(() => useDeviceFilters(args));

    act(() => {
      result.current.setDeviceSearch('srlinux');
    });

    expect(result.current.filteredDevices).toHaveLength(1);
    expect(result.current.filteredDevices[0].id).toBe('srlinux');
  });

  it('filters by tags in search', () => {
    const devices = [
      makeDevice({ id: 'd1', name: 'Device A', tags: ['layer3', 'bgp'] }),
      makeDevice({ id: 'd2', name: 'Device B', tags: ['layer2'] }),
    ];
    const args = defaultArgs({ deviceModels: devices });
    const { result } = renderHook(() => useDeviceFilters(args));

    act(() => {
      result.current.setDeviceSearch('bgp');
    });

    expect(result.current.filteredDevices).toHaveLength(1);
    expect(result.current.filteredDevices[0].id).toBe('d1');
  });

  it('returns no devices when search matches nothing', () => {
    const args = defaultArgs();
    const { result } = renderHook(() => useDeviceFilters(args));

    act(() => {
      result.current.setDeviceSearch('nonexistent');
    });

    expect(result.current.filteredDevices).toHaveLength(0);
  });

  // ── Vendor Filter ──

  it('filters devices by selected vendor', () => {
    const args = defaultArgs();
    const { result } = renderHook(() => useDeviceFilters(args));

    act(() => {
      result.current.toggleDeviceVendor('Arista');
    });

    expect(result.current.filteredDevices).toHaveLength(1);
    expect(result.current.filteredDevices[0].vendor).toBe('Arista');
  });

  it('toggles vendor off when clicked again', () => {
    const args = defaultArgs();
    const { result } = renderHook(() => useDeviceFilters(args));

    act(() => {
      result.current.toggleDeviceVendor('Arista');
    });
    expect(result.current.filteredDevices).toHaveLength(1);

    act(() => {
      result.current.toggleDeviceVendor('Arista');
    });
    expect(result.current.filteredDevices).toHaveLength(3);
  });

  it('supports multiple selected vendors', () => {
    const args = defaultArgs();
    const { result } = renderHook(() => useDeviceFilters(args));

    act(() => {
      result.current.toggleDeviceVendor('Arista');
    });
    act(() => {
      result.current.toggleDeviceVendor('Cisco');
    });

    expect(result.current.filteredDevices).toHaveLength(2);
    const ids = result.current.filteredDevices.map((d) => d.id);
    expect(ids).toContain('ceos');
    expect(ids).toContain('iosv');
  });

  // ── Image Status Filter ──

  it('filters to devices that have images', () => {
    const args = defaultArgs();
    const { result } = renderHook(() => useDeviceFilters(args));

    act(() => {
      result.current.setDeviceImageStatus('has_image');
    });

    expect(result.current.filteredDevices).toHaveLength(1);
    expect(result.current.filteredDevices[0].id).toBe('ceos');
  });

  it('filters to devices that have no images', () => {
    const args = defaultArgs();
    const { result } = renderHook(() => useDeviceFilters(args));

    act(() => {
      result.current.setDeviceImageStatus('no_image');
    });

    expect(result.current.filteredDevices).toHaveLength(2);
    const ids = result.current.filteredDevices.map((d) => d.id);
    expect(ids).toContain('srlinux');
    expect(ids).toContain('iosv');
  });

  it('shows all devices when image status is "all"', () => {
    const args = defaultArgs();
    const { result } = renderHook(() => useDeviceFilters(args));

    act(() => {
      result.current.setDeviceImageStatus('has_image');
    });
    expect(result.current.filteredDevices).toHaveLength(1);

    act(() => {
      result.current.setDeviceImageStatus('all');
    });
    expect(result.current.filteredDevices).toHaveLength(3);
  });

  // ── Sorting ──

  it('sorts by vendor (default), then by name', () => {
    const args = defaultArgs();
    const { result } = renderHook(() => useDeviceFilters(args));

    const names = result.current.filteredDevices.map((d) => d.name);
    // Arista < Cisco < Nokia
    expect(names).toEqual(['Arista cEOS', 'Cisco IOSv', 'Nokia SR Linux']);
  });

  it('sorts by name', () => {
    const args = defaultArgs();
    const { result } = renderHook(() => useDeviceFilters(args));

    act(() => {
      result.current.setDeviceSort('name');
    });

    const names = result.current.filteredDevices.map((d) => d.name);
    expect(names).toEqual(['Arista cEOS', 'Cisco IOSv', 'Nokia SR Linux']);
  });

  it('sorts by type', () => {
    const devices = [
      makeDevice({ id: 'd1', name: 'Device A', type: DeviceType.SWITCH }),
      makeDevice({ id: 'd2', name: 'Device B', type: DeviceType.ROUTER }),
      makeDevice({ id: 'd3', name: 'Device C', type: DeviceType.FIREWALL }),
    ];
    const args = defaultArgs({ deviceModels: devices });
    const { result } = renderHook(() => useDeviceFilters(args));

    act(() => {
      result.current.setDeviceSort('type');
    });

    const types = result.current.filteredDevices.map((d) => d.type);
    expect(types).toEqual([DeviceType.FIREWALL, DeviceType.ROUTER, DeviceType.SWITCH]);
  });

  // ── Combined Filters ──

  it('applies search and vendor filter together', () => {
    const devices = [
      makeDevice({ id: 'd1', name: 'Arista cEOS', vendor: 'Arista' }),
      makeDevice({ id: 'd2', name: 'Arista vEOS', vendor: 'Arista' }),
      makeDevice({ id: 'd3', name: 'Nokia SR Linux', vendor: 'Nokia' }),
    ];
    const args = defaultArgs({ deviceModels: devices });
    const { result } = renderHook(() => useDeviceFilters(args));

    act(() => {
      result.current.toggleDeviceVendor('Arista');
    });
    act(() => {
      result.current.setDeviceSearch('cEOS');
    });

    expect(result.current.filteredDevices).toHaveLength(1);
    expect(result.current.filteredDevices[0].id).toBe('d1');
  });

  it('applies search, vendor, and image status filters together', () => {
    const devices = [
      makeDevice({ id: 'ceos', name: 'Arista cEOS', vendor: 'Arista' }),
      makeDevice({ id: 'veos', name: 'Arista vEOS', vendor: 'Arista' }),
      makeDevice({ id: 'srlinux', name: 'Nokia SR Linux', vendor: 'Nokia' }),
    ];
    const imagesByDevice = new Map<string, ImageLibraryEntry[]>([
      ['ceos', [makeImage()]],
      ['veos', [makeImage({ id: 'img-veos' })]],
    ]);
    const args = defaultArgs({ deviceModels: devices, imagesByDevice });
    const { result } = renderHook(() => useDeviceFilters(args));

    act(() => {
      result.current.toggleDeviceVendor('Arista');
    });
    act(() => {
      result.current.setDeviceImageStatus('has_image');
    });
    act(() => {
      result.current.setDeviceSearch('ceos');
    });

    expect(result.current.filteredDevices).toHaveLength(1);
    expect(result.current.filteredDevices[0].id).toBe('ceos');
  });

  // ── hasDeviceFilters ──

  it('hasDeviceFilters is true when search is active', () => {
    const args = defaultArgs();
    const { result } = renderHook(() => useDeviceFilters(args));

    act(() => {
      result.current.setDeviceSearch('test');
    });

    expect(result.current.hasDeviceFilters).toBe(true);
  });

  it('hasDeviceFilters is true when vendors are selected', () => {
    const args = defaultArgs();
    const { result } = renderHook(() => useDeviceFilters(args));

    act(() => {
      result.current.toggleDeviceVendor('Arista');
    });

    expect(result.current.hasDeviceFilters).toBe(true);
  });

  it('hasDeviceFilters is true when image status is not "all"', () => {
    const args = defaultArgs();
    const { result } = renderHook(() => useDeviceFilters(args));

    act(() => {
      result.current.setDeviceImageStatus('has_image');
    });

    expect(result.current.hasDeviceFilters).toBe(true);
  });

  // ── Clear Filters ──

  it('clearDeviceFilters resets all filters', () => {
    const args = defaultArgs();
    const { result } = renderHook(() => useDeviceFilters(args));

    // Apply all filter types
    act(() => {
      result.current.setDeviceSearch('test');
    });
    act(() => {
      result.current.toggleDeviceVendor('Arista');
    });
    act(() => {
      result.current.setDeviceImageStatus('has_image');
    });
    expect(result.current.hasDeviceFilters).toBe(true);

    // Clear
    act(() => {
      result.current.clearDeviceFilters();
    });

    expect(result.current.deviceSearch).toBe('');
    expect(result.current.selectedDeviceVendors.size).toBe(0);
    expect(result.current.deviceImageStatus).toBe('all');
    expect(result.current.hasDeviceFilters).toBe(false);
    expect(result.current.filteredDevices).toHaveLength(3);
  });

  // ── Edge Cases ──

  it('handles empty device list', () => {
    const args = defaultArgs({ deviceModels: [] });
    const { result } = renderHook(() => useDeviceFilters(args));

    expect(result.current.filteredDevices).toHaveLength(0);
    expect(result.current.deviceVendors).toEqual([]);
  });

  it('handles devices with no vendor for vendor filter', () => {
    const devices = [
      makeDevice({ id: 'd1', name: 'Custom Device', vendor: '' }),
      makeDevice({ id: 'd2', name: 'Arista cEOS', vendor: 'Arista' }),
    ];
    const args = defaultArgs({ deviceModels: devices });
    const { result } = renderHook(() => useDeviceFilters(args));

    act(() => {
      result.current.toggleDeviceVendor('Arista');
    });

    // Device with no vendor should be excluded when vendor filter is active
    expect(result.current.filteredDevices).toHaveLength(1);
    expect(result.current.filteredDevices[0].id).toBe('d2');
  });

  it('handles empty imagesByDevice map', () => {
    const args = defaultArgs({ imagesByDevice: new Map() });
    const { result } = renderHook(() => useDeviceFilters(args));

    act(() => {
      result.current.setDeviceImageStatus('has_image');
    });

    expect(result.current.filteredDevices).toHaveLength(0);
  });
});
