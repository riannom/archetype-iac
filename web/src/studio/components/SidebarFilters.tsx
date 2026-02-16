import React, { useMemo, useState } from 'react';
import FilterChip from './FilterChip';
import { DeviceModel, ImageLibraryEntry } from '../types';
import { getImageDeviceIds, isInstantiableImageKind } from '../../utils/deviceModels';

export type ImageStatus = 'all' | 'has_image' | 'has_default' | 'no_image';

interface SidebarFiltersProps {
  devices: DeviceModel[];
  imageLibrary: ImageLibraryEntry[];
  searchQuery: string;
  onSearchChange: (query: string) => void;
  selectedVendors: Set<string>;
  onVendorToggle: (vendor: string) => void;
  selectedTypes: Set<string>;
  onTypeToggle: (type: string) => void;
  imageStatus: ImageStatus;
  onImageStatusChange: (status: ImageStatus) => void;
  onClearAll: () => void;
}

const SidebarFilters: React.FC<SidebarFiltersProps> = ({
  devices,
  imageLibrary,
  searchQuery,
  onSearchChange,
  selectedVendors,
  onVendorToggle,
  selectedTypes,
  onTypeToggle,
  imageStatus,
  onImageStatusChange,
  onClearAll,
}) => {
  const [isExpanded, setIsExpanded] = useState(false);

  // Extract unique vendors and types from devices
  const { vendors, types, vendorCounts, typeCounts } = useMemo(() => {
    const vendorSet = new Set<string>();
    const typeSet = new Set<string>();
    const vCounts: Record<string, number> = {};
    const tCounts: Record<string, number> = {};

    devices.forEach((device) => {
      if (device.vendor) {
        vendorSet.add(device.vendor);
        vCounts[device.vendor] = (vCounts[device.vendor] || 0) + 1;
      }
      if (device.type) {
        typeSet.add(device.type);
        tCounts[device.type] = (tCounts[device.type] || 0) + 1;
      }
    });

    return {
      vendors: Array.from(vendorSet).sort(),
      types: Array.from(typeSet).sort(),
      vendorCounts: vCounts,
      typeCounts: tCounts,
    };
  }, [devices]);

  // Count devices by image status
  const statusCounts = useMemo(() => {
    const counts = { has_image: 0, has_default: 0, no_image: 0 };
    const deviceImageMap = new Map<string, { imageKinds: Set<string>; defaultKinds: Set<string> }>();

    // Build a map of device_id to image info (uses compatible_devices for shared images)
    imageLibrary.forEach((img) => {
      if (!isInstantiableImageKind(img.kind)) {
        return;
      }
      const imageKind = (img.kind || '').toLowerCase();
      getImageDeviceIds(img).forEach((devId) => {
        const existing = deviceImageMap.get(devId) || { imageKinds: new Set<string>(), defaultKinds: new Set<string>() };
        existing.imageKinds.add(imageKind);
        if (img.is_default) {
          existing.defaultKinds.add(imageKind);
        }
        deviceImageMap.set(devId, existing);
      });
    });

    devices.forEach((device) => {
      const supportedKinds = device.supportedImageKinds
        ?.map((kind) => kind.toLowerCase())
        .filter((kind) => isInstantiableImageKind(kind));
      const allowedKinds = new Set((supportedKinds && supportedKinds.length > 0) ? supportedKinds : ['docker', 'qcow2']);
      const info = deviceImageMap.get(device.id);
      const hasDefault = info ? Array.from(allowedKinds).some((kind) => info.defaultKinds.has(kind)) : false;
      const hasImage = info ? Array.from(allowedKinds).some((kind) => info.imageKinds.has(kind)) : false;

      if (hasDefault) {
        counts.has_default++;
        counts.has_image++;
      } else if (hasImage) {
        counts.has_image++;
      } else {
        counts.no_image++;
      }
    });

    return counts;
  }, [devices, imageLibrary]);

  // 'has_image' is the default - any deviation counts as an active filter
  const hasActiveFilters =
    searchQuery.length > 0 ||
    selectedVendors.size > 0 ||
    selectedTypes.size > 0 ||
    imageStatus !== 'has_image';

  const typeLabels: Record<string, string> = {
    router: 'Routers',
    switch: 'Switches',
    firewall: 'Firewalls',
    host: 'Hosts',
    container: 'Containers',
    external: 'External',
  };

  return (
    <div className="border-b border-stone-200 dark:border-stone-800 bg-stone-50/80 dark:bg-stone-900/50">
      {/* Search bar */}
      <div className="p-3">
        <div className="relative">
          <i className="fa-solid fa-magnifying-glass absolute left-3 top-1/2 -translate-y-1/2 text-stone-500 dark:text-stone-400 text-xs" />
          <input
            type="text"
            placeholder="Search devices, vendors, tags..."
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            className="w-full pl-9 pr-8 py-2 bg-white dark:bg-stone-800 border border-stone-200 dark:border-stone-700 rounded-lg text-xs text-stone-900 dark:text-stone-100 placeholder:text-stone-400 dark:placeholder:text-stone-500 focus:outline-none focus:ring-2 focus:ring-sage-500/50 focus:border-sage-500"
          />
          {searchQuery && (
            <button
              onClick={() => onSearchChange('')}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-stone-500 hover:text-stone-700 dark:hover:text-stone-300"
            >
              <i className="fa-solid fa-xmark text-xs" />
            </button>
          )}
        </div>
      </div>

      {/* Filter toggle */}
      <div
        className="mx-3 mb-2 px-2 py-1.5 rounded-lg border"
        style={{
          backgroundColor: 'color-mix(in srgb, var(--color-accent-400) 24%, transparent)',
          borderColor: 'color-mix(in srgb, var(--color-accent-500) 28%, transparent)',
        }}
      >
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="flex items-center gap-2 text-[10px] font-bold text-stone-700 dark:text-stone-400 uppercase tracking-wide hover:text-stone-900 dark:hover:text-stone-200 transition-colors"
        >
          <i className={`fa-solid fa-filter ${hasActiveFilters ? 'text-sage-500' : ''}`} />
          <span>Filters</span>
          {hasActiveFilters && (
            <span className="px-1.5 py-0.5 bg-sage-500 text-white rounded text-[9px]">
              {(searchQuery.length > 0 ? 1 : 0) + selectedVendors.size + selectedTypes.size + (imageStatus !== 'has_image' ? 1 : 0)}
            </span>
          )}
          <i className={`fa-solid fa-chevron-down text-[8px] transition-transform ${isExpanded ? '' : '-rotate-90'}`} />
        </button>
      </div>

      {/* Filter chips */}
      {isExpanded && (
        <div className="px-3 pb-3 space-y-3 animate-in fade-in slide-in-from-top-1 duration-200">
          {/* Image Status */}
          <div>
            <div className="text-[9px] font-bold text-stone-600 dark:text-stone-500 uppercase tracking-widest mb-1.5">
              Image Status
            </div>
            <div className="flex flex-wrap gap-1.5">
              <FilterChip
                label="Has Default"
                isActive={imageStatus === 'has_default'}
                onClick={() => onImageStatusChange(imageStatus === 'has_default' ? 'all' : 'has_default')}
                count={statusCounts.has_default}
                variant="status"
                statusColor="green"
              />
              <FilterChip
                label="Has Image"
                isActive={imageStatus === 'has_image'}
                onClick={() => onImageStatusChange(imageStatus === 'has_image' ? 'all' : 'has_image')}
                count={statusCounts.has_image}
                variant="status"
                statusColor="blue"
              />
              <FilterChip
                label="No Image"
                isActive={imageStatus === 'no_image'}
                onClick={() => onImageStatusChange(imageStatus === 'no_image' ? 'all' : 'no_image')}
                count={statusCounts.no_image}
                variant="status"
                statusColor="amber"
              />
            </div>
          </div>

          {/* Vendors */}
          <div>
            <div className="text-[9px] font-bold text-stone-600 dark:text-stone-500 uppercase tracking-widest mb-1.5">
              Vendor
            </div>
            <div className="flex flex-wrap gap-1.5">
              {vendors.slice(0, 8).map((vendor) => (
                <FilterChip
                  key={vendor}
                  label={vendor}
                  isActive={selectedVendors.has(vendor)}
                  onClick={() => onVendorToggle(vendor)}
                  count={vendorCounts[vendor]}
                />
              ))}
              {vendors.length > 8 && (
                <span className="text-[10px] text-stone-600 dark:text-stone-500 self-center">
                  +{vendors.length - 8} more
                </span>
              )}
            </div>
          </div>

          {/* Types */}
          <div>
            <div className="text-[9px] font-bold text-stone-600 dark:text-stone-500 uppercase tracking-widest mb-1.5">
              Type
            </div>
            <div className="flex flex-wrap gap-1.5">
              {types.map((type) => (
                <FilterChip
                  key={type}
                  label={typeLabels[type] || type}
                  isActive={selectedTypes.has(type)}
                  onClick={() => onTypeToggle(type)}
                  count={typeCounts[type]}
                />
              ))}
            </div>
          </div>

          {/* Clear all */}
          {hasActiveFilters && (
            <button
              onClick={onClearAll}
              className="text-[10px] font-bold text-red-500 hover:text-red-600 dark:text-red-400 dark:hover:text-red-300 uppercase tracking-wide"
            >
              <i className="fa-solid fa-xmark mr-1" />
              Clear all filters
            </button>
          )}
        </div>
      )}
    </div>
  );
};

export default SidebarFilters;
