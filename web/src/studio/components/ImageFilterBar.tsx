import React, { useMemo } from 'react';
import FilterChip from './FilterChip';
import { DeviceModel, ImageLibraryEntry } from '../types';
import { getImageDeviceIds } from '../../utils/deviceModels';

export type ImageAssignmentFilter = 'all' | 'unassigned' | 'assigned';
export type ImageSortOption = 'name' | 'vendor' | 'kind' | 'date';

interface ImageFilterBarProps {
  images: ImageLibraryEntry[];
  devices: DeviceModel[];
  searchQuery: string;
  onSearchChange: (query: string) => void;
  selectedVendors: Set<string>;
  onVendorToggle: (vendor: string) => void;
  selectedKinds: Set<string>;
  onKindToggle: (kind: string) => void;
  assignmentFilter: ImageAssignmentFilter;
  onAssignmentFilterChange: (filter: ImageAssignmentFilter) => void;
  sortOption: ImageSortOption;
  onSortChange: (sort: ImageSortOption) => void;
  onClearAll: () => void;
}

const ImageFilterBar: React.FC<ImageFilterBarProps> = ({
  images,
  devices,
  searchQuery,
  onSearchChange,
  selectedVendors,
  onVendorToggle,
  selectedKinds,
  onKindToggle,
  assignmentFilter,
  onAssignmentFilterChange,
  sortOption,
  onSortChange,
  onClearAll,
}) => {
  // Extract unique values from images
  const { vendors, kinds, assignmentCounts } = useMemo(() => {
    const vendorSet = new Set<string>();
    const kindSet = new Set<string>();
    const deviceVendorById = new Map(
      devices
        .filter((device) => !!device.vendor)
        .map((device) => [device.id, String(device.vendor)])
    );
    let unassigned = 0;
    let assigned = 0;

    images.forEach((img) => {
      if (img.vendor) vendorSet.add(img.vendor);
      getImageDeviceIds(img).forEach((deviceId) => {
        const fallbackVendor = deviceVendorById.get(deviceId);
        if (fallbackVendor) vendorSet.add(fallbackVendor);
      });
      kindSet.add(img.kind);
      if (img.device_id) {
        assigned++;
      } else {
        unassigned++;
      }
    });

    return {
      vendors: Array.from(vendorSet).sort(),
      kinds: Array.from(kindSet).sort(),
      assignmentCounts: { unassigned, assigned, all: images.length },
    };
  }, [images, devices]);

  const hasActiveFilters =
    searchQuery.length > 0 ||
    selectedVendors.size > 0 ||
    selectedKinds.size > 0 ||
    assignmentFilter !== 'all';

  const kindLabels: Record<string, string> = {
    docker: 'Docker',
    qcow2: 'QCOW2',
  };

  return (
    <div className="glass-surface border-b border-stone-200 dark:border-stone-800 p-4 space-y-4">
      {/* Search bar and sort */}
      <div className="flex gap-2">
        <div className="relative flex-1">
          <i className="fa-solid fa-magnifying-glass absolute left-3 top-1/2 -translate-y-1/2 text-stone-400 text-sm" />
          <input
            type="text"
            placeholder="Search images by name, version, or vendor..."
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            className="w-full pl-10 pr-10 py-2.5 glass-control rounded-lg text-sm text-stone-900 dark:text-stone-100 placeholder:text-stone-400 dark:placeholder:text-stone-500 focus:outline-none focus:ring-2 focus:ring-sage-500/50 focus:border-sage-500"
          />
          {searchQuery && (
            <button
              onClick={() => onSearchChange('')}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-stone-400 hover:text-stone-600 dark:hover:text-stone-300"
            >
              <i className="fa-solid fa-xmark" />
            </button>
          )}
        </div>
        <select
          value={sortOption}
          onChange={(e) => onSortChange(e.target.value as ImageSortOption)}
          className="px-3 py-2.5 glass-control rounded-lg text-sm text-stone-700 dark:text-stone-300 focus:outline-none focus:ring-2 focus:ring-sage-500/50"
        >
          <option value="vendor">Sort: Vendor</option>
          <option value="name">Sort: Name</option>
          <option value="kind">Sort: Type</option>
          <option value="date">Sort: Date</option>
        </select>
      </div>

      {/* Filter chips row */}
      <div className="flex flex-wrap items-center gap-3">
        {/* Assignment status */}
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] font-bold text-stone-400 uppercase mr-1">Status:</span>
          <FilterChip
            label="All"
            isActive={assignmentFilter === 'all'}
            onClick={() => onAssignmentFilterChange('all')}
            count={assignmentCounts.all}
          />
          <FilterChip
            label="Unassigned"
            isActive={assignmentFilter === 'unassigned'}
            onClick={() => onAssignmentFilterChange('unassigned')}
            count={assignmentCounts.unassigned}
            variant="status"
            statusColor="amber"
          />
          <FilterChip
            label="Assigned"
            isActive={assignmentFilter === 'assigned'}
            onClick={() => onAssignmentFilterChange('assigned')}
            count={assignmentCounts.assigned}
            variant="status"
            statusColor="green"
          />
        </div>

        {/* Divider */}
        <div className="h-6 w-px bg-stone-200 dark:bg-stone-700" />

        {/* Image type */}
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] font-bold text-stone-400 uppercase mr-1">Type:</span>
          {kinds.map((kind) => (
            <FilterChip
              key={kind}
              label={kindLabels[kind] || kind}
              isActive={selectedKinds.has(kind)}
              onClick={() => onKindToggle(kind)}
            />
          ))}
        </div>

        {/* Divider */}
        {vendors.length > 0 && <div className="h-6 w-px bg-stone-200 dark:bg-stone-700" />}

        {/* Vendors (if any detected) */}
        {vendors.length > 0 && (
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="text-[10px] font-bold text-stone-400 uppercase mr-1">Vendor:</span>
            {vendors.map((vendor) => (
              <FilterChip
                key={vendor}
                label={vendor}
                isActive={selectedVendors.has(vendor)}
                onClick={() => onVendorToggle(vendor)}
              />
            ))}
          </div>
        )}

        {/* Clear all */}
        {hasActiveFilters && (
          <>
            <div className="flex-1" />
            <button
              onClick={onClearAll}
              className="text-[10px] font-bold text-red-500 hover:text-red-600 dark:text-red-400 dark:hover:text-red-300 uppercase tracking-wide"
            >
              <i className="fa-solid fa-xmark mr-1" />
              Clear filters
            </button>
          </>
        )}
      </div>
    </div>
  );
};

export default ImageFilterBar;
