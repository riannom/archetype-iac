import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import DeviceCatalogView from './DeviceCatalogView';
import type { DeviceModel, ImageLibraryEntry } from '../../types';
import { DeviceType } from '../../types';

// Mock DeviceCard - renders a simplified version for testing
vi.mock('../DeviceCard', () => ({
  default: ({
    device,
    assignedImages,
    isSelected,
    onSelect,
  }: {
    device: DeviceModel;
    assignedImages: ImageLibraryEntry[];
    isSelected: boolean;
    onSelect: () => void;
    onUnassignImage: (imageId: string, deviceId?: string) => void;
    onSetDefaultImage: (imageId: string) => void;
  }) => (
    <div
      data-testid={`device-card-${device.id}`}
      data-selected={isSelected}
      onClick={onSelect}
    >
      <span data-testid="device-name">{device.name}</span>
      <span data-testid="image-count">{assignedImages.length}</span>
    </div>
  ),
}));

// Mock FilterChip
vi.mock('../FilterChip', () => ({
  default: ({
    label,
    isActive,
    onClick,
  }: {
    label: string;
    isActive: boolean;
    onClick: () => void;
  }) => (
    <button
      data-testid={`filter-chip-${label}`}
      data-active={isActive}
      onClick={onClick}
    >
      {label}
    </button>
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
    filteredDevices: [
      makeDevice({ id: 'ceos', name: 'Arista cEOS', vendor: 'Arista' }),
      makeDevice({ id: 'srlinux', name: 'Nokia SR Linux', vendor: 'Nokia' }),
    ],
    imagesByDevice: new Map<string, ImageLibraryEntry[]>([
      ['ceos', [makeImage({ id: 'img-1' }), makeImage({ id: 'img-2' })]],
    ]),
    deviceSearch: '',
    setDeviceSearch: vi.fn(),
    deviceSort: 'vendor' as const,
    setDeviceSort: vi.fn(),
    deviceImageStatus: 'all' as const,
    setDeviceImageStatus: vi.fn(),
    deviceVendors: ['Arista', 'Nokia'],
    selectedDeviceVendors: new Set<string>(),
    toggleDeviceVendor: vi.fn(),
    hasDeviceFilters: false,
    clearDeviceFilters: vi.fn(),
    onUnassignImage: vi.fn(),
    onSetDefaultImage: vi.fn(),
  };
}

// ============================================================================
// Tests
// ============================================================================

describe('DeviceCatalogView', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Rendering ──

  it('renders device cards for each filtered device', () => {
    render(<DeviceCatalogView {...defaultProps()} />);

    expect(screen.getByTestId('device-card-ceos')).toBeInTheDocument();
    expect(screen.getByTestId('device-card-srlinux')).toBeInTheDocument();
  });

  it('passes correct images to each device card', () => {
    render(<DeviceCatalogView {...defaultProps()} />);

    const ceosCard = screen.getByTestId('device-card-ceos');
    expect(within(ceosCard).getByTestId('image-count').textContent).toBe('2');

    const srlinuxCard = screen.getByTestId('device-card-srlinux');
    expect(within(srlinuxCard).getByTestId('image-count').textContent).toBe('0');
  });

  it('shows empty state when no devices match filters', () => {
    const props = defaultProps();
    props.filteredDevices = [];
    render(<DeviceCatalogView {...props} />);

    expect(screen.getByText('No devices match your filters')).toBeInTheDocument();
  });

  // ── Search ──

  it('renders search input with current value', () => {
    const props = defaultProps();
    props.deviceSearch = 'arista';
    render(<DeviceCatalogView {...props} />);

    const input = screen.getByPlaceholderText('Search devices...');
    expect(input).toHaveValue('arista');
  });

  it('calls setDeviceSearch on input change', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<DeviceCatalogView {...props} />);

    const input = screen.getByPlaceholderText('Search devices...');
    await user.type(input, 'test');

    expect(props.setDeviceSearch).toHaveBeenCalled();
  });

  it('shows clear button when search has value', () => {
    const props = defaultProps();
    props.deviceSearch = 'query';
    render(<DeviceCatalogView {...props} />);

    // The clear button (xmark icon) should be visible
    const input = screen.getByPlaceholderText('Search devices...');
    const container = input.parentElement!;
    const clearButton = container.querySelector('button');
    expect(clearButton).toBeInTheDocument();
  });

  it('does not show clear button when search is empty', () => {
    const props = defaultProps();
    props.deviceSearch = '';
    render(<DeviceCatalogView {...props} />);

    const input = screen.getByPlaceholderText('Search devices...');
    const container = input.parentElement!;
    const clearButton = container.querySelector('button');
    expect(clearButton).not.toBeInTheDocument();
  });

  it('calls setDeviceSearch with empty string when clear button is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    props.deviceSearch = 'query';
    render(<DeviceCatalogView {...props} />);

    const input = screen.getByPlaceholderText('Search devices...');
    const container = input.parentElement!;
    const clearButton = container.querySelector('button')!;
    await user.click(clearButton);

    expect(props.setDeviceSearch).toHaveBeenCalledWith('');
  });

  // ── Sort ──

  it('renders sort select with current value', () => {
    const props = defaultProps();
    props.deviceSort = 'name';
    render(<DeviceCatalogView {...props} />);

    const select = screen.getByRole('combobox');
    expect(select).toHaveValue('name');
  });

  it('calls setDeviceSort when sort option changes', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<DeviceCatalogView {...props} />);

    const select = screen.getByRole('combobox');
    await user.selectOptions(select, 'type');

    expect(props.setDeviceSort).toHaveBeenCalledWith('type');
  });

  it('has all three sort options available', () => {
    render(<DeviceCatalogView {...defaultProps()} />);

    const options = screen.getAllByRole('option');
    const values = options.map((opt) => (opt as HTMLOptionElement).value);
    expect(values).toContain('vendor');
    expect(values).toContain('name');
    expect(values).toContain('type');
  });

  // ── Image Status Filter Chips ──

  it('renders Has Image and No Image filter chips', () => {
    render(<DeviceCatalogView {...defaultProps()} />);

    expect(screen.getByTestId('filter-chip-Has Image')).toBeInTheDocument();
    expect(screen.getByTestId('filter-chip-No Image')).toBeInTheDocument();
  });

  it('marks Has Image chip as active when deviceImageStatus is has_image', () => {
    const props = defaultProps();
    props.deviceImageStatus = 'has_image';
    render(<DeviceCatalogView {...props} />);

    expect(screen.getByTestId('filter-chip-Has Image')).toHaveAttribute('data-active', 'true');
    expect(screen.getByTestId('filter-chip-No Image')).toHaveAttribute('data-active', 'false');
  });

  it('toggles Has Image filter on click', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<DeviceCatalogView {...props} />);

    await user.click(screen.getByTestId('filter-chip-Has Image'));

    expect(props.setDeviceImageStatus).toHaveBeenCalledWith('has_image');
  });

  it('resets Has Image filter to "all" when already active', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    props.deviceImageStatus = 'has_image';
    render(<DeviceCatalogView {...props} />);

    await user.click(screen.getByTestId('filter-chip-Has Image'));

    expect(props.setDeviceImageStatus).toHaveBeenCalledWith('all');
  });

  it('toggles No Image filter on click', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<DeviceCatalogView {...props} />);

    await user.click(screen.getByTestId('filter-chip-No Image'));

    expect(props.setDeviceImageStatus).toHaveBeenCalledWith('no_image');
  });

  it('resets No Image filter to "all" when already active', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    props.deviceImageStatus = 'no_image';
    render(<DeviceCatalogView {...props} />);

    await user.click(screen.getByTestId('filter-chip-No Image'));

    expect(props.setDeviceImageStatus).toHaveBeenCalledWith('all');
  });

  // ── Vendor Filter Chips ──

  it('renders vendor filter chips for each vendor', () => {
    render(<DeviceCatalogView {...defaultProps()} />);

    expect(screen.getByTestId('filter-chip-Arista')).toBeInTheDocument();
    expect(screen.getByTestId('filter-chip-Nokia')).toBeInTheDocument();
  });

  it('does not render vendor chips when no vendors exist', () => {
    const props = defaultProps();
    props.deviceVendors = [];
    render(<DeviceCatalogView {...props} />);

    expect(screen.queryByText('Vendor:')).not.toBeInTheDocument();
  });

  it('marks selected vendor chips as active', () => {
    const props = defaultProps();
    props.selectedDeviceVendors = new Set(['Arista']);
    render(<DeviceCatalogView {...props} />);

    expect(screen.getByTestId('filter-chip-Arista')).toHaveAttribute('data-active', 'true');
    expect(screen.getByTestId('filter-chip-Nokia')).toHaveAttribute('data-active', 'false');
  });

  it('calls toggleDeviceVendor when vendor chip is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<DeviceCatalogView {...props} />);

    await user.click(screen.getByTestId('filter-chip-Nokia'));

    expect(props.toggleDeviceVendor).toHaveBeenCalledWith('Nokia');
  });

  // ── Clear Filters ──

  it('shows clear button when filters are active', () => {
    const props = defaultProps();
    props.hasDeviceFilters = true;
    render(<DeviceCatalogView {...props} />);

    expect(screen.getByText('Clear')).toBeInTheDocument();
  });

  it('does not show clear button when no filters are active', () => {
    const props = defaultProps();
    props.hasDeviceFilters = false;
    render(<DeviceCatalogView {...props} />);

    expect(screen.queryByText('Clear')).not.toBeInTheDocument();
  });

  it('calls clearDeviceFilters when clear button is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    props.hasDeviceFilters = true;
    render(<DeviceCatalogView {...props} />);

    await user.click(screen.getByText('Clear'));

    expect(props.clearDeviceFilters).toHaveBeenCalledTimes(1);
  });

  // ── Device Selection ──

  it('selects a device on click', async () => {
    const user = userEvent.setup();
    render(<DeviceCatalogView {...defaultProps()} />);

    const ceosCard = screen.getByTestId('device-card-ceos');
    expect(ceosCard).toHaveAttribute('data-selected', 'false');

    await user.click(ceosCard);

    expect(ceosCard).toHaveAttribute('data-selected', 'true');
  });

  it('only one device is selected at a time', async () => {
    const user = userEvent.setup();
    render(<DeviceCatalogView {...defaultProps()} />);

    await user.click(screen.getByTestId('device-card-ceos'));
    expect(screen.getByTestId('device-card-ceos')).toHaveAttribute('data-selected', 'true');

    await user.click(screen.getByTestId('device-card-srlinux'));
    expect(screen.getByTestId('device-card-ceos')).toHaveAttribute('data-selected', 'false');
    expect(screen.getByTestId('device-card-srlinux')).toHaveAttribute('data-selected', 'true');
  });

  // ── Edge Cases ──

  it('renders with empty imagesByDevice map', () => {
    const props = defaultProps();
    props.imagesByDevice = new Map();
    render(<DeviceCatalogView {...props} />);

    const ceosCard = screen.getByTestId('device-card-ceos');
    expect(within(ceosCard).getByTestId('image-count').textContent).toBe('0');
  });

  it('renders single device correctly', () => {
    const props = defaultProps();
    props.filteredDevices = [makeDevice({ id: 'solo', name: 'Solo Device' })];
    render(<DeviceCatalogView {...props} />);

    expect(screen.getByTestId('device-card-solo')).toBeInTheDocument();
    expect(screen.queryByText('No devices match your filters')).not.toBeInTheDocument();
  });
});
