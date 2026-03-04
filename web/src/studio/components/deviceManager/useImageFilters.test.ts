/**
 * Tests for useImageFilters hook.
 *
 * These tests verify:
 * 1. Filter by vendor
 * 2. Filter by kind (container, VM)
 * 3. Filter by assignment status
 * 4. Search query filtering
 * 5. Sort ordering
 * 6. filteredImages derived state
 * 7. filteredPendingQcow2Uploads derived state
 * 8. Combined filters
 * 9. Edge cases: empty lists, no matching filters
 * 10. clearImageFilters resets all filters
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useImageFilters } from './useImageFilters';
import type { ImageLibraryEntry } from '../../types';
import type { PendingQcow2Upload } from './deviceManagerTypes';

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

// Mock isInstantiableImageKind to pass through known kinds
vi.mock('../../../utils/deviceModels', () => ({
  isInstantiableImageKind: (kind?: string | null) => {
    return ['docker', 'qcow2', 'iol', 'img'].includes(kind || '');
  },
}));

// ============================================================================
// Helpers
// ============================================================================

function makeImage(overrides: Partial<ImageLibraryEntry> = {}): ImageLibraryEntry {
  return {
    id: 'docker:ceos:4.28.0F',
    kind: 'docker',
    reference: 'ceos:4.28.0F',
    ...overrides,
  };
}

function makePendingUpload(overrides: Partial<PendingQcow2Upload> = {}): PendingQcow2Upload {
  return {
    tempId: 'temp-1',
    filename: 'iosv.qcow2',
    progress: 50,
    phase: 'uploading',
    createdAt: Date.now(),
    ...overrides,
  };
}

function defaultArgs(overrides: Partial<Parameters<typeof useImageFilters>[0]> = {}) {
  return {
    imageLibrary: [] as ImageLibraryEntry[],
    deviceModels: [],
    runnableImageLibrary: overrides.runnableImageLibrary || [
      makeImage({ id: 'img-1', kind: 'docker', reference: 'ceos:4.28.0F', filename: 'ceos.tar', version: '4.28.0F' }),
      makeImage({ id: 'img-2', kind: 'qcow2', reference: 'iosv.qcow2', filename: 'iosv.qcow2', version: '15.9' }),
      makeImage({ id: 'img-3', kind: 'docker', reference: 'srlinux:23.7', filename: 'srlinux.tar', version: '23.7' }),
    ],
    resolveImageDeviceIds: overrides.resolveImageDeviceIds || (() => []),
    imageVendorsById: overrides.imageVendorsById || new Map<string, string[]>([
      ['img-1', ['Arista']],
      ['img-2', ['Cisco']],
      ['img-3', ['Nokia']],
    ]),
    isBuildJobsMode: overrides.isBuildJobsMode ?? false,
    pendingQcow2Uploads: overrides.pendingQcow2Uploads || [],
  };
}

// ============================================================================
// Tests
// ============================================================================

describe('useImageFilters', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Initial State ──

  it('returns all images unfiltered by default', () => {
    const args = defaultArgs();
    const { result } = renderHook(() => useImageFilters(args));

    expect(result.current.filteredImages).toHaveLength(3);
    expect(result.current.imageSearch).toBe('');
    expect(result.current.selectedImageVendors.size).toBe(0);
    expect(result.current.selectedImageKinds.size).toBe(0);
    expect(result.current.imageAssignmentFilter).toBe('all');
    expect(result.current.imageSort).toBe('vendor');
  });

  // ── Search Filter ──

  describe('search filter', () => {
    it('filters by filename', () => {
      const args = defaultArgs();
      const { result } = renderHook(() => useImageFilters(args));

      act(() => {
        result.current.setImageSearch('ceos');
      });

      expect(result.current.filteredImages).toHaveLength(1);
      expect(result.current.filteredImages[0].id).toBe('img-1');
    });

    it('filters by reference', () => {
      const args = defaultArgs();
      const { result } = renderHook(() => useImageFilters(args));

      act(() => {
        result.current.setImageSearch('srlinux');
      });

      expect(result.current.filteredImages).toHaveLength(1);
      expect(result.current.filteredImages[0].id).toBe('img-3');
    });

    it('filters by version', () => {
      const args = defaultArgs();
      const { result } = renderHook(() => useImageFilters(args));

      act(() => {
        result.current.setImageSearch('15.9');
      });

      expect(result.current.filteredImages).toHaveLength(1);
      expect(result.current.filteredImages[0].id).toBe('img-2');
    });

    it('filters by vendor name in search', () => {
      const args = defaultArgs();
      const { result } = renderHook(() => useImageFilters(args));

      act(() => {
        result.current.setImageSearch('arista');
      });

      expect(result.current.filteredImages).toHaveLength(1);
      expect(result.current.filteredImages[0].id).toBe('img-1');
    });

    it('search is case-insensitive', () => {
      const args = defaultArgs();
      const { result } = renderHook(() => useImageFilters(args));

      act(() => {
        result.current.setImageSearch('CISCO');
      });

      expect(result.current.filteredImages).toHaveLength(1);
      expect(result.current.filteredImages[0].id).toBe('img-2');
    });

    it('returns no images when search matches nothing', () => {
      const args = defaultArgs();
      const { result } = renderHook(() => useImageFilters(args));

      act(() => {
        result.current.setImageSearch('nonexistent');
      });

      expect(result.current.filteredImages).toHaveLength(0);
    });
  });

  // ── Vendor Filter ──

  describe('vendor filter', () => {
    it('filters images by selected vendor', () => {
      const args = defaultArgs();
      const { result } = renderHook(() => useImageFilters(args));

      act(() => {
        result.current.toggleImageVendor('Arista');
      });

      expect(result.current.filteredImages).toHaveLength(1);
      expect(result.current.filteredImages[0].id).toBe('img-1');
    });

    it('toggles vendor off when clicked again', () => {
      const args = defaultArgs();
      const { result } = renderHook(() => useImageFilters(args));

      act(() => {
        result.current.toggleImageVendor('Arista');
      });
      expect(result.current.filteredImages).toHaveLength(1);

      act(() => {
        result.current.toggleImageVendor('Arista');
      });
      expect(result.current.filteredImages).toHaveLength(3);
    });

    it('supports multiple selected vendors', () => {
      const args = defaultArgs();
      const { result } = renderHook(() => useImageFilters(args));

      act(() => {
        result.current.toggleImageVendor('Arista');
      });
      act(() => {
        result.current.toggleImageVendor('Cisco');
      });

      expect(result.current.filteredImages).toHaveLength(2);
      const ids = result.current.filteredImages.map(i => i.id);
      expect(ids).toContain('img-1');
      expect(ids).toContain('img-2');
    });

    it('excludes images with no matching vendor', () => {
      const args = defaultArgs({
        imageVendorsById: new Map([
          ['img-1', ['Arista']],
          // img-2 and img-3 have no vendors
        ]),
      });
      const { result } = renderHook(() => useImageFilters(args));

      act(() => {
        result.current.toggleImageVendor('Arista');
      });

      expect(result.current.filteredImages).toHaveLength(1);
      expect(result.current.filteredImages[0].id).toBe('img-1');
    });
  });

  // ── Kind Filter ──

  describe('kind filter', () => {
    it('filters images by selected kind', () => {
      const args = defaultArgs();
      const { result } = renderHook(() => useImageFilters(args));

      act(() => {
        result.current.toggleImageKind('qcow2');
      });

      expect(result.current.filteredImages).toHaveLength(1);
      expect(result.current.filteredImages[0].id).toBe('img-2');
    });

    it('shows all images when no kind filter is active', () => {
      const args = defaultArgs();
      const { result } = renderHook(() => useImageFilters(args));

      expect(result.current.filteredImages).toHaveLength(3);
    });

    it('filters by docker kind', () => {
      const args = defaultArgs();
      const { result } = renderHook(() => useImageFilters(args));

      act(() => {
        result.current.toggleImageKind('docker');
      });

      expect(result.current.filteredImages).toHaveLength(2);
      result.current.filteredImages.forEach(img => {
        expect(img.kind).toBe('docker');
      });
    });
  });

  // ── Assignment Filter ──

  describe('assignment filter', () => {
    it('filters to unassigned images', () => {
      const resolveImageDeviceIds = vi.fn((img: ImageLibraryEntry) => {
        if (img.id === 'img-1') return ['ceos'];
        return [];
      });
      const args = defaultArgs({ resolveImageDeviceIds });
      const { result } = renderHook(() => useImageFilters(args));

      act(() => {
        result.current.setImageAssignmentFilter('unassigned');
      });

      expect(result.current.filteredImages).toHaveLength(2);
      const ids = result.current.filteredImages.map(i => i.id);
      expect(ids).not.toContain('img-1');
    });

    it('filters to assigned images', () => {
      const resolveImageDeviceIds = vi.fn((img: ImageLibraryEntry) => {
        if (img.id === 'img-1') return ['ceos'];
        return [];
      });
      const args = defaultArgs({ resolveImageDeviceIds });
      const { result } = renderHook(() => useImageFilters(args));

      act(() => {
        result.current.setImageAssignmentFilter('assigned');
      });

      expect(result.current.filteredImages).toHaveLength(1);
      expect(result.current.filteredImages[0].id).toBe('img-1');
    });

    it('shows all images when assignment filter is "all"', () => {
      const args = defaultArgs();
      const { result } = renderHook(() => useImageFilters(args));

      act(() => {
        result.current.setImageAssignmentFilter('assigned');
      });
      act(() => {
        result.current.setImageAssignmentFilter('all');
      });

      expect(result.current.filteredImages).toHaveLength(3);
    });
  });

  // ── Sort Ordering ──

  describe('sort ordering', () => {
    it('sorts by vendor (default)', () => {
      const args = defaultArgs();
      const { result } = renderHook(() => useImageFilters(args));

      const vendors = result.current.filteredImages.map(
        img => (args.imageVendorsById.get(img.id) || [])[0] || ''
      );
      expect(vendors).toEqual(['Arista', 'Cisco', 'Nokia']);
    });

    it('sorts by name (reference)', () => {
      const args = defaultArgs();
      const { result } = renderHook(() => useImageFilters(args));

      act(() => {
        result.current.setImageSort('name');
      });

      const refs = result.current.filteredImages.map(img => img.reference);
      const sorted = [...refs].sort((a, b) => a.localeCompare(b));
      expect(refs).toEqual(sorted);
    });

    it('sorts by kind', () => {
      const args = defaultArgs();
      const { result } = renderHook(() => useImageFilters(args));

      act(() => {
        result.current.setImageSort('kind');
      });

      const kinds = result.current.filteredImages.map(img => img.kind);
      // docker < qcow2 alphabetically
      expect(kinds[0]).toBe('docker');
    });

    it('sorts by date (newest first)', () => {
      const images = [
        makeImage({ id: 'img-old', uploaded_at: '2025-01-01T00:00:00Z', reference: 'old' }),
        makeImage({ id: 'img-new', uploaded_at: '2026-03-01T00:00:00Z', reference: 'new' }),
        makeImage({ id: 'img-mid', uploaded_at: '2025-06-01T00:00:00Z', reference: 'mid' }),
      ];
      const args = defaultArgs({
        runnableImageLibrary: images,
        imageVendorsById: new Map(),
      });
      const { result } = renderHook(() => useImageFilters(args));

      act(() => {
        result.current.setImageSort('date');
      });

      const refs = result.current.filteredImages.map(img => img.reference);
      expect(refs).toEqual(['new', 'mid', 'old']);
    });
  });

  // ── Combined Filters ──

  describe('combined filters', () => {
    it('applies vendor + search together', () => {
      const args = defaultArgs();
      const { result } = renderHook(() => useImageFilters(args));

      act(() => {
        result.current.toggleImageVendor('Arista');
      });
      act(() => {
        result.current.setImageSearch('4.28');
      });

      expect(result.current.filteredImages).toHaveLength(1);
      expect(result.current.filteredImages[0].id).toBe('img-1');
    });

    it('applies vendor + kind together', () => {
      const args = defaultArgs();
      const { result } = renderHook(() => useImageFilters(args));

      // Select Arista vendor (img-1 is docker)
      act(() => {
        result.current.toggleImageVendor('Arista');
      });
      // Filter to qcow2 only
      act(() => {
        result.current.toggleImageKind('qcow2');
      });

      // No Arista qcow2 images
      expect(result.current.filteredImages).toHaveLength(0);
    });

    it('applies vendor + kind + search + assignment together', () => {
      const resolveImageDeviceIds = vi.fn((img: ImageLibraryEntry) => {
        if (img.id === 'img-1') return ['ceos'];
        return [];
      });
      const args = defaultArgs({ resolveImageDeviceIds });
      const { result } = renderHook(() => useImageFilters(args));

      act(() => {
        result.current.toggleImageVendor('Arista');
      });
      act(() => {
        result.current.toggleImageKind('docker');
      });
      act(() => {
        result.current.setImageAssignmentFilter('assigned');
      });
      act(() => {
        result.current.setImageSearch('ceos');
      });

      expect(result.current.filteredImages).toHaveLength(1);
      expect(result.current.filteredImages[0].id).toBe('img-1');
    });
  });

  // ── filteredPendingQcow2Uploads ──

  describe('filteredPendingQcow2Uploads', () => {
    it('returns pending uploads when no filters block them', () => {
      const uploads = [
        makePendingUpload({ tempId: 'u1', filename: 'iosv.qcow2', createdAt: 1000 }),
        makePendingUpload({ tempId: 'u2', filename: 'csr1000v.qcow2', createdAt: 2000 }),
      ];
      const args = defaultArgs({ pendingQcow2Uploads: uploads });
      const { result } = renderHook(() => useImageFilters(args));

      expect(result.current.filteredPendingQcow2Uploads).toHaveLength(2);
    });

    it('sorts pending uploads by createdAt descending (newest first)', () => {
      const uploads = [
        makePendingUpload({ tempId: 'u1', filename: 'old.qcow2', createdAt: 1000 }),
        makePendingUpload({ tempId: 'u2', filename: 'new.qcow2', createdAt: 3000 }),
        makePendingUpload({ tempId: 'u3', filename: 'mid.qcow2', createdAt: 2000 }),
      ];
      const args = defaultArgs({ pendingQcow2Uploads: uploads });
      const { result } = renderHook(() => useImageFilters(args));

      expect(result.current.filteredPendingQcow2Uploads[0].tempId).toBe('u2');
      expect(result.current.filteredPendingQcow2Uploads[1].tempId).toBe('u3');
      expect(result.current.filteredPendingQcow2Uploads[2].tempId).toBe('u1');
    });

    it('returns empty when in build jobs mode', () => {
      const uploads = [makePendingUpload()];
      const args = defaultArgs({ pendingQcow2Uploads: uploads, isBuildJobsMode: true });
      const { result } = renderHook(() => useImageFilters(args));

      expect(result.current.filteredPendingQcow2Uploads).toHaveLength(0);
    });

    it('returns empty when assignment filter is "assigned"', () => {
      const uploads = [makePendingUpload()];
      const args = defaultArgs({ pendingQcow2Uploads: uploads });
      const { result } = renderHook(() => useImageFilters(args));

      act(() => {
        result.current.setImageAssignmentFilter('assigned');
      });

      expect(result.current.filteredPendingQcow2Uploads).toHaveLength(0);
    });

    it('returns empty when vendor filter is active', () => {
      const uploads = [makePendingUpload()];
      const args = defaultArgs({ pendingQcow2Uploads: uploads });
      const { result } = renderHook(() => useImageFilters(args));

      act(() => {
        result.current.toggleImageVendor('Arista');
      });

      expect(result.current.filteredPendingQcow2Uploads).toHaveLength(0);
    });

    it('returns empty when kind filter excludes qcow2', () => {
      const uploads = [makePendingUpload()];
      const args = defaultArgs({ pendingQcow2Uploads: uploads });
      const { result } = renderHook(() => useImageFilters(args));

      act(() => {
        result.current.toggleImageKind('docker');
      });

      expect(result.current.filteredPendingQcow2Uploads).toHaveLength(0);
    });

    it('shows uploads when kind filter includes qcow2', () => {
      const uploads = [makePendingUpload()];
      const args = defaultArgs({ pendingQcow2Uploads: uploads });
      const { result } = renderHook(() => useImageFilters(args));

      act(() => {
        result.current.toggleImageKind('qcow2');
      });

      expect(result.current.filteredPendingQcow2Uploads).toHaveLength(1);
    });

    it('filters pending uploads by search query', () => {
      const uploads = [
        makePendingUpload({ tempId: 'u1', filename: 'iosv.qcow2' }),
        makePendingUpload({ tempId: 'u2', filename: 'csr1000v.qcow2' }),
      ];
      const args = defaultArgs({ pendingQcow2Uploads: uploads });
      const { result } = renderHook(() => useImageFilters(args));

      act(() => {
        result.current.setImageSearch('iosv');
      });

      expect(result.current.filteredPendingQcow2Uploads).toHaveLength(1);
      expect(result.current.filteredPendingQcow2Uploads[0].filename).toBe('iosv.qcow2');
    });
  });

  // ── Clear Filters ──

  describe('clearImageFilters', () => {
    it('resets all filters to defaults', () => {
      const args = defaultArgs();
      const { result } = renderHook(() => useImageFilters(args));

      // Apply various filters
      act(() => {
        result.current.setImageSearch('test');
      });
      act(() => {
        result.current.toggleImageVendor('Arista');
      });
      act(() => {
        result.current.toggleImageKind('docker');
      });
      act(() => {
        result.current.setImageAssignmentFilter('unassigned');
      });

      // Clear
      act(() => {
        result.current.clearImageFilters();
      });

      expect(result.current.imageSearch).toBe('');
      expect(result.current.selectedImageVendors.size).toBe(0);
      expect(result.current.selectedImageKinds.size).toBe(0);
      expect(result.current.imageAssignmentFilter).toBe('all');
      expect(result.current.filteredImages).toHaveLength(3);
    });
  });

  // ── Edge Cases ──

  describe('edge cases', () => {
    it('handles empty image list', () => {
      const args = defaultArgs({ runnableImageLibrary: [] });
      const { result } = renderHook(() => useImageFilters(args));

      expect(result.current.filteredImages).toHaveLength(0);
    });

    it('handles images with no vendor mapping', () => {
      const args = defaultArgs({
        imageVendorsById: new Map(),
      });
      const { result } = renderHook(() => useImageFilters(args));

      // With no vendor filter active, all images still show
      expect(result.current.filteredImages).toHaveLength(3);
    });

    it('handles images with missing optional fields', () => {
      const images = [
        makeImage({ id: 'img-bare', kind: 'docker', reference: 'bare', filename: undefined, version: undefined }),
      ];
      const args = defaultArgs({
        runnableImageLibrary: images,
        imageVendorsById: new Map(),
      });
      const { result } = renderHook(() => useImageFilters(args));

      expect(result.current.filteredImages).toHaveLength(1);
    });

    it('handles empty search query after having a search set', () => {
      const args = defaultArgs();
      const { result } = renderHook(() => useImageFilters(args));

      act(() => {
        result.current.setImageSearch('ceos');
      });
      expect(result.current.filteredImages).toHaveLength(1);

      act(() => {
        result.current.setImageSearch('');
      });
      expect(result.current.filteredImages).toHaveLength(3);
    });
  });
});
