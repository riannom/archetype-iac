import React, { useState } from 'react';
import { DeviceModel, ImageLibraryEntry } from '../../types';
import DeviceCard from '../DeviceCard';
import FilterChip from '../FilterChip';
import { Select } from '../../../components/ui/Select';

interface DeviceCatalogViewProps {
  filteredDevices: DeviceModel[];
  imagesByDevice: Map<string, ImageLibraryEntry[]>;
  deviceSearch: string;
  setDeviceSearch: (value: string) => void;
  deviceSort: 'name' | 'vendor' | 'type';
  setDeviceSort: (value: 'name' | 'vendor' | 'type') => void;
  deviceImageStatus: 'all' | 'has_image' | 'no_image';
  setDeviceImageStatus: (value: 'all' | 'has_image' | 'no_image') => void;
  deviceVendors: string[];
  selectedDeviceVendors: Set<string>;
  toggleDeviceVendor: (vendor: string) => void;
  hasDeviceFilters: boolean;
  clearDeviceFilters: () => void;
  onUnassignImage: (imageId: string, deviceId?: string) => Promise<void>;
  onSetDefaultImage: (imageId: string, deviceId: string) => Promise<void>;
}

const DeviceCatalogView: React.FC<DeviceCatalogViewProps> = ({
  filteredDevices,
  imagesByDevice,
  deviceSearch,
  setDeviceSearch,
  deviceSort,
  setDeviceSort,
  deviceImageStatus,
  setDeviceImageStatus,
  deviceVendors,
  selectedDeviceVendors,
  toggleDeviceVendor,
  hasDeviceFilters,
  clearDeviceFilters,
  onUnassignImage,
  onSetDefaultImage,
}) => {
  const [selectedDeviceId, setSelectedDeviceId] = useState<string | null>(null);

  return (
    <div className="w-2/5 border-r border-stone-200 dark:border-stone-800 flex flex-col overflow-hidden min-h-0">
      {/* Device filters */}
      <div className="p-4 border-b border-stone-200 dark:border-stone-800 glass-surface space-y-3">
        {/* Search and sort row */}
        <div className="flex gap-2">
          <div className="relative flex-1">
            <i className="fa-solid fa-magnifying-glass absolute left-3 top-1/2 -translate-y-1/2 text-stone-400 text-xs" />
            <input
              type="text"
              placeholder="Search devices..."
              value={deviceSearch}
              onChange={(e) => setDeviceSearch(e.target.value)}
              className="w-full pl-9 pr-8 py-2 glass-control rounded-lg text-xs text-stone-900 dark:text-stone-100 placeholder:text-stone-400 focus:outline-none focus:ring-2 focus:ring-sage-500/50"
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
          <Select
            value={deviceSort}
            onChange={(e) => setDeviceSort(e.target.value as 'name' | 'vendor' | 'type')}
            size="sm"
            className="glass-control"
            options={[
              { value: 'vendor', label: 'Sort: Vendor' },
              { value: 'name', label: 'Sort: Name' },
              { value: 'type', label: 'Sort: Type' },
            ]}
          />
        </div>

        {/* Filter chips */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] font-bold text-stone-400 uppercase mr-1">Status:</span>
            <FilterChip
              label="Has Image"
              isActive={deviceImageStatus === 'has_image'}
              onClick={() =>
                setDeviceImageStatus(deviceImageStatus === 'has_image' ? 'all' : 'has_image')
              }
              variant="status"
              statusColor="green"
            />
            <FilterChip
              label="No Image"
              isActive={deviceImageStatus === 'no_image'}
              onClick={() =>
                setDeviceImageStatus(deviceImageStatus === 'no_image' ? 'all' : 'no_image')
              }
              variant="status"
              statusColor="amber"
            />
          </div>
          {deviceVendors.length > 0 && <div className="h-6 w-px bg-stone-200 dark:bg-stone-700" />}
          {deviceVendors.length > 0 && (
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="text-[11px] font-bold text-stone-400 uppercase mr-1">Vendor:</span>
              {deviceVendors.map((vendor) => (
                <FilterChip
                  key={vendor}
                  label={vendor}
                  isActive={selectedDeviceVendors.has(vendor)}
                  onClick={() => toggleDeviceVendor(vendor)}
                />
              ))}
            </div>
          )}
          {hasDeviceFilters && (
            <button
              onClick={clearDeviceFilters}
              className="text-[11px] text-red-500 hover:text-red-600 font-bold uppercase"
            >
              Clear
            </button>
          )}
        </div>
      </div>

      {/* Device list */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3 custom-scrollbar">
        {filteredDevices.map((device) => (
          <DeviceCard
            key={device.id}
            device={device}
            assignedImages={imagesByDevice.get(device.id) || []}
            isSelected={selectedDeviceId === device.id}
            onSelect={() => setSelectedDeviceId(device.id)}
            onUnassignImage={onUnassignImage}
            onSetDefaultImage={(imageId) => onSetDefaultImage(imageId, device.id)}
          />
        ))}
        {filteredDevices.length === 0 && (
          <div className="text-center py-8">
            <i className="fa-solid fa-search text-2xl text-stone-300 dark:text-stone-700 mb-2" />
            <p className="text-xs text-stone-500">No devices match your filters</p>
          </div>
        )}
      </div>
    </div>
  );
};

export default DeviceCatalogView;
