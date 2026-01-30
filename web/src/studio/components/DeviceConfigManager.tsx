import React, { useMemo, useState } from 'react';
import { DeviceModel } from '../types';
import DeviceConfigCard from './DeviceConfigCard';
import DeviceConfigPanel from './DeviceConfigPanel';
import FilterChip from './FilterChip';

interface CustomDevice {
  id: string;
  label: string;
}

interface DeviceConfigManagerProps {
  deviceModels: DeviceModel[];
  customDevices: CustomDevice[];
  onAddCustomDevice: (device: CustomDevice) => void;
  onRemoveCustomDevice: (deviceId: string) => void;
  onRefresh: () => void;
}

const DeviceConfigManager: React.FC<DeviceConfigManagerProps> = ({
  deviceModels,
  customDevices,
  onAddCustomDevice,
  onRemoveCustomDevice,
  onRefresh,
}) => {
  // Device filters
  const [deviceSearch, setDeviceSearch] = useState('');
  const [selectedVendors, setSelectedVendors] = useState<Set<string>>(new Set());
  const [selectedTypes, setSelectedTypes] = useState<Set<string>>(new Set());

  // Selected device for config panel
  const [selectedDeviceId, setSelectedDeviceId] = useState<string | null>(null);

  // Custom device form
  const [customDeviceId, setCustomDeviceId] = useState('');
  const [customDeviceLabel, setCustomDeviceLabel] = useState('');

  // Track recently added devices for highlight animation
  const [recentlyAddedIds, setRecentlyAddedIds] = useState<Set<string>>(new Set());

  // Delete confirmation dialog
  const [deleteConfirmDevice, setDeleteConfirmDevice] = useState<CustomDevice | null>(null);

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

  // Filter devices and separate custom from regular
  const { filteredCustomDevices, filteredRegularDevices } = useMemo(() => {
    const customIds = new Set(customDevices.map(d => d.id));

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
      if (selectedVendors.size > 0 && !selectedVendors.has(device.vendor)) {
        return false;
      }

      // Type filter
      if (selectedTypes.size > 0 && !selectedTypes.has(device.type)) {
        return false;
      }

      return true;
    });

    return {
      filteredCustomDevices: filtered.filter(d => customIds.has(d.id)),
      filteredRegularDevices: filtered.filter(d => !customIds.has(d.id)),
    };
  }, [deviceModels, deviceSearch, selectedVendors, selectedTypes, customDevices]);

  const filteredDevices = [...filteredCustomDevices, ...filteredRegularDevices];

  // Handle adding a custom device
  const handleAddCustomDevice = () => {
    if (!customDeviceId.trim()) return;
    const newId = customDeviceId.trim();
    onAddCustomDevice({
      id: newId,
      label: customDeviceLabel.trim() || newId,
    });
    setCustomDeviceId('');
    setCustomDeviceLabel('');

    // Add temporary highlight for newly added device
    setRecentlyAddedIds(prev => new Set(prev).add(newId));
    setTimeout(() => {
      setRecentlyAddedIds(prev => {
        const next = new Set(prev);
        next.delete(newId);
        return next;
      });
    }, 3000);
  };

  // Handle confirming delete
  const handleConfirmDelete = () => {
    if (deleteConfirmDevice) {
      onRemoveCustomDevice(deleteConfirmDevice.id);
      setDeleteConfirmDevice(null);
      // If the deleted device was selected, clear selection
      if (selectedDeviceId === deleteConfirmDevice.id) {
        setSelectedDeviceId(null);
      }
    }
  };

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

          {/* Custom device form */}
          <div className="mt-4 p-3 bg-stone-100 dark:bg-stone-900/50 border border-stone-300 dark:border-stone-700 rounded-lg">
            <div className="text-[10px] font-bold text-stone-600 dark:text-stone-400 uppercase tracking-widest mb-2">
              <i className="fa-solid fa-plus-circle mr-1"></i>
              Add Custom Device
            </div>
            <div className="flex gap-2">
              <input
                className="flex-1 bg-white dark:bg-stone-900 border border-stone-300 dark:border-stone-700 rounded px-3 py-2 text-xs text-stone-900 dark:text-stone-200 placeholder:text-stone-400 focus:outline-none focus:ring-2 focus:ring-sage-500/50"
                placeholder="Device ID (e.g., my-router)"
                value={customDeviceId}
                onChange={(e) => setCustomDeviceId(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleAddCustomDevice()}
              />
              <input
                className="flex-1 bg-white dark:bg-stone-900 border border-stone-300 dark:border-stone-700 rounded px-3 py-2 text-xs text-stone-900 dark:text-stone-200 placeholder:text-stone-400 focus:outline-none focus:ring-2 focus:ring-sage-500/50"
                placeholder="Display Name (optional)"
                value={customDeviceLabel}
                onChange={(e) => setCustomDeviceLabel(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleAddCustomDevice()}
              />
              <button
                onClick={handleAddCustomDevice}
                disabled={!customDeviceId.trim()}
                className="px-4 py-2 bg-sage-600 hover:bg-sage-500 disabled:bg-stone-300 dark:disabled:bg-stone-700 text-white text-xs font-bold rounded transition-all disabled:cursor-not-allowed"
              >
                <i className="fa-solid fa-plus mr-1"></i>
                Add
              </button>
            </div>
            <p className="text-[10px] text-stone-500 dark:text-stone-400 mt-2">
              Custom devices appear at the top of the list and can be used to assign images in Image Management.
            </p>
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
              {/* Custom devices section */}
              {filteredCustomDevices.length > 0 && (
                <div className="mb-4">
                  <div className="flex items-center gap-2 mb-2 px-1">
                    <span className="w-2 h-2 rounded-full bg-stone-500"></span>
                    <span className="text-[10px] font-bold text-stone-600 dark:text-stone-400 uppercase tracking-widest">
                      Custom Devices
                    </span>
                    <span className="text-[10px] text-stone-400 dark:text-stone-500">
                      ({filteredCustomDevices.length})
                    </span>
                  </div>
                  <div className="space-y-2">
                    {filteredCustomDevices.map((device) => {
                      const customDevice = customDevices.find(c => c.id === device.id);
                      return (
                        <div key={device.id} className="relative group">
                          <div className="absolute -left-1 top-0 bottom-0 w-1 bg-stone-400 rounded-full"></div>
                          <DeviceConfigCard
                            device={device}
                            isSelected={selectedDeviceId === device.id}
                            onSelect={() => setSelectedDeviceId(device.id)}
                            isCustom={true}
                            isRecentlyAdded={recentlyAddedIds.has(device.id)}
                          />
                          {/* Delete button */}
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              if (customDevice) {
                                setDeleteConfirmDevice(customDevice);
                              }
                            }}
                            className="absolute top-2 right-2 w-7 h-7 flex items-center justify-center bg-stone-200 dark:bg-stone-800 hover:bg-stone-300 dark:hover:bg-stone-700 text-stone-600 dark:text-stone-400 rounded-lg opacity-0 group-hover:opacity-100 transition-all"
                            title="Delete custom device"
                          >
                            <i className="fa-solid fa-trash text-xs"></i>
                          </button>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Regular devices section */}
              {filteredRegularDevices.length > 0 && (
                <div>
                  {filteredCustomDevices.length > 0 && (
                    <div className="flex items-center gap-2 mb-2 px-1">
                      <span className="w-2 h-2 rounded-full bg-stone-400"></span>
                      <span className="text-[10px] font-bold text-stone-500 dark:text-stone-400 uppercase tracking-widest">
                        Vendor Devices
                      </span>
                      <span className="text-[10px] text-stone-400 dark:text-stone-500">
                        ({filteredRegularDevices.length})
                      </span>
                    </div>
                  )}
                  <div className="space-y-2">
                    {filteredRegularDevices.map((device) => (
                      <DeviceConfigCard
                        key={device.id}
                        device={device}
                        isSelected={selectedDeviceId === device.id}
                        onSelect={() => setSelectedDeviceId(device.id)}
                      />
                    ))}
                  </div>
                </div>
              )}

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

      {/* Delete confirmation dialog */}
      {deleteConfirmDevice && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 animate-in fade-in duration-200">
          <div className="bg-white dark:bg-stone-900 rounded-xl shadow-2xl border border-stone-200 dark:border-stone-700 p-6 max-w-md w-full mx-4 animate-in zoom-in-95 duration-200">
            <div className="flex items-center gap-3 mb-4">
              <div className="w-10 h-10 rounded-full bg-rose-100 dark:bg-rose-900/50 flex items-center justify-center">
                <i className="fa-solid fa-trash text-rose-600 dark:text-rose-400"></i>
              </div>
              <div>
                <h3 className="text-lg font-bold text-stone-900 dark:text-white">
                  Delete Custom Device
                </h3>
                <p className="text-xs text-stone-500 dark:text-stone-400">
                  This action cannot be undone
                </p>
              </div>
            </div>

            <p className="text-sm text-stone-600 dark:text-stone-300 mb-6">
              Are you sure you want to delete the custom device{' '}
              <span className="font-bold text-rose-600 dark:text-rose-400">
                "{deleteConfirmDevice.label}"
              </span>{' '}
              (<code className="text-xs bg-stone-100 dark:bg-stone-800 px-1 py-0.5 rounded">
                {deleteConfirmDevice.id}
              </code>)?
            </p>

            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setDeleteConfirmDevice(null)}
                className="px-4 py-2 bg-stone-100 dark:bg-stone-800 hover:bg-stone-200 dark:hover:bg-stone-700 text-stone-700 dark:text-stone-300 rounded-lg text-sm font-medium transition-all"
              >
                Cancel
              </button>
              <button
                onClick={handleConfirmDelete}
                className="px-4 py-2 bg-rose-600 hover:bg-rose-500 text-white rounded-lg text-sm font-bold transition-all"
              >
                <i className="fa-solid fa-trash mr-2"></i>
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default DeviceConfigManager;
