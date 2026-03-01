import { useMemo, useState } from 'react';
import { DeviceModel, ImageLibraryEntry } from '../../types';
import { usePersistedState, usePersistedSet } from '../../hooks/usePersistedState';
import type { ImageAssignmentFilter, ImageSortOption } from '../ImageFilterBar';
import {
  isInstantiableImageKind,
} from '../../../utils/deviceModels';
import { PendingQcow2Upload } from './deviceManagerTypes';

interface UseImageFiltersArgs {
  imageLibrary: ImageLibraryEntry[];
  deviceModels: DeviceModel[];
  runnableImageLibrary: ImageLibraryEntry[];
  resolveImageDeviceIds: (image: ImageLibraryEntry) => string[];
  imageVendorsById: Map<string, string[]>;
  isBuildJobsMode: boolean;
  pendingQcow2Uploads: PendingQcow2Upload[];
}

export function useImageFilters({
  runnableImageLibrary,
  resolveImageDeviceIds,
  imageVendorsById,
  isBuildJobsMode,
  pendingQcow2Uploads,
}: UseImageFiltersArgs) {
  // Image filters (persisted to localStorage)
  const [imageSearch, setImageSearch] = useState('');
  const [selectedImageVendors, toggleImageVendor, clearImageVendors] = usePersistedSet('archetype:filters:image:vendors');
  const [selectedImageKinds, toggleImageKind, clearImageKinds] = usePersistedSet('archetype:filters:image:kinds');
  const [imageAssignmentFilter, setImageAssignmentFilter] = usePersistedState<ImageAssignmentFilter>('archetype:filters:image:assignment', 'all');
  const [imageSort, setImageSort] = usePersistedState<ImageSortOption>('archetype:filters:image:sort', 'vendor');

  const selectedRunnableImageKinds = useMemo(() => {
    const kinds = new Set<string>();
    selectedImageKinds.forEach((kind) => {
      if (isInstantiableImageKind(kind)) kinds.add(kind);
    });
    return kinds;
  }, [selectedImageKinds]);

  // Filter and sort images
  const filteredImages = useMemo(() => {
    const filtered = runnableImageLibrary.filter((img) => {
      const imgVendors = imageVendorsById.get(img.id) || [];

      // Search filter
      if (imageSearch) {
        const query = imageSearch.toLowerCase();
        const matchesFilename = img.filename?.toLowerCase().includes(query);
        const matchesRef = img.reference?.toLowerCase().includes(query);
        const matchesVersion = img.version?.toLowerCase().includes(query);
        const matchesVendor = imgVendors.some((vendor) => vendor.toLowerCase().includes(query));
        if (!matchesFilename && !matchesRef && !matchesVersion && !matchesVendor) {
          return false;
        }
      }

      // Vendor filter
      if (selectedImageVendors.size > 0 && !imgVendors.some((vendor) => selectedImageVendors.has(vendor))) {
        return false;
      }

      // Kind filter
      if (selectedRunnableImageKinds.size > 0 && !selectedRunnableImageKinds.has(img.kind)) {
        return false;
      }

      // Assignment filter
      const hasAssignedDevices = resolveImageDeviceIds(img).length > 0;
      if (imageAssignmentFilter === 'unassigned' && hasAssignedDevices) return false;
      if (imageAssignmentFilter === 'assigned' && !hasAssignedDevices) return false;

      return true;
    });

    // Sort images
    return filtered.sort((a, b) => {
      const aPrimaryVendor = (imageVendorsById.get(a.id) || [])[0] || '';
      const bPrimaryVendor = (imageVendorsById.get(b.id) || [])[0] || '';
      switch (imageSort) {
        case 'name':
          return (a.reference || a.filename || '').localeCompare(b.reference || b.filename || '');
        case 'vendor':
          return aPrimaryVendor.localeCompare(bPrimaryVendor) || (a.reference || '').localeCompare(b.reference || '');
        case 'kind':
          return a.kind.localeCompare(b.kind) || (a.reference || '').localeCompare(b.reference || '');
        case 'date':
          return (b.uploaded_at || '').localeCompare(a.uploaded_at || '');
        default:
          return 0;
      }
    });
  }, [
    runnableImageLibrary,
    imageSearch,
    selectedImageVendors,
    selectedRunnableImageKinds,
    imageAssignmentFilter,
    imageSort,
    imageVendorsById,
    resolveImageDeviceIds,
  ]);

  const filteredPendingQcow2Uploads = useMemo(() => {
    if (isBuildJobsMode) return [];
    if (imageAssignmentFilter === 'assigned') return [];
    if (selectedImageVendors.size > 0) return [];
    if (selectedRunnableImageKinds.size > 0 && !selectedRunnableImageKinds.has('qcow2')) return [];

    const query = imageSearch.trim().toLowerCase();
    return pendingQcow2Uploads
      .filter((item) => !query || item.filename.toLowerCase().includes(query))
      .sort((a, b) => b.createdAt - a.createdAt);
  }, [
    isBuildJobsMode,
    imageAssignmentFilter,
    selectedImageVendors,
    selectedRunnableImageKinds,
    imageSearch,
    pendingQcow2Uploads,
  ]);

  const clearImageFilters = () => {
    setImageSearch('');
    clearImageVendors();
    clearImageKinds();
    setImageAssignmentFilter('all');
  };

  return {
    imageSearch,
    setImageSearch,
    selectedImageVendors,
    toggleImageVendor,
    selectedImageKinds,
    toggleImageKind,
    imageAssignmentFilter,
    setImageAssignmentFilter,
    imageSort,
    setImageSort,
    filteredImages,
    filteredPendingQcow2Uploads,
    clearImageFilters,
  };
}
