import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import React from 'react';
import ImageLibraryView from './ImageLibraryView';
import type { DeviceModel, ImageLibraryEntry } from '../../types';
import { DeviceType } from '../../types';
import type { ImageAssignmentFilter, ImageSortOption } from '../ImageFilterBar';
import type { PendingQcow2Upload } from './deviceManagerTypes';

// Mock ImageCard
vi.mock('../ImageCard', () => ({
  default: ({
    image,
    isPending,
    pendingMessage,
    showSyncStatus,
  }: {
    image: ImageLibraryEntry;
    isPending?: boolean;
    pendingMessage?: string;
    showSyncStatus?: boolean;
  }) => (
    <div data-testid={`image-card-${image.id}`}>
      <span data-testid="image-ref">{image.reference}</span>
      {isPending && <span data-testid="pending">{pendingMessage}</span>}
      {showSyncStatus && <span data-testid="sync-status" />}
    </div>
  ),
}));

// Mock ImageFilterBar
vi.mock('../ImageFilterBar', () => ({
  default: ({
    searchQuery,
    onSearchChange,
  }: {
    searchQuery: string;
    onSearchChange: (v: string) => void;
  }) => (
    <div data-testid="image-filter-bar">
      <input
        data-testid="search-input"
        value={searchQuery}
        onChange={(e) => onSearchChange(e.target.value)}
      />
    </div>
  ),
}));

// ============================================================================
// Helpers
// ============================================================================

function makeDevice(overrides: Partial<DeviceModel> = {}): DeviceModel {
  return {
    id: 'ceos',
    type: DeviceType.ROUTER,
    name: 'Arista cEOS',
    icon: 'router',
    versions: ['4.28.0F'],
    isActive: true,
    vendor: 'arista',
    ...overrides,
  };
}

function makeImage(overrides: Partial<ImageLibraryEntry> = {}): ImageLibraryEntry {
  return {
    id: 'img-1',
    kind: 'docker',
    reference: 'ceos:4.28.0F',
    filename: 'ceos.tar',
    device_id: null,
    uploaded_at: '2026-01-01T00:00:00Z',
    vendor: null,
    version: null,
    ...overrides,
  };
}

function makePending(overrides: Partial<PendingQcow2Upload> = {}): PendingQcow2Upload {
  return {
    tempId: 'pending-1',
    filename: 'veos.qcow2',
    progress: 50,
    phase: 'uploading',
    createdAt: Date.now(),
    ...overrides,
  };
}

function defaultProps() {
  return {
    runnableImageLibrary: [] as ImageLibraryEntry[],
    deviceModels: [] as DeviceModel[],
    filteredImages: [] as ImageLibraryEntry[],
    unassignedImages: [] as ImageLibraryEntry[],
    assignedImagesByDevice: new Map<string, ImageLibraryEntry[]>(),
    filteredPendingQcow2Uploads: [] as PendingQcow2Upload[],
    imageSearch: '',
    setImageSearch: vi.fn(),
    selectedImageVendors: new Set<string>(),
    toggleImageVendor: vi.fn(),
    selectedImageKinds: new Set<string>(),
    toggleImageKind: vi.fn(),
    imageAssignmentFilter: 'all' as ImageAssignmentFilter,
    setImageAssignmentFilter: vi.fn(),
    imageSort: 'name' as ImageSortOption,
    setImageSort: vi.fn(),
    clearImageFilters: vi.fn(),
    onUnassignImage: vi.fn(),
    onSetDefaultImage: vi.fn(),
    onDeleteImage: vi.fn(),
    onRefresh: vi.fn(),
    showSyncStatus: false,
  };
}

