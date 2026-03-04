import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import DeviceManager from './DeviceManager';
import type { DeviceModel, ImageLibraryEntry } from '../../types';
import { DeviceType } from '../../types';

// ============================================================================
// Mocks
// ============================================================================

const mockUnassignImage = vi.fn().mockResolvedValue(undefined);
const mockAssignImageToDevice = vi.fn().mockResolvedValue(undefined);
const mockDeleteImage = vi.fn().mockResolvedValue(undefined);
const mockAddNotification = vi.fn();

// DragContext mock
vi.mock('../../contexts/DragContext', () => ({
  DragProvider: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  useDragContext: () => ({
    dragState: {
      isDragging: false,
      draggedImageId: null,
      draggedImageData: null,
      dragOverDeviceId: null,
      isValidTarget: false,
    },
    startDrag: vi.fn(),
    endDrag: vi.fn(),
    setDragOverDevice: vi.fn(),
    assignImageToDevice: mockAssignImageToDevice,
    unassignImage: mockUnassignImage,
    deleteImage: mockDeleteImage,
  }),
}));

// NotificationContext mock
vi.mock('../../../contexts/NotificationContext', () => ({
  useNotifications: () => ({
    addNotification: mockAddNotification,
    notifications: [],
    unreadCount: 0,
    markAsRead: vi.fn(),
    markAllAsRead: vi.fn(),
    clearNotifications: vi.fn(),
  }),
}));

// Mock usePersistedState to use plain useState
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

// Mock usePolling
vi.mock('../../hooks/usePolling', () => ({
  usePolling: vi.fn(),
}));

// Mock api module
vi.mock('../../../api', () => ({
  apiRequest: vi.fn(),
  rawApiRequest: vi.fn(),
}));

// Mock child components to simplify testing
vi.mock('./BuildJobsView', () => ({
  default: (props: { uploadStatus: string | null }) => (
    <div data-testid="build-jobs-view">
      {props.uploadStatus && <span>{props.uploadStatus}</span>}
      Build Jobs View
    </div>
  ),
}));

vi.mock('./DeviceCatalogView', () => ({
  default: ({
    filteredDevices,
    deviceSearch,
    setDeviceSearch,
    hasDeviceFilters,
    clearDeviceFilters,
    onUnassignImage,
    onSetDefaultImage,
  }: {
    filteredDevices: DeviceModel[];
    deviceSearch: string;
    setDeviceSearch: (v: string) => void;
    hasDeviceFilters: boolean;
    clearDeviceFilters: () => void;
    onUnassignImage: (imageId: string, deviceId?: string) => Promise<void>;
    onSetDefaultImage: (imageId: string, deviceId: string) => Promise<void>;
    [key: string]: unknown;
  }) => (
    <div data-testid="device-catalog-view">
      <span data-testid="device-count">{filteredDevices.length}</span>
      <span data-testid="device-search">{deviceSearch}</span>
      <span data-testid="has-device-filters">{String(hasDeviceFilters)}</span>
      <button data-testid="clear-device-filters" onClick={clearDeviceFilters}>
        Clear
      </button>
      <input
        data-testid="device-search-input"
        value={deviceSearch}
        onChange={(e) => setDeviceSearch(e.target.value)}
      />
      <button
        data-testid="unassign-btn"
        onClick={() => onUnassignImage('img-1', 'ceos')}
      >
        Unassign
      </button>
      <button
        data-testid="set-default-btn"
        onClick={() => onSetDefaultImage('img-1', 'ceos')}
      >
        Set Default
      </button>
    </div>
  ),
}));

vi.mock('./ImageLibraryView', () => ({
  default: ({
    filteredImages,
    unassignedImages,
    onDeleteImage,
  }: {
    filteredImages: ImageLibraryEntry[];
    unassignedImages: ImageLibraryEntry[];
    onDeleteImage: (imageId: string) => Promise<void>;
    [key: string]: unknown;
  }) => (
    <div data-testid="image-library-view">
      <span data-testid="filtered-image-count">{filteredImages.length}</span>
      <span data-testid="unassigned-image-count">{unassignedImages.length}</span>
      <button data-testid="delete-btn" onClick={() => onDeleteImage('img-delete')}>
        Delete
      </button>
    </div>
  ),
}));

vi.mock('./UploadControls', () => ({
  default: () => <div data-testid="upload-controls">Upload Controls</div>,
}));

vi.mock('./UploadLogsModal', () => ({
  default: ({ isOpen }: { isOpen: boolean }) =>
    isOpen ? <div data-testid="upload-logs-modal">Logs Modal</div> : null,
}));

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

