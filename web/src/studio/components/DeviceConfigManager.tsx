import React, { useMemo, useState } from 'react';
import { DeviceModel } from '../types';
import DeviceConfigCard from './DeviceConfigCard';
import DeviceConfigPanel from './DeviceConfigPanel';
import FilterChip from './FilterChip';

interface DeviceConfigManagerProps {
  deviceModels: DeviceModel[];
  onRefresh: () => void;
}

const DeviceConfigManager: React.FC<DeviceConfigManagerProps> = ({
  deviceModels,
  onRefresh,
}) => {
  // Device filters
  const [deviceSearch, setDeviceSearch] = useState('');
  const [selectedVendors, setSelectedVendors] = useState<Set<string>>(new Set());
  const [selectedTypes, setSelectedTypes] = useState<Set<string>>(new Set());

  // Selected device for config panel
  const [selectedDeviceId, setSelectedDeviceId] = useState<string | null>(null);

  // Get unique vendors
  const vendors = useMemo(() => {
    const vendorSet = new Set<string>();
    deviceModels.forEach((d) => {
      if (d.vendor) vendorSet.add(d.vendor);
    });
    return Array.from(vendorSet).sort();
  }, [deviceModels]);

  // Get unique device types
  const deviceTypes = useMemo(() => {
    const typeSet = new Set<string>();
    deviceModels.forEach((d) => {
      if (d.type) typeSet.add(d.type);
    });
    return Array.from(typeSet).sort();
  }, [deviceModels]);

  // Filter devices
  const filteredDevices = useMemo(() => {
    return deviceModels.filter((device) => {
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
      if (selectedVendors.size > 0 && !selectedVendors.has(device.vendor)) {
        return false;
      }

      // Type filter
      if (selectedTypes.size > 0 && !selectedTypes.has(device.type)) {
        return false;
      }

      return true;
    });
  }, [deviceModels, deviceSearch, selectedVendors, selectedTypes]);

  const clearFilters = () => {
    setDeviceSearch('');
    setSelectedVendors(new Set());
    setSelectedTypes(new Set());
  };

  const hasFilters =
    deviceSearch.length > 0 || selectedVendors.size > 0 || selectedTypes.size > 0;

  const selectedDevice = selectedDeviceId
    ? deviceModels.find((d) => d.id === selectedDeviceId)
    : null;

  return (
    <div className="flex-1 bg-stone-50 dark:bg-stone-950 flex flex-col overflow-hidden animate-in fade-in duration-300">
      <div className="flex flex-col h-full">
        {/* Header */}
        <header className="px-6 py-4 border-b border-stone-200 dark:border-stone-800 bg-white/50 dark:bg-stone-900/50 backdrop-blur-sm">
          <div className="flex flex-wrap justify-between items-end gap-4">
            <div>
              <h1 className="text-2xl font-black text-stone-900 dark:text-white tracking-tight">
                Device Configuration
              </h1>
              <p className="text-stone-500 dark:text-stone-400 text-xs mt-1">
                Configure device properties: ports, resources, boot options, and vendor-specific settings.
              </p>
            </div>
            <div className="flex gap-3">
              <button
                onClick={onRefresh}
                className="px-3 py-2 bg-stone-200 dark:bg-stone-800 hover:bg-stone-300 dark:hover:bg-stone-700 text-stone-700 dark:text-white rounded-lg text-xs font-bold transition-all"
              >
                <i className="fa-solid fa-rotate"></i>
              </button>
            </div>
          </div>
        </header>

        {/* Two-panel layout */}
        <div className="flex-1 flex overflow-hidden">
          {/* Left panel - Device list (40%) */}
          <div className="w-2/5 border-r border-stone-200 dark:border-stone-800 flex flex-col overflow-hidden">
            {/* Device filters */}
            <div className="p-4 border-b border-stone-200 dark:border-stone-800 bg-stone-100/50 dark:bg-stone-900/30 space-y-3">
              {/* Search */}
              <div className="relative">
                <i className="fa-solid fa-magnifying-glass absolute left-3 top-1/2 -translate-y-1/2 text-stone-400 text-xs" />
                <input
                  type="text"
                  placeholder="Search devices..."
                  value={deviceSearch}
                  onChange={(e) => setDeviceSearch(e.target.value)}
                  className="w-full pl-9 pr-8 py-2 bg-white dark:bg-stone-800 border border-stone-200 dark:border-stone-700 rounded-lg text-xs text-stone-900 dark:text-stone-100 placeholder:text-stone-400 focus:outline-none focus:ring-2 focus:ring-sage-500/50"
                />
                {deviceSearch && (
                  <button
                    onClick={() => setDeviceSearch('')}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-stone-400 hover:text-stone-600"
                  >
                    <i className="fa-solid fa-xmark text-xs" />
                  </button>
                )}
              </div>

              {/* Filter chips */}
              <div className="flex flex-wrap gap-1.5">
                {/* Type filters */}
                {deviceTypes.slice(0, 3).map((type) => (
                  <FilterChip
                    key={type}
                    label={type}
                    isActive={selectedTypes.has(type)}
                    onClick={() => {
                      const next = new Set(selectedTypes);
                      if (next.has(type)) {
                        next.delete(type);
                      } else {
                        next.add(type);
                      }
                      setSelectedTypes(next);
                    }}
                  />
                ))}
                {/* Vendor filters */}
                {vendors.slice(0, 4).map((vendor) => (
                  <FilterChip
                    key={vendor}
                    label={vendor}
                    isActive={selectedVendors.has(vendor)}
                    onClick={() => {
                      const next = new Set(selectedVendors);
                      if (next.has(vendor)) {
                        next.delete(vendor);
                      } else {
                        next.add(vendor);
                      }
                      setSelectedVendors(next);
                    }}
                  />
                ))}
                {hasFilters && (
                  <button
                    onClick={clearFilters}
                    className="text-[10px] text-red-500 hover:text-red-600 font-bold uppercase"
                  >
                    Clear
                  </button>
                )}
              </div>
            </div>

            {/* Device list */}
            <div className="flex-1 overflow-y-auto p-4 space-y-2 custom-scrollbar">
              {filteredDevices.map((device) => (
                <DeviceConfigCard
                  key={device.id}
                  device={device}
                  isSelected={selectedDeviceId === device.id}
                  onSelect={() => setSelectedDeviceId(device.id)}
                />
              ))}
              {filteredDevices.length === 0 && (
                <div className="text-center py-8">
                  <i className="fa-solid fa-search text-2xl text-stone-300 dark:text-stone-700 mb-2" />
                  <p className="text-xs text-stone-500">No devices match your filters</p>
                </div>
              )}
            </div>

            {/* Device count */}
            <div className="p-3 border-t border-stone-200 dark:border-stone-800 bg-stone-100/50 dark:bg-stone-900/30">
              <p className="text-[10px] text-stone-500 uppercase tracking-wider font-bold">
                {filteredDevices.length} of {deviceModels.length} devices
              </p>
            </div>
          </div>

          {/* Right panel - Config panel (60%) */}
          <div className="flex-1 flex flex-col overflow-hidden">
            {selectedDevice ? (
              <DeviceConfigPanel
                device={selectedDevice}
                onRefresh={onRefresh}
              />
            ) : (
              <div className="flex-1 flex items-center justify-center">
                <div className="text-center">
                  <i className="fa-solid fa-sliders text-4xl text-stone-300 dark:text-stone-700 mb-4" />
                  <h3 className="text-sm font-bold text-stone-500 dark:text-stone-400">
                    Select a device
                  </h3>
                  <p className="text-xs text-stone-400 mt-1">
                    Choose a device from the list to view and edit its configuration
                  </p>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default DeviceConfigManager;