describe('ImageLibraryView', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Empty state ──

  it('shows empty state when filteredImages is empty', () => {
    render(<ImageLibraryView {...defaultProps()} />);
    expect(screen.getByText('No images found')).toBeInTheDocument();
    expect(screen.getByText('Upload Docker or QCOW2 images to get started')).toBeInTheDocument();
  });

  it('does not show empty state when filteredImages has entries', () => {
    const img = makeImage();
    const props = defaultProps();
    props.filteredImages = [img];
    props.unassignedImages = [img];
    render(<ImageLibraryView {...props} />);
    expect(screen.queryByText('No images found')).not.toBeInTheDocument();
  });

  // ── Unassigned images section ──

  it('renders unassigned images section with correct count', () => {
    const img1 = makeImage({ id: 'img-1', reference: 'image-one' });
    const img2 = makeImage({ id: 'img-2', reference: 'image-two' });
    const props = defaultProps();
    props.filteredImages = [img1, img2];
    props.unassignedImages = [img1, img2];
    render(<ImageLibraryView {...props} />);

    expect(screen.getByText('Unassigned Images')).toBeInTheDocument();
    expect(screen.getByText('(2)')).toBeInTheDocument();
    expect(screen.getByTestId('image-card-img-1')).toBeInTheDocument();
    expect(screen.getByTestId('image-card-img-2')).toBeInTheDocument();
  });

  it('hides unassigned section when no unassigned images and no pending uploads', () => {
    const img = makeImage({ id: 'img-1', device_id: 'ceos' });
    const props = defaultProps();
    props.filteredImages = [img];
    props.assignedImagesByDevice = new Map([['ceos', [img]]]);
    render(<ImageLibraryView {...props} />);

    expect(screen.queryByText('Unassigned Images')).not.toBeInTheDocument();
  });

  // ── Pending uploads ──

  it('renders pending qcow2 uploads with uploading message', () => {
    const pending = makePending({ tempId: 'p-1', filename: 'test.qcow2', progress: 75, phase: 'uploading' });
    const props = defaultProps();
    props.filteredPendingQcow2Uploads = [pending];
    props.filteredImages = [makeImage()]; // avoid empty state
    render(<ImageLibraryView {...props} />);

    expect(screen.getByText('Unassigned Images')).toBeInTheDocument();
    expect(screen.getByTestId('image-card-p-1')).toBeInTheDocument();
    expect(screen.getByText('Uploading 75%')).toBeInTheDocument();
  });

  it('renders pending qcow2 uploads with processing message', () => {
    const pending = makePending({ tempId: 'p-2', phase: 'processing' });
    const props = defaultProps();
    props.filteredPendingQcow2Uploads = [pending];
    props.filteredImages = [makeImage()];
    render(<ImageLibraryView {...props} />);

    expect(screen.getByText('Processing image (validation and metadata)...')).toBeInTheDocument();
  });

  it('counts pending uploads in unassigned section header', () => {
    const pending = makePending();
    const img = makeImage();
    const props = defaultProps();
    props.filteredPendingQcow2Uploads = [pending];
    props.unassignedImages = [img];
    props.filteredImages = [img];
    render(<ImageLibraryView {...props} />);

    // 1 unassigned + 1 pending = 2
    expect(screen.getByText('(2)')).toBeInTheDocument();
  });

  // ── Assigned images by device ──

  it('renders assigned images grouped by device', () => {
    const device = makeDevice({ id: 'ceos', name: 'Arista cEOS' });
    const img = makeImage({ id: 'img-a', device_id: 'ceos', reference: 'ceos:latest' });
    const props = defaultProps();
    props.deviceModels = [device];
    props.filteredImages = [img];
    props.assignedImagesByDevice = new Map([['ceos', [img]]]);
    render(<ImageLibraryView {...props} />);

    expect(screen.getByText('Arista cEOS')).toBeInTheDocument();
    expect(screen.getByText('(1)')).toBeInTheDocument();
    expect(screen.getByTestId('image-card-img-a')).toBeInTheDocument();
  });

  it('falls back to deviceId when device name not found', () => {
    const img = makeImage({ id: 'img-b', device_id: 'unknown-device' });
    const props = defaultProps();
    props.filteredImages = [img];
    props.assignedImagesByDevice = new Map([['unknown-device', [img]]]);
    render(<ImageLibraryView {...props} />);

    expect(screen.getByText('unknown-device')).toBeInTheDocument();
  });

  it('renders multiple device groups', () => {
    const device1 = makeDevice({ id: 'ceos', name: 'Arista cEOS' });
    const device2 = makeDevice({ id: 'srlinux', name: 'Nokia SR Linux' });
    const img1 = makeImage({ id: 'img-1', device_id: 'ceos' });
    const img2 = makeImage({ id: 'img-2', device_id: 'srlinux' });
    const props = defaultProps();
    props.deviceModels = [device1, device2];
    props.filteredImages = [img1, img2];
    props.assignedImagesByDevice = new Map([
      ['ceos', [img1]],
      ['srlinux', [img2]],
    ]);
    render(<ImageLibraryView {...props} />);

    expect(screen.getByText('Arista cEOS')).toBeInTheDocument();
    expect(screen.getByText('Nokia SR Linux')).toBeInTheDocument();
  });

  // ── Filter bar ──

  it('renders the image filter bar', () => {
    render(<ImageLibraryView {...defaultProps()} />);
    expect(screen.getByTestId('image-filter-bar')).toBeInTheDocument();
  });

  // ── Sync status passthrough ──

  it('passes showSyncStatus to image cards', () => {
    const img = makeImage({ id: 'img-sync' });
    const props = defaultProps();
    props.filteredImages = [img];
    props.unassignedImages = [img];
    props.showSyncStatus = true;
    render(<ImageLibraryView {...props} />);

    expect(screen.getByTestId('sync-status')).toBeInTheDocument();
  });
});