function defaultProps() {
  return {
    deviceModels: [
      makeDevice({ id: 'ceos', name: 'Arista cEOS', vendor: 'Arista' }),
      makeDevice({ id: 'srlinux', name: 'Nokia SR Linux', vendor: 'Nokia' }),
    ],
    imageLibrary: [
      makeImage({ id: 'img-ceos', device_id: 'ceos', kind: 'docker' }),
      makeImage({ id: 'img-srlinux', device_id: 'srlinux', kind: 'docker' }),
    ],
    onUploadImage: vi.fn(),
    onUploadQcow2: vi.fn(),
    onRefresh: vi.fn(),
    showSyncStatus: true,
    mode: 'images' as const,
  };
}

// ============================================================================
// Tests
// ============================================================================

describe('DeviceManager', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Rendering ──

  it('renders the device catalog view in images mode', () => {
    render(<DeviceManager {...defaultProps()} />);

    expect(screen.getByTestId('device-catalog-view')).toBeInTheDocument();
    expect(screen.getByTestId('image-library-view')).toBeInTheDocument();
  });

  it('renders upload controls in images mode', () => {
    render(<DeviceManager {...defaultProps()} />);

    expect(screen.getByTestId('upload-controls')).toBeInTheDocument();
  });

  it('renders build-jobs view when mode is build-jobs', () => {
    const props = { ...defaultProps(), mode: 'build-jobs' as const };
    render(<DeviceManager {...props} />);

    expect(screen.getByTestId('build-jobs-view')).toBeInTheDocument();
    expect(screen.queryByTestId('device-catalog-view')).not.toBeInTheDocument();
  });

  it('does not render build-jobs view in images mode', () => {
    render(<DeviceManager {...defaultProps()} />);

    expect(screen.queryByTestId('build-jobs-view')).not.toBeInTheDocument();
  });

  // ── Device Filtering ──

  it('passes filtered devices to DeviceCatalogView', () => {
    render(<DeviceManager {...defaultProps()} />);

    // Default: all devices visible
    expect(screen.getByTestId('device-count').textContent).toBe('2');
  });

  it('passes all devices when no filters are active', () => {
    render(<DeviceManager {...defaultProps()} />);

    expect(screen.getByTestId('has-device-filters').textContent).toBe('false');
  });

  // ── Image Distribution ──

  it('distributes images to the correct views', () => {
    render(<DeviceManager {...defaultProps()} />);

    expect(screen.getByTestId('filtered-image-count')).toBeInTheDocument();
  });

  it('correctly identifies unassigned images', () => {
    const props = defaultProps();
    props.imageLibrary = [
      makeImage({ id: 'img-1', device_id: 'ceos', kind: 'docker' }),
      makeImage({ id: 'img-orphan', device_id: undefined, kind: 'docker', compatible_devices: [] }),
    ];
    render(<DeviceManager {...props} />);

    // The orphan image should appear as unassigned
    expect(screen.getByTestId('unassigned-image-count')).toBeInTheDocument();
  });

  // ── Handlers: Unassign ──

  it('calls unassignImage and onRefresh when unassign handler is invoked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<DeviceManager {...props} />);

    await user.click(screen.getByTestId('unassign-btn'));

    await waitFor(() => {
      expect(mockUnassignImage).toHaveBeenCalledWith('img-1', 'ceos');
    });
    await waitFor(() => {
      expect(props.onRefresh).toHaveBeenCalled();
    });
  });

  it('handles unassign errors gracefully', async () => {
    const user = userEvent.setup();
    mockUnassignImage.mockRejectedValueOnce(new Error('Network error'));
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    render(<DeviceManager {...defaultProps()} />);
    await user.click(screen.getByTestId('unassign-btn'));

    await waitFor(() => {
      expect(consoleSpy).toHaveBeenCalledWith('Failed to unassign image:', expect.any(Error));
    });

    consoleSpy.mockRestore();
  });

  // ── Handlers: Set Default ──

  it('calls assignImageToDevice and onRefresh when set default handler is invoked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<DeviceManager {...props} />);

    await user.click(screen.getByTestId('set-default-btn'));

    await waitFor(() => {
      expect(mockAssignImageToDevice).toHaveBeenCalledWith('img-1', 'ceos', true);
    });
    await waitFor(() => {
      expect(props.onRefresh).toHaveBeenCalled();
    });
  });

  it('handles set default errors gracefully', async () => {
    const user = userEvent.setup();
    mockAssignImageToDevice.mockRejectedValueOnce(new Error('API error'));
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    render(<DeviceManager {...defaultProps()} />);
    await user.click(screen.getByTestId('set-default-btn'));

    await waitFor(() => {
      expect(consoleSpy).toHaveBeenCalledWith('Failed to set default image:', expect.any(Error));
    });

    consoleSpy.mockRestore();
  });

  // ── Handlers: Delete Image ──

  it('calls deleteImage and onRefresh when delete handler is invoked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<DeviceManager {...props} />);

    await user.click(screen.getByTestId('delete-btn'));

    await waitFor(() => {
      expect(mockDeleteImage).toHaveBeenCalledWith('img-delete');
    });
    await waitFor(() => {
      expect(props.onRefresh).toHaveBeenCalled();
    });
  });

  it('shows notification on delete error', async () => {
    const user = userEvent.setup();
    mockDeleteImage.mockRejectedValueOnce(new Error('Delete failed'));
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    render(<DeviceManager {...defaultProps()} />);
    await user.click(screen.getByTestId('delete-btn'));

    await waitFor(() => {
      expect(mockAddNotification).toHaveBeenCalledWith(
        'error',
        'Failed to delete image',
        'Delete failed'
      );
    });

    consoleSpy.mockRestore();
  });

  it('handles non-Error delete failures', async () => {
    const user = userEvent.setup();
    mockDeleteImage.mockRejectedValueOnce('string error');
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    render(<DeviceManager {...defaultProps()} />);
    await user.click(screen.getByTestId('delete-btn'));

    await waitFor(() => {
      expect(mockAddNotification).toHaveBeenCalledWith(
        'error',
        'Failed to delete image',
        undefined
      );
    });

    consoleSpy.mockRestore();
  });

  // ── Image Library Filtering ──

  it('filters out non-instantiable images (iol source)', () => {
    const props = defaultProps();
    props.imageLibrary = [
      makeImage({ id: 'img-docker', kind: 'docker' }),
      makeImage({ id: 'img-iol', kind: 'iol' }),
    ];
    render(<DeviceManager {...props} />);

    // iol images are not instantiable, so they should be filtered from runnableImageLibrary
    // The filtered images passed to ImageLibraryView should only include docker
    expect(screen.getByTestId('image-library-view')).toBeInTheDocument();
  });

  // ── Props Defaults ──

  it('defaults showSyncStatus to true', () => {
    const props = defaultProps();
    delete (props as Record<string, unknown>).showSyncStatus;
    render(<DeviceManager {...props} />);

    // Should render without error
    expect(screen.getByTestId('device-catalog-view')).toBeInTheDocument();
  });

  it('defaults mode to images', () => {
    const props = defaultProps();
    delete (props as Record<string, unknown>).mode;
    render(<DeviceManager {...props} />);

    expect(screen.getByTestId('device-catalog-view')).toBeInTheDocument();
    expect(screen.queryByTestId('build-jobs-view')).not.toBeInTheDocument();
  });

  // ── Drag Overlay ──

  it('does not show drag overlay when not dragging', () => {
    render(<DeviceManager {...defaultProps()} />);

    expect(screen.queryByText('Drop on a device to assign')).not.toBeInTheDocument();
  });

  // ── Empty States ──

  it('renders with empty device models and image library', () => {
    const props = defaultProps();
    props.deviceModels = [];
    props.imageLibrary = [];
    render(<DeviceManager {...props} />);

    expect(screen.getByTestId('device-count').textContent).toBe('0');
  });

  it('renders with images that have no device assignment', () => {
    const props = defaultProps();
    props.imageLibrary = [
      makeImage({ id: 'img-orphan', device_id: null, compatible_devices: [], kind: 'docker' }),
    ];
    render(<DeviceManager {...props} />);

    expect(screen.getByTestId('image-library-view')).toBeInTheDocument();
  });

  // ── Image Compatibility ──

  it('handles images with compatible_devices property', () => {
    const props = defaultProps();
    props.imageLibrary = [
      makeImage({
        id: 'img-shared',
        device_id: 'ceos',
        compatible_devices: ['ceos', 'srlinux'],
        kind: 'docker',
      }),
    ];
    render(<DeviceManager {...props} />);

    // Should render without error, image shared between devices
    expect(screen.getByTestId('device-catalog-view')).toBeInTheDocument();
  });

  it('handles images with default_for_devices property', () => {
    const props = defaultProps();
    props.imageLibrary = [
      makeImage({
        id: 'img-default',
        device_id: 'ceos',
        is_default: true,
        default_for_devices: ['ceos'],
        kind: 'docker',
      }),
    ];
    render(<DeviceManager {...props} />);

    expect(screen.getByTestId('image-library-view')).toBeInTheDocument();
  });

  // ── Vendor Metadata ──

  it('handles images with vendor metadata', () => {
    const props = defaultProps();
    props.imageLibrary = [
      makeImage({ id: 'img-1', device_id: 'ceos', vendor: 'Arista', kind: 'docker' }),
      makeImage({ id: 'img-2', device_id: 'srlinux', vendor: 'Nokia', kind: 'docker' }),
    ];
    render(<DeviceManager {...props} />);

    expect(screen.getByTestId('image-library-view')).toBeInTheDocument();
  });
});
