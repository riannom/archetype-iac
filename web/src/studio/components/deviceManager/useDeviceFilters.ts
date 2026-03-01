import { useMemo, useState } from 'react';
import { DeviceModel, ImageLibraryEntry } from '../../types';
import { usePersistedState, usePersistedSet } from '../../hooks/usePersistedState';

interface UseDeviceFiltersArgs {
  deviceModels: DeviceModel[];
  imagesByDevice: Map<string, ImageLibraryEntry[]>;
}

export function useDeviceFilters({ deviceModels, imagesByDevice }: UseDeviceFiltersArgs) {
  const [deviceSearch, setDeviceSearch] = useState('');
  const [selectedDeviceVendors, toggleDeviceVendor, clearDeviceVendors] = usePersistedSet('archetype:filters:device:vendors');
  const [deviceImageStatus, setDeviceImageStatus] = usePersistedState<'all' | 'has_image' | 'no_image'>('archetype:filters:device:imageStatus', 'all');
  const [deviceSort, setDeviceSort] = usePersistedState<'name' | 'vendor' | 'type'>('archetype:filters:device:sort', 'vendor');

  // Get unique device vendors
  const deviceVendors = useMemo(() => {
    const vendors = new Set<string>();
    deviceModels.forEach((d) => {
      if (d.vendor) vendors.add(d.vendor);
    });
    return Array.from(vendors).sort();
  }, [deviceModels]);

  // Filter and sort devices
  const filteredDevices = useMemo(() => {
    const filtered = deviceModels.filter((device) => {
      // Search filter
      if (deviceSearch) {
        const query = deviceSearch.toLowerCase();
        const matchesName = device.name.toLowerCase().includes(query);
        const matchesVendor = device.vendor?.toLowerCase().includes(query);
        const matchesId = device.id.toLowerCase().includes(query);
        const matchesTags = device.tags?.some((tag) => tag.toLowerCase().includes(query));
        if (!matchesName && !matchesVendor && !matchesId && !matchesTags) {
          return false;
        }
      }

      // Vendor filter
      if (selectedDeviceVendors.size > 0 && !selectedDeviceVendors.has(device.vendor)) {
        return false;
      }

      // Image status filter
      const hasImages = (imagesByDevice.get(device.id)?.length || 0) > 0;
      if (deviceImageStatus === 'has_image' && !hasImages) return false;
      if (deviceImageStatus === 'no_image' && hasImages) return false;

      return true;
    });

    // Sort devices
    return filtered.sort((a, b) => {
      switch (deviceSort) {
        case 'name':
          return a.name.localeCompare(b.name);
        case 'vendor':
          return (a.vendor || '').localeCompare(b.vendor || '') || a.name.localeCompare(b.name);
        case 'type':
          return (a.type || '').localeCompare(b.type || '') || a.name.localeCompare(b.name);
        default:
          return 0;
      }
    });
  }, [deviceModels, deviceSearch, selectedDeviceVendors, deviceImageStatus, imagesByDevice, deviceSort]);

  const hasDeviceFilters =
    deviceSearch.length > 0 || selectedDeviceVendors.size > 0 || deviceImageStatus !== 'all';

  const clearDeviceFilters = () => {
    setDeviceSearch('');
    clearDeviceVendors();
    setDeviceImageStatus('all');
  };

  return {
    deviceSearch,
    setDeviceSearch,
    selectedDeviceVendors,
    toggleDeviceVendor,
    deviceImageStatus,
    setDeviceImageStatus,
    deviceSort,
    setDeviceSort,
    deviceVendors,
    filteredDevices,
    hasDeviceFilters,
    clearDeviceFilters,
  };
}
